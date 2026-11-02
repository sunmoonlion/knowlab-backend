"""Author-only freeze/read and binding preview for independent method versions.

One existing repository transaction owns the method and workdraft locks. This
service cannot alter a released expert or attach a method without a later,
explicit expert-reference operation. Preview is not publication or execution.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.application.errors.exceptions import (
    BadRequestError,
    ConcurrencyConflictError,
    NotFoundError,
)
from app.application.ports.knowbots import KnowlabRepository, MethodVersionRecord
from app.application.services.knowbot_service import parse_id
from app.application.services.knowbot_workdraft import (
    KnowbotWorkdraftService,
    parse_body,
)
from app.domain.knowbots.methods import (
    BindMethod,
    FreezeMethod,
    MethodDefinition,
    bind,
    freeze,
)


def method_version_view(record: MethodVersionRecord) -> dict[str, Any]:
    return {
        "method_id": str(record.method_id),
        "version": record.version,
        "digest": record.digest,
        "definition": record.definition,
        "provenance": record.provenance,
        "created_at": record.created_at.isoformat(),
    }


def _definition(record: MethodVersionRecord) -> MethodDefinition:
    try:
        value = MethodDefinition.model_validate(record.definition)
        if value.digest() != record.digest:
            raise ValueError("method digest mismatch")
        return value
    except ValueError:
        raise ConcurrencyConflictError(
            "Stored method definition failed integrity checks", code="method_integrity"
        ) from None


class KnowbotMethodService:
    def __init__(self, repo: KnowlabRepository) -> None:
        self.repo = repo
        self.workdrafts = KnowbotWorkdraftService(repo)

    async def get(
        self, owner: str, method_id: str, version: int
    ) -> MethodVersionRecord:
        record = await self.repo.method_version(parse_id(method_id, "method"), version)
        if record is None or record.owner_subject != owner:
            raise NotFoundError("No such method version", code="method_not_found")
        _definition(record)
        return record

    async def listing(
        self, owner: str, *, limit: int, offset: int
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100 or offset < 0:
            raise BadRequestError("Invalid method page", code="method_page")
        return [
            {
                "method_id": str(row["id"]),
                "latest_version": row["version"],
                "created_at": row["created_at"].isoformat(),
            }
            for row in await self.repo.list_methods(owner, limit=limit, offset=offset)
        ]

    async def publish(
        self, owner: str, method_id: str, version: int, command: FreezeMethod
    ) -> MethodVersionRecord:
        if not 1 <= version <= 999999:
            raise BadRequestError("Invalid method version", code="method_version")
        mid = parse_id(method_id, "method")
        async with self.repo.transaction():
            # Always method -> source request. Workdraft writers never lock a
            # method; concurrent method freezes from one draft use this order.
            await self.repo.lock_method(mid)
            have_owner = await self.repo.method_owner(mid)
            if have_owner is not None and have_owner != owner:
                raise NotFoundError("No such method", code="method_not_found")
            existing = await self.repo.method_version(mid, version)
            if existing is not None:
                if existing.command_digest != command.digest():
                    raise ConcurrencyConflictError(
                        "This version already records a different freeze",
                        code="method_version_conflict",
                    )
                _definition(existing)
                return existing
            if version != 1 + await self.repo.method_head(mid):
                raise ConcurrencyConflictError(
                    "Publish the next method version", code="method_version_conflict"
                )
            request = await self.workdrafts._request(owner, str(command.request_id))
            current = await self.workdrafts.locked(request, command.revision)
            # Reuse author acceptance and compiler checks; don't turn unaccepted
            # AI proposals into methods via a new endpoint.
            _, problems = await self.workdrafts._compile(request, current, "1")
            if problems:
                raise BadRequestError(
                    "Resolve the workdraft's author/compile checks first",
                    code="method_source_invalid",
                )
            body = parse_body(current.body)
            try:
                definition = freeze(body, command)
            except (ValueError, KeyError):
                raise BadRequestError(
                    "Invalid method or input slots", code="method_definition_invalid"
                ) from None
            selected = {command.step_id, *command.input_slots.keys()}
            prefixes = tuple(f"steps/{alias}/" for alias in selected)
            provenance = {
                **command.model_dump(mode="json"),
                "workdraft_id": str(current.id),
                "confirmed_by": owner,
                "source_steps": [
                    s.model_dump(mode="json") for s in body.steps if s.id in selected
                ],
                "sources": {
                    p: value
                    for p, value in current.sources.items()
                    if p.startswith(prefixes)
                },
            }
            if have_owner is None:
                await self.repo.create_method(mid, owner)
            await self.repo.insert_method_version(
                method_id=mid,
                version=version,
                digest=definition.digest(),
                command_digest=command.digest(),
                definition=definition.document(),
                provenance=provenance,
                source_workdraft_id=current.id,
            )
            result = await self.repo.method_version(mid, version)
            assert result is not None
            return result

    async def preview_binding(
        self, owner: str, method_id: str, version: int, command: BindMethod
    ) -> dict[str, Any]:
        async with self.repo.transaction():
            record = await self.get(owner, method_id, version)
            request = await self.workdrafts._request(owner, str(command.request_id))
            current = await self.workdrafts.locked(request, command.revision)
            body = parse_body(current.body)
            try:
                step = bind(_definition(record), body, command)
                # Replacement is just a candidate. Re-run graph, mandatory-output
                # and compiler checks against the whole target expert.
                raw = body.model_dump(mode="json")
                old = next(
                    (i for i, s in enumerate(body.steps) if s.id == command.alias), None
                )
                if old is None:
                    raw["steps"].append(step.model_dump(mode="json"))
                else:
                    raw["steps"][old] = step.model_dump(mode="json")
                candidate = parse_body(raw)
                from app.domain.knowbots.compile import compile_manifest, pack_problems

                manifest, problems = compile_manifest(
                    candidate,
                    {},
                    pack_id="binding-preview",
                    version="1",
                    runtime=request.runtime,
                    fallback_name=request.title,
                )
                problems += pack_problems(manifest["pack"])
                if problems:
                    raise ValueError("invalid expert composition")
            except (ValueError, ValidationError):
                raise BadRequestError(
                    "Method does not fit the proposed expert binding",
                    code="method_binding_invalid",
                ) from None
            return {
                "method_id": str(record.method_id),
                "version": record.version,
                "digest": record.digest,
                "workdraft_revision": current.revision,
                "inputs": dict(command.inputs),
                "step": step.model_dump(mode="json"),
                "preview_only": True,
            }
