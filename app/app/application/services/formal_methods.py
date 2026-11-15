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
from app.domain.knowbots.formal_method_execution import (
    EXECUTION_CONTRACT,
    DecideFormalExecution,
    ExecuteFormalMethod,
    FormalExecutionDecision,
    FormalMethodExecutionRequest,
    FormalMethodExecutionResult,
    FormalTestSuite,
)
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

    async def test_suite(
        self, owner: str, request_id: str, suite_id: str, version: int
    ):
        async with self.repo.transaction():
            request = await self.workdrafts._request(owner, request_id)
            document = await self.repo.formal_test_suite(
                owner, request.id, parse_id(suite_id, "suite"), version
            )
        if document is None:
            raise NotFoundError("No such test suite", code="formal_suite_not_found")
        return FormalTestSuite.model_validate(document).model_dump(mode="json")

    @staticmethod
    def _execution_result(raw, *, run_id, request_id, method_id):
        try:
            result = FormalMethodExecutionResult.model_validate(raw)
            if (
                result.run_id != run_id
                or result.request_id != request_id
                or result.method_id != method_id
            ):
                raise ValueError("Execution belongs to another author request")
            return result
        except ValueError as exc:
            raise BadGatewayError(
                "Invalid formal execution response", code="formal_execution_invalid"
            ) from exc

    @staticmethod
    def _execution_cases(result: FormalMethodExecutionResult, suite: FormalTestSuite):
        expected = {
            (case.case_id, repetition): case.caller
            for case in suite.cases
            for repetition in range(1, case.repetitions + 1)
        }
        observed = {
            (case.case_id, case.repetition): case.caller for case in result.cases
        }
        if (
            any(expected.get(key) != caller for key, caller in observed.items())
            or result.status == "passed"
            and observed != expected
        ):
            raise BadGatewayError(
                "Execution cases differ from the frozen test suite",
                code="formal_execution_invalid",
            )

    async def execute(self, owner: str, method_id: str, command: ExecuteFormalMethod):
        mid = parse_id(method_id, "method")
        async with self.repo.transaction():
            request, _, admission, _ = await self._prepare(
                owner,
                mid,
                ValidateFormalMethod(
                    request_id=command.request_id,
                    revision=command.revision,
                    run_id=command.run_id,
                ),
            )
            try:
                payload = FormalMethodExecutionRequest.model_validate(
                    {
                        **admission.model_dump(mode="json", by_alias=True),
                        "contract": EXECUTION_CONTRACT,
                        "execution": command.execution.freeze().model_dump(mode="json"),
                    }
                )
            except ValueError as exc:
                raise BadRequestError(
                    str(exc), code="formal_execution_unsupported"
                ) from exc
            await self.repo.freeze_formal_test_suite(
                owner, request.id, payload.execution.suite.model_dump(mode="json")
            )
        raw = await self.runtimes.submit_formal_method_execution(
            request.runtime, payload
        )
        result = self._execution_result(
            raw, run_id=payload.run_id, request_id=request.id, method_id=mid
        )
        if (
            result.request_digest != payload.fingerprint()
            or result.suite != payload.execution.suite.reference()
        ):
            raise BadGatewayError(
                "Execution changed the frozen test request",
                code="formal_execution_invalid",
            )
        if result.admission.receipt:
            self._receipt(admission, result.admission)
        self._execution_cases(result, payload.execution.suite)
        return result.model_dump(mode="json")

    async def execution(self, owner: str, method_id: str, request_id: str, run_id: str):
        async with self.repo.transaction():
            request = await self.workdrafts._request(owner, request_id)
        rid, mid = parse_id(run_id, "execution"), parse_id(method_id, "method")
        result = self._execution_result(
            await self.runtimes.formal_method_execution(
                request.runtime, owner, str(rid)
            ),
            run_id=rid,
            request_id=request.id,
            method_id=mid,
        )
        async with self.repo.transaction():
            suite = await self.repo.formal_test_suite(
                owner, request.id, result.suite.suite_id, result.suite.version
            )
        if suite is None or suite["digest"] != result.suite.digest:
            raise BadGatewayError(
                "Execution test suite is not frozen by this author",
                code="formal_execution_invalid",
            )
        self._execution_cases(result, FormalTestSuite.model_validate(suite))
        return result.model_dump(mode="json")

    async def cancel_execution(
        self, owner: str, method_id: str, request_id: str, run_id: str
    ):
        # Resolve the immutable run through this author/name/method before mutation.
        await self.execution(owner, method_id, request_id, run_id)
        async with self.repo.transaction():
            request = await self.workdrafts._request(owner, request_id)
        raw = await self.runtimes.cancel_formal_method_execution(
            request.runtime, owner, run_id
        )
        return self._execution_result(
            raw,
            run_id=parse_id(run_id, "execution"),
            request_id=request.id,
            method_id=parse_id(method_id, "method"),
        ).model_dump(mode="json")

    async def decide_execution(
        self,
        owner: str,
        method_id: str,
        run_id: str,
        call_id: str,
        command: DecideFormalExecution,
    ):
        current = FormalMethodExecutionResult.model_validate(
            await self.execution(owner, method_id, str(command.request_id), run_id)
        )
        cid = parse_id(call_id, "call")
        action = next((a for a in current.actions if a.call_id == cid), None)
        if action is not None and (
            action.case_id,
            action.repetition,
            action.token,
            action.subject_digest,
        ) != (
            command.case_id,
            command.repetition,
            command.token,
            command.subject_digest,
        ):
            raise ConcurrencyConflictError(
                "Approval no longer matches this execution",
                code="formal_execution_action_stale",
            )
        async with self.repo.transaction():
            request = await self.workdrafts._request(owner, str(command.request_id))
        decision = FormalExecutionDecision.model_validate(
            {
                **command.model_dump(mode="json", exclude={"request_id"}),
                "owner_subject": owner,
            }
        )
        raw = await self.runtimes.decide_formal_method_execution(
            request.runtime, run_id, str(cid), decision
        )
        return self._execution_result(
            raw,
            run_id=current.run_id,
            request_id=request.id,
            method_id=current.method_id,
        ).model_dump(mode="json")

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
            or "model" in payload.draft.allowed_callers
            and receipt.execution is None
            or receipt.execution is not None
            and not receipt.execution.covers_callers(payload.draft.allowed_callers)
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
        if result.receipt is not None and result.receipt.execution is not None:
            execution = FormalMethodExecutionResult.model_validate(
                await self.execution(
                    owner, method_id, str(command.request_id), str(command.run_id)
                )
            )
            if execution.admission.receipt != result.receipt:
                raise ConcurrencyConflictError(
                    "Execution receipt changed", code="formal_validation_stale"
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
