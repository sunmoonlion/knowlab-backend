"""分身提交与草稿的 PostgreSQL 实现（SDD 0027）。

一个实例对应一次请求的数据库会话；transaction 可嵌套，最外层提交、出错回滚。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, cast

from sqlalchemy import func, select, text, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports.knowbots import (
    AmendmentRecord,
    DatasetRecord,
    DraftRecord,
    QuestionRecord,
    RequestRecord,
    TestOutputRecord,
)
from app.domain.knowbots.drafts import OUTPUTS_EXPIRED
from app.infrastructure.models.knowbots import (
    KnowbotAmendment,
    KnowbotDataset,
    KnowbotDraft,
    KnowbotQuestion,
    KnowbotRequest,
    KnowbotTestConsent,
    KnowbotTestOutput,
)

DRAFT_FIELDS = frozenset(
    {
        "status",
        "rules_reviewed_by",
        "rules_reviewed_at",
        "run_id",
        "run_attempts",
        "run_started_at",
        "run_timeout_seconds",
        "report",
        "delivery",
        "last_error",
        "run_grant_id",
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
        run_id=row.run_id,
        run_attempts=row.run_attempts,
        run_started_at=row.run_started_at,
        run_timeout_seconds=row.run_timeout_seconds,
        report=row.report,
        delivery=row.delivery,
        last_error=row.last_error,
        created_at=row.created_at,
        run_grant_id=row.run_grant_id,
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
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return _request(row)

    async def get_request(self, request_id: uuid.UUID) -> RequestRecord | None:
        row = await self.session.get(KnowbotRequest, request_id)
        return None if row is None else _request(row)

    async def list_requests_for_owner(
        self, owner_subject: str
    ) -> Sequence[RequestRecord]:
        rows = (
            await self.session.execute(
                select(KnowbotRequest)
                .where(KnowbotRequest.owner_subject == owner_subject)
                .order_by(KnowbotRequest.created_at.desc(), KnowbotRequest.id)
            )
        ).scalars()
        return [_request(r) for r in rows]

    async def list_requests(self) -> Sequence[RequestRecord]:
        rows = (
            await self.session.execute(
                select(KnowbotRequest).order_by(
                    KnowbotRequest.created_at.desc(), KnowbotRequest.id
                )
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
        revision: int,
        version: str,
        manifest: dict[str, Any],
    ) -> DraftRecord:
        row = KnowbotDraft(
            id=uuid.uuid4(),
            request_id=request_id,
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
        self, *, status: str | None = None, request_id: uuid.UUID | None = None
    ) -> Sequence[DraftRecord]:
        query = select(KnowbotDraft).order_by(
            KnowbotDraft.created_at, KnowbotDraft.revision
        )
        if status is not None:
            query = query.where(KnowbotDraft.status == status)
        if request_id is not None:
            query = query.where(KnowbotDraft.request_id == request_id)
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

    async def dataset_in_use(self, owner_subject: str, dataset_id: uuid.UUID) -> bool:
        binding = func.jsonb_build_array(
            func.jsonb_build_object("dataset_id", str(dataset_id))
        )
        found = await self.session.execute(
            select(KnowbotDraft.id)
            .join(KnowbotRequest, KnowbotRequest.id == KnowbotDraft.request_id)
            .where(
                KnowbotRequest.owner_subject == owner_subject,
                KnowbotDraft.status.in_(
                    ("pending_test", "testing", "passed", "delivered")
                ),
                KnowbotDraft.manifest["data_bindings"].op("@>")(binding),
            )
            .limit(1)
        )
        return found.first() is not None

    async def find_delivered_draft(
        self, knowbot_id: str, version: str
    ) -> DraftRecord | None:
        row = (
            await self.session.execute(
                select(KnowbotDraft)
                .where(
                    KnowbotDraft.status == "delivered",
                    KnowbotDraft.delivery["knowbot_id"].astext == knowbot_id,
                    KnowbotDraft.version == version,
                )
                .order_by(KnowbotDraft.created_at)
                .limit(1)
            )
        ).scalar_one_or_none()
        return None if row is None else _draft(row)

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
                 (d.status = 'delivered'
                  and (d.delivery ->> 'delivered_at')::timestamptz < :before)
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

    async def find_testing_draft(self, run_id: str) -> DraftRecord | None:
        row = (
            await self.session.execute(
                select(KnowbotDraft)
                .where(KnowbotDraft.status == "testing", KnowbotDraft.run_id == run_id)
                .limit(1)
            )
        ).scalar_one_or_none()
        return None if row is None else _draft(row)

    async def update_request_cases(
        self, request_id: uuid.UUID, cases: list[dict[str, Any]]
    ) -> None:
        await self.session.execute(
            update(KnowbotRequest)
            .where(KnowbotRequest.id == request_id)
            .values(cases=cases)
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
