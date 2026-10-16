"""分身提交与草稿的 PostgreSQL 实现（SDD 0027）。

一个实例对应一次请求的数据库会话；transaction 可嵌套，最外层提交、出错回滚。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports.knowbots import DraftRecord, RequestRecord
from app.infrastructure.models.knowbots import KnowbotDraft, KnowbotRequest

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
