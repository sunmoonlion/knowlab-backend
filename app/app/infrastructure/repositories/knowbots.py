"""创意到专家的 PostgreSQL 实现（SDD 0027、0037）。

一个实例对应一次请求的数据库会话；transaction 可嵌套，最外层提交、出错回滚。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Collection, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, cast

from sqlalchemy import exists, func, or_, select, text, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.errors.exceptions import ConcurrencyConflictError
from app.application.ports.knowbots import (
    AcceptanceRecord,
    AmendmentRecord,
    DatasetRecord,
    DraftRecord,
    ExpertRecord,
    QuestionRecord,
    RequestRecord,
    RequestVersionRecord,
    TestOutputRecord,
    TestRecord,
)
from app.domain.knowbots.drafts import OUTPUTS_EXPIRED
from app.infrastructure.models.knowbots import (
    KnowbotAcceptance,
    KnowbotAmendment,
    KnowbotConsentWithdrawal,
    KnowbotDataset,
    KnowbotDraft,
    KnowbotExpert,
    KnowbotQuestion,
    KnowbotRequest,
    KnowbotRequestVersion,
    KnowbotTest,
    KnowbotTestConsent,
    KnowbotTestOutput,
)

# 0012 起准专家只改这几栏；run、报告、交付几栏不再写（内容在测试、专家表）
DRAFT_FIELDS = frozenset(
    {"status", "rules_reviewed_by", "rules_reviewed_at", "user_review"}
)
TEST_FIELDS = frozenset({"status", "grant_id", "finished_at", "report", "error"})
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
        grant_id=row.grant_id,
        started_by=row.started_by,
        finished_at=row.finished_at,
        report=row.report,
        error=row.error,
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
    )


def _dataset(row: KnowbotDataset) -> DatasetRecord:
    return DatasetRecord(
        id=row.id,
        owner_subject=row.owner_subject,
        name=row.name,
        version=row.version,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        object_key=row.object_key,
        version_id=row.version_id,
        created_at=row.created_at,
        deleted_at=row.deleted_at,
        item_id=row.knowbase_item_id,
        no_resale=row.no_resale,
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
    }
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
            grant_id=record.grant_id,
            started_by=record.started_by,
            started_at=record.started_at,
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
    ) -> Sequence[TestRecord]:
        query = select(KnowbotTest).order_by(KnowbotTest.request_id, KnowbotTest.number)
        if request_ids is not None:
            query = query.where(KnowbotTest.request_id.in_(request_ids))
        if draft_id is not None:
            query = query.where(KnowbotTest.draft_id == draft_id)
        rows = (
            await self.session.execute(query.execution_options(populate_existing=True))
        ).scalars()
        return [_test(r) for r in rows]

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

    async def find_listed_expert(
        self, knowbot_id: str, version: str
    ) -> ExpertRecord | None:
        if not version.isdigit() or len(version) > 9:
            return None
        row = (
            await self.session.execute(
                select(KnowbotExpert)
                .where(
                    KnowbotExpert.knowmart_knowbot_id == knowbot_id,
                    KnowbotExpert.version == int(version),
                    KnowbotExpert.listed_at.is_not(None),
                    KnowbotExpert.stopped_at.is_(None),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        return None if row is None else _expert(row)

    # ---------- 用户的数据 ----------
    async def lock_owner_data(self, owner_subject: str) -> None:
        await self.session.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:k, 0))"),
            {"k": "knowbot_dataset:" + owner_subject},
        )

    async def create_dataset(self, record: DatasetRecord) -> DatasetRecord:
        row = KnowbotDataset(
            id=record.id,
            owner_subject=record.owner_subject,
            name=record.name,
            version=record.version,
            content_type=record.content_type,
            size_bytes=record.size_bytes,
            sha256=record.sha256,
            object_key=record.object_key,
            version_id=record.version_id,
            knowbase_item_id=record.item_id,
            no_resale=record.no_resale,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return _dataset(row)

    async def list_datasets(self, owner_subject: str) -> Sequence[DatasetRecord]:
        rows = (
            await self.session.execute(
                select(KnowbotDataset)
                .where(
                    KnowbotDataset.owner_subject == owner_subject,
                    KnowbotDataset.deleted_at.is_(None),
                )
                .order_by(KnowbotDataset.created_at.desc(), KnowbotDataset.id)
            )
        ).scalars()
        return [_dataset(r) for r in rows]

    async def get_dataset_by_item(self, item_id: str) -> DatasetRecord | None:
        row = (
            await self.session.execute(
                select(KnowbotDataset).where(KnowbotDataset.knowbase_item_id == item_id)
            )
        ).scalar_one_or_none()
        return _dataset(row) if row else None

    async def get_dataset(self, dataset_id: uuid.UUID) -> DatasetRecord | None:
        row = await self.session.get(KnowbotDataset, dataset_id)
        return None if row is None else _dataset(row)

    async def mark_dataset_deleted(self, dataset_id: uuid.UUID, at: Any) -> None:
        await self.session.execute(
            update(KnowbotDataset)
            .where(KnowbotDataset.id == dataset_id)
            .values(deleted_at=at)
        )

    async def set_dataset_item(self, dataset_id: uuid.UUID, item_id: str) -> None:
        await self.session.execute(
            update(KnowbotDataset)
            .where(KnowbotDataset.id == dataset_id)
            .values(knowbase_item_id=item_id)
        )

    async def datasets_to_import(self, limit: int = 50) -> Sequence[DatasetRecord]:
        rows = (
            await self.session.execute(
                select(KnowbotDataset)
                .where(
                    KnowbotDataset.knowbase_item_id.is_(None),
                    KnowbotDataset.object_key.is_not(None),
                    KnowbotDataset.deleted_at.is_(None),
                )
                .order_by(KnowbotDataset.created_at, KnowbotDataset.id)
                .limit(limit)
            )
        ).scalars()
        return [_dataset(r) for r in rows]

    async def get_test_consent(self, owner_subject: str) -> dict[str, str] | None:
        row = await self.session.get(KnowbotTestConsent, owner_subject)
        if row is None:
            return None
        return {"consent_id": row.consent_id, "items_digest": row.items_digest}

    async def put_test_consent(
        self, owner_subject: str, consent_id: str, items_digest: str
    ) -> None:
        row = await self.session.get(KnowbotTestConsent, owner_subject)
        if row is None:
            self.session.add(
                KnowbotTestConsent(
                    owner_subject=owner_subject,
                    consent_id=consent_id,
                    items_digest=items_digest,
                )
            )
        else:
            row.consent_id = consent_id
            row.items_digest = items_digest
        await self.session.flush()

    async def delete_test_consent(self, owner_subject: str) -> None:
        row = await self.session.get(KnowbotTestConsent, owner_subject)
        if row is not None:
            await self.session.delete(row)
            await self.session.flush()

    async def add_consent_withdrawal(self, owner_subject: str, consent_id: str) -> None:
        if await self.session.get(KnowbotConsentWithdrawal, consent_id) is None:
            self.session.add(
                KnowbotConsentWithdrawal(
                    consent_id=consent_id, owner_subject=owner_subject
                )
            )
            await self.session.flush()

    async def consent_withdrawals(self, owner_subject: str) -> list[str]:
        rows = await self.session.execute(
            select(KnowbotConsentWithdrawal.consent_id)
            .where(KnowbotConsentWithdrawal.owner_subject == owner_subject)
            .order_by(KnowbotConsentWithdrawal.created_at)
        )
        return list(rows.scalars())

    async def drop_consent_withdrawal(self, consent_id: str) -> None:
        row = await self.session.get(KnowbotConsentWithdrawal, consent_id)
        if row is not None:
            await self.session.delete(row)
            await self.session.flush()

    async def dataset_in_use(self, owner_subject: str, dataset_id: uuid.UUID) -> bool:
        binding = func.jsonb_build_array(
            func.jsonb_build_object("dataset_id", str(dataset_id))
        )
        # 还没走完、没停用的专家：用户没说不要、审核没驳回、没停用
        live_expert = exists().where(
            KnowbotExpert.draft_id == KnowbotDraft.id,
            KnowbotExpert.stopped_at.is_(None),
            KnowbotExpert.user_accepted.is_not(False),
            KnowbotExpert.review_approved.is_not(False),
        )
        found = await self.session.execute(
            select(KnowbotDraft.id)
            .join(KnowbotRequest, KnowbotRequest.id == KnowbotDraft.request_id)
            .where(
                KnowbotRequest.owner_subject == owner_subject,
                or_(
                    KnowbotDraft.status.in_(("pending_test", "testing")),
                    live_expert,
                ),
                KnowbotDraft.manifest["data_bindings"].op("@>")(binding),
            )
            .limit(1)
        )
        return found.first() is not None

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
        query = select(KnowbotQuestion).order_by(
            KnowbotQuestion.created_at, KnowbotQuestion.question_id
        )
        if draft_id is not None:
            query = query.where(KnowbotQuestion.draft_id == draft_id)
        if open_only:
            query = query.where(KnowbotQuestion.answer.is_(None))
        rows = (
            await self.session.execute(query.execution_options(populate_existing=True))
        ).scalars()
        return [_question(r) for r in rows]

    async def update_question(self, question: uuid.UUID, **changes: Any) -> None:
        unknown = set(changes) - QUESTION_FIELDS
        if unknown:
            raise ValueError(f"unknown question fields: {sorted(unknown)}")
        await self.session.execute(
            update(KnowbotQuestion)
            .where(KnowbotQuestion.id == question)
            .values(**changes)
        )
