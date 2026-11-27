"""Build and recheck the directory for an author-reviewed proposal."""

import json
from itertools import zip_longest
from typing import Any

from app.application.errors.exceptions import (
    BadRequestError,
    ConcurrencyConflictError,
    NotFoundError,
)
from app.application.services.formal_methods import (
    FormalMethodService,
    admitted_definition,
)
from app.application.services.knowbot_workdraft import parse_body
from app.domain.knowbots.formal_method import (
    FormalMethodDefinition,
    MethodDraftV2,
    MethodRef,
)
from app.domain.knowbots.formal_method_admission import FormalMethodDraft
from app.domain.knowbots.formal_method_compiler import FrozenMethod
from app.domain.knowbots.selection import (
    Selection,
    SelectionCandidate,
    TargetDeliverableHint,
    annotate_catalog_reachability,
    available,
    check_delivery_targets,
    expand,
    verify_selection_graph,
)


def installation(row):
    return next(
        (
            f"{i['asset_id']}:{i['revision']}"
            for i in row["installations"]
            if i["asset_id"] == row["selected"]
        ),
        None,
    )


def instruction_pin(row):
    return {
        k: row[k]
        for k in (
            "kind",
            "source",
            "entry",
            "version",
            "digest",
            "asset_id",
            "asset_revision",
        )
        if row.get(k) is not None
    }


PAGE = 24


class SelectionService:
    def __init__(self, repo, runtimes):
        self.methods = FormalMethodService(repo, runtimes)
        self.repo = repo
        self.runtimes = runtimes

    async def catalog(
        self, owner, rid, preferred: MethodRef | None = None, goal: str | None = None
    ) -> dict[str, Any]:
        runtime, declaration = await self.methods._incubation_basis(owner, rid)
        search = {"goal": goal} if goal else {}
        owned = await self.runtimes.method_skill_center_incubation_methods(
            runtime,
            owner,
            limit=2 * PAGE + 1,
            offset=0,
            declaration=declaration,
            **search,
        )
        shared = await self.methods._shared_tools(
            runtime, owner, declaration=declaration, **search
        )
        capabilities = await self.methods.planning_capabilities(owner, rid)
        flags = capabilities["patterns"]

        def owned_ref(row):
            return MethodRef(
                method_id=row["method_id"],
                version=row["latest_version"],
                digest=row["digest"],
            )

        owned_refs = [owned_ref(row) for row in owned[: 2 * PAGE]]
        installed = [row for row in shared["items"] if row.get("selected")]
        shared_refs = [
            MethodRef.model_validate(row["reference"]) for row in installed[:PAGE]
        ]
        # The version selected in the directory may be outside the ranked window.
        if preferred and preferred not in owned_refs and preferred not in shared_refs:
            exact = await self.runtimes.method_skill_center_incubation_methods(
                runtime,
                owner,
                limit=1,
                offset=0,
                all_versions=True,
                references=[preferred.model_dump(mode="json")],
            )
            if any(owned_ref(row) == preferred for row in exact):
                owned_refs.insert(0, preferred)
            else:
                shared_refs.insert(0, preferred)
        candidates = []
        for ref in owned_refs:
            try:
                async with self.repo.transaction():
                    definition = admitted_definition(
                        await self.methods.get(owner, str(ref.method_id), ref.version),
                        require_host=True,
                    )
            except (NotFoundError, ConcurrencyConflictError, BadRequestError):
                continue
            if definition.digest() == ref.digest:
                candidates.append(("owned", ref, definition, None))
        shared_start = len(candidates)
        for ref in shared_refs:
            if any(c[1] == ref for c in candidates):
                continue
            page = await self.methods._shared_tools(runtime, owner, reference=ref)
            if page["items"] and page["items"][0]["selected"]:
                candidates.append(
                    (
                        "shared",
                        ref,
                        FormalMethodDefinition.model_validate(
                            page["items"][0]["definition"]
                        ),
                        installation(page["items"][0]),
                    )
                )
        # Reserve space for both owned and installed methods before the byte
        # and alias limits, even when the owned list alone fills the snapshot.
        candidates = [
            candidate
            for pair in zip_longest(
                candidates[:shared_start], candidates[shared_start:]
            )
            for candidate in pair
            if candidate
        ]
        if preferred:
            candidates.sort(key=lambda candidate: candidate[1] != preferred)
        catalog = {}
        remaining_bytes = 480_000
        truncated = False
        for index, (scope, ref, definition, binding) in enumerate(candidates, 1):
            if not available(definition, capabilities):
                continue
            view = definition.interfaces()
            entry = dict(
                scope=scope,
                isolated_instruction=(
                    definition.procedure.kind == "direct"
                    and definition.procedure.implementation.kind
                    in ("registered_instruction", "skill_instruction")
                ),
                **({"installation": binding} if binding else {}),
                reference=ref.model_dump(mode="json"),
                declaration=definition.capability_declaration.model_dump(mode="json"),
                inputs={n: i["slot"] for n, i in view["inputs"].items()},
                outputs={n: i["slot"] for n, i in view["outputs"].items()},
            )
            size = len(json.dumps(entry, ensure_ascii=False).encode())
            if size > remaining_bytes or len(catalog) >= 2 * PAGE:
                truncated = True
                continue
            remaining_bytes -= size
            catalog[f"m{index}"] = entry
        sources = await self.methods.sources(owner, rid)
        instructions = [
            instruction_pin(row)
            for row in sources["items"]
            if row["kind"] in {"registered_instruction", "skill_instruction"}
            and row["license_basis"] != "pending"
        ]
        target_deliverables: list[dict[str, Any]] = []
        allowed_capabilities: list[str] = []
        async with self.repo.transaction():
            _, workdraft = await self.methods.workdrafts.get(owner, rid)
        if workdraft and workdraft.draft_contract:
            try:
                v2 = MethodDraftV2.model_validate(workdraft.draft_contract)
                target_deliverables = [
                    TargetDeliverableHint(
                        promise_id=p.promise_id,
                        summary=p.summary,
                        expected_domain_id=p.expected_domain_id,
                        outputs=tuple(
                            dict.fromkeys(
                                output
                                for rule in v2.validation_rules
                                if rule.promise_id == p.promise_id
                                for output in rule.outputs
                            )
                        ),
                    ).model_dump(mode="json")
                    for p in v2.expected_deliverables
                    if p.expected_domain_id
                ]
                allowed_capabilities = sorted(
                    {r.requirement_id for r in v2.access_ceiling}
                )
                if target_deliverables:
                    parsed_candidates = {
                        alias: SelectionCandidate.model_validate(entry)
                        for alias, entry in catalog.items()
                    }
                    annotated, _ = annotate_catalog_reachability(
                        parsed_candidates,
                        tuple(d["expected_domain_id"] for d in target_deliverables),
                    )
                    catalog = {
                        alias: cand.model_dump(mode="json", by_alias=True)
                        for alias, cand in annotated.items()
                    }
            except ValueError:
                pass
        return dict(
            instructions={f"i{i}": row for i, row in enumerate(instructions[:PAGE], 1)},
            methods=catalog,
            patterns=flags,
            partial=bool(
                truncated
                or len(instructions) > PAGE
                or len(owned) > 2 * PAGE
                or shared.get("partial")
                or shared["next"]
                or len(installed) > PAGE
            ),
            **(
                {"target_deliverables": target_deliverables}
                if target_deliverables
                else {}
            ),
            **(
                {"allowed_capabilities": allowed_capabilities}
                if allowed_capabilities
                else {}
            ),
        )

    async def confirm(self, owner, request, record, body):
        if record.workdraft_revision != body.revision:
            raise ConcurrencyConflictError("Stale selection", code="workdraft_revision")
        workdrafts = self.methods.workdrafts
        request_id = str(request.id)
        if record.decisions.get("selection"):
            receipt = record.decisions["selection"]
            async with self.repo.transaction():
                latest, saved = await workdrafts.get(owner, request_id)
                if (
                    saved
                    and latest.latest_version == record.request_version
                    and saved.revision == receipt["revision"]
                    and receipt.get("command") == body.model_dump(mode="json")
                ):
                    return saved
            raise ConcurrencyConflictError(
                "Already reviewed", code="workdraft_revision"
            )
        draft, roles = await self.materialize(
            owner,
            request,
            record,
            body.method_id,
            {step: set(values) for step, values in body.cases.items()},
            body.template,
            body.step_text,
        )
        async with self.repo.transaction():
            await self.repo.lock_method(body.method_id)
            await self.repo.lock_request(request.id)
            latest = await self.repo.get_request(request.id)
            if latest is None or latest.latest_version != record.request_version:
                raise ConcurrencyConflictError(
                    "Idea revised", code="workdraft_revision"
                )
            current = await workdrafts.locked(request, body.revision)
            if current.draft_contract is None:
                raise BadRequestError(
                    "Select a studio draft", code="method_draft_not_found"
                )
            if current.method_id != body.method_id:
                raise ConcurrencyConflictError(
                    "Different studio method", code="workdraft_revision"
                )
            from app.application.services.method_drafts import (
                MethodDraftService,
                PatchMethodDraftIn,
            )

            procedure = draft.definition.procedure.model_dump(
                mode="json", by_alias=True
            )
            if roles and procedure["kind"] == "orchestration":
                procedure["collaboration"] = roles
            await MethodDraftService(self.repo).patch(
                owner,
                str(body.method_id),
                PatchMethodDraftIn(
                    revision=body.revision,
                    elements={"procedure": procedure},
                    source=draft.source,
                    provenance_kind="accept",
                    suggestion_ref=f"selection:{record.id}",
                ),
            )
            found = await self.repo.get_workdraft(request.id)
            assert found is not None
            await self.repo.update_incubation(
                record.id,
                decisions={
                    **record.decisions,
                    "selection": {
                        "state": "accepted",
                        "command": body.model_dump(mode="json"),
                        "revision": found.revision,
                        "method_id": str(body.method_id),
                    },
                },
            )
        return found

    async def materialize(
        self, owner, request, record, target, required_cases, template, step_text
    ):
        saved = record.seed.get("selection_context")
        if (
            not saved
            or record.status != "succeeded"
            or not record.handback
            or not record.handback.get("selection")
        ):
            raise BadRequestError("No reviewed selection", code="selection_unresolved")
        proposal = Selection.model_validate(record.handback["selection"])
        if proposal.status != "ready" or record.handback.get("gaps"):
            raise BadRequestError(
                "Resolve the author's questions first", code="selection_unresolved"
            )
        async with self.repo.transaction():
            fresh, current = await self.methods.workdrafts.get(owner, str(request.id))
            if (
                not current
                or current.id != record.workdraft_id
                or fresh.latest_version != record.request_version
                or current.revision != record.workdraft_revision
            ):
                raise ConcurrencyConflictError(
                    "Selection based on an older revision", code="workdraft_revision"
                )
            body = parse_body(current.body)
        try:
            check_delivery_targets(
                proposal, saved.get("methods", {}), saved.get("target_deliverables")
            )
        except ValueError as exc:
            raise BadRequestError(str(exc), code="selection_export") from exc
        if body.method_template and body.method_template != template:
            raise ConcurrencyConflictError(
                "Confirmed template changed", code="workdraft_revision"
            )
        if set(step_text) != {s.id for s in proposal.steps} or any(
            not v.strip() or len(v) > 1500 for v in step_text.values()
        ):
            raise BadRequestError("Review every step", code="selection_input")
        proposal = proposal.model_copy(
            update={
                "steps": tuple(
                    s.model_copy(update={"task": step_text[s.id]})
                    for s in proposal.steps
                )
            }
        )
        runtime = request.runtime
        capabilities = await self.methods.planning_capabilities(owner, str(request.id))
        flags = capabilities["patterns"]
        aliases = {a for s in proposal.steps for a in (s.method, *s.cases.values())}
        instructions = saved.get("instructions", {})
        if aliases.intersection(instructions):
            step = proposal.steps[0]
            if (
                len(proposal.steps) != 1
                or step.method not in instructions
                or proposal.patterns
                or step.inputs
                or step.deliver_as
                or step.cases
                or step.reviews
                or step.route_input
                or proposal.object_batch
                or required_cases
                or proposal.routing_required
            ):
                raise BadRequestError(
                    "A direct instruction requires one step", code="selection_graph"
                )
            sources = await self.methods.sources(owner, str(request.id))
            source = next(
                (
                    s
                    for s in sources["items"]
                    if s["license_basis"] != "pending"
                    and instruction_pin(s) == instructions[step.method]
                ),
                None,
            )
            if not source:
                raise NotFoundError(
                    "Instruction unavailable", code="selection_candidate"
                )
            return direct_instruction(request.title, template, step.task, source), None
        tools = {}
        shared = {}
        for alias in sorted(aliases):
            entry = saved["methods"].get(alias)
            if not entry:
                raise BadRequestError(
                    "Unknown directory candidate", code="selection_candidate"
                )
            ref = MethodRef.model_validate(entry["reference"])
            if ref.method_id == target:
                raise BadRequestError("Recursive selection", code="selection_candidate")
            if entry["scope"] == "owned":
                rows = await self.runtimes.method_skill_center_incubation_methods(
                    runtime,
                    owner,
                    limit=1,
                    offset=0,
                    all_versions=True,
                    references=[ref.model_dump(mode="json")],
                )
                if not any(
                    str(r["method_id"]) == str(ref.method_id)
                    and r["latest_version"] == ref.version
                    and r["digest"] == ref.digest
                    for r in rows
                ):
                    raise NotFoundError(
                        "Method unavailable", code="selection_candidate"
                    )
                async with self.repo.transaction():
                    definition = admitted_definition(
                        await self.methods.get(owner, str(ref.method_id), ref.version),
                        require_host=True,
                    )
            else:
                page = await self.methods._shared_tools(runtime, owner, reference=ref)
                if (
                    not page["items"]
                    or not entry.get("installation")
                    or installation(page["items"][0]) != entry["installation"]
                ):
                    raise NotFoundError(
                        "Binding unavailable", code="selection_candidate"
                    )
                definition = FormalMethodDefinition.model_validate(
                    page["items"][0]["definition"]
                )
                shared[ref] = definition
            if definition.digest() != ref.digest:
                raise ConcurrencyConflictError(
                    "Method version changed", code="selection_candidate"
                )
            tools[alias] = FrozenMethod(ref, definition)
        try:
            draft, roles = expand(
                proposal,
                tools,
                template,
                flags,
                required_cases=required_cases,
                title=request.title,
                scope=(body.scope.does, body.scope.does_not),
                draft_contract=(
                    MethodDraftV2.model_validate(current.draft_contract)
                    if current.draft_contract is not None
                    else None
                ),
            )
            # Resolve nested dependencies through the same gate as runtime admission.
            shared.update(
                await self.methods._shared_dependencies(
                    owner, str(request.id), record.workdraft_revision, draft.definition
                )
            )
            async with self.repo.transaction():
                resolved, _ = await self.methods._dependencies(
                    owner, draft.definition, shared=shared
                )
            verify_selection_graph(
                target, draft.definition, resolved, capabilities, roles
            )
            return draft, roles
        except ValueError as exc:
            code = str(exc)
            if code.startswith("selection_export:"):
                raise BadRequestError(code, code="selection_export") from exc
            raise BadRequestError(
                "The proposed graph needs revision",
                code=code
                if code.startswith("selection_") and len(code) < 64
                else "selection_graph",
            ) from exc


def direct_instruction(title, template, task, source):
    def slot(description):
        return dict(
            description=description,
            schema={"type": "string"},
            required=True,
            meaning={},
            on_missing="fail",
        )

    return FormalMethodDraft.model_validate(
        dict(
            allowed_callers=["host"],
            source=source,
            definition={
                "contract": "knowlab/formal-method/v1",
                "capability_declaration": dict(
                    title=title,
                    purpose=template.goal,
                    applies_when=[template.goal],
                    excludes=[],
                    promises={"template_delivery": template.deliverable},
                ),
                "access_requirements": [],
                "what_requires_approval": [],
                "procedure": dict(
                    kind="direct",
                    implementation={
                        **{
                            k: source[k]
                            for k in ("kind", "source", "entry", "version", "digest")
                        },
                        "requirement_ids": [],
                    },
                    instructions="\n".join(
                        [
                            task,
                            template.goal,
                            template.deliverable,
                            template.check,
                            template.evidence,
                        ]
                    ),
                    inputs={"input": slot(template.goal)},
                    outputs={"result": slot(template.deliverable)},
                    stop_conditions=[template.check],
                    max_model_turns=1,
                ),
                "how_to_validate": [
                    dict(
                        promise_id="template_delivery",
                        outputs=["result"],
                        check=template.check,
                        evidence=template.evidence,
                        on_failure="fail",
                    )
                ],
            },
        )
    )
