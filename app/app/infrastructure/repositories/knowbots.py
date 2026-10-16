"""分身提交与草稿的 PostgreSQL 实现（SDD 0027）。

一个实例对应一次请求的数据库会话；transaction 可嵌套，最外层提交、出错回滚。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports.knowbots import DatasetRecord, DraftRecord, RequestRecord
from app.infrastructure.models.knowbots import (
    KnowbotDataset,
    KnowbotDraft,
    KnowbotRequest,
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
    ) -> RequestRecord:
        row = KnowbotRequest(
            id=uuid.uuid4(),
            owner_subject=owner_subject,
            title=title,
            runtime=runtime,
            idea=idea,
            cases=cases,
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
