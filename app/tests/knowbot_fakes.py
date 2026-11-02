"""创意到专家的测试共用：合格的申请与清单、内存仓储、假运行方、假 knowmart。"""

from __future__ import annotations

import copy
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from app.application.errors.exceptions import (
    BadGatewayError,
    ConcurrencyConflictError,
    ServiceUnavailableError,
)
from app.application.ports.knowbots import (
    AcceptanceRecord,
    AmendmentRecord,
    DeliveryRecord,
    DraftRecord,
    ExpertRecord,
    GrantRecord,
    IncubationRecord,
    ProposalRecord,
    QuestionRecord,
    RequestRecord,
    RequestVersionRecord,
    TestOutputRecord,
    TestRecord,
    TestSetRecord,
    WorkdraftEntryRecord,
    WorkdraftRecord,
)
from app.domain.knowbots.delivery import DeliveryReviewIn, DeliverySubmitIn
from app.domain.knowbots.drafts import KnowbotRequestIn
from app.domain.knowbots.incubation import (
    IncubationRequestV1,
    IncubationResultV1,
)
from app.domain.knowbots.incubation_rounds import ACTIVE_ROUNDS
from app.domain.knowbots.lifecycle import (
    ACTIVE_TESTS,
    AcceptanceIn,
)
from app.domain.knowbots.test_run import (
    CONTRACT,
    AnswersV1,
    CaseOutputsV1,
    RunRequestV1,
    RunResultV1,
)

OWNER = "user-123"  # 与 tests/test_auth_routes_security.session 的用户一致


def request_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "title": "财务分析与预测",
        "runtime": "knowbench",
        "idea": {
            "kind": "six_questions",
            "answers": {
                "solves": "按我的口径分析一家公司的三张表",
                "not_solves": "不给买卖建议",
                "inputs": "我上传的年报",
                "steps": "先算毛利率，毛利率按营业收入减营业成本",
                "outputs": "一页表格",
                "correctness": "毛利率与我手算的一致",
            },
        },
        "cases": [
            {
                "case_id": "c1",
                "input": {"question": "分析 2025 年报"},
                "expectations": [
                    {
                        "artifact": "summary",
                        "path": "gross_margin",
                        "kind": "approx",
                        "value": 0.52,
                        "tolerance": 0.01,
                    }
                ],
            }
        ],
        "consent_test_data": True,
        # 老的那批测试走 0037 的平台流程：请平台孵化、运营受理、平台孵化与测试
        "mode": "platform",
        "budget_limit": "20",
    }
    body.update(overrides)
    return body


def manifest(version: str = "1", **overrides: Any) -> dict[str, Any]:
    """清单 v2 的草稿：逻辑；没有数据需求声明，不带绑定（作者挑选的数据
    交付时才冻结）。"""
    doc: dict[str, Any] = {
        "schema": "knowbot-manifest/v2",
        "name": "财务分析与预测",
        "tagline": "按我的方法分析一家公司的财报",
        "version": version,
        "runtime": "knowbench",
        "pack": {
            "pack_id": "fin-forecast",
            "version": version,
            "workflow": [{"step_id": "s1", "title": "读财报"}],
        },
        "rule_sources": [
            {"rule": "毛利率口径", "source": "user_text", "ref": "想法原文第 4 问"}
        ],
    }
    doc.update(overrides)
    return doc


def result(run_id: str, status: str = "passed", **extra: Any) -> RunResultV1:
    cases = extra.pop("cases", None)
    if cases is None:
        case_status = "passed" if status in {"passed", "running"} else "failed"
        cases = [
            {
                "case_id": "c1",
                "status": case_status,
                "steps": [{"step_id": "s1", "accepted": case_status == "passed"}],
                "expectations": [case_status == "passed"],
            }
        ]
        if status in {"accepted", "running"}:
            cases = []
    return RunResultV1.model_validate(
        {"contract": CONTRACT, "run_id": run_id, "status": status, "cases": cases}
        | extra
    )


def all_clear(**over: Any) -> AcceptanceIn:
    """三样都过的受理单。"""
    body: dict[str, Any] = {
        "checks": {c: {"ok": True} for c in ("C-L5", "C-L2", "C-L1")},
        "accepted": True,
    }
    return AcceptanceIn.model_validate(body | over)


async def accepted_request(svc: Any, owner: str = OWNER, **body: Any) -> RequestRecord:
    """申请并受理了 v1 的创意（孵化要从受理了的版本起）。"""
    request = await svc.submit(
        owner, KnowbotRequestIn.model_validate(request_body(**body))
    )
    await svc.accept(str(request.id), 1, all_clear(), "admin-1")
    return request


async def user_token() -> str:
    return "user-token-for-knowbase"


async def list_expert(
    deliveries: Any, expert_id: Any, listing: str = "self", owner: str = OWNER
) -> ExpertRecord:
    """（要交回的先交回）→ 作者提交交付 → 自用自动审过，或运营审过：上架。"""
    found = await deliveries.repo.get_expert(uuid.UUID(str(expert_id)))
    if found.needs_return and found.returned_at is None:
        await deliveries.experts.return_to_user(str(expert_id), "admin-1")
    delivery = await deliveries.submit(
        owner,
        str(expert_id),
        DeliverySubmitIn(
            listing=listing,  # type: ignore[arg-type]
            authorize_reviewer=True,
            budget_limit="5" if listing == "public" else None,  # type: ignore[arg-type]
        ),
        user_token,
    )
    if delivery.status == "reviewing":
        await deliveries.review(
            str(delivery.id),
            DeliveryReviewIn(approved=True, category="tools.lookup"),
            "admin-1",
        )
    return await deliveries.repo.get_expert(found.id)


class MemoryKnowlab:
    def __init__(self) -> None:
        self.requests: dict[uuid.UUID, RequestRecord] = {}
        self.versions: dict[tuple[uuid.UUID, int], RequestVersionRecord] = {}
        self.acceptances: dict[tuple[uuid.UUID, int], AcceptanceRecord] = {}
        self.tests: dict[uuid.UUID, TestRecord] = {}
        self.experts: dict[uuid.UUID, ExpertRecord] = {}
        self.drafts: dict[uuid.UUID, DraftRecord] = {}
        self.questions: dict[uuid.UUID, QuestionRecord] = {}
        self.test_sets: list[TestSetRecord] = []
        self.amendments: dict[uuid.UUID, AmendmentRecord] = {}
        self.outputs: dict[tuple[uuid.UUID, str, str], TestOutputRecord] = {}
        self.workdrafts: dict[uuid.UUID, WorkdraftRecord] = {}  # 按名称
        self.entries: dict[uuid.UUID, WorkdraftEntryRecord] = {}
        self.incubations: dict[uuid.UUID, IncubationRecord] = {}
        self.grants: dict[uuid.UUID, GrantRecord] = {}
        self.proposals: dict[uuid.UUID, ProposalRecord] = {}
        self.deliveries: dict[uuid.UUID, DeliveryRecord] = {}
        # 作者在别的名称上自助测过的（不在这份假仓里）：用户 → 那一次。平台测试照它定在
        # 哪跑（decisions ⑯），测试里用 own_computer 预置
        self.earlier_self_tests: dict[str, TestRecord] = {}
        self.commits = 0
        self.rollbacks = 0
        self._tick = 0

    def _now(self) -> datetime:
        self._tick += 1
        return datetime(2026, 10, 16, tzinfo=UTC).replace(microsecond=self._tick)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        snapshot = (
            dict(self.requests),
            dict(self.drafts),
            dict(self.questions),
            dict(self.amendments),
            dict(self.outputs),
            dict(self.versions),
            dict(self.acceptances),
            dict(self.tests),
            dict(self.experts),
            dict(self.workdrafts),
            dict(self.entries),
            dict(self.incubations),
            dict(self.grants),
            dict(self.proposals),
            dict(self.deliveries),
        )
        try:
            yield
            self.commits += 1
        except BaseException:
            (
                self.requests,
                self.drafts,
                self.questions,
                self.amendments,
                self.outputs,
                self.versions,
                self.acceptances,
                self.tests,
                self.experts,
                self.workdrafts,
                self.entries,
                self.incubations,
                self.grants,
                self.proposals,
                self.deliveries,
            ) = snapshot
            self.rollbacks += 1
            raise

    async def create_request(self, **fields: Any) -> RequestRecord:
        record = RequestRecord(
            id=uuid.uuid4(), created_at=self._now(), **copy.deepcopy(fields)
        )
        self.requests[record.id] = record
        self.versions[(record.id, 1)] = RequestVersionRecord(
            request_id=record.id,
            version=1,
            idea=copy.deepcopy(record.idea),
            cases=copy.deepcopy(record.cases),
            consent_version=record.consent_version,
            created_by=record.owner_subject,
            created_at=record.created_at,
        )
        return record

    # ---------- 创意版本与受理单 ----------
    async def add_version(self, **fields: Any) -> RequestVersionRecord:
        record = RequestVersionRecord(created_at=self._now(), **copy.deepcopy(fields))
        key = (record.request_id, record.version)
        assert key not in self.versions
        self.versions[key] = record
        self.requests[record.request_id] = replace(
            self.requests[record.request_id],
            latest_version=record.version,
            idea=copy.deepcopy(record.idea),
            cases=copy.deepcopy(record.cases),
            consent_version=record.consent_version,
        )
        return record

    async def get_version(
        self, request_id: uuid.UUID, version: int
    ) -> RequestVersionRecord | None:
        return self.versions.get((request_id, version))

    async def list_versions(
        self, request_ids: Any = None
    ) -> Sequence[RequestVersionRecord]:
        return sorted(
            (
                v
                for v in self.versions.values()
                if request_ids is None or v.request_id in request_ids
            ),
            key=lambda v: (str(v.request_id), v.version),
        )

    async def add_acceptance(self, record: AcceptanceRecord) -> None:
        key = (record.request_id, record.version)
        if key in self.acceptances:
            raise ConcurrencyConflictError("decided", code="already_decided")
        self.acceptances[key] = record

    async def get_acceptance(
        self, request_id: uuid.UUID, version: int
    ) -> AcceptanceRecord | None:
        return self.acceptances.get((request_id, version))

    async def list_acceptances(
        self, request_ids: Any = None
    ) -> Sequence[AcceptanceRecord]:
        return sorted(
            (
                a
                for a in self.acceptances.values()
                if request_ids is None or a.request_id in request_ids
            ),
            key=lambda a: (str(a.request_id), a.version),
        )

    async def get_request(self, request_id: uuid.UUID) -> RequestRecord | None:
        return self.requests.get(request_id)

    async def set_incubation_mode(self, request_id: uuid.UUID, mode: str) -> None:
        self.requests[request_id] = replace(
            self.requests[request_id], incubation_mode=mode
        )

    async def replace_acceptance(self, record: AcceptanceRecord) -> None:
        key = (record.request_id, record.version)
        assert key in self.acceptances
        self.acceptances[key] = record

    # ---------- 给平台的授权 ----------
    async def add_grant(self, record: GrantRecord) -> None:
        for g in self.grants.values():
            if g.status not in ("pending", "active"):
                continue
            same = (
                record.kind == "incubate"
                and g.kind == "incubate"
                and g.request_id == record.request_id
            ) or (
                record.kind == "test"
                and g.kind == "test"
                and g.draft_id == record.draft_id
            )
            if same:
                raise ConcurrencyConflictError("live grant", code="grant_exists")
        self.grants[record.id] = record

    async def get_grant(
        self, grant_id: uuid.UUID, *, for_update: bool = False
    ) -> GrantRecord | None:
        return self.grants.get(grant_id)

    async def list_grants(self, request_ids: Any = None) -> Sequence[GrantRecord]:
        return sorted(
            (
                g
                for g in self.grants.values()
                if request_ids is None or g.request_id in request_ids
            ),
            key=lambda g: (g.granted_at, str(g.id)),
        )

    async def update_grant(self, grant_id: uuid.UUID, **changes: Any) -> None:
        self.grants[grant_id] = replace(self.grants[grant_id], **changes)

    # ---------- 运营的提议 ----------
    async def add_proposals(self, records: Sequence[ProposalRecord]) -> None:
        for r in records:
            self.proposals[r.id] = copy.deepcopy(r)

    async def get_proposal(
        self, proposal_id: uuid.UUID, *, for_update: bool = False
    ) -> ProposalRecord | None:
        return self.proposals.get(proposal_id)

    async def list_proposals(self, request_id: uuid.UUID) -> Sequence[ProposalRecord]:
        return sorted(
            (p for p in self.proposals.values() if p.request_id == request_id),
            key=lambda p: (p.proposed_at, str(p.id)),
        )

    async def update_proposal(self, proposal_id: uuid.UUID, **changes: Any) -> None:
        self.proposals[proposal_id] = replace(self.proposals[proposal_id], **changes)

    async def list_requests_for_owner(
        self, owner_subject: str
    ) -> Sequence[RequestRecord]:
        mine = [r for r in self.requests.values() if r.owner_subject == owner_subject]
        return sorted(mine, key=lambda r: r.created_at or 0, reverse=True)

    async def list_requests(self) -> Sequence[RequestRecord]:
        return sorted(
            self.requests.values(), key=lambda r: r.created_at or 0, reverse=True
        )

    async def lock_request(self, request_id: uuid.UUID) -> None:
        return None

    async def create_draft(self, **fields: Any) -> DraftRecord:
        record = DraftRecord(
            id=uuid.uuid4(),
            status="pending_test",
            created_at=self._now(),
            **copy.deepcopy(fields),
        )
        self.drafts[record.id] = record
        return record

    async def get_draft(
        self, draft_id: uuid.UUID, *, for_update: bool = False
    ) -> DraftRecord | None:
        return self.drafts.get(draft_id)

    async def list_drafts(
        self,
        *,
        status: str | None = None,
        request_id: uuid.UUID | None = None,
        request_ids: Any = None,
    ) -> Sequence[DraftRecord]:
        found = [
            d
            for d in self.drafts.values()
            if (status is None or d.status == status)
            and (request_id is None or d.request_id == request_id)
            and (request_ids is None or d.request_id in request_ids)
        ]
        return sorted(found, key=lambda d: d.created_at or 0)

    async def update_draft(self, draft_id: uuid.UUID, **changes: Any) -> None:
        self.drafts[draft_id] = replace(self.drafts[draft_id], **changes)

    async def set_case_answers(
        self, request_id: uuid.UUID, answers: dict[str, Any]
    ) -> None:
        self.requests[request_id] = replace(
            self.requests[request_id], case_answers=copy.deepcopy(answers)
        )

    # ---------- 测试 ----------
    async def create_test(self, record: TestRecord) -> TestRecord:
        if record.status in ACTIVE_TESTS and any(
            t.request_id == record.request_id and t.status in ACTIVE_TESTS
            for t in self.tests.values()
        ):
            raise ConcurrencyConflictError("running", code="test_running")
        self.tests[record.id] = record
        return record

    async def get_test(
        self, test_id: uuid.UUID, *, for_update: bool = False
    ) -> TestRecord | None:
        return self.tests.get(test_id)

    async def last_self_test(self, owner_subject: str) -> TestRecord | None:
        found = [
            t
            for t in self.tests.values()
            if t.mode == "self"
            and t.environment_id
            and t.workspace_root
            and (r := self.requests.get(t.request_id)) is not None
            and r.owner_subject == owner_subject
        ]
        if owner_subject in self.earlier_self_tests:
            found.append(self.earlier_self_tests[owner_subject])
        return max(found, key=lambda t: t.started_at, default=None)

    async def list_tests(
        self,
        *,
        request_ids: Any = None,
        draft_id: uuid.UUID | None = None,
        statuses: Any = None,
    ) -> Sequence[TestRecord]:
        return sorted(
            (
                t
                for t in self.tests.values()
                if (request_ids is None or t.request_id in request_ids)
                and (draft_id is None or t.draft_id == draft_id)
                and (statuses is None or t.status in statuses)
            ),
            key=lambda t: (str(t.request_id), t.number),
        )

    async def update_test(self, test_id: uuid.UUID, **changes: Any) -> None:
        self.tests[test_id] = replace(self.tests[test_id], **changes)

    async def find_test_by_run(self, run_id: str) -> TestRecord | None:
        return next((t for t in self.tests.values() if t.run_id == run_id), None)

    # ---------- 专家 ----------
    async def create_expert(self, record: ExpertRecord) -> ExpertRecord:
        if any(
            e.request_id == record.request_id and e.version == record.version
            for e in self.experts.values()
        ):
            raise ConcurrencyConflictError("taken", code="expert_version_taken")
        self.experts[record.id] = record
        return record

    async def get_expert(
        self, expert_id: uuid.UUID, *, for_update: bool = False
    ) -> ExpertRecord | None:
        return self.experts.get(expert_id)

    async def list_experts(self, request_ids: Any = None) -> Sequence[ExpertRecord]:
        return sorted(
            (
                e
                for e in self.experts.values()
                if request_ids is None or e.request_id in request_ids
            ),
            key=lambda e: (str(e.request_id), e.version),
        )

    async def update_expert(self, expert_id: uuid.UUID, **changes: Any) -> None:
        self.experts[expert_id] = replace(self.experts[expert_id], **changes)

    # ---------- 交付过程 ----------
    async def create_delivery(self, record: DeliveryRecord) -> DeliveryRecord:
        if record.status == "reviewing" and any(
            d.expert_id == record.expert_id and d.status == "reviewing"
            for d in self.deliveries.values()
        ):
            raise ConcurrencyConflictError("open", code="delivery_open")
        self.deliveries[record.id] = record
        return record

    async def get_delivery(
        self, delivery_id: uuid.UUID, *, for_update: bool = False
    ) -> DeliveryRecord | None:
        return self.deliveries.get(delivery_id)

    async def list_deliveries(
        self,
        request_ids: Any = None,
        *,
        expert_id: uuid.UUID | None = None,
        status: str | None = None,
    ) -> Sequence[DeliveryRecord]:
        return sorted(
            (
                d
                for d in self.deliveries.values()
                if (request_ids is None or d.request_id in request_ids)
                and (expert_id is None or d.expert_id == expert_id)
                and (status is None or d.status == status)
            ),
            key=lambda d: (d.submitted_at, str(d.id)),
        )

    async def update_delivery(self, delivery_id: uuid.UUID, **changes: Any) -> None:
        self.deliveries[delivery_id] = replace(self.deliveries[delivery_id], **changes)

    async def deliveries_to_close(self, limit: int = 100) -> Sequence[DeliveryRecord]:
        return [
            d
            for d in self.deliveries.values()
            if d.status != "reviewing" and d.consent_ended_at is None
        ][:limit]

    async def get_test_set(
        self,
        request_id: uuid.UUID,
        *,
        version: int | None = None,
        from_test_id: uuid.UUID | None = None,
    ) -> TestSetRecord | None:
        mine = [
            t
            for t in self.test_sets
            if t.request_id == request_id
            and (version is None or t.version == version)
            and (from_test_id is None or t.from_test_id == from_test_id)
        ]
        return max(mine, key=lambda t: t.version) if mine else None

    async def directory_rows(self) -> Sequence[dict[str, Any]]:
        out = []
        for request in sorted(self.requests.values(), key=lambda r: r.created_at):
            drafts = [d for d in self.drafts.values() if d.request_id == request.id]
            if not drafts:
                continue
            latest = max(drafts, key=lambda d: d.revision)
            out.append(
                {
                    "request_id": request.id,
                    "owner_subject": request.owner_subject,
                    "title": request.title,
                    "directory_sent": request.directory_sent,
                    "version": latest.version,
                    "revision": latest.revision,
                }
            )
        return out

    async def set_directory_sent(
        self, request_id: uuid.UUID, sent: dict[str, Any]
    ) -> None:
        self.requests[request_id] = replace(
            self.requests[request_id], directory_sent=copy.deepcopy(sent)
        )

    async def tests_without_evidence(
        self, now: datetime, limit: int = 100
    ) -> Sequence[TestRecord]:
        return [
            t
            for t in sorted(self.tests.values(), key=lambda t: t.number)
            if t.status in ("passed", "failed")
            and (
                t.evidence is None
                or str(t.evidence.get("retry_after") or "~") <= now.isoformat()
            )
        ][:limit]

    async def experts_unlisted_unsent(self, limit: int = 100) -> Sequence[ExpertRecord]:
        """同 SQL：没上架、用户不要或审核没过、还没送。"""
        found = [
            e
            for e in self.experts.values()
            if e.unlisted_sent_at is None
            and e.listed_at is None
            and (e.user_accepted is False or e.review_approved is False)
        ]
        return sorted(found, key=lambda e: (e.created_at, str(e.id)))[:limit]

    # ---------- 补充方法 ----------
    async def create_amendment(
        self, *, request_id: uuid.UUID, owner_subject: str, text: str
    ) -> AmendmentRecord:
        record = AmendmentRecord(
            id=uuid.uuid4(),
            request_id=request_id,
            owner_subject=owner_subject,
            text=text,
            created_at=self._now(),
        )
        self.amendments[record.id] = record
        return record

    async def list_amendments(self, request_id: uuid.UUID) -> Sequence[AmendmentRecord]:
        found = [a for a in self.amendments.values() if a.request_id == request_id]
        return sorted(found, key=lambda a: a.created_at or 0)

    async def get_amendment(
        self, amendment_id: uuid.UUID, *, for_update: bool = False
    ) -> AmendmentRecord | None:
        return self.amendments.get(amendment_id)

    async def decline_amendment(
        self, amendment_id: uuid.UUID, *, reason: str, by: str, at: Any
    ) -> None:
        self.amendments[amendment_id] = replace(
            self.amendments[amendment_id],
            declined_reason=reason,
            declined_by=by,
            declined_at=at,
        )

    # ---------- 测试中的问题 ----------
    async def add_questions(
        self, draft_id: uuid.UUID, run_id: str, questions: list[dict[str, Any]]
    ) -> None:
        seen = {(q.draft_id, q.run_id, q.question_id) for q in self.questions.values()}
        for q in questions:
            if (draft_id, run_id, q["question_id"]) in seen:
                continue
            record = QuestionRecord(
                id=uuid.uuid4(),
                draft_id=draft_id,
                run_id=run_id,
                question_id=q["question_id"],
                case_id=q["case_id"],
                kind=q["kind"],
                text=q["text"],
                options=list(q.get("options") or []),
                allow_free_text=bool(q.get("allow_free_text", True)),
                asked_at=q["asked_at"],
                step_id=q.get("step_id"),
                key=q.get("key"),
                route=q.get("route"),
                requests=list(q.get("requests") or []),
            )
            self.questions[record.id] = record

    async def get_question(
        self, question: uuid.UUID, *, for_update: bool = False
    ) -> QuestionRecord | None:
        return self.questions.get(question)

    async def list_questions(
        self, *, draft_id: uuid.UUID | None = None, open_only: bool = False
    ) -> Sequence[QuestionRecord]:
        return [
            q
            for q in self.questions.values()
            if q.kind != "gap"
            and (draft_id is None or q.draft_id == draft_id)
            and (not open_only or q.answer is None)
        ]

    # ---------- AI 孵化的一轮 ----------
    async def create_incubation(self, record: IncubationRecord) -> IncubationRecord:
        if record.status in ACTIVE_ROUNDS and any(
            r.workdraft_id == record.workdraft_id and r.status in ACTIVE_ROUNDS
            for r in self.incubations.values()
        ):
            raise ConcurrencyConflictError("running", code="incubation_running")
        if any(
            r.request_id == record.request_id and r.round == record.round
            for r in self.incubations.values()
        ):
            raise ConcurrencyConflictError("round", code="incubation_running")
        record = replace(record, created_at=self._now())
        self.incubations[record.id] = record
        return copy.deepcopy(record)

    async def get_incubation(
        self, incubation_id: uuid.UUID, *, for_update: bool = False
    ) -> IncubationRecord | None:
        found = self.incubations.get(incubation_id)
        return None if found is None else copy.deepcopy(found)

    async def list_incubations(
        self,
        *,
        request_id: uuid.UUID | None = None,
        owner_subject: str | None = None,
        statuses: Any = None,
    ) -> Sequence[IncubationRecord]:
        return sorted(
            (
                copy.deepcopy(r)
                for r in self.incubations.values()
                if (request_id is None or r.request_id == request_id)
                and (owner_subject is None or r.owner_subject == owner_subject)
                and (statuses is None or r.status in statuses)
            ),
            key=lambda r: (str(r.request_id), r.round),
        )

    async def update_incubation(self, incubation_id: uuid.UUID, **changes: Any) -> None:
        self.incubations[incubation_id] = replace(
            self.incubations[incubation_id], **copy.deepcopy(changes)
        )

    async def add_gaps(
        self, record: IncubationRecord, gaps: list[dict[str, Any]]
    ) -> None:
        seen = {
            (q.incubation_id, q.question_id)
            for q in self.questions.values()
            if q.kind == "gap"
        }
        for gap in gaps:
            if (record.id, gap["question_id"]) in seen:
                continue
            found = QuestionRecord(
                id=uuid.uuid4(),
                draft_id=None,
                run_id=record.run_id,
                question_id=gap["question_id"],
                case_id="",
                kind="gap",
                text=gap["text"],
                options=list(gap.get("options") or []),
                allow_free_text=True,
                asked_at=gap["asked_at"],
                route="user",
                incubation_id=record.id,
                request_id=record.request_id,
                proposal=gap.get("proposal"),
            )
            self.questions[found.id] = found

    async def list_gaps(
        self,
        *,
        request_ids: Any = None,
        incubation_id: uuid.UUID | None = None,
        open_only: bool = False,
    ) -> Sequence[QuestionRecord]:
        return [
            q
            for q in self.questions.values()
            if q.kind == "gap"
            and (request_ids is None or q.request_id in request_ids)
            and (incubation_id is None or q.incubation_id == incubation_id)
            and (not open_only or q.answer is None)
        ]

    async def update_question(self, question: uuid.UUID, **changes: Any) -> None:
        self.questions[question] = replace(self.questions[question], **changes)

    async def latest_test_set(self, request_id: uuid.UUID) -> TestSetRecord | None:
        mine = [t for t in self.test_sets if t.request_id == request_id]
        return max(mine, key=lambda t: t.version) if mine else None

    async def add_test_set(self, record: TestSetRecord) -> None:
        self.test_sets.append(record)

    async def set_consent_version(self, request_id: uuid.UUID, version: int) -> None:
        self.requests[request_id] = replace(
            self.requests[request_id], consent_version=version
        )

    async def test_output_cases(self, draft_id: uuid.UUID, run_id: str) -> set[str]:
        return {c for (d, r, c) in self.outputs if (d, r) == (draft_id, run_id)}

    async def expire_test_outputs(self, before: datetime) -> int:
        """同 SQL：上架早于 before，或有更新的一稿建于 before 之前。"""
        from app.domain.knowbots.drafts import OUTPUTS_EXPIRED

        count = 0
        for key, out in list(self.outputs.items()):
            draft = self.drafts[out.draft_id]
            gone = any(
                e.draft_id == draft.id and e.listed_at and e.listed_at < before
                for e in self.experts.values()
            )
            newer = any(
                d.request_id == draft.request_id
                and d.created_at
                and draft.created_at
                and draft.created_at < d.created_at < before
                for d in self.drafts.values()
            )
            if out.outputs is not None and (gone or newer):
                self.outputs[key] = replace(out, outputs=None, note=OUTPUTS_EXPIRED)
                count += 1
        return count

    # ---------- 工作稿 ----------
    async def get_workdraft(
        self, request_id: uuid.UUID, *, for_update: bool = False
    ) -> WorkdraftRecord | None:
        found = self.workdrafts.get(request_id)
        return None if found is None else copy.deepcopy(found)

    async def create_workdraft(
        self, *, request_id: uuid.UUID, owner_subject: str, body: dict[str, Any]
    ) -> WorkdraftRecord:
        if request_id in self.workdrafts:
            raise ConcurrencyConflictError("exists", code="workdraft_revision")
        record = WorkdraftRecord(
            id=uuid.uuid4(),
            request_id=request_id,
            owner_subject=owner_subject,
            body=copy.deepcopy(body),
            sources={},
            revision=0,
            created_at=self._now(),
            updated_at=self._now(),
        )
        self.workdrafts[request_id] = record
        return record

    async def update_workdraft(
        self,
        workdraft_id: uuid.UUID,
        *,
        body: dict[str, Any],
        sources: dict[str, Any],
        revision: int,
        data_formats: dict[str, Any] | None = None,
    ) -> None:
        for key, found in self.workdrafts.items():
            if found.id == workdraft_id:
                self.workdrafts[key] = replace(
                    found,
                    body=copy.deepcopy(body),
                    sources=copy.deepcopy(sources),
                    revision=revision,
                    updated_at=self._now(),
                    data_formats=found.data_formats
                    if data_formats is None
                    else copy.deepcopy(data_formats),
                )

    async def add_workdraft_entry(self, **fields: Any) -> WorkdraftEntryRecord:
        record = WorkdraftEntryRecord(id=uuid.uuid4(), created_at=self._now(), **fields)
        self.entries[record.id] = record
        return record

    async def list_workdraft_entries(
        self, entry_ids: Any
    ) -> Sequence[WorkdraftEntryRecord]:
        return [self.entries[i] for i in entry_ids if i in self.entries]

    async def put_test_output(self, record: TestOutputRecord) -> None:
        key = (record.draft_id, record.run_id, record.case_id)
        self.outputs.setdefault(key, replace(record, fetched_at=self._now()))

    async def list_test_outputs(
        self, draft_id: uuid.UUID, run_id: str
    ) -> Sequence[TestOutputRecord]:
        return sorted(
            (
                o
                for (d, r, _), o in self.outputs.items()
                if (d, r) == (draft_id, run_id)
            ),
            key=lambda o: o.case_id,
        )


COMPUTER = "11111111-1111-4111-8111-111111111111"  # FakeRuntime 列出的那台电脑
COMPUTER_ROOT = "/home/me/work"


def own_computer(repo: MemoryKnowlab, owner: str = OWNER) -> None:
    """作者以前在自己的电脑上自助测过（别的名称上）：平台测试在那台电脑、那个目录跑
    （decisions ⑯）。"""
    repo.earlier_self_tests[owner] = TestRecord(
        id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        number=1,
        draft_id=uuid.uuid4(),
        status="failed",
        run_id="knowlab-test-earlier",
        started_at=datetime(2026, 10, 1, tzinfo=UTC),
        mode="self",
        environment_id=COMPUTER,
        workspace_root=COMPUTER_ROOT,
    )


class FakeRuntime:
    """按 run_id 记下请求；start 回 accepted，结果由测试塞进 results。"""

    def __init__(self, names: frozenset[str] = frozenset({"knowbench"})) -> None:
        self.names = names
        self.started: list[tuple[str, RunRequestV1]] = []
        self.results: dict[str, RunResultV1] = {}
        self.fail_start = False
        self.answers: list[tuple[str, AnswersV1]] = []
        self.fail_answer = False
        # 一条用例的产出（SDD 0037）：(run_id, case_id) → 产出；
        # 没有的回 None（运行方 404）
        self.case_outputs: dict[tuple[str, str], CaseOutputsV1] = {}
        self.outputs_asked: list[tuple[str, str]] = []
        self.fail_outputs = False
        self.bad_outputs = False  # 回包不合契约
        # 用户在 knowbench 填了模型 key 没有：None 是运行方还没有这个接口
        self.key_ready: bool | None = True
        self.key_asked: list[str] = []
        self.cancelled: list[str] = []
        self.can_cancel = True
        self.on_start: Any = None

    def registered(self) -> frozenset[str]:
        return self.names

    async def start(self, runtime: str, request: RunRequestV1) -> RunResultV1:
        if self.fail_start:
            raise ServiceUnavailableError("down", code="knowbench_unavailable")
        self.started.append((runtime, request))
        if self.on_start is not None:  # 插桩：开跑的同时别处做点什么
            await self.on_start()
        return self.results.get(request.run_id, result(request.run_id, "accepted"))

    async def status(self, runtime: str, run_id: str) -> RunResultV1:
        return self.results.get(run_id, result(run_id, "running"))

    async def cancel(self, runtime: str, run_id: str) -> RunResultV1:
        self.cancelled.append(run_id)
        if not self.can_cancel:  # 运行方还没有停的接口
            raise BadGatewayError("not found", code="not_found")
        self.results[run_id] = result(run_id, "cancelled")
        return self.results[run_id]

    async def outputs(
        self, runtime: str, run_id: str, case_id: str
    ) -> CaseOutputsV1 | None:
        self.outputs_asked.append((run_id, case_id))
        if self.fail_outputs:
            raise ServiceUnavailableError("down", code="knowbench_unavailable")
        if self.bad_outputs:
            raise BadGatewayError("bad", code="test_run_contract_invalid")
        return self.case_outputs.get((run_id, case_id))

    async def computers(self, runtime: str, subject: str) -> list[dict[str, Any]]:
        return [
            {
                "environment_id": COMPUTER,
                "name": "我的电脑",
                "online": True,
                "roots": [COMPUTER_ROOT],
            }
        ]

    async def model_key(self, runtime: str, subject: str) -> bool | None:
        self.key_asked.append(subject)
        return self.key_ready

    async def answer(
        self, runtime: str, run_id: str, answers: AnswersV1
    ) -> RunResultV1:
        if self.fail_answer:
            raise ServiceUnavailableError("down", code="knowbench_unavailable")
        self.answers.append((run_id, answers))
        return self.results.get(run_id, result(run_id, "running"))


class FakeIncubator:
    """假的 knowbench 孵化：按 incubation_id 记下交来的；start 回 queued，之后的结果由
    测试塞进 results。"""

    def __init__(self, configured: bool = True) -> None:
        self.on = configured
        self.started: list[IncubationRequestV1] = []
        self.results: dict[str, IncubationResultV1] = {}
        self.cancelled: list[str] = []
        self.fail_start = False

    def configured(self) -> bool:
        return self.on

    def _result(self, incubation_id: str, status: str) -> IncubationResultV1:
        return IncubationResultV1.model_validate(
            {
                "contract": "knowbot-incubation/v1",
                "incubation_id": incubation_id,
                "status": status,
            }
        )

    async def start(self, request: IncubationRequestV1) -> IncubationResultV1:
        if self.fail_start:
            raise ServiceUnavailableError("down", code="knowbench_unavailable")
        self.started.append(request)
        return self.results.get(
            request.incubation_id, self._result(request.incubation_id, "queued")
        )

    async def status(self, incubation_id: str) -> IncubationResultV1:
        return self.results.get(incubation_id, self._result(incubation_id, "running"))

    async def cancel(self, incubation_id: str) -> IncubationResultV1:
        self.cancelled.append(incubation_id)
        found = self._result(incubation_id, "cancelled")
        self.results[incubation_id] = found
        return found


MATERIAL = "m-annual-2024"


def shelf_entry(
    material_id: str = MATERIAL, name: str = "2024 年报.pdf", **description: Any
) -> dict[str, Any]:
    """knowbase 知识库栏里的一份（ShelfEntryRead 的形状，只留用得上的几项）。"""
    return {
        "category": "upload",
        "material": {
            "material_id": material_id,
            "name": name,
            "mine": True,
            "content_origin": "upload",
            "license": "private",
            "description": {
                "subject": "600519.SH",
                "doc_type": "annual_report",
                "period_start": "2024-01-01",
                "period_end": "2024-12-31",
                "data_format": "pdf",
                "language": "zh",
                "status": "confirmed",
                **description,
            },
            "current_version": 1,
        },
        "holding": {"basis": "upload", "license": "private", "version_no": None},
        "version": {
            "version_no": 1,
            "sha256": "a" * 64,
            "size_bytes": 2048,
            "media_type": "application/pdf",
            "created_at": "2026-10-20T00:00:00Z",
        },
    }


class FakeLibrary:
    """假的 knowbase 本人接口：知识库栏与读法。记下用的令牌，好核是作者本人的。"""

    def __init__(self, entries: list[dict[str, Any]] | None = None) -> None:
        self.entries = [shelf_entry()] if entries is None else entries
        self.readings: dict[str, list[dict[str, Any]]] = {
            MATERIAL: [
                {"kind": "text", "status": "ready"},
                {"kind": "pages", "status": "ready"},
                {"kind": "tables", "status": "failed"},
            ]
        }
        self.tokens: list[str] = []
        self.tables: dict[str, list[dict[str, Any]]] = {}
        self.fail = False

    async def read_format(
        self, user_token: str, material_id: str, version_no: int
    ) -> dict[str, Any]:
        self.tokens.append(user_token)
        entry = next(
            e for e in self.entries if e["material"]["material_id"] == material_id
        )
        return {
            "material_id": material_id,
            "version_no": version_no,
            "sha256": entry["version"]["sha256"],
            "media_type": entry["version"]["media_type"],
            "tables": copy.deepcopy(self.tables.get(material_id, [])),
        }

    async def column(self, user_token: str) -> list[dict[str, Any]]:
        if self.fail:
            raise ServiceUnavailableError("down", code="knowbase_unavailable")
        self.tokens.append(user_token)
        return copy.deepcopy(self.entries)

    async def renditions(
        self, user_token: str, material_id: str, version_no: int
    ) -> list[dict[str, Any]]:
        self.tokens.append(user_token)
        return copy.deepcopy(self.readings.get(material_id, []))


class FakeKnowbaseDelivery:
    """假的 knowbase 交付接口：名录、冻结、许可预查、平台过程依据、机器授权、产出区。

    照 knowbase 的规矩做（真规则另在 knowbase 的测试里核，这里跟它一致）：
    - 冻结只一次：同一版再冻回第一次的那几份（版本、许可、摘要照第一次）；preview 不写；
    - 许可预查带了 basis_ref 的回冻结时的许可，能不能给别人用要冻结的那一档也够；
    - 机器重跑授权：依据只认 platform、reference、public、frozen_binding，平台过程恰好
      一个（platform 项的 basis_ref 与 platform_use 合起来）。

    bound：作者绑给专家的几份（冻结时取）；states：（资料）→ 现在的状态与许可。"""

    BASES = {"platform", "reference", "public", "frozen_binding"}
    RANK = {"self": 0, "via_expert": 1, "sale": 2}

    def __init__(self) -> None:
        self.bound: list[dict[str, Any]] = []
        self.states: dict[str, dict[str, Any]] = {}
        self.directory: dict[str, dict[str, Any]] = {}
        self.frozen: list[tuple[str, str, str]] = []
        self.frozen_sets: dict[str, list[dict[str, Any]]] = {}
        self.consents: list[dict[str, Any]] = []
        self.ended: list[dict[str, Any]] = []
        self.grants: list[dict[str, Any]] = []
        self.outputs: list[dict[str, Any]] = []
        self.fail = False
        self.fail_consent_kind: str | None = None
        self.withdrawn_in_knowbase: set[str] = set()
        self.voided: list[str] = []
        self.revoked: list[str] = []
        self.fail_grant_after: int | None = None
        self.raised: list[tuple[str, str, str]] = []
        self.fail_outputs = False

    def _check(self) -> None:
        if self.fail:
            raise ServiceUnavailableError("down", code="knowbase_unavailable")

    async def push_expert(
        self, expert_id: str, *, owner: str, name: str, latest_version: str | None
    ) -> None:
        self._check()
        self.directory[expert_id] = {
            "owner": owner,
            "name": name,
            "latest_version": latest_version,
        }

    async def freeze(
        self, expert_id: str, version: str, *, author: str, preview: bool = False
    ) -> list[dict[str, Any]]:
        self._check()
        if expert_id not in self.directory:
            raise BadGatewayError("not pushed", code="expert_not_found")
        ref = f"expert:{expert_id}@{version}"
        if ref in self.frozen_sets:
            return copy.deepcopy(self.frozen_sets[ref])
        made = [{**b, "basis_ref": ref} for b in copy.deepcopy(self.bound)]
        for b in made:  # 冻结的是现在的许可
            current = self.states.get(b["material_id"])
            if current and current.get("license"):
                b["license"] = current["license"]
        if not preview:
            self.frozen.append((expert_id, version, author))
            self.frozen_sets[ref] = copy.deepcopy(made)
        return made

    async def binding_states(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self._check()
        out = []
        for i in items:
            state = dict(
                self.states.get(
                    i["material_id"],
                    {"state": "live", "license": "via_expert", "shareable": True},
                )
            )
            state.setdefault(
                "shareable",
                state.get("state") == "live"
                and self.RANK.get(state.get("license") or "self", 0) >= 1,
            )
            ref = i.get("basis_ref")
            if ref is not None:
                frozen = next(
                    (
                        b
                        for b in self.frozen_sets.get(ref, [])
                        if b["material_id"] == i["material_id"]
                        and b["version_no"] == i["version_no"]
                    ),
                    None,
                )
                if frozen is None:
                    state = {"state": "gone", "license": None, "shareable": False}
                else:
                    state["frozen_license"] = frozen.get("license") or "self"
                    state["shareable"] = (
                        bool(state["shareable"])
                        and self.RANK.get(state["frozen_license"], 0) >= 1
                    )
            out.append(state)
        return out

    async def consent_platform(
        self, user_token: str, *, kind: str, ref: str, items: list[dict[str, Any]]
    ) -> int:
        self._check()
        if kind == self.fail_consent_kind:
            raise BadGatewayError("refused", code="not_found")
        self.consents.append(
            {"token": user_token, "kind": kind, "ref": ref, "items": items}
        )
        return max(len(items), 1)  # 没资料也记一条（账号级）

    async def end_platform(
        self, *, subject: str, kind: str, ref: str, reason: str
    ) -> None:
        self._check()
        self.ended.append(
            {"subject": subject, "kind": kind, "ref": ref, "reason": reason}
        )

    async def void_frozen(self, expert_id: str, version: str, *, author: str) -> int:
        self._check()
        ref = f"expert:{expert_id}@{version}"
        self.voided.append(ref)
        return len(self.frozen_sets.pop(ref, []))

    async def raise_frozen_license(
        self,
        expert_id: str,
        version: str,
        material_id: str,
        *,
        author: str,
        license_: str,
    ) -> dict[str, Any]:
        self._check()
        ref = f"expert:{expert_id}@{version}"
        current = self.states.get(material_id, {}).get("license", "via_expert")
        if self.RANK.get(current, 0) < self.RANK[license_]:
            raise BadGatewayError("too low", code="license_too_low")
        for b in self.frozen_sets.get(ref, []):
            if b["material_id"] == material_id:
                if self.RANK.get(b.get("license") or "self", 0) >= self.RANK[license_]:
                    raise BadGatewayError("not higher", code="license_not_higher")
                b["license"] = license_
                self.raised.append((ref, material_id, license_))
                return dict(b)
        raise BadGatewayError("no binding", code="binding_not_found")

    async def platform_consent_live(self, *, subject: str, kind: str, ref: str) -> int:
        self._check()
        if ref in self.withdrawn_in_knowbase:  # 作者在 knowbase 总栏撤了
            return 0
        return sum(
            len(c["items"])
            for c in self.consents
            if (c["kind"], c["ref"]) == (kind, ref)
        )

    async def machine_grant(
        self,
        *,
        owner: str,
        ref: str,
        items: list[dict[str, Any]],
        ttl_seconds: int,
        platform_use: str,
    ) -> str:
        self._check()
        if any(i["basis"] not in self.BASES for i in items):
            raise BadGatewayError("basis", code="basis_not_allowed")
        uses = {i["basis_ref"] for i in items if i["basis"] == "platform"}
        uses.add(platform_use)
        if len(uses) != 1:
            raise BadGatewayError("process", code="basis_ref_invalid")
        _, _, kind, ref = platform_use.split(":")
        consented = any((c["kind"], c["ref"]) == (kind, ref) for c in self.consents)
        ended = any((e["kind"], e["ref"]) == (kind, ref) for e in self.ended)
        if not consented or ended:  # 这次过程本人的同意要在
            raise BadGatewayError("consent", code="outside_consent")
        if (
            self.fail_grant_after is not None
            and len(self.grants) >= self.fail_grant_after
        ):
            raise BadGatewayError("refused", code="not_shown")
        self.grants.append(
            {"owner": owner, "ref": ref, "items": items, "platform_use": platform_use}
        )
        return f"grant-{len(self.grants)}"

    async def revoke_grant(self, grant_id: str) -> None:
        self._check()
        self.revoked.append(grant_id)

    async def write_output(self, **body: Any) -> dict[str, Any]:
        self._check()
        if self.fail_outputs:
            raise BadGatewayError("refused", code="quota_exceeded")
        self.outputs.append(body)
        return {"material_id": f"out-{len(self.outputs)}", "version_no": 1}


class FakeKnowmart:
    def __init__(self) -> None:
        self.delivered: list[dict[str, Any]] = []
        self.stopped: list[dict[str, Any]] = []
        self.public_withdrawn: list[dict[str, Any]] = []
        self.unlisted: list[dict[str, Any]] = []
        # knowmart 那头各版现在怎样（{version, status, held_reason, public_allowed}）
        self.version_states: list[dict[str, Any]] = []
        self.on_deliver: Any = None
        self.fail = False
        self.fail_stop = False
        self.fail_withdraw_public = False

    async def deliver(self, **body: Any) -> dict[str, Any]:
        if self.fail:
            raise ServiceUnavailableError("down", code="knowmart_unavailable")
        if self.on_deliver is not None:  # 插桩：交的同时别处做点什么
            await self.on_deliver()
        if body.get("category") is None:
            body.pop("category", None)
        self.delivered.append(body)
        found = {
            "knowbot_id": "kb-1",
            "version": body["manifest"]["version"],
            "digest": "sha256:" + "d" * 64,
        }
        if body.get("request_public"):
            found["listing_status"] = "pending"
        return found

    async def stop(self, **body: Any) -> None:
        if self.fail or self.fail_stop:
            raise ServiceUnavailableError("down", code="knowmart_unavailable")
        self.stopped.append(body)

    async def withdraw_public(self, **body: Any) -> None:
        if self.fail or self.fail_withdraw_public:
            raise ServiceUnavailableError("down", code="knowmart_unavailable")
        self.public_withdrawn.append(body)

    async def set_unlisted(self, **body: Any) -> None:
        if self.fail:
            raise ServiceUnavailableError("down", code="knowmart_unavailable")
        self.unlisted.append(body)

    async def versions(self, **body: Any) -> list[dict[str, Any]]:
        if self.fail:
            raise ServiceUnavailableError("down", code="knowmart_unavailable")
        return copy.deepcopy(self.version_states)


def waiting(run_id: str) -> RunResultV1:
    """跑到要人决定的地方：一个审批、一个开步前提问、一个 Codex 反问。"""
    asked = "2026-10-17T00:00:00Z"
    return RunResultV1.model_validate(
        {
            "contract": CONTRACT,
            "run_id": run_id,
            "status": "waiting",
            "waited_seconds": 600,
            "questions": [
                {
                    "question_id": "q-approve",
                    "case_id": "c1",
                    "step_id": "s1",
                    "kind": "tool_approval",
                    "text": "执行命令 python calc.py？",
                    "asked_at": asked,
                },
                {
                    "question_id": "q-scope",
                    "case_id": "c1",
                    "step_id": "s1",
                    "kind": "ask_before",
                    "key": "period",
                    "text": "看哪几年？",
                    "options": [{"id": "3y", "label": "近三年"}],
                    "asked_at": asked,
                },
                {
                    "question_id": "q-judge",
                    "case_id": "c1",
                    "kind": "question",
                    "text": "毛利率按哪个口径？",
                    "asked_at": asked,
                },
            ],
        }
    )


async def platform_draft(
    svc: Any,
    request_id: str,
    manifest_doc: dict[str, Any],
    request_version: int | None = None,
) -> DraftRecord:
    """测试里建一位平台孵化的准专家：等于运营在管理端「出一版」的结果，跳过工作稿
    （工作稿与出处把关另有测试）。出自最新正式受理了的版本；版本号 = 已有专家的最大
    版本号 + 1，修订号在这个名称里递增。"""
    repo = svc.repo
    rid = uuid.UUID(str(request_id))
    accepted = sorted(
        a.version
        for a in await repo.list_acceptances([rid])
        if a.accepted and a.kind == "formal"
    )
    drafts = await repo.list_drafts(request_id=rid)
    experts = await repo.list_experts([rid])
    async with repo.transaction():
        return await repo.create_draft(
            request_id=rid,
            request_version=request_version or accepted[-1],
            revision=max((d.revision for d in drafts), default=0) + 1,
            version=str(max((e.version for e in experts), default=0) + 1),
            manifest=manifest_doc,
        )
