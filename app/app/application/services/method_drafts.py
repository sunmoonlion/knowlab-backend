"""Method-first Five-Element authoring, C-L1 ledger & fractal gap resolution."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.application.errors.exceptions import (
    BadRequestError,
    ConcurrencyConflictError,
    NotFoundError,
)
from app.application.ports.knowbots import (
    KnowlabRepository,
    RequestRecord,
    WorkdraftRecord,
)
from app.application.services.knowbot_service import parse_id
from app.domain.knowbots.formal_method import (
    ArchetypeId,
    ExpectedDeliverableSpec,
    MethodDraftV2,
    MethodRef,
    Orchestration,
    ValidationRule,
    build_engineering_archetype,
    extract_public_contract,
    project_contract_state,
)
from app.domain.knowbots.formal_method_admission import (
    FormalMethodDraft,
    FormalMethodSource,
)
from app.domain.knowbots.workdraft import empty_body

AUTHOR_ROLE = "author"

EDITABLE_DRAFT_FIELDS = frozenset(
    {
        "title",
        "purpose",
        "applies_when",
        "excludes",
        "expected_deliverables",
        "access_ceiling",
        "procedure",
        "validation_rules",
        "approval_rules",
        "unconfirmed_paths",
    }
)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


class CreateMethodDraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method_id: uuid.UUID | None = None
    archetype: ArchetypeId = "S1"
    method_kind: Literal["tool", "expert"] = "expert"
    title: str = Field(min_length=1, max_length=200)
    purpose: str | None = Field(default=None, min_length=1, max_length=4000)
    applies_when: tuple[str, ...] | None = Field(default=None, max_length=30)
    excludes: tuple[str, ...] | None = Field(default=None, max_length=30)
    source: FormalMethodSource | None = None
    allowed_callers: tuple[Literal["host", "model"], ...] = ("host",)
    runtime: str = Field(default="knowbench", min_length=1, max_length=32)


class PatchMethodDraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(strict=True, ge=0)
    draft_contract: MethodDraftV2 | None = None
    elements: dict[str, Any] | None = None
    confirm_paths: tuple[str, ...] = ()
    source: FormalMethodSource | None = None
    allowed_callers: tuple[Literal["host", "model"], ...] | None = None
    htn_state: dict[str, Any] | None = None
    provenance_kind: Literal["edit", "accept", "copy"] = "edit"
    suggestion_ref: str | None = Field(default=None, max_length=200)


class SpawnGapChildIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(strict=True, ge=0)
    step_id: str = Field(min_length=1, max_length=64)
    node_id: str | None = Field(default=None, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1, max_length=4000)
    kind: Literal["tool", "expert"] = "tool"
    archetype: ArchetypeId | None = None


class BindGapResolutionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(strict=True, ge=0)
    step_id: str = Field(min_length=1, max_length=64)
    child_reference: MethodRef


class HandoffSeedIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract: Literal["knowbench/formal-handoff/v2"] = "knowbench/formal-handoff/v2"
    title: str = Field(min_length=1, max_length=200)
    goal: str = Field(min_length=1, max_length=4000)
    archetype: ArchetypeId = "S1"
    method_kind: Literal["tool", "expert"] = "expert"
    draft_contract: MethodDraftV2 | None = None
    htn_state: dict[str, Any] | None = None
    origin: dict[str, Any] | None = None


class MethodDraftService:
    """Author-controlled Five-Element draft service with structured C-L1 ledger."""

    def __init__(
        self,
        repo: KnowlabRepository,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.repo = repo
        self.now = now

    async def list_drafts(self, owner: str) -> list[dict[str, Any]]:
        rows = await self.repo.list_method_workdrafts(owner)
        out: list[dict[str, Any]] = []
        for row in rows:
            if row.draft_contract is None or row.method_id is None:
                continue
            try:
                draft = MethodDraftV2.model_validate(row.draft_contract)
            except ValidationError:
                continue
            state = project_contract_state(draft)
            out.append(
                {
                    "method_id": str(row.method_id),
                    "request_id": str(row.request_id) if row.request_id else None,
                    "revision": row.revision,
                    "archetype": draft.archetype_id,
                    "method_kind": draft.method_kind,
                    "title": draft.title,
                    "purpose": draft.purpose,
                    "ready_to_freeze": state["ready_to_freeze"],
                    "parent_gap": row.parent_gap,
                    "updated_at": row.updated_at.isoformat()
                    if row.updated_at
                    else None,
                }
            )
        return out

    async def get(self, owner: str, method_id: str) -> dict[str, Any]:
        mid = parse_id(method_id, "method")
        row = await self.repo.get_method_workdraft(mid)
        if row is None or row.owner_subject != owner or row.draft_contract is None:
            raise NotFoundError("No such method draft", code="method_draft_not_found")
        draft = MethodDraftV2.model_validate(row.draft_contract)
        return await self._view(row, draft)

    async def delete(self, owner: str, method_id: str, revision: int) -> None:
        mid = parse_id(method_id, "method")
        async with self.repo.transaction():
            await self.repo.lock_method(mid)
            current = await self.repo.get_method_workdraft(mid, for_update=True)
            if current is None or current.owner_subject != owner:
                raise NotFoundError(
                    "No such method draft", code="method_draft_not_found"
                )
            if current.draft_contract is None:
                return
            if current.revision != revision:
                raise ConcurrencyConflictError(
                    "Draft changed since it was displayed", code="workdraft_revision"
                )
            await self.repo.retire_method_workdraft(current)

    async def create(self, owner: str, body: CreateMethodDraftIn) -> dict[str, Any]:
        mid = body.method_id or uuid.uuid4()
        starter = build_engineering_archetype(
            body.archetype,
            method_kind=body.method_kind,
            title=body.title,
        )
        updates: dict[str, Any] = {}
        confirmed: set[str] = set()
        if body.purpose is not None:
            updates["purpose"] = body.purpose
            confirmed.add("capability.purpose")
        if body.applies_when is not None:
            updates["applies_when"] = body.applies_when
            confirmed.add("capability.applies_when/0")
        if body.excludes is not None:
            updates["excludes"] = body.excludes
            confirmed.add("capability.excludes/0")
        if confirmed:
            updates["unconfirmed_paths"] = tuple(
                p for p in starter.unconfirmed_paths if p not in confirmed
            )
        draft = starter.model_copy(update=updates) if updates else starter

        async with self.repo.transaction():
            await self.repo.lock_method(mid)
            have_owner = await self.repo.method_owner(mid)
            if have_owner is not None and have_owner != owner:
                raise NotFoundError("No such method", code="method_not_found")
            if have_owner is None:
                await self.repo.create_method(mid, owner)
            if await self.repo.get_method_workdraft(mid, for_update=True) is not None:
                raise ConcurrencyConflictError(
                    "Method draft already exists", code="workdraft_revision"
                )
            initial_body = self._project_runtime_body(
                empty_body(),
                draft,
                source=body.source,
                allowed_callers=body.allowed_callers,
            )
            current = await self.repo.create_method_workdraft(
                method_id=mid,
                owner_subject=owner,
                body=initial_body,
                draft_contract=draft.model_dump(mode="json", by_alias=True),
            )
            sources = await self._record_provenance(
                current,
                owner,
                draft,
                source=body.source,
                allowed_callers=body.allowed_callers,
                kind="edit",
                suggestion_ref=None,
            )
            await self.repo.update_workdraft(
                current.id,
                body=initial_body,
                sources=sources,
                revision=1,
                draft_contract=draft.model_dump(mode="json", by_alias=True),
            )
            saved = await self.repo.get_method_workdraft(mid)
            assert saved is not None
        return await self._view(saved, draft)

    async def create_from_handoff(
        self,
        owner: str,
        body: HandoffSeedIn,
        *,
        method_id: uuid.UUID | None = None,
        request_id: uuid.UUID | None = None,
        source: FormalMethodSource | None = None,
        allowed_callers: tuple[Literal["host", "model"], ...] = ("host",),
    ) -> dict[str, Any]:
        mid = method_id or uuid.uuid4()
        if body.draft_contract is not None:
            draft = body.draft_contract
        else:
            starter = build_engineering_archetype(
                body.archetype,
                method_kind=body.method_kind,
                title=body.title,
            )
            draft = starter.model_copy(update={"purpose": body.goal})
        digest_hex = hashlib.sha256(
            _canonical_json(body.model_dump(mode="json")).encode()
        ).hexdigest()[:32]
        suggestion_ref = f"handoff:{digest_hex}"
        async with self.repo.transaction():
            await self.repo.lock_method(mid)
            prior_owner = await self.repo.method_owner(mid)
            if prior_owner is None:
                await self.repo.create_method(mid, owner)
            elif prior_owner != owner:
                raise NotFoundError("No such method", code="method_not_found")
            else:
                current = await self.repo.get_method_workdraft(mid, for_update=True)
                if current and current.draft_contract is not None:
                    # Feedback must never replace the author’s in-progress edits.
                    return await self._view(
                        current, MethodDraftV2.model_validate(current.draft_contract)
                    )
            initial_body = self._project_runtime_body(
                empty_body(), draft, source=source, allowed_callers=allowed_callers
            )
            current = await self.repo.create_method_workdraft(
                method_id=mid,
                owner_subject=owner,
                request_id=request_id,
                body=initial_body,
                draft_contract=draft.model_dump(mode="json", by_alias=True),
                htn_state=body.htn_state,
            )
            sources = await self._record_provenance(
                current,
                owner,
                draft,
                source=source,
                allowed_callers=allowed_callers,
                kind="accept",
                suggestion_ref=suggestion_ref,
            )
            await self.repo.update_workdraft(
                current.id,
                body=initial_body,
                sources=sources,
                revision=1,
                draft_contract=draft.model_dump(mode="json", by_alias=True),
                htn_state=body.htn_state,
            )
            saved = await self.repo.get_method_workdraft(mid)
            assert saved is not None
        return await self._view(saved, draft)

    async def patch(
        self, owner: str, method_id: str, body: PatchMethodDraftIn
    ) -> dict[str, Any]:
        mid = parse_id(method_id, "method")
        if body.provenance_kind == "accept" and not body.suggestion_ref:
            raise BadRequestError(
                "Accepted AI/handoff proposals must carry suggestion_ref",
                code="provenance_ref_missing",
            )
        if body.provenance_kind != "accept" and body.suggestion_ref is not None:
            raise BadRequestError(
                "Only accepted suggestions may record suggestion_ref",
                code="provenance_ref_invalid",
            )
        async with self.repo.transaction():
            await self.repo.lock_method(mid)
            current = await self.repo.get_method_workdraft(mid, for_update=True)
            if (
                current is None
                or current.owner_subject != owner
                or current.draft_contract is None
            ):
                raise NotFoundError(
                    "No such method draft", code="method_draft_not_found"
                )
            if current.revision != body.revision:
                raise ConcurrencyConflictError(
                    "Method draft revision changed; reload before saving",
                    code="workdraft_revision",
                )
            raw_contract = dict(current.draft_contract)
            if body.draft_contract is not None:
                raw_contract = body.draft_contract.model_dump(
                    mode="json", by_alias=True
                )
            elif body.elements:
                unknown = set(body.elements) - EDITABLE_DRAFT_FIELDS
                if unknown:
                    raise BadRequestError(
                        "Cannot directly edit derived or unknown element: "
                        f"{sorted(unknown)}",
                        code="method_element_readonly",
                    )
                raw_contract.update(body.elements)
            if body.confirm_paths:
                existing_unconfirmed = list(raw_contract.get("unconfirmed_paths") or ())
                raw_contract["unconfirmed_paths"] = [
                    p for p in existing_unconfirmed if p not in set(body.confirm_paths)
                ]
            try:
                draft = MethodDraftV2.model_validate(raw_contract)
                project_contract_state(draft)
            except (ValidationError, ValueError) as exc:
                raise BadRequestError(
                    f"Five-Element contract invalid: {exc}",
                    code="formal_method_invalid",
                ) from exc

            existing_fm = (
                current.body.get("formal_method")
                or current.body.get("studio_binding")
                or {}
            )
            source_obj = (
                body.source
                if body.source is not None
                else (
                    FormalMethodSource.model_validate(existing_fm["source"])
                    if existing_fm.get("source")
                    else None
                )
            )
            callers = (
                body.allowed_callers
                if body.allowed_callers is not None
                else tuple(existing_fm.get("allowed_callers") or ("host",))
            )
            next_body = self._project_runtime_body(
                current.body, draft, source=source_obj, allowed_callers=callers
            )
            sources = await self._record_provenance(
                current,
                owner,
                draft,
                source=source_obj,
                allowed_callers=callers,
                kind=body.provenance_kind,
                suggestion_ref=body.suggestion_ref,
            )
            await self.repo.update_workdraft(
                current.id,
                body=next_body,
                sources=sources,
                revision=current.revision + 1,
                draft_contract=draft.model_dump(mode="json", by_alias=True),
                htn_state=body.htn_state,
            )
            saved = await self.repo.get_method_workdraft(mid)
            assert saved is not None
        return await self._view(saved, draft)

    async def ensure_runtime_anchor(
        self, owner: str, method_id: str, *, runtime: str = "knowbench"
    ) -> RequestRecord:
        """Lazily attach a self-mode RequestRecord for validation/trial/freeze."""
        mid = parse_id(method_id, "method")
        async with self.repo.transaction():
            await self.repo.lock_method(mid)
            current = await self.repo.get_method_workdraft(mid, for_update=True)
            if (
                current is None
                or current.owner_subject != owner
                or current.draft_contract is None
            ):
                raise NotFoundError(
                    "No such method draft", code="method_draft_not_found"
                )
            if current.request_id is not None:
                req = await self.repo.get_request(current.request_id)
                assert req is not None
                return req
            draft = MethodDraftV2.model_validate(current.draft_contract)
            problems = await self.verify_provenance(current, owner, draft)
            if problems:
                raise BadRequestError("; ".join(problems), code="method_source_invalid")
            req = await self.repo.create_request(
                owner_subject=owner,
                title=draft.title[:120] or "未命名方法",
                runtime=runtime,
                idea={
                    "kind": "method_contract_v2",
                    "method_id": str(mid),
                    "archetype": draft.archetype_id,
                    "purpose": draft.purpose,
                },
                cases=[],
                consent_version=1,
            )
            await self.repo.set_incubation_mode(req.id, "self")
            await self.repo.update_workdraft(
                current.id,
                body=current.body,
                sources=current.sources,
                revision=current.revision,
                request_id=req.id,
            )
            updated_req = await self.repo.get_request(req.id)
            assert updated_req is not None
            return updated_req

    async def spawn_gap_child(
        self, owner: str, parent_method_id: str, body: SpawnGapChildIn
    ) -> dict[str, Any]:
        """Spawn a fractal child method draft pre-bound to parent step slots."""
        pmid = parse_id(parent_method_id, "method")
        child_mid = uuid.uuid4()
        async with self.repo.transaction():
            await self.repo.lock_method(pmid)
            parent = await self.repo.get_method_workdraft(pmid, for_update=True)
            if (
                parent is None
                or parent.owner_subject != owner
                or parent.draft_contract is None
            ):
                raise NotFoundError(
                    "No such parent method draft", code="method_draft_not_found"
                )
            if parent.revision != body.revision:
                raise ConcurrencyConflictError(
                    "Parent draft revision changed", code="workdraft_revision"
                )
            parent_draft = MethodDraftV2.model_validate(parent.draft_contract)
            if not isinstance(parent_draft.procedure, Orchestration):
                raise BadRequestError(
                    "Only orchestration methods contain sub-step gaps",
                    code="formal_method_invalid",
                )
            step = next(
                (s for s in parent_draft.procedure.steps if s.id == body.step_id),
                None,
            )
            if step is None:
                raise NotFoundError("No such parent step", code="step_not_found")

            archetype: ArchetypeId = body.archetype or (
                "S4" if body.kind == "tool" else "S1"
            )
            starter = build_engineering_archetype(
                archetype, method_kind=body.kind, title=body.title
            )
            expected_specs = tuple(
                ExpectedDeliverableSpec(
                    promise_id=out_name,
                    summary=slot.description,
                    expected_domain_id=str(
                        slot.meaning.get("domain_id", "core.record.object")
                    ),
                    expected_schema=slot.schema_,
                )
                for out_name, slot in step.outputs.items()
            )
            rules = tuple(
                ValidationRule(
                    promise_id=spec.promise_id,
                    outputs=(spec.promise_id,),
                    check=f"满足父流程步骤 {body.step_id} 的输出契约",
                    evidence="步骤输入与输出结构核验",
                    on_failure="fail",
                )
                for spec in expected_specs
            )
            child_draft = starter.model_copy(
                update={
                    "purpose": body.purpose,
                    "expected_deliverables": expected_specs
                    or starter.expected_deliverables,
                    "validation_rules": rules or starter.validation_rules,
                    "unconfirmed_paths": (),
                }
            )
            gap_link = {
                "parent_method_id": str(pmid),
                "parent_revision": parent.revision,
                "step_id": body.step_id,
                "node_id": body.node_id,
                "expected_inputs": {
                    k: v.slot.model_dump(mode="json", by_alias=True)
                    for k, v in step.inputs.items()
                },
                "expected_outputs": {
                    k: v.model_dump(mode="json", by_alias=True)
                    for k, v in step.outputs.items()
                },
            }
            await self.repo.lock_method(child_mid)
            await self.repo.create_method(child_mid, owner)
            initial_body = self._project_runtime_body(
                empty_body(), child_draft, source=None, allowed_callers=("host",)
            )
            child_row = await self.repo.create_method_workdraft(
                method_id=child_mid,
                owner_subject=owner,
                body=initial_body,
                draft_contract=child_draft.model_dump(mode="json", by_alias=True),
                parent_gap=gap_link,
            )
            sources = await self._record_provenance(
                child_row,
                owner,
                child_draft,
                source=None,
                allowed_callers=("host",),
                kind="edit",
                suggestion_ref=None,
            )
            await self.repo.update_workdraft(
                child_row.id,
                body=initial_body,
                sources=sources,
                revision=1,
                draft_contract=child_draft.model_dump(mode="json", by_alias=True),
                parent_gap=gap_link,
            )
            saved_child = await self.repo.get_method_workdraft(child_mid)
            assert saved_child is not None
        return await self._view(saved_child, child_draft)

    async def bind_gap_resolution(
        self, owner: str, parent_method_id: str, body: BindGapResolutionIn
    ) -> dict[str, Any]:
        """Pin a frozen child MethodRef back into the parent orchestration step."""
        pmid = parse_id(parent_method_id, "method")
        ref = body.child_reference
        async with self.repo.transaction():
            await self.repo.lock_method(pmid)
            parent = await self.repo.get_method_workdraft(pmid, for_update=True)
            if (
                parent is None
                or parent.owner_subject != owner
                or parent.draft_contract is None
            ):
                raise NotFoundError(
                    "No such parent method draft", code="method_draft_not_found"
                )
            if parent.revision != body.revision:
                raise ConcurrencyConflictError(
                    "Parent draft revision changed", code="workdraft_revision"
                )
            frozen_child = await self.repo.method_version(ref.method_id, ref.version)
            if (
                frozen_child is None
                or frozen_child.owner_subject != owner
                or frozen_child.digest != ref.digest
            ):
                raise NotFoundError(
                    "Child method version is not frozen by this author",
                    code="method_not_found",
                )
            parent_draft = MethodDraftV2.model_validate(parent.draft_contract)
            if not isinstance(parent_draft.procedure, Orchestration):
                raise BadRequestError(
                    "Parent method is not an orchestration",
                    code="formal_method_invalid",
                )
            updated_steps = []
            matched = False
            for step in parent_draft.procedure.steps:
                if step.id == body.step_id:
                    matched = True
                    updated_steps.append(step.model_copy(update={"candidates": (ref,)}))
                else:
                    updated_steps.append(step)
            if not matched:
                raise NotFoundError("No such parent step", code="step_not_found")
            next_proc = parent_draft.procedure.model_copy(
                update={"steps": tuple(updated_steps)}
            )
            next_draft = parent_draft.model_copy(update={"procedure": next_proc})
            project_contract_state(next_draft)
            existing_fm = (
                parent.body.get("formal_method")
                or parent.body.get("studio_binding")
                or {}
            )
            source_obj = (
                FormalMethodSource.model_validate(existing_fm["source"])
                if existing_fm.get("source")
                else None
            )
            callers = tuple(existing_fm.get("allowed_callers") or ("host",))
            next_body = self._project_runtime_body(
                parent.body, next_draft, source=source_obj, allowed_callers=callers
            )
            sources = await self._record_provenance(
                parent,
                owner,
                next_draft,
                source=source_obj,
                allowed_callers=callers,
                kind="edit",
                suggestion_ref=None,
            )
            await self.repo.update_workdraft(
                parent.id,
                body=next_body,
                sources=sources,
                revision=parent.revision + 1,
                draft_contract=next_draft.model_dump(mode="json", by_alias=True),
            )
            saved_parent = await self.repo.get_method_workdraft(pmid)
            assert saved_parent is not None
        return await self._view(saved_parent, next_draft)

    async def verify_provenance(
        self, current: WorkdraftRecord, owner: str, draft: MethodDraftV2
    ) -> list[str]:
        """Verify structured C-L1 provenance for every non-derived Five-Element cell."""
        expected_cells = self._element_cells(current.body, draft)
        entry_ids = []
        for path in expected_cells:
            src = (current.sources or {}).get(path)
            if not isinstance(src, dict) or not str(src.get("ref", "")).startswith(
                "record:"
            ):
                return [f"Missing C-L1 provenance for {path}"]
            try:
                entry_ids.append(uuid.UUID(str(src["ref"]).removeprefix("record:")))
            except ValueError:
                return [f"Malformed C-L1 record reference for {path}"]
        entries = {e.id: e for e in await self.repo.list_workdraft_entries(entry_ids)}
        problems: list[str] = []
        for path, canonical_text in expected_cells.items():
            src = current.sources[path]
            rid = uuid.UUID(str(src["ref"]).removeprefix("record:"))
            entry = entries.get(rid)
            if (
                entry is None
                or entry.workdraft_id != current.id
                or entry.actor_subject != owner
                or entry.path != path
                or entry.text != canonical_text
            ):
                problems.append(f"Unverified C-L1 provenance at {path}")
        return problems

    def _element_cells(
        self, runtime_body: dict[str, Any] | None, draft: MethodDraftV2
    ) -> dict[str, str]:
        dumped = draft.model_dump(mode="json", by_alias=True)
        cells = {
            "element:declaration": _canonical_json(
                {
                    "title": dumped["title"],
                    "purpose": dumped["purpose"],
                    "applies_when": dumped["applies_when"],
                    "excludes": dumped["excludes"],
                    "expected_deliverables": dumped["expected_deliverables"],
                }
            ),
            "element:access_ceiling": _canonical_json(dumped["access_ceiling"]),
            "element:procedure": _canonical_json(dumped.get("procedure")),
            "element:validation_rules": _canonical_json(dumped["validation_rules"]),
            "element:approval_rules": _canonical_json(dumped["approval_rules"]),
        }
        fm = (runtime_body or {}).get("formal_method")
        if isinstance(fm, dict):
            cells["formal_method"] = _canonical_json(fm)
        if binding := (runtime_body or {}).get("studio_binding"):
            cells["studio_binding"] = _canonical_json(binding)
        return cells

    async def _record_provenance(
        self,
        current: WorkdraftRecord,
        owner: str,
        draft: MethodDraftV2,
        *,
        source: FormalMethodSource | None,
        allowed_callers: tuple[Literal["host", "model"], ...],
        kind: Literal["edit", "accept", "copy"],
        suggestion_ref: str | None,
    ) -> dict[str, Any]:
        next_body = self._project_runtime_body(
            current.body, draft, source=source, allowed_callers=allowed_callers
        )
        after_cells = self._element_cells(next_body, draft)
        prior_entries = {}
        existing_ids = []
        for src in (current.sources or {}).values():
            if isinstance(src, dict) and str(src.get("ref", "")).startswith("record:"):
                try:
                    existing_ids.append(
                        uuid.UUID(str(src["ref"]).removeprefix("record:"))
                    )
                except ValueError:
                    pass
        if existing_ids:
            prior_entries = {
                e.id: e for e in await self.repo.list_workdraft_entries(existing_ids)
            }
        sources = dict(current.sources or {})
        for path, text in after_cells.items():
            prev_src = (current.sources or {}).get(path)
            if isinstance(prev_src, dict) and str(prev_src.get("ref", "")).startswith(
                "record:"
            ):
                prev_id = uuid.UUID(str(prev_src["ref"]).removeprefix("record:"))
                prev_entry = prior_entries.get(prev_id)
                if (
                    prev_entry is not None
                    and prev_entry.workdraft_id == current.id
                    and prev_entry.path == path
                    and prev_entry.actor_subject == owner
                    and prev_entry.actor_role == AUTHOR_ROLE
                    and prev_entry.text == text
                ):
                    sources[path] = prev_src
                    continue
            record_kind = "edit" if kind == "copy" else kind
            entry = await self.repo.add_workdraft_entry(
                workdraft_id=current.id,
                kind=record_kind,
                actor_subject=owner,
                actor_role=AUTHOR_ROLE,
                path=path,
                text=text,
                suggestion_ref=suggestion_ref if record_kind == "accept" else None,
            )
            sources[path] = {
                "kind": "user_confirmed" if record_kind == "accept" else "user_text",
                "ref": f"record:{entry.id}",
            }
        return sources

    @staticmethod
    def _project_runtime_body(
        base_body: dict[str, Any] | None,
        draft: MethodDraftV2,
        *,
        source: FormalMethodSource | None,
        allowed_callers: tuple[Literal["host", "model"], ...],
    ) -> dict[str, Any]:
        """Project MethodDraftV2 into FormalMethodDraft when definition is complete."""
        doc = dict(base_body or empty_body())
        try:
            defn = draft.to_definition()
            fm = FormalMethodDraft(
                definition=defn,
                source=source,
                allowed_callers=allowed_callers,
            )
            doc.pop("studio_binding", None)
            doc["formal_method"] = fm.model_dump(mode="json", by_alias=True)
        except (ValidationError, ValueError):
            doc.pop("formal_method", None)
            doc["studio_binding"] = {
                "source": source.model_dump(mode="json") if source else None,
                "allowed_callers": list(allowed_callers),
            }
        return doc

    async def _view(self, row: WorkdraftRecord, draft: MethodDraftV2) -> dict[str, Any]:
        projection = project_contract_state(draft)
        provenance_issues = await self.verify_provenance(row, row.owner_subject, draft)
        public_view = None
        definition_digest = None
        try:
            defn = draft.to_definition()
            definition_digest = defn.digest()
            public_view = extract_public_contract(defn).model_dump(
                mode="json", by_alias=True
            )
        except (ValidationError, ValueError):
            pass
        fm = (row.body or {}).get("formal_method") or {}
        binding = fm or row.body.get("studio_binding") or {}
        saved_draft = None
        if fm and definition_digest:
            try:
                candidate = FormalMethodDraft.model_validate(fm)
                if candidate.definition.digest() == definition_digest:
                    saved_draft = fm
            except ValidationError:
                pass
        return {
            "method_id": str(row.method_id),
            "request_id": str(row.request_id) if row.request_id else None,
            "workdraft_id": str(row.id),
            "revision": row.revision,
            "draft_contract": draft.model_dump(mode="json", by_alias=True),
            "projection": projection,
            "public_contract": public_view,
            "definition_digest": definition_digest,
            "formal_method": saved_draft,
            "source": binding.get("source"),
            "allowed_callers": binding.get("allowed_callers", ["host"]),
            "feedback": await self.repo.method_feedback(row.method_id),
            "htn_state": row.htn_state,
            "parent_gap": row.parent_gap,
            "sources": row.sources,
            "provenance_verified": not provenance_issues,
            "provenance_issues": provenance_issues,
        }
