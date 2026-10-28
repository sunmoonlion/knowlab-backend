"""创意到专家要的外部能力（SDD 0027、0037）。实现在 app/infrastructure/。"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

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
    # 平台孵化（运营建的）还是自助（作者从工作稿出的一版，专家孵化方案第十一节）
    mode: str = "platform"


@dataclass(frozen=True)
class WorkdraftRecord:
    """工作稿（专家孵化方案第五节）：一个名称一份。body 是方法层的格子，sources 是每格
    的出处（格子路径 → {kind, ref}）；revision 每存一次加 1。"""

    id: uuid.UUID
    request_id: uuid.UUID
    owner_subject: str
    body: dict[str, Any] = field(repr=False)
    sources: dict[str, Any] = field(repr=False)
    revision: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class WorkdraftEntryRecord:
    """工作稿的一条记录（表 knowbot_workdraft_record，只增不改）：谁、以什么身份、改了
    哪一格、存的原文。kind：edit 编辑、accept 接受建议、import 导入、copy 复制。"""

    id: uuid.UUID
    workdraft_id: uuid.UUID
    kind: str
    actor_subject: str
    actor_role: str
    text: str = field(repr=False)
    path: str | None = None
    suggestion_ref: str | None = None
    created_at: datetime | None = None


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
    started_by: str | None = None
    finished_at: datetime | None = None
    report: dict[str, Any] | None = None
    error: str | None = None
    test_set_version: int | None = None
    case_grants: list[str] = field(default_factory=list)
    # 自助（self）还是平台（platform）；自助的那台电脑与根目录（SDD 0046 第三节）
    mode: str = "platform"
    environment_id: str | None = None
    workspace_root: str | None = None


@dataclass(frozen=True)
class TestSetRecord:
    """测试集的一版（SDD 0046 第三节）：用例编号 → 钉住的几份。"""

    __test__ = False  # 不是 pytest 的测试类

    id: uuid.UUID
    request_id: uuid.UUID
    version: int
    cases: dict[str, Any]
    created_at: datetime
    from_test_id: uuid.UUID | None = None


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
    unlisted_sent_at: datetime | None = None  # 没上架的结局送到 knowmart 的时刻


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
    # data 问题（SDD 0046 第三节）：专家提出的几样；在 knowbase 解析、确认、补采到哪
    requests: list[dict[str, Any]] = field(default_factory=list)
    data_state: dict[str, Any] | None = None
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
        mode: str = "platform",
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

    async def experts_unlisted_unsent(self, limit: int = 100) -> Sequence[ExpertRecord]:
        """没上架（用户不要 / 审核没过）、结局还没送到 knowmart 的专家。"""
        ...

    async def update_expert(self, expert_id: uuid.UUID, **changes: Any) -> None: ...

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

    async def latest_test_set(self, request_id: uuid.UUID) -> TestSetRecord | None: ...

    # ---------- 工作稿（专家孵化方案） ----------
    async def get_workdraft(
        self, request_id: uuid.UUID, *, for_update: bool = False
    ) -> WorkdraftRecord | None: ...

    async def create_workdraft(
        self, *, request_id: uuid.UUID, owner_subject: str, body: dict[str, Any]
    ) -> WorkdraftRecord:
        """建一份空的（revision 0）；这个名称已有就抛 ConcurrencyConflictError。"""
        ...

    async def update_workdraft(
        self,
        workdraft_id: uuid.UUID,
        *,
        body: dict[str, Any],
        sources: dict[str, Any],
        revision: int,
    ) -> None: ...

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
        """记一条（只增不改）。"""
        ...

    async def list_workdraft_entries(
        self, entry_ids: Collection[uuid.UUID]
    ) -> Sequence[WorkdraftEntryRecord]:
        """按编号取记录，不论属于哪份工作稿（核出处时好认出引错了的）。"""
        ...

    async def add_test_set(self, record: TestSetRecord) -> None: ...

    async def update_question(self, question: uuid.UUID, **changes: Any) -> None: ...

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

    async def computers(self, runtime: str, subject: str) -> list[dict[str, Any]]:
        """用户接入过的电脑（SDD 0046 第三节）：名字、系统、在线与否、白名单根目录。"""
        ...


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
        self, *, owner_subject: str, knowbot_ref: str, version: str, outcome: str
    ) -> None:
        """这个创意没上架的那一版的结局（declined | rejected），按版本逐条写、重发无害
        （SDD 0037 第三节：knowmart 详情页写明空号那一版）。"""
        ...
