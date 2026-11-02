"""创意到专家的 PostgreSQL 实现（SDD 0027、0037）。

一个实例对应一次请求的数据库会话；transaction 可嵌套，最外层提交、出错回滚。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Collection, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, cast

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.errors.exceptions import ConcurrencyConflictError
from app.application.ports.knowbots import (
    AcceptanceRecord,
    AmendmentRecord,
    DeliveryRecord,
    DraftRecord,
    ExpertRecord,
    GrantRecord,
    IncubationRecord,
    MethodVersionRecord,
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
from app.domain.knowbots.drafts import OUTPUTS_EXPIRED
from app.infrastructure.models.knowbots import (
    KnowbotAcceptance,
    KnowbotAmendment,
    KnowbotDelivery,
    KnowbotDraft,
    KnowbotExpert,
    KnowbotIncubation,
    KnowbotMethod,
    KnowbotMethodVersion,
    KnowbotPlatformGrant,
    KnowbotQuestion,
    KnowbotRequest,
    KnowbotRequestVersion,
    KnowbotTest,
    KnowbotTestOutput,
    KnowbotTestSet,
    KnowbotWorkdraft,
    KnowbotWorkdraftProposal,
    KnowbotWorkdraftRecord,
)

# 0012 起准专家只改这几栏；run、报告、交付几栏不再写（内容在测试、专家表）
DRAFT_FIELDS = frozenset(
    {"status", "rules_reviewed_by", "rules_reviewed_at", "user_review"}
)
TEST_FIELDS = frozenset(
    {
        "status",
        "finished_at",
        "report",
        "error",
        "test_set_version",
        "case_grants",
        "evidence",
    }
)
EXPERT_FIELDS = frozenset(
    {
        "returned_by",
        "returned_at",
        "user_accepted",
        "user_decided_by",
        "user_decided_at",
        "review_approved",
        "review_listing",
        "review_reason",
        "reviewed_by",
        "reviewed_at",
        "listed_at",
        "knowmart_knowbot_id",
        "digest",
        "delivery",
        "delivery_error",
        "stopped_at",
        "stopped_by",
        "stop_reason",
        "unlisted_sent_at",
    }
)


DELIVERY_FIELDS = frozenset(
    {
        "status",
        "binding_check",
        "auto_check",
        "consents",
        "rerun_test_id",
        "rerun_attempts",
        "rerun_spent",
        "decided_by",
        "decided_at",
        "reason",
        "category",
        "delivered_at",
        "delivery_error",
        "receipt",
        "consent_ended_at",
        "withdrawn_by",
        "withdrawn_at",
        "withdraw_reason",
    }
)


def _delivery(row: KnowbotDelivery) -> DeliveryRecord:
    return DeliveryRecord(
        id=row.id,
        expert_id=row.expert_id,
        request_id=row.request_id,
        listing=row.listing,
        test_mode=row.test_mode,
        needs_rerun=row.needs_rerun,
        submitted_by=row.submitted_by,
        submitted_at=row.submitted_at,
        conversion=row.conversion,
        budget_limit=row.budget_limit,
        bindings=list(row.bindings or []),
        **{name: getattr(row, name) for name in DELIVERY_FIELDS},
    )


GRANT_FIELDS = frozenset(
    {
        "status",
        "budget_limit",
        "expires_at",
        "decided_by",
        "decided_at",
        "reason",
        "ended_by",
        "ended_at",
    }
)


def _grant(row: KnowbotPlatformGrant) -> GrantRecord:
    return GrantRecord(
        id=row.id,
        request_id=row.request_id,
        kind=row.kind,
        status=row.status,
        budget_limit=row.budget_limit,
        granted_by=row.granted_by,
        granted_at=row.granted_at,
        draft_id=row.draft_id,
        expires_at=row.expires_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        reason=row.reason,
        ended_by=row.ended_by,
        ended_at=row.ended_at,
    )


PROPOSAL_FIELDS = frozenset({"state", "decided_at", "records"})


def _proposal(row: KnowbotWorkdraftProposal) -> ProposalRecord:
    return ProposalRecord(
        id=row.id,
        request_id=row.request_id,
        path=row.path,
        op=row.op,
        value=row.value,
        reason=row.reason,
        proposed_by=row.proposed_by,
        proposed_at=row.proposed_at,
        state=row.state,
        decided_at=row.decided_at,
        records=list(row.records or []),
    )


def _request(row: KnowbotRequest) -> RequestRecord:
    return RequestRecord(
        id=row.id,
        owner_subject=row.owner_subject,
        title=row.title,
        runtime=row.runtime,
        idea=row.idea,
        cases=row.cases,
        created_at=row.created_at,
        consent_version=row.consent_version,
        latest_version=row.latest_version,
        case_answers=row.case_answers or {},
        incubation_mode=row.incubation_mode,
        origin=row.origin,
        reference_answer=row.reference_answer,
        directory_sent=row.directory_sent,
    )


def _version(row: KnowbotRequestVersion) -> RequestVersionRecord:
    return RequestVersionRecord(
        request_id=row.request_id,
        version=row.version,
        idea=row.idea,
        cases=row.cases,
        consent_version=row.consent_version,
        created_by=row.created_by,
        created_at=row.created_at,
    )


def _acceptance(row: KnowbotAcceptance) -> AcceptanceRecord:
    return AcceptanceRecord(
        request_id=row.request_id,
        version=row.version,
        accepted=row.accepted,
        checks=row.checks,
        reason=row.reason,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        trial=row.trial,
        kind=row.kind,
    )


def _draft(row: KnowbotDraft) -> DraftRecord:
    return DraftRecord(
        id=row.id,
        request_id=row.request_id,
        revision=row.revision,
        version=row.version,
        status=row.status,
        manifest=row.manifest,
        rules_reviewed_by=row.rules_reviewed_by,
        rules_reviewed_at=row.rules_reviewed_at,
        created_at=row.created_at,
        request_version=row.request_version,
        user_review=row.user_review,
        mode=row.mode,
    )


def _workdraft(row: KnowbotWorkdraft) -> WorkdraftRecord:
    return WorkdraftRecord(
        id=row.id,
        request_id=row.request_id,
        owner_subject=row.owner_subject,
        body=row.body,
        sources=row.sources or {},
        revision=row.revision,
        created_at=row.created_at,
        updated_at=row.updated_at,
        data_formats=row.data_formats or {},
    )


def _entry(row: KnowbotWorkdraftRecord) -> WorkdraftEntryRecord:
    return WorkdraftEntryRecord(
        id=row.id,
        workdraft_id=row.workdraft_id,
        kind=row.kind,
        actor_subject=row.actor_subject,
        actor_role=row.actor_role,
        text=row.text,
        path=row.path,
        suggestion_ref=row.suggestion_ref,
        created_at=row.created_at,
    )


def _test(row: KnowbotTest) -> TestRecord:
    return TestRecord(
        id=row.id,
        request_id=row.request_id,
        number=row.number,
        draft_id=row.draft_id,
        status=row.status,
        run_id=row.run_id,
        started_at=row.started_at,
        budget_limit=row.budget_limit,
        timeout_seconds=row.timeout_seconds,
        started_by=row.started_by,
        finished_at=row.finished_at,
        report=row.report,
        error=row.error,
        test_set_version=row.test_set_version,
        case_grants=list(row.case_grants or []),
        mode=row.mode,
        environment_id=row.environment_id,
        workspace_root=row.workspace_root,
        evidence=row.evidence,
    )


def _expert(row: KnowbotExpert) -> ExpertRecord:
    return ExpertRecord(
        id=row.id,
        request_id=row.request_id,
        version=row.version,
        draft_id=row.draft_id,
        created_at=row.created_at,
        test_id=row.test_id,
        **{name: getattr(row, name) for name in EXPERT_FIELDS},
        trial=row.trial,
        needs_return=row.needs_return,
    )


def _amendment(row: KnowbotAmendment) -> AmendmentRecord:
    return AmendmentRecord(
        id=row.id,
        request_id=row.request_id,
        owner_subject=row.owner_subject,
        text=row.text,
        created_at=row.created_at,
        declined_reason=row.declined_reason,
        declined_by=row.declined_by,
        declined_at=row.declined_at,
    )


QUESTION_FIELDS = frozenset(
    {
        "route",
        "route_reason",
        "answer",
        "answered_by",
        "answered_at",
        "sent_at",
        "last_error",
        "data_state",
    }
)


INCUBATION_FIELDS = frozenset(
    {
        "status",
        "spent",
        "task_id",
        "waiting_for",
        "error",
        "handback",
        "review",
        "decisions",
        "started_at",
        "finished_at",
    }
)


def _incubation(row: KnowbotIncubation) -> IncubationRecord:
    return IncubationRecord(
        id=row.id,
        request_id=row.request_id,
        workdraft_id=row.workdraft_id,
        owner_subject=row.owner_subject,
        round=row.round,
        mode=row.mode,
        action=row.action,
        instruction=row.instruction,
        step_id=row.step_id,
        status=row.status,
        budget_limit=row.budget_limit,
        spent=row.spent,
        timeout_seconds=row.timeout_seconds,
        run_id=row.run_id,
        task_id=row.task_id,
        environment_id=row.environment_id,
        workspace_root=row.workspace_root,
        waiting_for=row.waiting_for,
        request_version=row.request_version,
        seed=dict(row.seed),
        workdraft_revision=row.workdraft_revision,
        answer_ids=list(row.answer_ids or []),
        error=row.error,
        handback=row.handback,
        review=row.review,
        decisions=dict(row.decisions or {}),
        started_at=row.started_at,
        finished_at=row.finished_at,
        created_at=row.created_at,
    )


def _question(row: KnowbotQuestion) -> QuestionRecord:
    return QuestionRecord(
        id=row.id,
        draft_id=row.draft_id,
        run_id=row.run_id,
        question_id=row.question_id,
        case_id=row.case_id,
        kind=row.kind,
        text=row.text,
        options=row.options,
        allow_free_text=row.allow_free_text,
        asked_at=row.asked_at,
        step_id=row.step_id,
        key=row.key,
        route=row.route,
        route_reason=row.route_reason,
        answer=row.answer,
        answered_by=row.answered_by,
        answered_at=row.answered_at,
        sent_at=row.sent_at,
        last_error=row.last_error,
        requests=list(row.requests or []),
        data_state=row.data_state,
        incubation_id=row.incubation_id,
        request_id=row.request_id,
        proposal=row.proposal,
    )


class SqlKnowlabRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._depth = 0

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        outer = self._depth == 0
        self._depth += 1
        try:
            yield
            if outer:
                await self.session.commit()
        except BaseException:
            if outer:
                await self.session.rollback()
            raise
        finally:
            self._depth -= 1

    async def create_request(
        self,
        *,
        owner_subject: str,
        title: str,
        runtime: str,
        idea: dict[str, Any],
        cases: list[dict[str, Any]],
        consent_version: int = 1,
        origin: dict[str, Any] | None = None,
        reference_answer: str | None = None,
    ) -> RequestRecord:
        row = KnowbotRequest(
            id=uuid.uuid4(),
            owner_subject=owner_subject,
            title=title,
            runtime=runtime,
            idea=idea,
            cases=cases,
            consent_version=consent_version,
            latest_version=1,
            case_answers={},
            origin=origin,
            reference_answer=reference_answer,
        )
        self.session.add(row)
        await self.session.flush()
        self.session.add(
            KnowbotRequestVersion(
                request_id=row.id,
                version=1,
                idea=idea,
                cases=cases,
                consent_version=consent_version,
                created_by=owner_subject,
            )
        )
        await self.session.flush()
        await self.session.refresh(row)
        return _request(row)

    # ---------- 创意版本与受理单 ----------
    async def add_version(
        self,
        *,
        request_id: uuid.UUID,
        version: int,
        idea: dict[str, Any],
        cases: list[dict[str, Any]],
        consent_version: int,
        created_by: str,
    ) -> RequestVersionRecord:
        row = KnowbotRequestVersion(
            request_id=request_id,
            version=version,
            idea=idea,
            cases=cases,
            consent_version=consent_version,
            created_by=created_by,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.execute(
            update(KnowbotRequest)
            .where(KnowbotRequest.id == request_id)
            .values(
                latest_version=version,
                idea=idea,
                cases=cases,
                consent_version=consent_version,
            )
        )
        await self.session.refresh(row)
        return _version(row)

    async def get_version(
        self, request_id: uuid.UUID, version: int
    ) -> RequestVersionRecord | None:
        row = await self.session.get(KnowbotRequestVersion, (request_id, version))
        return None if row is None else _version(row)

    async def list_versions(
        self, request_ids: Collection[uuid.UUID] | None = None
    ) -> Sequence[RequestVersionRecord]:
        query = select(KnowbotRequestVersion).order_by(
            KnowbotRequestVersion.request_id, KnowbotRequestVersion.version
        )
        if request_ids is not None:
            query = query.where(KnowbotRequestVersion.request_id.in_(request_ids))
        return [_version(r) for r in (await self.session.execute(query)).scalars()]

    async def add_acceptance(self, record: AcceptanceRecord) -> None:
        self.session.add(
            KnowbotAcceptance(
                request_id=record.request_id,
                version=record.version,
                accepted=record.accepted,
                checks=record.checks,
                reason=record.reason,
                decided_by=record.decided_by,
                decided_at=record.decided_at,
                trial=record.trial,
                kind=record.kind,
            )
        )
        try:
            async with self.session.begin_nested():
                await self.session.flush()
        except IntegrityError as exc:
            raise ConcurrencyConflictError(
                "This version already has its acceptance", code="already_decided"
            ) from exc

    async def get_acceptance(
        self, request_id: uuid.UUID, version: int
    ) -> AcceptanceRecord | None:
        row = await self.session.get(KnowbotAcceptance, (request_id, version))
        return None if row is None else _acceptance(row)

    async def list_acceptances(
        self, request_ids: Collection[uuid.UUID] | None = None
    ) -> Sequence[AcceptanceRecord]:
        query = select(KnowbotAcceptance).order_by(
            KnowbotAcceptance.request_id, KnowbotAcceptance.version
        )
        if request_ids is not None:
            query = query.where(KnowbotAcceptance.request_id.in_(request_ids))
        return [_acceptance(r) for r in (await self.session.execute(query)).scalars()]

    async def get_request(self, request_id: uuid.UUID) -> RequestRecord | None:
        # 改版、记答案走 update，身份表里那份可能旧了：每次都读库
        row = (
            await self.session.execute(
                select(KnowbotRequest)
                .where(KnowbotRequest.id == request_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        return None if row is None else _request(row)

    async def list_requests_for_owner(
        self, owner_subject: str
    ) -> Sequence[RequestRecord]:
        rows = (
            await self.session.execute(
                select(KnowbotRequest)
                .where(KnowbotRequest.owner_subject == owner_subject)
                .order_by(KnowbotRequest.created_at.desc(), KnowbotRequest.id)
                .execution_options(populate_existing=True)
            )
        ).scalars()
        return [_request(r) for r in rows]

    async def list_requests(self) -> Sequence[RequestRecord]:
        rows = (
            await self.session.execute(
                select(KnowbotRequest)
                .order_by(KnowbotRequest.created_at.desc(), KnowbotRequest.id)
                .execution_options(populate_existing=True)
            )
        ).scalars()
        return [_request(r) for r in rows]

    async def set_incubation_mode(self, request_id: uuid.UUID, mode: str) -> None:
        await self.session.execute(
            update(KnowbotRequest)
            .where(KnowbotRequest.id == request_id)
            .values(incubation_mode=mode)
        )

    async def replace_acceptance(self, record: AcceptanceRecord) -> None:
        await self.session.execute(
            update(KnowbotAcceptance)
            .where(
                KnowbotAcceptance.request_id == record.request_id,
                KnowbotAcceptance.version == record.version,
            )
            .values(
                accepted=record.accepted,
                checks=record.checks,
                reason=record.reason,
                decided_by=record.decided_by,
                decided_at=record.decided_at,
                trial=record.trial,
                kind=record.kind,
            )
        )

    # ---------- 给平台的授权 ----------
    async def add_grant(self, record: GrantRecord) -> None:
        self.session.add(
            KnowbotPlatformGrant(
                id=record.id,
                request_id=record.request_id,
                kind=record.kind,
                draft_id=record.draft_id,
                status=record.status,
                budget_limit=record.budget_limit,
                granted_by=record.granted_by,
                granted_at=record.granted_at,
                expires_at=record.expires_at,
            )
        )
        try:
            async with self.session.begin_nested():
                await self.session.flush()
        except IntegrityError as exc:
            raise ConcurrencyConflictError(
                "There is already a live grant", code="grant_exists"
            ) from exc

    async def get_grant(
        self, grant_id: uuid.UUID, *, for_update: bool = False
    ) -> GrantRecord | None:
        query = select(KnowbotPlatformGrant).where(KnowbotPlatformGrant.id == grant_id)
        if for_update:
            query = query.with_for_update()
        row = (await self.session.execute(query)).scalar_one_or_none()
        return None if row is None else _grant(row)

    async def list_grants(
        self, request_ids: Collection[uuid.UUID] | None = None
    ) -> Sequence[GrantRecord]:
        query = select(KnowbotPlatformGrant).order_by(
            KnowbotPlatformGrant.granted_at, KnowbotPlatformGrant.id
        )
        if request_ids is not None:
            query = query.where(KnowbotPlatformGrant.request_id.in_(request_ids))
        return [_grant(r) for r in (await self.session.execute(query)).scalars()]

    async def update_grant(self, grant_id: uuid.UUID, **changes: Any) -> None:
        unknown = set(changes) - GRANT_FIELDS
        if unknown:
            raise ValueError(f"unknown grant fields: {sorted(unknown)}")
        await self.session.execute(
            update(KnowbotPlatformGrant)
            .where(KnowbotPlatformGrant.id == grant_id)
            .values(**changes)
        )

    # ---------- 运营的提议 ----------
    async def add_proposals(self, records: Sequence[ProposalRecord]) -> None:
        for r in records:
            self.session.add(
                KnowbotWorkdraftProposal(
                    id=r.id,
                    request_id=r.request_id,
                    path=r.path,
                    op=r.op,
                    value=r.value,
                    reason=r.reason,
                    proposed_by=r.proposed_by,
                    proposed_at=r.proposed_at,
                    state=r.state,
                    records=list(r.records),
                )
            )
        await self.session.flush()

    async def get_proposal(
        self, proposal_id: uuid.UUID, *, for_update: bool = False
    ) -> ProposalRecord | None:
        query = select(KnowbotWorkdraftProposal).where(
            KnowbotWorkdraftProposal.id == proposal_id
        )
        if for_update:
            query = query.with_for_update()
        row = (await self.session.execute(query)).scalar_one_or_none()
        return None if row is None else _proposal(row)

    async def list_proposals(self, request_id: uuid.UUID) -> Sequence[ProposalRecord]:
        query = (
            select(KnowbotWorkdraftProposal)
            .where(KnowbotWorkdraftProposal.request_id == request_id)
            .order_by(KnowbotWorkdraftProposal.proposed_at, KnowbotWorkdraftProposal.id)
        )
        return [_proposal(r) for r in (await self.session.execute(query)).scalars()]

    async def update_proposal(self, proposal_id: uuid.UUID, **changes: Any) -> None:
        unknown = set(changes) - PROPOSAL_FIELDS
        if unknown:
            raise ValueError(f"unknown proposal fields: {sorted(unknown)}")
        await self.session.execute(
            update(KnowbotWorkdraftProposal)
            .where(KnowbotWorkdraftProposal.id == proposal_id)
            .values(**changes)
        )

    async def lock_request(self, request_id: uuid.UUID) -> None:
        await self.session.execute(
            select(KnowbotRequest.id)
            .where(KnowbotRequest.id == request_id)
            .with_for_update()
        )

    async def create_draft(
        self,
        *,
        request_id: uuid.UUID,
        request_version: int,
        revision: int,
        version: str,
        manifest: dict[str, Any],
        mode: str = "platform",
    ) -> DraftRecord:
        row = KnowbotDraft(
            id=uuid.uuid4(),
            request_id=request_id,
            request_version=request_version,
            revision=revision,
            version=version,
            status="pending_test",
            manifest=manifest,
            run_attempts=0,
            mode=mode,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return _draft(row)

    async def get_draft(
        self, draft_id: uuid.UUID, *, for_update: bool = False
    ) -> DraftRecord | None:
        query = select(KnowbotDraft).where(KnowbotDraft.id == draft_id)
        if for_update:
            query = query.with_for_update()
        row = (
            await self.session.execute(query.execution_options(populate_existing=True))
        ).scalar_one_or_none()
        return None if row is None else _draft(row)

    async def list_drafts(
        self,
        *,
        status: str | None = None,
        request_id: uuid.UUID | None = None,
        request_ids: Collection[uuid.UUID] | None = None,
    ) -> Sequence[DraftRecord]:
        query = select(KnowbotDraft).order_by(
            KnowbotDraft.created_at, KnowbotDraft.revision
        )
        if status is not None:
            query = query.where(KnowbotDraft.status == status)
        if request_id is not None:
            query = query.where(KnowbotDraft.request_id == request_id)
        if request_ids is not None:
            query = query.where(KnowbotDraft.request_id.in_(request_ids))
        rows = (
            await self.session.execute(query.execution_options(populate_existing=True))
        ).scalars()
        return [_draft(r) for r in rows]

    async def update_draft(self, draft_id: uuid.UUID, **changes: Any) -> None:
        unknown = set(changes) - DRAFT_FIELDS
        if unknown:
            raise ValueError(f"unknown draft fields: {sorted(unknown)}")
        await self.session.execute(
            update(KnowbotDraft).where(KnowbotDraft.id == draft_id).values(**changes)
        )

    # ---------- 测试 ----------
    async def create_test(self, record: TestRecord) -> TestRecord:
        row = KnowbotTest(
            id=record.id,
            request_id=record.request_id,
            number=record.number,
            draft_id=record.draft_id,
            status=record.status,
            budget_limit=record.budget_limit,
            timeout_seconds=record.timeout_seconds,
            run_id=record.run_id,
            started_by=record.started_by,
            started_at=record.started_at,
            test_set_version=record.test_set_version,
            case_grants=list(record.case_grants),
            mode=record.mode,
            environment_id=record.environment_id,
            workspace_root=record.workspace_root,
        )
        self.session.add(row)
        try:
            async with self.session.begin_nested():
                await self.session.flush()
        except IntegrityError as exc:
            raise ConcurrencyConflictError(
                "Another test of this knowbot is running", code="test_running"
            ) from exc
        await self.session.refresh(row)
        return _test(row)

    async def get_test(
        self, test_id: uuid.UUID, *, for_update: bool = False
    ) -> TestRecord | None:
        query = select(KnowbotTest).where(KnowbotTest.id == test_id)
        if for_update:
            query = query.with_for_update()
        row = (
            await self.session.execute(query.execution_options(populate_existing=True))
        ).scalar_one_or_none()
        return None if row is None else _test(row)

    async def list_tests(
        self,
        *,
        request_ids: Collection[uuid.UUID] | None = None,
        draft_id: uuid.UUID | None = None,
        statuses: Collection[str] | None = None,
    ) -> Sequence[TestRecord]:
        query = select(KnowbotTest).order_by(KnowbotTest.request_id, KnowbotTest.number)
        if request_ids is not None:
            query = query.where(KnowbotTest.request_id.in_(request_ids))
        if draft_id is not None:
            query = query.where(KnowbotTest.draft_id == draft_id)
        if statuses is not None:
            query = query.where(KnowbotTest.status.in_(list(statuses)))
        rows = (
            await self.session.execute(query.execution_options(populate_existing=True))
        ).scalars()
        return [_test(r) for r in rows]

    async def last_self_test(self, owner_subject: str) -> TestRecord | None:
        row = (
            await self.session.execute(
                select(KnowbotTest)
                .join(KnowbotRequest, KnowbotRequest.id == KnowbotTest.request_id)
                .where(
                    KnowbotRequest.owner_subject == owner_subject,
                    KnowbotTest.mode == "self",
                    KnowbotTest.environment_id.is_not(None),
                    KnowbotTest.workspace_root.is_not(None),
                )
                .order_by(KnowbotTest.started_at.desc())
                .limit(1)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        return None if row is None else _test(row)

    async def update_test(self, test_id: uuid.UUID, **changes: Any) -> None:
        unknown = set(changes) - TEST_FIELDS
        if unknown:
            raise ValueError(f"unknown test fields: {sorted(unknown)}")
        await self.session.execute(
            update(KnowbotTest).where(KnowbotTest.id == test_id).values(**changes)
        )

    async def find_test_by_run(self, run_id: str) -> TestRecord | None:
        row = (
            await self.session.execute(
                select(KnowbotTest)
                .where(KnowbotTest.run_id == run_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        return None if row is None else _test(row)

    # ---------- 专家 ----------
    async def create_expert(self, record: ExpertRecord) -> ExpertRecord:
        row = KnowbotExpert(
            id=record.id,
            request_id=record.request_id,
            version=record.version,
            draft_id=record.draft_id,
            test_id=record.test_id,
            created_at=record.created_at,
            trial=record.trial,
            needs_return=record.needs_return,
        )
        self.session.add(row)
        try:
            async with self.session.begin_nested():
                await self.session.flush()
        except IntegrityError as exc:
            raise ConcurrencyConflictError(
                "This expert version already exists", code="expert_version_taken"
            ) from exc
        await self.session.refresh(row)
        return _expert(row)

    async def get_expert(
        self, expert_id: uuid.UUID, *, for_update: bool = False
    ) -> ExpertRecord | None:
        query = select(KnowbotExpert).where(KnowbotExpert.id == expert_id)
        if for_update:
            query = query.with_for_update()
        row = (
            await self.session.execute(query.execution_options(populate_existing=True))
        ).scalar_one_or_none()
        return None if row is None else _expert(row)

    async def list_experts(
        self, request_ids: Collection[uuid.UUID] | None = None
    ) -> Sequence[ExpertRecord]:
        query = select(KnowbotExpert).order_by(
            KnowbotExpert.request_id, KnowbotExpert.version
        )
        if request_ids is not None:
            query = query.where(KnowbotExpert.request_id.in_(request_ids))
        rows = (
            await self.session.execute(query.execution_options(populate_existing=True))
        ).scalars()
        return [_expert(r) for r in rows]

    async def experts_unlisted_unsent(self, limit: int = 100) -> Sequence[ExpertRecord]:
        rows = (
            await self.session.execute(
                select(KnowbotExpert)
                .where(
                    KnowbotExpert.unlisted_sent_at.is_(None),
                    KnowbotExpert.listed_at.is_(None),
                    or_(
                        KnowbotExpert.user_accepted.is_(False),
                        KnowbotExpert.review_approved.is_(False),
                    ),
                )
                .order_by(KnowbotExpert.created_at, KnowbotExpert.id)
                .limit(limit)
            )
        ).scalars()
        return [_expert(r) for r in rows]

    async def update_expert(self, expert_id: uuid.UUID, **changes: Any) -> None:
        unknown = set(changes) - EXPERT_FIELDS
        if unknown:
            raise ValueError(f"unknown expert fields: {sorted(unknown)}")
        await self.session.execute(
            update(KnowbotExpert).where(KnowbotExpert.id == expert_id).values(**changes)
        )

    # ---------- 交付过程 ----------
    async def create_delivery(self, record: DeliveryRecord) -> DeliveryRecord:
        row = KnowbotDelivery(
            id=record.id,
            expert_id=record.expert_id,
            request_id=record.request_id,
            listing=record.listing,
            conversion=record.conversion,
            test_mode=record.test_mode,
            needs_rerun=record.needs_rerun,
            budget_limit=record.budget_limit,
            submitted_by=record.submitted_by,
            submitted_at=record.submitted_at,
            bindings=list(record.bindings),
            **{name: getattr(record, name) for name in DELIVERY_FIELDS},
        )
        self.session.add(row)
        try:
            async with self.session.begin_nested():
                await self.session.flush()
        except IntegrityError as exc:
            raise ConcurrencyConflictError(
                "This expert is already being reviewed", code="delivery_open"
            ) from exc
        await self.session.refresh(row)
        return _delivery(row)

    async def get_delivery(
        self, delivery_id: uuid.UUID, *, for_update: bool = False
    ) -> DeliveryRecord | None:
        query = select(KnowbotDelivery).where(KnowbotDelivery.id == delivery_id)
        if for_update:
            query = query.with_for_update()
        row = (
            await self.session.execute(query.execution_options(populate_existing=True))
        ).scalar_one_or_none()
        return None if row is None else _delivery(row)

    async def list_deliveries(
        self,
        request_ids: Collection[uuid.UUID] | None = None,
        *,
        expert_id: uuid.UUID | None = None,
        status: str | None = None,
    ) -> Sequence[DeliveryRecord]:
        query = select(KnowbotDelivery).order_by(
            KnowbotDelivery.submitted_at, KnowbotDelivery.id
        )
        if status is not None:
            query = query.where(KnowbotDelivery.status == status)
        if request_ids is not None:
            query = query.where(KnowbotDelivery.request_id.in_(request_ids))
        if expert_id is not None:
            query = query.where(KnowbotDelivery.expert_id == expert_id)
        rows = (
            await self.session.execute(query.execution_options(populate_existing=True))
        ).scalars()
        return [_delivery(r) for r in rows]

    async def update_delivery(self, delivery_id: uuid.UUID, **changes: Any) -> None:
        unknown = set(changes) - DELIVERY_FIELDS
        if unknown:
            raise ValueError(f"unknown delivery fields: {sorted(unknown)}")
        await self.session.execute(
            update(KnowbotDelivery)
            .where(KnowbotDelivery.id == delivery_id)
            .values(**changes)
        )

    async def deliveries_to_close(self, limit: int = 100) -> Sequence[DeliveryRecord]:
        rows = (
            await self.session.execute(
                select(KnowbotDelivery)
                .where(
                    KnowbotDelivery.status != "reviewing",
                    KnowbotDelivery.consent_ended_at.is_(None),
                )
                .order_by(KnowbotDelivery.decided_at, KnowbotDelivery.id)
                .limit(limit)
            )
        ).scalars()
        return [_delivery(r) for r in rows]

    async def get_test_set(
        self,
        request_id: uuid.UUID,
        *,
        version: int | None = None,
        from_test_id: uuid.UUID | None = None,
    ) -> TestSetRecord | None:
        query = select(KnowbotTestSet).where(KnowbotTestSet.request_id == request_id)
        if version is not None:
            query = query.where(KnowbotTestSet.version == version)
        if from_test_id is not None:
            query = query.where(KnowbotTestSet.from_test_id == from_test_id)
        row = (
            await self.session.execute(
                query.order_by(KnowbotTestSet.version.desc()).limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return TestSetRecord(
            id=row.id,
            request_id=row.request_id,
            version=row.version,
            cases=dict(row.cases),
            created_at=row.created_at,
            from_test_id=row.from_test_id,
        )

    async def directory_rows(self) -> Sequence[dict[str, Any]]:
        """名录要的几样：每个有准专家的名称（编号、作者、名称、推过的那份）与最新那位准专家
        的版本号、修订号。只取这几栏，不读创意、清单的大栏位。"""
        latest = (
            select(
                KnowbotDraft.request_id,
                KnowbotDraft.version,
                KnowbotDraft.revision,
            )
            .distinct(KnowbotDraft.request_id)
            .order_by(KnowbotDraft.request_id, KnowbotDraft.revision.desc())
            .subquery()
        )
        rows = (
            await self.session.execute(
                select(
                    KnowbotRequest.id,
                    KnowbotRequest.owner_subject,
                    KnowbotRequest.title,
                    KnowbotRequest.directory_sent,
                    latest.c.version,
                    latest.c.revision,
                )
                .join(latest, latest.c.request_id == KnowbotRequest.id)
                .order_by(KnowbotRequest.created_at, KnowbotRequest.id)
            )
        ).all()
        return [
            {
                "request_id": r[0],
                "owner_subject": r[1],
                "title": r[2],
                "directory_sent": r[3],
                "version": r[4],
                "revision": r[5],
            }
            for r in rows
        ]

    async def set_directory_sent(
        self, request_id: uuid.UUID, sent: dict[str, Any]
    ) -> None:
        await self.session.execute(
            update(KnowbotRequest)
            .where(KnowbotRequest.id == request_id)
            .values(directory_sent=sent)
        )

    async def tests_without_evidence(
        self, now: datetime, limit: int = 100
    ) -> Sequence[TestRecord]:
        rows = (
            await self.session.execute(
                select(KnowbotTest)
                .where(
                    KnowbotTest.status.in_(("passed", "failed")),
                    or_(
                        KnowbotTest.evidence.is_(None),
                        KnowbotTest.evidence["retry_after"].astext <= now.isoformat(),
                    ),
                )
                .order_by(KnowbotTest.finished_at, KnowbotTest.id)
                .limit(limit)
            )
        ).scalars()
        return [_test(r) for r in rows]

    async def set_consent_version(self, request_id: uuid.UUID, version: int) -> None:
        await self.session.execute(
            update(KnowbotRequest)
            .where(KnowbotRequest.id == request_id)
            .values(consent_version=version)
        )

    async def test_output_cases(self, draft_id: uuid.UUID, run_id: str) -> set[str]:
        rows = await self.session.execute(
            select(KnowbotTestOutput.case_id).where(
                KnowbotTestOutput.draft_id == draft_id,
                KnowbotTestOutput.run_id == run_id,
            )
        )
        return set(rows.scalars())

    async def expire_test_outputs(self, before: datetime) -> int:
        sql = text(
            """update knowbot_test_output o
               set outputs = null, note = :note, updated_at = now()
               from knowbot_draft d
               where o.draft_id = d.id and o.outputs is not null and (
                 exists (
                   select 1 from knowbot_expert e
                   where e.draft_id = d.id and e.listed_at < :before)
                 or exists (
                   select 1 from knowbot_draft n
                   where n.request_id = d.request_id
                     and n.created_at > d.created_at and n.created_at < :before))"""
        )
        params = {"note": OUTPUTS_EXPIRED, "before": before}
        found = cast(CursorResult[Any], await self.session.execute(sql, params))
        return found.rowcount or 0

    async def put_test_output(self, record: TestOutputRecord) -> None:
        from sqlalchemy.dialects.postgresql import insert

        await self.session.execute(
            insert(KnowbotTestOutput)
            .values(
                draft_id=record.draft_id,
                run_id=record.run_id,
                case_id=record.case_id,
                outputs=record.outputs,
                note=record.note,
            )
            .on_conflict_do_nothing()
        )

    async def list_test_outputs(
        self, draft_id: uuid.UUID, run_id: str
    ) -> Sequence[TestOutputRecord]:
        rows = (
            await self.session.execute(
                select(KnowbotTestOutput)
                .where(
                    KnowbotTestOutput.draft_id == draft_id,
                    KnowbotTestOutput.run_id == run_id,
                )
                .order_by(KnowbotTestOutput.case_id)
            )
        ).scalars()
        return [
            TestOutputRecord(
                draft_id=r.draft_id,
                run_id=r.run_id,
                case_id=r.case_id,
                outputs=r.outputs,
                note=r.note,
                fetched_at=r.created_at,
            )
            for r in rows
        ]

    async def set_case_answers(
        self, request_id: uuid.UUID, answers: dict[str, Any]
    ) -> None:
        await self.session.execute(
            update(KnowbotRequest)
            .where(KnowbotRequest.id == request_id)
            .values(case_answers=answers)
        )

    # ---------- 补充方法 ----------
    async def create_amendment(
        self, *, request_id: uuid.UUID, owner_subject: str, text: str
    ) -> AmendmentRecord:
        row = KnowbotAmendment(
            id=uuid.uuid4(),
            request_id=request_id,
            owner_subject=owner_subject,
            text=text,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return _amendment(row)

    async def list_amendments(self, request_id: uuid.UUID) -> Sequence[AmendmentRecord]:
        rows = (
            await self.session.execute(
                select(KnowbotAmendment)
                .where(KnowbotAmendment.request_id == request_id)
                .order_by(KnowbotAmendment.created_at, KnowbotAmendment.id)
                .execution_options(populate_existing=True)
            )
        ).scalars()
        return [_amendment(r) for r in rows]

    async def get_amendment(
        self, amendment_id: uuid.UUID, *, for_update: bool = False
    ) -> AmendmentRecord | None:
        query = select(KnowbotAmendment).where(KnowbotAmendment.id == amendment_id)
        if for_update:
            query = query.with_for_update()
        row = (
            await self.session.execute(query.execution_options(populate_existing=True))
        ).scalar_one_or_none()
        return None if row is None else _amendment(row)

    async def decline_amendment(
        self, amendment_id: uuid.UUID, *, reason: str, by: str, at: Any
    ) -> None:
        await self.session.execute(
            update(KnowbotAmendment)
            .where(KnowbotAmendment.id == amendment_id)
            .values(declined_reason=reason, declined_by=by, declined_at=at)
        )

    # ---------- 测试中的问题 ----------
    async def add_questions(
        self, draft_id: uuid.UUID, run_id: str, questions: list[dict[str, Any]]
    ) -> None:
        from sqlalchemy.dialects.postgresql import insert

        for q in questions:
            await self.session.execute(
                insert(KnowbotQuestion)
                .values(
                    id=uuid.uuid4(),
                    draft_id=draft_id,
                    run_id=run_id,
                    question_id=q["question_id"],
                    case_id=q["case_id"],
                    step_id=q.get("step_id"),
                    kind=q["kind"],
                    key=q.get("key"),
                    text=q["text"],
                    options=list(q.get("options") or []),
                    allow_free_text=bool(q.get("allow_free_text", True)),
                    asked_at=q["asked_at"],
                    route=q.get("route"),
                    requests=list(q.get("requests") or []),
                )
                .on_conflict_do_nothing(constraint="uq_knowbot_question_once")
            )

    async def get_question(
        self, question: uuid.UUID, *, for_update: bool = False
    ) -> QuestionRecord | None:
        query = select(KnowbotQuestion).where(KnowbotQuestion.id == question)
        if for_update:
            query = query.with_for_update()
        row = (
            await self.session.execute(query.execution_options(populate_existing=True))
        ).scalar_one_or_none()
        return None if row is None else _question(row)

    async def list_questions(
        self, *, draft_id: uuid.UUID | None = None, open_only: bool = False
    ) -> Sequence[QuestionRecord]:
        # 测试中的问题；孵化的缺口另列（list_gaps）
        query = (
            select(KnowbotQuestion)
            .where(KnowbotQuestion.kind != "gap")
            .order_by(KnowbotQuestion.created_at, KnowbotQuestion.question_id)
        )
        if draft_id is not None:
            query = query.where(KnowbotQuestion.draft_id == draft_id)
        if open_only:
            query = query.where(KnowbotQuestion.answer.is_(None))
        rows = (
            await self.session.execute(query.execution_options(populate_existing=True))
        ).scalars()
        return [_question(r) for r in rows]

    async def latest_test_set(self, request_id: uuid.UUID) -> TestSetRecord | None:
        row = (
            await self.session.execute(
                select(KnowbotTestSet)
                .where(KnowbotTestSet.request_id == request_id)
                .order_by(KnowbotTestSet.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return TestSetRecord(
            id=row.id,
            request_id=row.request_id,
            version=row.version,
            cases=dict(row.cases),
            created_at=row.created_at,
            from_test_id=row.from_test_id,
        )

    # ---------- AI 孵化的一轮 ----------
    async def create_incubation(self, record: IncubationRecord) -> IncubationRecord:
        row = KnowbotIncubation(
            id=record.id,
            request_id=record.request_id,
            workdraft_id=record.workdraft_id,
            owner_subject=record.owner_subject,
            round=record.round,
            mode=record.mode,
            action=record.action,
            instruction=record.instruction,
            step_id=record.step_id,
            status=record.status,
            budget_limit=record.budget_limit,
            timeout_seconds=record.timeout_seconds,
            run_id=record.run_id,
            environment_id=record.environment_id,
            workspace_root=record.workspace_root,
            request_version=record.request_version,
            seed=record.seed,
            workdraft_revision=record.workdraft_revision,
            answer_ids=list(record.answer_ids),
            decisions={},
            started_at=record.started_at,
        )
        self.session.add(row)
        try:
            async with self.session.begin_nested():
                await self.session.flush()
        except IntegrityError as exc:
            raise ConcurrencyConflictError(
                "Another round of this workdraft is running",
                code="incubation_running",
            ) from exc
        await self.session.refresh(row)
        return _incubation(row)

    async def get_incubation(
        self, incubation_id: uuid.UUID, *, for_update: bool = False
    ) -> IncubationRecord | None:
        query = select(KnowbotIncubation).where(KnowbotIncubation.id == incubation_id)
        if for_update:
            query = query.with_for_update()
        row = (
            await self.session.execute(query.execution_options(populate_existing=True))
        ).scalar_one_or_none()
        return None if row is None else _incubation(row)

    async def list_incubations(
        self,
        *,
        request_id: uuid.UUID | None = None,
        owner_subject: str | None = None,
        statuses: Collection[str] | None = None,
    ) -> Sequence[IncubationRecord]:
        query = select(KnowbotIncubation).order_by(
            KnowbotIncubation.request_id, KnowbotIncubation.round
        )
        if request_id is not None:
            query = query.where(KnowbotIncubation.request_id == request_id)
        if owner_subject is not None:
            query = query.where(KnowbotIncubation.owner_subject == owner_subject)
        if statuses is not None:
            query = query.where(KnowbotIncubation.status.in_(list(statuses)))
        rows = (
            await self.session.execute(query.execution_options(populate_existing=True))
        ).scalars()
        return [_incubation(r) for r in rows]

    async def update_incubation(self, incubation_id: uuid.UUID, **changes: Any) -> None:
        unknown = set(changes) - INCUBATION_FIELDS
        if unknown:
            raise ValueError(f"unknown incubation fields: {sorted(unknown)}")
        await self.session.execute(
            update(KnowbotIncubation)
            .where(KnowbotIncubation.id == incubation_id)
            .values(**changes)
        )

    async def add_gaps(
        self, record: IncubationRecord, gaps: list[dict[str, Any]]
    ) -> None:
        from sqlalchemy.dialects.postgresql import insert

        for gap in gaps:
            await self.session.execute(
                insert(KnowbotQuestion)
                .values(
                    id=uuid.uuid4(),
                    incubation_id=record.id,
                    request_id=record.request_id,
                    run_id=record.run_id,
                    question_id=gap["question_id"],
                    case_id="",
                    kind="gap",
                    text=gap["text"],
                    options=list(gap.get("options") or []),
                    allow_free_text=True,
                    asked_at=gap["asked_at"],
                    route="user",
                    proposal=gap.get("proposal"),
                    requests=[],
                )
                .on_conflict_do_nothing(
                    index_elements=["incubation_id", "question_id"],
                    index_where=text("incubation_id IS NOT NULL"),
                )
            )

    async def list_gaps(
        self,
        *,
        request_ids: Collection[uuid.UUID] | None = None,
        incubation_id: uuid.UUID | None = None,
        open_only: bool = False,
    ) -> Sequence[QuestionRecord]:
        query = (
            select(KnowbotQuestion)
            .where(KnowbotQuestion.kind == "gap")
            .order_by(KnowbotQuestion.created_at, KnowbotQuestion.question_id)
        )
        if request_ids is not None:
            query = query.where(KnowbotQuestion.request_id.in_(list(request_ids)))
        if incubation_id is not None:
            query = query.where(KnowbotQuestion.incubation_id == incubation_id)
        if open_only:
            query = query.where(KnowbotQuestion.answer.is_(None))
        rows = (
            await self.session.execute(query.execution_options(populate_existing=True))
        ).scalars()
        return [_question(r) for r in rows]

    async def add_test_set(self, record: TestSetRecord) -> None:
        self.session.add(
            KnowbotTestSet(
                id=record.id,
                request_id=record.request_id,
                version=record.version,
                cases=record.cases,
                from_test_id=record.from_test_id,
                created_at=record.created_at,
            )
        )
        await self.session.flush()

    # ---------- 工作稿 ----------
    async def get_workdraft(
        self, request_id: uuid.UUID, *, for_update: bool = False
    ) -> WorkdraftRecord | None:
        query = select(KnowbotWorkdraft).where(
            KnowbotWorkdraft.request_id == request_id
        )
        if for_update:
            query = query.with_for_update()
        row = (
            await self.session.execute(query.execution_options(populate_existing=True))
        ).scalar_one_or_none()
        return None if row is None else _workdraft(row)

    async def create_workdraft(
        self, *, request_id: uuid.UUID, owner_subject: str, body: dict[str, Any]
    ) -> WorkdraftRecord:
        row = KnowbotWorkdraft(
            id=uuid.uuid4(),
            request_id=request_id,
            owner_subject=owner_subject,
            body=body,
            sources={},
            revision=0,
        )
        self.session.add(row)
        try:
            async with self.session.begin_nested():
                await self.session.flush()
        except IntegrityError as exc:
            raise ConcurrencyConflictError(
                "This knowbot already has its workdraft", code="workdraft_revision"
            ) from exc
        await self.session.refresh(row)
        return _workdraft(row)

    async def update_workdraft(
        self,
        workdraft_id: uuid.UUID,
        *,
        body: dict[str, Any],
        sources: dict[str, Any],
        revision: int,
        data_formats: dict[str, Any] | None = None,
    ) -> None:
        values: dict[str, Any] = {
            "body": body,
            "sources": sources,
            "revision": revision,
        }
        if data_formats is not None:
            values["data_formats"] = data_formats
        await self.session.execute(
            update(KnowbotWorkdraft)
            .where(KnowbotWorkdraft.id == workdraft_id)
            .values(**values)
        )

    async def add_workdraft_entry(
        self,
        *,
        workdraft_id: uuid.UUID,
        kind: str,
        actor_subject: str,
        actor_role: str,
        text: str,
        path: str | None = None,
        suggestion_ref: str | None = None,
    ) -> WorkdraftEntryRecord:
        row = KnowbotWorkdraftRecord(
            id=uuid.uuid4(),
            workdraft_id=workdraft_id,
            kind=kind,
            actor_subject=actor_subject,
            actor_role=actor_role,
            path=path,
            text=text,
            suggestion_ref=suggestion_ref,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return _entry(row)

    async def list_workdraft_entries(
        self, entry_ids: Collection[uuid.UUID]
    ) -> Sequence[WorkdraftEntryRecord]:
        if not entry_ids:
            return []
        rows = (
            await self.session.execute(
                select(KnowbotWorkdraftRecord).where(
                    KnowbotWorkdraftRecord.id.in_(list(entry_ids))
                )
            )
        ).scalars()
        return [_entry(r) for r in rows]

    async def update_question(self, question: uuid.UUID, **changes: Any) -> None:
        unknown = set(changes) - QUESTION_FIELDS
        if unknown:
            raise ValueError(f"unknown question fields: {sorted(unknown)}")
        await self.session.execute(
            update(KnowbotQuestion)
            .where(KnowbotQuestion.id == question)
            .values(**changes)
        )

    # ---------- 独立作者方法：版本只插，不覆盖 ----------
    async def lock_method(self, method_id: uuid.UUID) -> None:
        # Also serializes first creation, when no row exists yet.
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"knowlab-method:{method_id}"},
        )

    async def method_owner(self, method_id: uuid.UUID) -> str | None:
        return (
            await self.session.execute(
                select(KnowbotMethod.owner_subject).where(KnowbotMethod.id == method_id)
            )
        ).scalar_one_or_none()

    async def create_method(self, method_id: uuid.UUID, owner_subject: str) -> None:
        self.session.add(KnowbotMethod(id=method_id, owner_subject=owner_subject))
        await self.session.flush()

    async def method_head(self, method_id: uuid.UUID) -> int:
        return int(
            (
                await self.session.execute(
                    select(func.max(KnowbotMethodVersion.version)).where(
                        KnowbotMethodVersion.method_id == method_id
                    )
                )
            ).scalar_one_or_none()
            or 0
        )

    async def method_version(
        self, method_id: uuid.UUID, version: int
    ) -> MethodVersionRecord | None:
        result = (
            await self.session.execute(
                select(KnowbotMethodVersion, KnowbotMethod.owner_subject)
                .join(KnowbotMethod)
                .where(
                    KnowbotMethodVersion.method_id == method_id,
                    KnowbotMethodVersion.version == version,
                )
            )
        ).first()
        if result is None:
            return None
        row, owner = result
        return MethodVersionRecord(
            method_id=row.method_id,
            version=row.version,
            owner_subject=owner,
            digest=row.digest,
            command_digest=row.command_digest,
            definition=row.definition,
            provenance=row.provenance,
            source_workdraft_id=row.source_workdraft_id,
            created_at=row.created_at,
        )

    async def insert_method_version(
        self,
        *,
        method_id: uuid.UUID,
        version: int,
        digest: str,
        command_digest: str,
        definition: dict[str, Any],
        provenance: dict[str, Any],
        source_workdraft_id: uuid.UUID,
    ) -> None:
        self.session.add(
            KnowbotMethodVersion(
                method_id=method_id,
                version=version,
                digest=digest,
                command_digest=command_digest,
                definition=definition,
                provenance=provenance,
                source_workdraft_id=source_workdraft_id,
            )
        )
        await self.session.flush()

    async def list_methods(
        self, owner_subject: str, *, limit: int, offset: int
    ) -> Sequence[dict[str, Any]]:
        latest = (
            select(
                KnowbotMethodVersion.method_id,
                func.max(KnowbotMethodVersion.version).label("version"),
            )
            .group_by(KnowbotMethodVersion.method_id)
            .subquery()
        )
        rows = (
            await self.session.execute(
                select(KnowbotMethod.id, KnowbotMethod.created_at, latest.c.version)
                .join(latest, latest.c.method_id == KnowbotMethod.id)
                .where(KnowbotMethod.owner_subject == owner_subject)
                .order_by(KnowbotMethod.created_at.desc(), KnowbotMethod.id)
                .limit(limit)
                .offset(offset)
            )
        ).mappings()
        return [dict(row) for row in rows]
