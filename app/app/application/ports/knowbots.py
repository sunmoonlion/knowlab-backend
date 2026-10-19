"""分身创建与测试要的外部能力（SDD 0027）。实现在 app/infrastructure/。"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from app.domain.knowbots.data_read import Extracted, Tables
from app.domain.knowbots.test_run import (
    AnswersV1,
    CaseOutputsV1,
    RunRequestV1,
    RunResultV1,
)


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


@dataclass(frozen=True)
class TestOutputRecord:
    """一次测试里一条用例的产出（SDD 0037）：成稿与各步交回物。

    运行方没有时只有 note。
    """

    draft_id: uuid.UUID
    run_id: str
    case_id: str
    outputs: dict[str, Any] | None = field(default=None, repr=False)
    note: str | None = None
    fetched_at: datetime | None = None


@dataclass(frozen=True)
class QuestionRecord:
    """测试中待人决定的一个问题（有人值守）。

    route：None 在运营那里；"user" 已转给用户。
    """

    id: uuid.UUID
    draft_id: uuid.UUID
    run_id: str
    question_id: str
    case_id: str
    kind: str
    text: str = field(repr=False)
    options: list[dict[str, Any]] = field(default_factory=list)
    allow_free_text: bool = True
    asked_at: str = ""
    step_id: str | None = None
    key: str | None = None
    route: str | None = None
    route_reason: str | None = None
    answer: dict[str, Any] | None = None
    answered_by: str | None = None
    answered_at: datetime | None = None
    sent_at: datetime | None = None
    last_error: str | None = None


@dataclass(frozen=True)
class AmendmentRecord:
    """用户给自己的想法写的一条补充方法（SDD 0035）。原话写下后不改、不删。"""

    id: uuid.UUID
    request_id: uuid.UUID
    owner_subject: str
    text: str = field(repr=False)
    created_at: datetime | None = None
    declined_reason: str | None = None
    declined_by: str | None = None
    declined_at: datetime | None = None


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

    async def update_request_cases(
        self, request_id: uuid.UUID, cases: list[dict[str, Any]]
    ) -> None:
        """答过的开步前提问记回用例的预设答案。"""
        ...

    # ---------- 补充方法 ----------
    async def create_amendment(
        self, *, request_id: uuid.UUID, owner_subject: str, text: str
    ) -> AmendmentRecord: ...

    async def list_amendments(self, request_id: uuid.UUID) -> Sequence[AmendmentRecord]:
        """这个想法的补充，先提交的在前。"""
        ...

    async def get_amendment(
        self, amendment_id: uuid.UUID, *, for_update: bool = False
    ) -> AmendmentRecord | None: ...

    async def decline_amendment(
        self, amendment_id: uuid.UUID, *, reason: str, by: str, at: datetime
    ) -> None:
        """只记「不照做」的理由、谁、何时；原话不动。"""
        ...

    # ---------- 测试中的问题 ----------
    async def add_questions(
        self, draft_id: uuid.UUID, run_id: str, questions: list[dict[str, Any]]
    ) -> None:
        """同一次测试的同一个问题只记一次。"""
        ...

    async def get_question(
        self, question: uuid.UUID, *, for_update: bool = False
    ) -> QuestionRecord | None: ...

    async def list_questions(
        self, *, draft_id: uuid.UUID | None = None, open_only: bool = False
    ) -> Sequence[QuestionRecord]:
        """open_only：还没回答的。"""
        ...

    async def update_question(self, question: uuid.UUID, **changes: Any) -> None: ...

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

    async def find_delivered_draft(
        self, knowbot_id: str, version: str
    ) -> DraftRecord | None:
        """交付后 knowmart 给的分身 id 与版本对应的那份草稿。

        运行方读数据时核（0027 §3.5）。
        """
        ...

    async def put_test_output(self, record: TestOutputRecord) -> None:
        """存一条用例的产出；同一次测试同一条用例已有就不动（只取一次）。"""
        ...

    async def list_test_outputs(
        self, draft_id: uuid.UUID, run_id: str
    ) -> Sequence[TestOutputRecord]:
        """这一稿这一次测试取到的产出，按用例编号。"""
        ...

    async def find_testing_draft(self, run_id: str) -> DraftRecord | None:
        """正在测试、run_id 是这一次的草稿。"""
        ...


class RuntimeTestClient(Protocol):
    """调登记的运行方跑测试（契约 knowbot-test-run/v1）。

    不可达抛 ServiceUnavailableError。
    """

    def registered(self) -> frozenset[str]: ...

    async def start(self, runtime: str, request: RunRequestV1) -> RunResultV1: ...

    async def status(self, runtime: str, run_id: str) -> RunResultV1: ...

    async def outputs(
        self, runtime: str, run_id: str, case_id: str
    ) -> CaseOutputsV1 | None:
        """一条用例的产出（SDD 0037）；运行方没有回 None。"""
        ...

    async def answer(
        self, runtime: str, run_id: str, answers: AnswersV1
    ) -> RunResultV1: ...


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

    async def get(
        self, *, object_key: str, version_id: str, sha256: str, max_bytes: int
    ) -> bytes:
        """按版本读回，核 sha256；对不上抛错，不返回内容。"""
        ...


class TextExtractor(Protocol):
    """把用户的数据抽成文字（CSV、文本原样；Excel 每张表转 CSV；PDF 逐页抽字）。"""

    async def extract(
        self, *, data: bytes, content_type: str, sha256: str
    ) -> Extracted: ...


class TableExtractor(Protocol):
    """把 CSV、Excel 解析成带行标签、列标签的格子（0030 §3.1）。

    不是表格抛 TableUnreadable。
    """

    async def tables(
        self, *, data: bytes, content_type: str, sha256: str
    ) -> Tables: ...


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
