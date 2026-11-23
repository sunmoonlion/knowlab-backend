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
    ServiceUnavailableError,
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
from app.domain.knowbots.delivery import output_findings
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
from app.domain.knowbots.formal_method_compiler import (
    CandidateLimits,
    FrozenMethod,
    compile_method,
)
from app.domain.knowbots.formal_method_execution import (
    EXECUTION_CONTRACT,
    BranchDelivery,
    DecideFormalExecution,
    ExecuteFormalMethod,
    FormalExecutionDecision,
    FormalMethodExecutionRequest,
    FormalMethodExecutionResult,
    FormalTestSuite,
    PrepareBranchReview,
    ReceiveBranch,
)
from app.domain.knowbots.formal_method_plan import (
    expand_unique_plan,
    review_group,
    select_plan,
)
from app.domain.knowbots.workdraft import (
    orchestration_gaps,
    record_ids,
    source_problems,
)


def admitted_definition(
    record: MethodVersionRecord, *, require_host: bool = False
) -> FormalMethodDefinition:
    """Old method versions remain history; they never become formal dependencies."""
    try:
        definition = FormalMethodDefinition.model_validate(record.definition)
        receipt = FormalMethodValidationReceipt.model_validate(
            record.provenance["validation"]
        )
        if require_host and (
            "host" not in record.provenance.get("allowed_callers", ["host"])
            or (
                receipt.execution
                and "host" not in (receipt.execution.validated_callers or ("host",))
            )
        ):
            raise BadRequestError(
                "Candidate lacks host qualification", code="formal_method_invalid"
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
    def __init__(
        self, repo: KnowlabRepository, runtimes: RuntimeTestClient, market=None
    ):
        self.market = market
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

    async def list_for_self(self, owner: str, reference: MethodRef):
        if self.market is None:
            raise ServiceUnavailableError("KnowMart delivery is unavailable")
        ref = reference.model_dump(mode="json")
        async with self.repo.transaction():
            record = await self.get(owner, str(reference.method_id), reference.version)
            if reference.digest != record.digest:
                raise ConcurrencyConflictError(
                    "Frozen version changed", code="formal_listing_conflict"
                )
            receipt = FormalMethodValidationReceipt.model_validate(
                record.provenance["validation"]
            )
            request = await self.workdrafts._request(owner, str(receipt.request_id))
        review = await self.runtimes.formal_self_review(request.runtime, owner, ref)
        if (
            not isinstance(review, dict)
            or review.get("owner_subject") != owner
            or review.get("reference") != ref
            or review.get("run_id") != str(receipt.run_id)
            or not isinstance(review.get("samples"), list)
            or not all(
                isinstance(s, dict)
                and isinstance(s.get("steps"), list)
                and s["steps"]
                and all(
                    isinstance(step, dict) and isinstance(step.get("artifact"), dict)
                    for step in s["steps"]
                )
                for s in review["samples"]
            )
        ):
            raise BadGatewayError("Invalid formal self-review response")
        samples = review["samples"]
        findings = set()
        if not samples or receipt.execution is None:
            findings.add("outputs_missing")
        for index, sample in enumerate(samples):
            findings.update(
                item["kind"] for item in output_findings(str(index), sample)
            )
        declaration = record.definition["capability_declaration"]
        result = await self.market.formal_self_listing(
            {
                "owner_subject": owner,
                "reference": ref,
                "title": declaration["title"],
                "purpose": declaration["purpose"],
                "review_findings": sorted(findings),
            }
        )
        if (
            not isinstance(result, dict)
            or result.get("reference") != ref
            or result.get("scope") != "self"
            or result.get("status")
            not in ("listed", "pending", "rejected", "withdrawn")
            or not isinstance(result.get("listing_id"), str)
        ):
            raise BadGatewayError("Invalid KnowMart listing receipt")
        try:
            listing_id = UUID(result["listing_id"])
        except ValueError as exc:
            raise BadGatewayError("Invalid KnowMart listing ID") from exc
        return {
            "listing_id": str(listing_id),
            "scope": "self",
            "reference": ref,
            "status": result["status"],
        }

    async def planning_capabilities(self, owner: str, request_id: str):
        async with self.repo.transaction():
            request = await self.workdrafts._request(owner, request_id)
        return await self.runtimes.planning_capabilities(request.runtime)

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
        shared = await self._shared_dependencies(
            owner, command.request_id, command.revision
        )
        async with self.repo.transaction():
            request, current, admission, _, _ = await self._prepare(
                owner,
                mid,
                ValidateFormalMethod(
                    request_id=command.request_id,
                    revision=command.revision,
                    run_id=command.run_id,
                ),
                shared=shared,
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
            if current.body.get("method_template") and not any(
                case.expected.outcome == "succeeded"
                and "template_delivery" in case.covers_promises
                for case in payload.execution.suite.cases
            ):
                raise BadRequestError(
                    "Select a positive case that verifies the template delivery",
                    code="method_template_examples",
                )
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

    async def receive_trial_branch(
        self, owner: str, method_id: str, run_id: str, command: ReceiveBranch
    ):
        current = FormalMethodExecutionResult.model_validate(
            await self.execution(owner, method_id, str(command.request_id), run_id)
        )
        report = next(
            (
                c.partial or c.verified_steps
                for c in current.cases
                if (c.case_id, c.repetition) == (command.case_id, command.repetition)
            ),
            None,
        )
        if (
            not report
            or current.status not in ("failed", "cancelled", "passed")
            or report.scope_digest != command.scope_digest
        ):
            raise ConcurrencyConflictError(
                "Partial delivery differs from the reviewed case",
                code="partial_scope_changed",
            )
        step = next((s for s in report.steps if s.step == command.step), None)
        if not step or step.state != "accepted" or not step.artifact:
            raise BadRequestError(
                "This step has no verified output", code="partial_output_missing"
            )
        async with self.repo.transaction():
            request = await self.workdrafts._request(owner, str(command.request_id))
        scope = set(ReceiveBranch.model_fields)
        if isinstance(command, PrepareBranchReview):
            if command.step not in report.received or command.artifact != step.artifact:
                raise ConcurrencyConflictError(
                    "Reopen the accepted artifact before review",
                    code="review_scope_changed",
                )
            selected = set(command.criteria.outputs)
            if command.criteria.comparison:
                selected.add(command.criteria.comparison.assessment_output)
            if not selected.issubset(step.artifact.output_names):
                raise BadRequestError(
                    "Unknown review output", code="review_output_missing"
                )
            raw = await self.runtimes.prepare_trial_review(
                request.runtime, owner, run_id, command
            )
        else:
            raw = await self.runtimes.receive_trial_branch(
                request.runtime, owner, run_id, command
            )
        try:
            result = BranchDelivery.model_validate(raw)
            if (
                result.method_id != current.method_id
                or result.run_id != current.run_id
                or result.artifact != step.artifact
                or result.model_dump(include=scope) != command.model_dump(include=scope)
                or (
                    isinstance(command, PrepareBranchReview)
                    and (
                        not result.review or result.review.criteria != command.criteria
                    )
                )
            ):
                raise ValueError("Different partial scope or artifact")
        except ValueError as exc:
            raise BadGatewayError(
                "Invalid branch delivery receipt", code="formal_execution_invalid"
            ) from exc
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

    async def _incubation_basis(self, owner, request_id):
        async with self.repo.transaction():
            request, saved = await self.workdrafts.get(owner, request_id)
            draft = parse_body(saved.body).formal_method if saved else None
        declaration = (
            draft.definition.capability_declaration.model_dump(mode="json")
            if draft
            else None
        )
        return request.runtime, declaration

    async def listing(self, owner: str, *, limit: int, offset: int, request_id=None):
        if not 1 <= limit <= 100 or offset < 0:
            raise BadRequestError("Invalid method page", code="method_page")
        runtime = "knowbench"
        declaration = None
        if request_id is not None:
            runtime, declaration = await self._incubation_basis(owner, request_id)
        return await self.runtimes.method_skill_center_incubation_methods(
            runtime, owner, limit=limit, offset=offset, declaration=declaration
        )

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

    async def select_incubation_binding(
        self, owner, request_id, reference, asset_id, revision
    ):
        async with self.repo.transaction():
            request = await self.workdrafts._request(owner, request_id)
        expected = {
            "reference": reference.model_dump(mode="json"),
            "asset_id": str(asset_id),
            "revision": revision,
        }
        receipt = await self.runtimes.select_incubation_binding(
            request.runtime, {"subject": owner, **expected}
        )
        if receipt != expected:
            raise BadGatewayError("Different incubation binding receipt")
        return expected

    async def preview_candidate_choice(
        self,
        owner: str,
        request_id: str,
        method_id: UUID,
        revision: int,
        definition: FormalMethodDefinition,
    ):
        graph = definition.procedure
        if not isinstance(graph, Orchestration) or not graph.has_candidate_choice:
            raise BadRequestError(
                "Expected a candidate choice", code="formal_method_invalid"
            )
        shared = await self._shared_dependencies(
            owner, request_id, revision, definition
        )
        async with self.repo.transaction():
            request, current = await self.workdrafts.get(owner, request_id)
            if revision != (current.revision if current else 0):
                raise ConcurrencyConflictError(
                    "Draft revision changed", code="workdraft_revision"
                )
            if (await self.repo.method_owner(method_id)) not in (None, owner):
                raise NotFoundError("No such method", code="method_not_found")
            resolved, owned = await self._dependencies(owner, definition, shared=shared)
        for reference in owned:
            if not await self._registered(request.runtime, owner, reference):
                raise ConcurrencyConflictError(
                    "Candidate registration changed", code="formal_method_unregistered"
                )
        try:
            compiled = compile_method(method_id, definition, resolved)
        except CandidateLimits as exc:
            return {
                "fits": False,
                "depth": exc.depth,
                "steps": exc.steps,
                "expansions": exc.expansions,
                "revision": revision,
            }
        except ValueError as exc:
            raise BadRequestError(str(exc), code="formal_method_invalid") from exc
        return {
            "fits": True,
            "depth": compiled.required_depth,
            "steps": compiled.required_steps,
            "expansions": compiled.required_expansions,
            "revision": revision,
        }

    async def matching_outputs(
        self,
        owner,
        request_id,
        slot,
        exclude_method_id,
        offset,
        *,
        shared=False,
        after=None,
    ):
        limit = 20
        runtime, declaration = await self._incubation_basis(owner, request_id)
        # A shared page is bounded by the market cursor. Match only the exact
        # versions authorized on this page, including their immutable digests.
        next_page = None
        granted = None
        if shared:
            page = await self._shared_tools(
                runtime, owner, after=after, declaration=declaration
            )
            granted = [item["reference"] for item in page["items"]]
            next_page = page["next"]
            if not granted:
                return {"items": [], "next": next_page}
        async with self.repo.transaction():
            rows = await self.repo.method_output_slots(
                slot,
                exclude_method_id,
                offset,
                None if shared else limit + 1,
                granted=granted,
                owner=None if shared else owner,
            )
        if not shared:
            next_page = offset + limit if len(rows) > limit else None
            rows = rows[:limit]
            if rows:
                pins = [
                    dict(
                        method_id=str(r["method_id"]),
                        version=r["version"],
                        digest=r["digest"],
                    )
                    for r in rows
                ]
                authorized = await self.runtimes.method_skill_center_incubation_methods(
                    runtime,
                    owner,
                    limit=limit,
                    offset=0,
                    all_versions=True,
                    declaration=declaration,
                    references=pins,
                )
                rank = {
                    (str(r["method_id"]), r["latest_version"], r["digest"]): n
                    for n, r in enumerate(authorized)
                }

                def key(row):
                    return (str(row["method_id"]), row["version"], row["digest"])

                rows = sorted(
                    (r for r in rows if key(r) in rank), key=lambda r: rank[key(r)]
                )
        items = [
            {
                "reference": {
                    "method_id": str(row["method_id"]),
                    "version": row["version"],
                    "digest": row["digest"],
                },
                "output": row["output"],
                "title": row["title"],
            }
            for row in rows
        ]
        return {"items": items, "next": next_page}

    async def shared_tools(self, owner, request_id, *, reference=None, after=None):
        runtime, declaration = await self._incubation_basis(owner, request_id)
        page = await self._shared_tools(
            runtime,
            owner,
            reference=reference,
            after=after,
            declaration=declaration if reference is None else None,
        )
        for item in page["items"]:
            definition = item.pop("definition")
            if definition:
                procedure = definition.pop("procedure")
                item["interface"] = {
                    **definition,
                    "inputs": procedure["inputs"],
                    "outputs": procedure["outputs"],
                }
        return page

    async def _shared_tools(
        self, runtime, owner, *, reference=None, after=None, declaration=None
    ):
        from app.domain.knowbots.incubation_tools import SharedToolsPage

        raw = await self.runtimes.incubation_tool_versions(
            runtime, owner, reference=reference, after=after, declaration=declaration
        )
        try:
            page = SharedToolsPage.model_validate(raw)
            if after and page.next and page.next <= UUID(str(after)):
                raise ValueError("Incubation directory cursor did not advance")
            if any(
                (reference is None) != (item.definition is None)
                or (reference and item.reference != reference)
                for item in page.items
            ) or (reference and (page.next or len(page.items) > 1)):
                raise ValueError("Unexpected incubation receipt")
        except ValueError as exc:
            raise BadGatewayError("Invalid incubation tool response") from exc
        return page.model_dump(mode="json", by_alias=True, exclude_none=False)

    async def _shared_dependencies(self, owner, request_id, revision, definition=None):
        missing = set()
        async with self.repo.transaction():
            request, current = await self.workdrafts.get(owner, str(request_id))
            if revision != (current.revision if current else 0):
                raise ConcurrencyConflictError(
                    "Draft revision changed", code="workdraft_revision"
                )
            if definition is None:
                draft = parse_body(current.body).formal_method if current else None
                definition = draft.definition if draft else None
            if definition and isinstance(definition.procedure, Orchestration):
                await self._dependencies(owner, definition, shared={}, missing=missing)
        shared = {}
        for ref in sorted(missing, key=lambda r: (r.method_id, r.version, r.digest)):
            page = await self._shared_tools(request.runtime, owner, reference=ref)
            if not page["items"] or not page["items"][0]["selected"]:
                raise NotFoundError(
                    "Incubation tool unavailable", code="method_not_found"
                )
            shared[ref] = FormalMethodDefinition.model_validate(
                page["items"][0]["definition"]
            )
        return shared

    async def _dependencies(
        self,
        owner: str,
        definition: FormalMethodDefinition,
        *,
        shared,
        missing=None,
        public=False,
    ):
        require_host = (
            isinstance(definition.procedure, Orchestration)
            and definition.procedure.has_candidate_choice
        )
        resolved: dict[tuple[UUID, int], FrozenMethod] = {}
        owned: list[MethodRef] = []

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
                    record = await self.repo.method_version(ref.method_id, ref.version)
                    if public and (
                        record is None
                        or record.owner_subject != owner
                        or record.provenance.get("visibility") != "public"
                    ):
                        raise BadRequestError(
                            "Public dependencies must belong to the expert author",
                            code="formal_delivery_denied",
                        )
                    if record is not None and record.owner_subject == owner:
                        if record.digest != ref.digest:
                            raise ConcurrencyConflictError(
                                "Method digest changed", code="method_integrity"
                            )
                        child = admitted_definition(record, require_host=require_host)
                        owned.append(ref)
                    else:
                        if missing is not None:
                            missing.add(ref)
                            if len(missing) + len(resolved) > 1000:
                                raise BadRequestError(
                                    "Method dependency limit",
                                    code="formal_method_invalid",
                                )
                            continue
                        child = shared.get(ref)
                        if child is None:
                            raise NotFoundError(
                                "No such dependency", code="method_not_found"
                            )
                    resolved[key] = FrozenMethod(ref, child)
                    await visit(child, depth + 1)

        await visit(definition, 0)
        return resolved, owned

    async def _prepare(
        self,
        owner: str,
        method_id: UUID,
        command: ValidateFormalMethod,
        *,
        shared,
        public=False,
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
        if body.collaboration and isinstance(draft.definition.procedure, Orchestration):
            graph = draft.definition.procedure.model_copy(
                update={"collaboration": body.collaboration}
            )
            definition = draft.definition.model_copy(update={"procedure": graph})
            draft = draft.model_copy(update={"definition": definition})
        resolved, owned = await self._dependencies(
            owner, draft.definition, shared=shared, public=public
        )
        if review_group(draft.definition.procedure):
            try:
                expand_unique_plan(
                    method_id, select_plan(draft.definition, {}), resolved
                )
            except ValueError as exc:
                raise ConcurrencyConflictError(
                    str(exc), code="runtime_capability_gap"
                ) from exc
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
        return request, current, payload, compiled, owned

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
        shared = await self._shared_dependencies(
            owner, command.request_id, command.revision
        )
        async with self.repo.transaction():
            request, _, payload, _, owned = await self._prepare(
                owner, parse_id(method_id, "method"), command, shared=shared
            )
        # _dependencies already resolved shared versions under live grants.
        # Registration by subject covers only the dependencies this author owns.
        for reference in owned:
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
            else:
                # Reject unsupported graphs before asking the runtime for evidence.
                # _prepare checks this same immutable revision again before writing.
                current = await self.workdrafts.locked(request, command.revision)
                if orchestration_gaps(current.body):
                    raise ConcurrencyConflictError(
                        "The runner lacks a required orchestration primitive",
                        code="runtime_capability_gap",
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
            already_frozen = await self.repo.method_version(mid, version)
        shared = (
            {}
            if already_frozen
            else await self._shared_dependencies(
                owner, command.request_id, command.revision
            )
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
            request, current, payload, compiled, _ = await self._prepare(
                owner,
                mid,
                ValidateFormalMethod(
                    request_id=command.request_id,
                    revision=command.revision,
                    run_id=command.run_id,
                ),
                shared=shared,
                public=command.visibility == "public",
            )
            receipt = self._receipt(payload, result)
            source = payload.draft.source
            template = current.body.get("method_template")
            if template and receipt.execution is None:
                raise BadRequestError(
                    "The delivery template requires a completed trial",
                    code="formal_execution_required",
                )
            if (
                payload.draft.definition.capability_declaration.conditions
                and receipt.execution is None
            ):
                raise BadRequestError(
                    "Conditional delivery promises require execution evidence",
                    code="formal_promise_trial_required",
                )
            if (
                source
                and source.kind in ("registered_script", "service_function")
                and (command.visibility != "private" or receipt.execution is None)
            ):
                raise BadRequestError(
                    "This source requires a private freeze with trial evidence",
                    code="formal_function_trial_required"
                    if source.kind == "service_function"
                    else "formal_script_trial_required",
                )
            if (
                source
                and source.is_instruction
                and (command.visibility != "private" or receipt.execution is None)
            ):
                raise BadRequestError(
                    "Instruction methods require a private freeze with trial evidence",
                    code="formal_instruction_trial_required",
                )
            if command.visibility == "public" and (
                receipt.uncovered or "business" not in receipt.checks
            ):
                raise BadRequestError(
                    "Public methods require complete declared coverage",
                    code="formal_validation_uncovered",
                )
            if (
                command.visibility == "public"
                and source
                and source.kind in ("skill_instruction", "skill_script")
                and source.delivery_terms is None
            ):
                raise BadRequestError(
                    "The Skill lacks reviewed delivery terms",
                    code="formal_delivery_denied",
                )
            if command.visibility == "public" and receipt.execution is None:
                raise BadRequestError(
                    "Public candidates require runtime execution evidence",
                    code="formal_execution_required",
                )
            provenance = {
                **({"method_template": template} if template else {}),
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
