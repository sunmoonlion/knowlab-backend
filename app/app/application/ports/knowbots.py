"""分身创建与测试要的外部能力（SDD 0027）。实现在 app/infrastructure/。"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from app.domain.knowbots.test_run import RunRequestV1, RunResultV1


@dataclass(frozen=True)
class RequestRecord:
    id: uuid.UUID
    owner_subject: str
    title: str
    runtime: str
    idea: dict[str, Any] = field(repr=False)
    cases: list[dict[str, Any]] = field(repr=False)
    created_at: datetime | None = None


@dataclass(frozen=True)
class DraftRecord:
    id: uuid.UUID
    request_id: uuid.UUID
    revision: int
    version: str
    status: str
    manifest: dict[str, Any] = field(repr=False)
    rules_reviewed_by: str | None = None
    rules_reviewed_at: datetime | None = None
    run_id: str | None = None
    run_attempts: int = 0
    run_started_at: datetime | None = None
    run_timeout_seconds: int | None = None
    report: dict[str, Any] | None = None
    delivery: dict[str, Any] | None = None
    last_error: str | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class DatasetRecord:
    id: uuid.UUID
    owner_subject: str
    name: str
    version: str
    content_type: str
    size_bytes: int
    sha256: str
    object_key: str
    version_id: str
    created_at: datetime | None = None
    deleted_at: datetime | None = None


class KnowlabRepository(Protocol):
    def transaction(self) -> AbstractAsyncContextManager[None]: ...

    async def create_request(
        self,
        *,
        owner_subject: str,
        title: str,
        runtime: str,
        idea: dict[str, Any],
        cases: list[dict[str, Any]],
    ) -> RequestRecord: ...

    async def get_request(self, request_id: uuid.UUID) -> RequestRecord | None: ...

    async def list_requests_for_owner(
        self, owner_subject: str
    ) -> Sequence[RequestRecord]: ...

    async def list_requests(self) -> Sequence[RequestRecord]:
        """管理端用：全部提交，新的在前。"""
        ...

    async def create_draft(
        self,
        *,
        request_id: uuid.UUID,
        revision: int,
        version: str,
        manifest: dict[str, Any],
    ) -> DraftRecord: ...

    async def get_draft(
        self, draft_id: uuid.UUID, *, for_update: bool = False
    ) -> DraftRecord | None: ...

    async def list_drafts(
        self, *, status: str | None = None, request_id: uuid.UUID | None = None
    ) -> Sequence[DraftRecord]: ...

    async def update_draft(self, draft_id: uuid.UUID, **changes: Any) -> None: ...

    async def lock_request(self, request_id: uuid.UUID) -> None:
        """串行化同一个分身的建草稿（版本号、修订号不重）。"""
        ...

    # ---------- 用户的数据 ----------
    async def lock_owner_data(self, owner_subject: str) -> None:
        """串行化同一个人的上传与删除（配额不超）。"""
        ...

    async def create_dataset(self, record: DatasetRecord) -> DatasetRecord: ...

    async def list_datasets(self, owner_subject: str) -> Sequence[DatasetRecord]:
        """这个人没删的数据，新的在前。"""
        ...

    async def get_dataset(self, dataset_id: uuid.UUID) -> DatasetRecord | None: ...

    async def mark_dataset_deleted(
        self, dataset_id: uuid.UUID, at: datetime
    ) -> None: ...

    async def dataset_in_use(self, owner_subject: str, dataset_id: uuid.UUID) -> bool:
        """这个人的草稿里，在做的或已交付的有没有绑着这份数据。"""
        ...


class RuntimeTestClient(Protocol):
    """调登记的运行方跑测试（契约 knowbot-test-run/v1）。

    不可达抛 ServiceUnavailableError。
    """

    def registered(self) -> frozenset[str]: ...

    async def start(self, runtime: str, request: RunRequestV1) -> RunResultV1: ...

    async def status(self, runtime: str, run_id: str) -> RunResultV1: ...


class DataStore(Protocol):
    """用户数据的对象存储（本应用自己的桶）。没接时 enabled 为 False。"""

    @property
    def enabled(self) -> bool: ...

    async def put(
        self, *, object_key: str, data: bytes, content_type: str
    ) -> tuple[str, str]:
        """存进去，回（版本号，sha256）。"""
        ...

    async def delete(self, *, object_key: str) -> None: ...


class KnowmartClient(Protocol):
    """交付测试通过的版本到 knowmart（服务身份 knowmart:deliver）。"""

    async def deliver(
        self,
        *,
        delivery_id: str,
        owner_subject: str,
        knowbot_ref: str,
        manifest: dict[str, Any],
    ) -> dict[str, Any]: ...
