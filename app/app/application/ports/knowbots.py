"""创意到专家要的外部能力（SDD 0027、0037）。实现在 app/infrastructure/。"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Sequence
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
    consent_version: int = 1  # 同意的是哪一版测试数据说明
    # 最新的创意版本；idea、cases 是它的内容（SDD 0037）
    latest_version: int = 1
    # 测试中用户答过的开步前提问：{用例: {键: 答案}}，开跑时并进用例
    case_answers: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class RequestVersionRecord:
    """创意的一个版本（SDD 0037）：只插不改。"""

    request_id: uuid.UUID
    version: int
    idea: dict[str, Any] = field(repr=False)
    cases: list[dict[str, Any]] = field(repr=False)
    consent_version: int
    created_by: str
    created_at: datetime | None = None


@dataclass(frozen=True)
class AcceptanceRecord:
    """受理单：一个创意版本一张。checks：{C-L5|C-L2|C-L1: {ok, note}}。"""

    request_id: uuid.UUID
    version: int
    accepted: bool
    checks: dict[str, Any]
    reason: str | None
    decided_by: str
    decided_at: datetime
    trial: bool = False


@dataclass(frozen=True)
class DraftRecord:
    """准专家（一稿）。测试的经过在 TestRecord，专家在 ExpertRecord。"""

    id: uuid.UUID
    request_id: uuid.UUID
    revision: int
    version: str
    status: str
    manifest: dict[str, Any] = field(repr=False)
    rules_reviewed_by: str | None = None
    rules_reviewed_at: datetime | None = None
    created_at: datetime | None = None
    request_version: int = 1  # 出自创意第几版
    # 请用户审核：{requested_by, requested_at, ok, note, answered_at}
    user_review: dict[str, Any] | None = None


@dataclass(frozen=True)
class TestRecord:
    """一次测试（SDD 0037）：编号在同一个名称里只增。"""

    __test__ = False  # 不是 pytest 的测试类

    id: uuid.UUID
    request_id: uuid.UUID
    number: int
    draft_id: uuid.UUID
    status: str
    run_id: str
    started_at: datetime
    budget_limit: str | None = None
    timeout_seconds: int | None = None
    grant_id: str | None = None
    started_by: str | None = None
    finished_at: datetime | None = None
    report: dict[str, Any] | None = None
    error: str | None = None


@dataclass(frozen=True)
class ExpertRecord:
    """一版专家：测试通过即是；两道门、上架、停用都记在这里。"""

    id: uuid.UUID
    request_id: uuid.UUID
    version: int
    draft_id: uuid.UUID
    created_at: datetime
    test_id: uuid.UUID | None = None
    returned_by: str | None = None
    returned_at: datetime | None = None
    user_accepted: bool | None = None
    user_decided_by: str | None = None
    user_decided_at: datetime | None = None
    review_approved: bool | None = None
    review_listing: str | None = None
    review_reason: str | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    listed_at: datetime | None = None
    knowmart_knowbot_id: str | None = None
    digest: str | None = None
    delivery: dict[str, Any] | None = None
    delivery_error: str | None = None
    stopped_at: datetime | None = None
    stopped_by: str | None = None
    stop_reason: str | None = None
    trial: bool = False


@dataclass(frozen=True)
class DatasetRecord:
    id: uuid.UUID
    owner_subject: str
    name: str
    version: str
    content_type: str
    size_bytes: int
    sha256: str
    # knowlab 自己的旧对象（10-23 前传的）；新传的只在 knowbase，这两栏为空
    object_key: str | None
    version_id: str | None
    created_at: datetime | None = None
    deleted_at: datetime | None = None
    # knowbase 里 knowlab 分区的那份（SDD 0042 第七节）
    item_id: str | None = None


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
        consent_version: int = 1,
    ) -> RequestRecord:
        """建创意的名称与它的 v1（同一个事务）。"""
        ...

    async def get_request(self, request_id: uuid.UUID) -> RequestRecord | None: ...

    # ---------- 创意版本与受理单（SDD 0037） ----------
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
        """加一个版本，名称指向它（idea、cases 镜像跟着换）。"""
        ...

    async def get_version(
        self, request_id: uuid.UUID, version: int
    ) -> RequestVersionRecord | None: ...

    async def list_versions(
        self, request_ids: Collection[uuid.UUID] | None = None
    ) -> Sequence[RequestVersionRecord]:
        """按创意、版本号排。"""
        ...

    async def add_acceptance(self, record: AcceptanceRecord) -> None:
        """写受理单；这个版本已有就抛 ConcurrencyConflictError。"""
        ...

    async def get_acceptance(
        self, request_id: uuid.UUID, version: int
    ) -> AcceptanceRecord | None: ...

    async def list_acceptances(
        self, request_ids: Collection[uuid.UUID] | None = None
    ) -> Sequence[AcceptanceRecord]: ...

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
        request_version: int,
        revision: int,
        version: str,
        manifest: dict[str, Any],
    ) -> DraftRecord: ...

    async def get_draft(
        self, draft_id: uuid.UUID, *, for_update: bool = False
    ) -> DraftRecord | None: ...

    async def list_drafts(
        self,
        *,
        status: str | None = None,
        request_id: uuid.UUID | None = None,
        request_ids: Collection[uuid.UUID] | None = None,
    ) -> Sequence[DraftRecord]: ...

    async def update_draft(self, draft_id: uuid.UUID, **changes: Any) -> None: ...

    async def lock_request(self, request_id: uuid.UUID) -> None:
        """串行化同一个名称的改版、建稿、发起测试、出专家（版本号、编号不重）。"""
        ...

    async def set_case_answers(
        self, request_id: uuid.UUID, answers: dict[str, Any]
    ) -> None:
        """答过的开步前提问：{用例: {键: 答案}}，开跑时并进用例，重测不再问。"""
        ...

    # ---------- 测试（SDD 0037） ----------
    async def create_test(self, record: TestRecord) -> TestRecord: ...

    async def get_test(
        self, test_id: uuid.UUID, *, for_update: bool = False
    ) -> TestRecord | None: ...

    async def list_tests(
        self,
        *,
        request_ids: Collection[uuid.UUID] | None = None,
        draft_id: uuid.UUID | None = None,
    ) -> Sequence[TestRecord]:
        """按创意、编号排。"""
        ...

    async def update_test(self, test_id: uuid.UUID, **changes: Any) -> None: ...

    async def find_test_by_run(self, run_id: str) -> TestRecord | None: ...

    # ---------- 专家（SDD 0037） ----------
    async def create_expert(self, record: ExpertRecord) -> ExpertRecord: ...

    async def get_expert(
        self, expert_id: uuid.UUID, *, for_update: bool = False
    ) -> ExpertRecord | None: ...

    async def list_experts(
        self, request_ids: Collection[uuid.UUID] | None = None
    ) -> Sequence[ExpertRecord]:
        """按创意、版本号排。"""
        ...

    async def update_expert(self, expert_id: uuid.UUID, **changes: Any) -> None: ...

    async def find_listed_expert(
        self, knowbot_id: str, version: str
    ) -> ExpertRecord | None:
        """knowmart 的专家 id 与版本对应的、上架了没停用的那版（运行方读数据时核）。"""
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
        """这个人在待测、测试中的准专家，或还没走完、没停用的专家，有没有绑着这份数据。"""
        ...

    async def set_dataset_item(self, dataset_id: uuid.UUID, item_id: str) -> None:
        """记下搬进 knowbase 后的资料编号。"""
        ...

    async def datasets_to_import(self, limit: int = 50) -> Sequence[DatasetRecord]:
        """还没搬进 knowbase 的旧数据（有旧对象、没删、没有资料编号）。"""
        ...

    async def get_test_consent(self, owner_subject: str) -> dict[str, str] | None:
        """这个人在 knowbase 的测试同意：{consent_id, items_digest}；没有就是 None。"""
        ...

    async def put_test_consent(
        self, owner_subject: str, consent_id: str, items_digest: str
    ) -> None: ...

    async def delete_test_consent(self, owner_subject: str) -> None: ...

    async def set_consent_version(self, request_id: uuid.UUID, version: int) -> None:
        """用户同意了新一版测试数据说明。"""
        ...

    async def test_output_cases(self, draft_id: uuid.UUID, run_id: str) -> set[str]:
        """这一稿这一次测试已经存了产出（或原因）的用例编号；不读正文。"""
        ...

    async def expire_test_outputs(self, before: datetime) -> int:
        """上架或被下一个准专家取代早于 before 的准专家：清掉产出正文、留一句原因。

        回清了几条。
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


class KnowledgeBase(Protocol):
    """knowbase 里 knowlab 的知识库分区（契约 user-data/v1，SDD 0042 第七节）。

    本人的操作凭换来的用户令牌；签测试授权、撤、搬旧数据凭 knowlab 的服务身份。
    knowbase 拒了按情形转成本应用的错（资料在用 409 dataset_in_use 等）；不可达、
    没配好是 503。
    """

    async def upload(self, user_token: str, name: str, data: bytes) -> dict[str, Any]:
        """传一份，回 knowbase 的资料（item_id、sha256……）。"""
        ...

    async def delete(self, user_token: str, item_id: str) -> None:
        """删一份；已经没有了当删过了。"""
        ...

    async def consent(
        self, user_token: str, ref: str, items: list[dict[str, str]]
    ) -> str:
        """立一条测试同意（范围是这几份），回同意编号。"""
        ...

    async def withdraw(self, user_token: str, consent_id: str) -> None: ...

    async def test_grant(
        self,
        *,
        owner: str,
        consent_id: str,
        ref: str,
        items: list[dict[str, str]],
        ttl_seconds: int,
    ) -> str:
        """凭同意签测试运行授权（读的一方是 knowbench），回授权编号。"""
        ...

    async def revoke_test_grant(self, grant_id: str) -> None: ...

    async def import_item(
        self, *, owner: str, name: str, data: bytes, ref: str
    ) -> dict[str, Any]:
        """把一份旧数据搬进这个用户的 knowlab 分区（按 ref 幂等），回资料。"""
        ...


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
    """交付过了两道门的专家到 knowmart、停用某一版（服务身份 knowmart:deliver）。"""

    async def deliver(
        self,
        *,
        delivery_id: str,
        owner_subject: str,
        knowbot_ref: str,
        manifest: dict[str, Any],
        request_public: bool = False,
    ) -> dict[str, Any]:
        """回 {knowbot_id, version, digest, listing_status?}。"""
        ...

    async def stop(self, *, owner_subject: str, knowbot_id: str, version: str) -> None:
        """停用 knowmart 里的这一版；停过的再停也成功。"""
        ...

    async def set_unlisted(
        self, *, owner_subject: str, knowbot_ref: str, versions: list[dict[str, str]]
    ) -> None:
        """这个创意没上架那几版的结局（[{version, outcome: declined | rejected}]），
        整份替换（SDD 0037 第三节：knowmart 详情页写明空号那一版）。"""
        ...
