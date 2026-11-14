"""Formal incubation uses the existing workdraft and immutable method repository.

Runtime receipts are obtained over the authenticated service boundary. Neither
browser evidence IDs nor legacy expert test results confer formal admission.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.application.errors.exceptions import (
    BadGatewayError,
    BadRequestError,
    ConcurrencyConflictError,
    NotFoundError,
)
from app.application.ports.knowbots import (
    KnowlabRepository,
    MethodVersionRecord,
    RuntimeTestClient,
)
from app.application.services.knowbot_service import parse_id
from app.application.services.knowbot_workdraft import (
    KnowbotWorkdraftService,
    parse_body,
)
from app.domain.knowbots.formal_method import (
    FormalMethodDefinition,
    MethodRef,
    Orchestration,
)
from app.domain.knowbots.formal_method_admission import (
    CONTRACT,
    FormalMethodRegistrationResult,
    FormalMethodValidationReceipt,
    FormalMethodValidationRequest,
    FormalMethodValidationResult,
    FreezeFormalMethod,
    FrozenFormalMethod,
    ValidateFormalMethod,
)
from app.domain.knowbots.formal_method_compiler import FrozenMethod, compile_method
from app.domain.knowbots.workdraft import record_ids, source_problems


def admitted_definition(record: MethodVersionRecord) -> FormalMethodDefinition:
    """Old method versions remain history; they never become formal dependencies."""
    try:
        definition = FormalMethodDefinition.model_validate(record.definition)
        receipt = FormalMethodValidationReceipt.model_validate(
            record.provenance["validation"]
        )
        if (
            definition.digest() != record.digest
            or receipt.definition_digest != record.digest
            or receipt.method_id != record.method_id
            or receipt.owner_subject != record.owner_subject
            or record.provenance.get("contract") != CONTRACT
        ):
            raise ValueError("Formal method identity mismatch")
        return definition
    except (ValueError, KeyError) as exc:
        raise ConcurrencyConflictError(
            "This version is not an admitted formal method",
            code="formal_method_unadmitted",
        ) from exc


class FormalMethodService:
    def __init__(self, repo: KnowlabRepository, runtimes: RuntimeTestClient):
        self.repo = repo
        self.runtimes = runtimes
        self.workdrafts = KnowbotWorkdraftService(repo)

    async def get(
        self, owner: str, method_id: str, version: int
    ) -> MethodVersionRecord:
        found = await self.repo.method_version(parse_id(method_id, "method"), version)
        if found is None or found.owner_subject != owner:
            raise NotFoundError("No such formal method", code="method_not_found")
        admitted_definition(found)
        return found

    async def sources(self, owner: str, request_id: str) -> dict[str, Any]:
        async with self.repo.transaction():
            request = await self.workdrafts._request(owner, request_id)
        return await self.runtimes.formal_method_sources(request.runtime, owner)

    async def listing(self, owner: str, *, limit: int, offset: int):
        if not 1 <= limit <= 100 or offset < 0:
            raise BadRequestError("Invalid method page", code="method_page")
        # The runtime owns registration. A locally committed version awaiting
        # delivery is recoverable by ID, but is not an author-selectable method.
        visible: list[dict[str, Any]] = []
        scanned = 0
        while len(visible) < offset + limit:
            async with self.repo.transaction():
                rows = await self.repo.list_methods(
                    owner, limit=100, offset=scanned, formal_only=True
                )
                candidates = []
                for row in rows:
                    record = await self.get(owner, str(row["id"]), row["version"])
                    validation = FormalMethodValidationReceipt.model_validate(
                        record.provenance["validation"]
                    )
                    request = await self.workdrafts._request(
                        owner, str(validation.request_id)
                    )
                    candidates.append((row, request.runtime))
            for row, runtime in candidates:
                reference = MethodRef(
                    method_id=row["id"], version=row["version"], digest=row["digest"]
                )
                if await self._registered(runtime, owner, reference):
                    visible.append(
                        {
                            "method_id": str(row["id"]),
                            "latest_version": row["version"],
                            "digest": row["digest"],
                            "method_kind": row["method_kind"],
                            "title": str(row.get("title") or ""),
                            "summary": str(row.get("summary") or "")[:500],
                            "created_at": row["created_at"].isoformat(),
                        }
                    )
            scanned += len(rows)
            if len(rows) < 100:
                break
        return visible[offset : offset + limit]

    async def _registered(self, runtime: str, owner: str, reference: MethodRef) -> bool:
        raw = await self.runtimes.formal_method_registration(
            runtime, owner, str(reference.method_id), reference.version
        )
        if raw is None:
            return False
        try:
            result = FormalMethodRegistrationResult.model_validate(raw)
            if result.reference != reference or result.owner_subject != owner:
                raise ValueError("Different registered method")
        except ValueError as exc:
            raise BadGatewayError(
                "Invalid method registration response",
                code="formal_registration_invalid",
            ) from exc
        return True

    async def _dependencies(self, owner: str, definition: FormalMethodDefinition):
        resolved: dict[tuple[UUID, int], FrozenMethod] = {}

        async def visit(current: FormalMethodDefinition, depth: int):
            if depth > 16 or len(resolved) > 1000:
                raise BadRequestError(
                    "Method dependency limit", code="formal_method_invalid"
                )
            if not isinstance(current.procedure, Orchestration):
                return
            for step in current.procedure.steps:
                for ref in step.candidates:
                    key = (ref.method_id, ref.version)
                    if key in resolved:
                        if resolved[key].reference != ref:
                            raise BadRequestError(
                                "Conflicting dependency digest",
                                code="formal_method_invalid",
                            )
                        continue
                    record = await self.get(owner, str(ref.method_id), ref.version)
                    if record.digest != ref.digest:
                        raise ConcurrencyConflictError(
                            "Method digest changed", code="method_integrity"
                        )
                    child = admitted_definition(record)
                    resolved[key] = FrozenMethod(ref, child)
                    await visit(child, depth + 1)

        await visit(definition, 0)
        return resolved

    async def _prepare(
        self, owner: str, method_id: UUID, command: ValidateFormalMethod
    ):
        method_owner = await self.repo.method_owner(method_id)
        if method_owner is not None and method_owner != owner:
            raise NotFoundError("No such method", code="method_not_found")
        request = await self.workdrafts._request(owner, str(command.request_id))
        current = await self.workdrafts.locked(request, command.revision)
        body = parse_body(current.body)
        draft = body.formal_method
        if draft is None:
            raise BadRequestError(
                "Save a formal method definition first", code="formal_method_missing"
            )
        entries = await self.repo.list_workdraft_entries(record_ids(current.sources))
        problems = source_problems(
            body,
            current.sources,
            workdraft_id=current.id,
            owner_subject=owner,
            entries={entry.id: entry for entry in entries},
            answered=await self.workdrafts._answered(request),
            ideas=await self.workdrafts._ideas(request),
        )
        if problems:
            raise BadRequestError(
                "Confirm the author's definition and source first",
                code="method_source_invalid",
            )
        resolved = await self._dependencies(owner, draft.definition)
        try:
            compiled = compile_method(method_id, draft.definition, resolved)
        except ValueError as exc:
            raise BadRequestError(str(exc), code="formal_method_invalid") from exc
        payload = FormalMethodValidationRequest(
            contract=CONTRACT,
            run_id=command.run_id,
            owner_subject=owner,
            request_id=request.id,
            workdraft_id=current.id,
            revision=current.revision,
            method_id=method_id,
            draft=draft,
            dependencies=compiled.dependencies,
        )
        return request, current, payload, compiled

    @staticmethod
    def _result(raw: Any, run_id: UUID) -> FormalMethodValidationResult:
        try:
            result = FormalMethodValidationResult.model_validate(raw)
            if result.run_id != run_id:
                raise ValueError("Different validation run")
            return result
        except ValueError as exc:
            raise BadGatewayError(
                "Invalid formal method validation response",
                code="formal_validation_invalid",
            ) from exc

    @staticmethod
    def _receipt(
        payload: FormalMethodValidationRequest, result: FormalMethodValidationResult
    ):
        receipt = result.receipt
        if receipt is None:
            raise ConcurrencyConflictError(
                "Method validation is not complete", code="formal_validation_pending"
            )
        source_digest = (
            payload.draft.source.fingerprint() if payload.draft.source else None
        )
        if (
            any(
                getattr(receipt, key) != getattr(payload, key)
                for key in (
                    "run_id",
                    "owner_subject",
                    "request_id",
                    "workdraft_id",
                    "revision",
                    "method_id",
                )
            )
            or receipt.definition_digest != payload.draft.definition.digest()
            or receipt.source_digest != source_digest
            or set(receipt.dependencies) != set(payload.dependencies)
        ):
            raise ConcurrencyConflictError(
                "Validation refers to another definition or source revision",
                code="formal_validation_stale",
            )
        return receipt

    async def validate(self, owner: str, method_id: str, command: ValidateFormalMethod):
        async with self.repo.transaction():
            request, _, payload, _ = await self._prepare(
                owner, parse_id(method_id, "method"), command
            )
        # No database transaction is held while the runtime validates/executes.
        for reference in payload.dependencies:
            if not await self._registered(request.runtime, owner, reference):
                raise ConcurrencyConflictError(
                    "A dependency is frozen locally but not registered for execution",
                    code="formal_method_unregistered",
                )
        raw = await self.runtimes.submit_formal_method_validation(
            request.runtime, payload
        )
        result = self._result(raw, command.run_id)
        if result.receipt is not None:
            self._receipt(payload, result)
        return result.model_dump(mode="json")

    async def validation(
        self, owner: str, method_id: str, request_id: str, run_id: str
    ):
        async with self.repo.transaction():
            request = await self.workdrafts._request(owner, request_id)
        rid = parse_id(run_id, "validation")
        result = self._result(
            await self.runtimes.formal_method_validation(
                request.runtime, owner, str(rid)
            ),
            rid,
        )
        if result.receipt is not None and (
            result.receipt.owner_subject != owner
            or result.receipt.request_id != request.id
            or result.receipt.method_id != parse_id(method_id, "method")
        ):
            raise NotFoundError(
                "No such validation", code="formal_validation_not_found"
            )
        return result.model_dump(mode="json")

    async def freeze(
        self, owner: str, method_id: str, version: int, command: FreezeFormalMethod
    ):
        mid = parse_id(method_id, "method")
        async with self.repo.transaction():
            request = await self.workdrafts._request(owner, str(command.request_id))
            frozen = await self.repo.method_version(mid, version)
            if frozen is not None:
                if frozen.owner_subject != owner:
                    raise NotFoundError("No such method", code="method_not_found")
                admitted_definition(frozen)
                if frozen.command_digest != command.digest():
                    raise ConcurrencyConflictError(
                        "Version already records another freeze",
                        code="method_version_conflict",
                    )
        if frozen is None:
            frozen = await self._freeze_version(owner, method_id, version, command)
        # Freeze commits before registration. Retrying the same command sends
        # this exact stored snapshot, even if the workdraft has since changed.
        payload = FrozenFormalMethod.model_validate(
            {
                "contract": "knowlab/frozen-formal-method/v1",
                "reference": {
                    "method_id": str(frozen.method_id),
                    "version": frozen.version,
                    "digest": frozen.digest,
                },
                "owner_subject": frozen.owner_subject,
                "draft": {
                    "definition": frozen.definition,
                    "source": frozen.provenance["source"],
                    "allowed_callers": frozen.provenance["allowed_callers"],
                },
                "validation": frozen.provenance["validation"],
                "visibility": frozen.provenance["visibility"],
            }
        )
        raw = await self.runtimes.register_formal_method(request.runtime, payload)
        try:
            registered = FormalMethodRegistrationResult.model_validate(raw)
            if (
                registered.reference != payload.reference
                or registered.owner_subject != owner
            ):
                raise ValueError("Different registered method")
        except ValueError as exc:
            raise BadGatewayError(
                "Invalid formal method registration response",
                code="formal_registration_invalid",
            ) from exc
        return frozen

    async def _freeze_version(
        self, owner: str, method_id: str, version: int, command: FreezeFormalMethod
    ):
        mid = parse_id(method_id, "method")
        async with self.repo.transaction():
            request = await self.workdrafts._request(owner, str(command.request_id))
        result = self._result(
            await self.runtimes.formal_method_validation(
                request.runtime, owner, str(command.run_id)
            ),
            command.run_id,
        )
        async with self.repo.transaction():
            await self.repo.lock_method(mid)
            have_owner = await self.repo.method_owner(mid)
            if have_owner is not None and have_owner != owner:
                raise NotFoundError("No such method", code="method_not_found")
            existing = await self.repo.method_version(mid, version)
            if existing is not None:
                admitted_definition(existing)
                if existing.command_digest != command.digest():
                    raise ConcurrencyConflictError(
                        "Version already records another freeze",
                        code="method_version_conflict",
                    )
                return existing
            head = await self.repo.method_head(mid)
            if version != head + 1:
                raise ConcurrencyConflictError(
                    "Freeze the next method version", code="method_version_conflict"
                )
            if head:
                await self.get(owner, method_id, head)
            request, current, payload, compiled = await self._prepare(
                owner,
                mid,
                ValidateFormalMethod(
                    request_id=command.request_id,
                    revision=command.revision,
                    run_id=command.run_id,
                ),
            )
            receipt = self._receipt(payload, result)
            if command.visibility == "public" and (
                receipt.uncovered or "business" not in receipt.checks
            ):
                raise BadRequestError(
                    "Public methods require complete declared coverage",
                    code="formal_validation_uncovered",
                )
            provenance = {
                "contract": CONTRACT,
                "source": payload.draft.source.model_dump(mode="json")
                if payload.draft.source
                else None,
                "allowed_callers": list(payload.draft.allowed_callers),
                "validation": receipt.model_dump(mode="json"),
                "visibility": command.visibility,
                "method_kind": compiled.method_kind,
                "dispatch": compiled.dispatch,
                "dependencies": [
                    ref.model_dump(mode="json") for ref in compiled.dependencies
                ],
                "interfaces": compiled.interfaces,
                "confirmed_by": owner,
                "workdraft_id": str(current.id),
                "revision": current.revision,
            }
            if have_owner is None:
                await self.repo.create_method(mid, owner)
            await self.repo.insert_method_version(
                method_id=mid,
                version=version,
                digest=compiled.definition_digest,
                command_digest=command.digest(),
                definition=payload.draft.definition.document(),
                provenance=provenance,
                source_workdraft_id=current.id,
            )
            frozen = await self.repo.method_version(mid, version)
            assert frozen is not None
            return frozen
