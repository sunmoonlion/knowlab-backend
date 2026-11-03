"""创意到专家要的外部能力（SDD 0027、0037）。实现在 app/infrastructure/。"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from app.domain.knowbots.incubation import IncubationRequestV1, IncubationResultV1
from app.domain.knowbots.test_run import (
    AnswersV1,
    AnyRunRequest,
    CaseOutputsV1,
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
    # 现在的孵化是自助还是平台（SDD 0046 第四节）
    incubation_mode: str = "self"
    # 从 knowbench 转来的：来源（app、项目、委托、幂等钥匙）
    # 与用户的模型直接答过的回答（只算
    # 提议，请专家方案设计第 4.9 节第 6 条）
    origin: dict[str, Any] | None = field(default=None, repr=False)
    reference_answer: str | None = field(default=None, repr=False)
    # 推给 knowbase 名录的最近一份 {name, latest_version}（0044 第三节）
    directory_sent: dict[str, Any] | None = field(default=None, repr=False)


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
    # formal：运营照 0037 写的正式受理；self：选自助时自动记的「自助（不受理）」
    kind: str = "formal"


@dataclass(frozen=True)
class GrantRecord:
    """作者给平台的一张授权（domain/knowbots/platform_grants.py）。"""

    id: uuid.UUID
    request_id: uuid.UUID
    kind: str
    status: str
    budget_limit: Decimal
    granted_by: str
    granted_at: datetime
    draft_id: uuid.UUID | None = None
    expires_at: datetime | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    reason: str | None = None
    ended_by: str | None = None
    ended_at: datetime | None = None


@dataclass(frozen=True)
class ProposalRecord:
    """运营对工作稿一格的提议（形状同 AI 的建议），作者接受了才进稿。"""

    id: uuid.UUID
    request_id: uuid.UUID
    path: str
    op: str
    value: Any
    reason: str
    proposed_by: str
    proposed_at: datetime
    state: str = "pending"
    decided_at: datetime | None = None
    records: list[str] = field(default_factory=list)


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
class MethodVersionRecord:
    method_id: uuid.UUID
    version: int
    owner_subject: str
    digest: str
    command_digest: str
    definition: dict[str, Any] = field(repr=False)
    provenance: dict[str, Any] = field(repr=False)
    source_workdraft_id: uuid.UUID
    created_at: datetime


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
    # 挑的数据的格式（资料编号 → 契约 DataFormat 的形状）：作者挑的时候凭本人令牌向
    # knowbase 取下记着，平台人员发起的那一轮没有作者令牌也照样交得出去（第 8.2 节）
    data_formats: dict[str, Any] = field(default_factory=dict, repr=False)


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
class IncubationRecord:
    """AI 孵化的一轮（表 knowbot_incubation，专家孵化方案第 8.2 节）：作者点「AI 起草」
    或「让 AI 改…」一次是一轮，这个名称里第 round 轮。交出去的种子（创意第几版、拼成的
    原文）、答过的缺口、工作稿的 revision 都记着，取回后照它们核出处；handback 是运行方
    交回的四样，review 是 knowlab 核过的每条建议（verified、kinds、problems），
    decisions 是作者对每条建议的处理（{建议: {state, at, records}}，没有的是还没
    处理）。"""

    id: uuid.UUID
    request_id: uuid.UUID
    workdraft_id: uuid.UUID
    owner_subject: str
    round: int
    action: str
    status: str
    run_id: str
    budget_limit: str
    timeout_seconds: int
    environment_id: str
    workspace_root: str
    request_version: int
    seed: dict[str, Any] = field(repr=False)
    workdraft_revision: int = 0
    answer_ids: list[str] = field(default_factory=list)
    mode: str = "self"
    instruction: str | None = None
    step_id: str | None = None
    spent: str | None = None
    task_id: str | None = None
    waiting_for: str | None = None
    error: str | None = None
    handback: dict[str, Any] | None = field(default=None, repr=False)
    review: dict[str, Any] | None = None
    decisions: dict[str, Any] = field(default_factory=dict)
    started_at: datetime | None = None
    finished_at: datetime | None = None
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
    # 自助（self）、平台（platform），或公开交付时平台发起的重跑（rerun）；跑在哪台电脑
    # 与根目录（SDD 0046 第三、五节）
    mode: str = "platform"
    environment_id: str | None = None
    workspace_root: str | None = None
    # 测试证据写进作者产出区的那一份 {material_id, version_no}（0043 第 4.2 节）
    evidence: dict[str, Any] | None = None


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
    # 平台孵化、平台测的才要平台先交回；其余作者「提交交付」即过同意门（0046 第五节）
    needs_return: bool = False
    manual_release: dict[str, Any] | None = None


@dataclass(frozen=True)
class ManualReleaseRecord:
    id: uuid.UUID
    request_id: uuid.UUID
    draft_id: uuid.UUID
    owner_subject: str
    manifest_sha256: str
    baseline_test_id: uuid.UUID
    reason: str
    evidence: dict[str, Any]
    requested_at: datetime
    status: str = "pending"
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_reason: str | None = None
    expert_id: uuid.UUID | None = None


@dataclass(frozen=True)
class DeliveryRecord:
    """一次交付过程（SDD 0046 第五节）：作者提交交付时建。

    bindings：冻结下来的绑定（清单 v2 `bindings` 的形状，外加交付那一刻的许可）；
    binding_check：许可预查 {problem, items, checked_at}；auto_check：自用轻审的自动查
    {passed, findings, checked_at}；consents：在 knowbase 留的两种依据各几份
    {review, rerun}；status：reviewing 在审 / approved 过了 / rejected 没过 /
    withdrawn 撤回（作者撤回、平台人员作废）。
    """

    id: uuid.UUID
    expert_id: uuid.UUID
    request_id: uuid.UUID
    listing: str
    test_mode: str
    needs_rerun: bool
    submitted_by: str
    submitted_at: datetime
    status: str = "reviewing"
    conversion: bool = False
    budget_limit: Decimal | None = None
    bindings: list[dict[str, Any]] = field(default_factory=list)
    binding_check: dict[str, Any] | None = None
    auto_check: dict[str, Any] | None = None
    consents: dict[str, Any] = field(default_factory=dict)
    rerun_test_id: uuid.UUID | None = None
    # 重跑发起过几次、之前几次已花多少（上限按一次交付累计；平台出错后再发起最多 3 次）
    rerun_attempts: int = 0
    rerun_spent: Decimal = Decimal("0")
    decided_by: str | None = None
    decided_at: datetime | None = None
    reason: str | None = None
    category: str | None = None
    delivered_at: datetime | None = None
    delivery_error: str | None = None
    receipt: dict[str, Any] | None = None
    consent_ended_at: datetime | None = None
    # 撤回：作者撤回没走完的，或平台人员作废审过却交不成的（10-30 交付审查第 4、7 条）
    withdrawn_by: str | None = None
    withdrawn_at: datetime | None = None
    withdraw_reason: str | None = None


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
    """待人决定的一个问题：测试中的（有人值守，挂在准专家上），或孵化的缺口（kind gap，
    挂在孵化的那一轮上，request_id 是名称；专家孵化方案第 8.4 节）。

    route：None 在运营那里；"user" 已转给用户。缺口建出来就归作者本人，run_id 是那一轮
    交给运行方的编号，question_id 是缺口在那一轮里的编号，proposal 是 AI 附的提议。
    """

    id: uuid.UUID
    draft_id: uuid.UUID | None
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
    incubation_id: uuid.UUID | None = None
    request_id: uuid.UUID | None = None
    proposal: str | None = None


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
        origin: dict[str, Any] | None = None,
        reference_answer: str | None = None,
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

    async def set_incubation_mode(self, request_id: uuid.UUID, mode: str) -> None:
        """名称现在的孵化模式（self / platform）。"""
        ...

    async def replace_acceptance(self, record: AcceptanceRecord) -> None:
        """自助受理单换成正式受理（作者转平台后运营写的那一张）。"""
        ...

    async def add_grant(self, record: GrantRecord) -> None:
        """记一张授权；同一名称已有没结束的孵化授权、同一准专家已有没结束的测试
        授权时回 409。"""
        ...

    async def get_grant(
        self, grant_id: uuid.UUID, *, for_update: bool = False
    ) -> GrantRecord | None: ...

    async def list_grants(
        self, request_ids: Collection[uuid.UUID] | None = None
    ) -> Sequence[GrantRecord]: ...

    async def update_grant(self, grant_id: uuid.UUID, **changes: Any) -> None: ...

    async def add_proposals(self, records: Sequence[ProposalRecord]) -> None: ...

    async def get_proposal(
        self, proposal_id: uuid.UUID, *, for_update: bool = False
    ) -> ProposalRecord | None: ...

    async def list_proposals(self, request_id: uuid.UUID) -> Sequence[ProposalRecord]:
        """这个名称的提议，按提的先后。"""
        ...

    async def update_proposal(self, proposal_id: uuid.UUID, **changes: Any) -> None: ...

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
        statuses: Collection[str] | None = None,
    ) -> Sequence[TestRecord]:
        """按创意、编号排。statuses：只要这几种状态的（后台刷新取在跑的）。"""
        ...

    async def update_test(self, test_id: uuid.UUID, **changes: Any) -> None: ...

    async def last_self_test(self, owner_subject: str) -> TestRecord | None:
        """这个用户最近一次自助测试（记着那台电脑与根目录，SDD 0046 第三节）；没有回
        None。平台测试照它定在哪跑（decisions ⑯），选电脑时默认也是它。"""
        ...

    async def find_test_by_run(self, run_id: str) -> TestRecord | None: ...

    # ---------- 专家（SDD 0037） ----------
    async def create_manual_release(
        self, record: ManualReleaseRecord
    ) -> ManualReleaseRecord: ...
    async def get_manual_release(
        self, release_id: uuid.UUID, *, for_update: bool = False
    ) -> ManualReleaseRecord | None: ...
    async def list_manual_releases(
        self, *, owner_subject: str | None = None
    ) -> Sequence[ManualReleaseRecord]: ...
    async def decide_manual_release(
        self,
        release_id: uuid.UUID,
        *,
        status: str,
        actor: str,
        at: datetime,
        reason: str,
        expert_id: uuid.UUID | None,
    ) -> None: ...

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

    # ---------- 交付过程（SDD 0046 第五节） ----------
    async def create_delivery(self, record: DeliveryRecord) -> DeliveryRecord:
        """同一位专家已有在审的抛 ConcurrencyConflictError(delivery_open)。"""
        ...

    async def get_delivery(
        self, delivery_id: uuid.UUID, *, for_update: bool = False
    ) -> DeliveryRecord | None: ...

    async def list_deliveries(
        self,
        request_ids: Collection[uuid.UUID] | None = None,
        *,
        expert_id: uuid.UUID | None = None,
        status: str | None = None,
    ) -> Sequence[DeliveryRecord]:
        """按提交先后。"""
        ...

    async def update_delivery(self, delivery_id: uuid.UUID, **changes: Any) -> None: ...

    async def deliveries_to_close(self, limit: int = 100) -> Sequence[DeliveryRecord]:
        """审完了、给审核员与重跑的授权还没撤成的。"""
        ...

    async def get_test_set(
        self,
        request_id: uuid.UUID,
        *,
        version: int | None = None,
        from_test_id: uuid.UUID | None = None,
    ) -> TestSetRecord | None: ...

    async def directory_rows(self) -> Sequence[dict[str, Any]]:
        """有准专家的名称：request_id、owner_subject、title、directory_sent、最新准专家的
        version、revision（不读大栏位）。"""
        ...

    async def set_directory_sent(
        self, request_id: uuid.UUID, sent: dict[str, Any]
    ) -> None: ...

    async def tests_without_evidence(
        self, now: datetime, limit: int = 100
    ) -> Sequence[TestRecord]:
        """测完了（通过、没通过）、证据还没写进产出区的：没写过的，和写不成、到了再试
        时候（evidence.retry_after）的；按测完的先后。"""
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
        """测试中的问题（孵化的缺口不在里面，另见 list_gaps）。

        open_only：还没回答的。
        """
        ...

    async def latest_test_set(self, request_id: uuid.UUID) -> TestSetRecord | None: ...

    # ---------- 工作稿（专家孵化方案） ----------
    async def lock_method(self, method_id: uuid.UUID) -> None: ...

    async def method_owner(self, method_id: uuid.UUID) -> str | None: ...

    async def create_method(self, method_id: uuid.UUID, owner_subject: str) -> None: ...

    async def method_head(self, method_id: uuid.UUID) -> int: ...

    async def method_version(
        self, method_id: uuid.UUID, version: int
    ) -> MethodVersionRecord | None: ...

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
    ) -> None: ...

    async def list_methods(
        self, owner_subject: str, *, limit: int, offset: int
    ) -> Sequence[dict[str, Any]]: ...

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
        data_formats: dict[str, Any] | None = None,
    ) -> None:
        """data_formats 给了才换（挑数据时记下的格式），不给照旧。"""
        ...

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

    # ---------- AI 孵化的一轮（专家孵化方案第八节） ----------
    async def create_incubation(self, record: IncubationRecord) -> IncubationRecord:
        """记一轮；这份工作稿已有一轮没结束就抛 ConcurrencyConflictError
        （incubation_running）。"""
        ...

    async def get_incubation(
        self, incubation_id: uuid.UUID, *, for_update: bool = False
    ) -> IncubationRecord | None: ...

    async def list_incubations(
        self,
        *,
        request_id: uuid.UUID | None = None,
        owner_subject: str | None = None,
        statuses: Collection[str] | None = None,
    ) -> Sequence[IncubationRecord]:
        """按第几轮排。statuses：只要这几种状态的（后台刷新取没结束的）。"""
        ...

    async def update_incubation(
        self, incubation_id: uuid.UUID, **changes: Any
    ) -> None: ...

    async def add_gaps(
        self, record: IncubationRecord, gaps: list[dict[str, Any]]
    ) -> None:
        """这一轮交回的缺口记进问题表（kind gap，归作者本人）；同一轮的同一个缺口只记
        一次。gaps：{question_id, text, options, proposal, asked_at}。"""
        ...

    async def list_gaps(
        self,
        *,
        request_ids: Collection[uuid.UUID] | None = None,
        incubation_id: uuid.UUID | None = None,
        open_only: bool = False,
    ) -> Sequence[QuestionRecord]:
        """孵化的缺口（问题表里 kind gap 的），先问的在前。"""
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

    async def start(self, runtime: str, request: AnyRunRequest) -> RunResultV1: ...

    async def status(self, runtime: str, run_id: str) -> RunResultV1: ...

    async def execution(
        self, runtime: str, run_id: str, owner_subject: str, case_id: str | None
    ) -> dict[str, Any]:
        """Read-only knowbot-test-execution/v1 projection for an authorized viewer."""
        ...

    async def cancel(self, runtime: str, run_id: str) -> RunResultV1:
        """停下这次测试；运行方没有这个接口时抛 AppException（404）。"""
        ...

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

    async def model_key(self, runtime: str, subject: str) -> bool | None:
        """用户在运行方填了能用的模型 key 没有（SDD 0046 第三节「没填不能发起」）：
        True / False；运行方还没有这个接口回 None（说不准，不拦，跑起来照旧会停）。"""
        ...


class KnowbaseLibraryClient(Protocol):
    """凭作者本人令牌读 knowbase（契约 user-data/v2，SDD 0043、0044）：知识库栏只列
    作者展示给 knowlab 的资料，挑数据时取它们的格式（描述、读法），不取内容。

    资料不在抛 NotFoundError（material_not_found），别的拒绝抛 BadGatewayError
    （knowbase_refused），不可达抛 ServiceUnavailableError。"""

    async def column(self, user_token: str) -> list[dict[str, Any]]:
        """knowlab 栏：作者展示给 knowlab、自己还有使用权的资料（ShelfEntryRead）。"""
        ...

    async def renditions(
        self, user_token: str, material_id: str, version_no: int
    ) -> list[dict[str, Any]]:
        """一份资料某一版的各种读法与就绪与否（RenditionRead）。"""
        ...

    async def read_format(
        self, user_token: str, material_id: str, version_no: int
    ) -> dict[str, Any]:
        """某一版的 MaterialFormatRead：版本摘要与表轴，不取内容。"""
        ...


class IncubationClient(Protocol):
    """交运行方跑孵化的一轮（契约 knowbot-incubation/v1，服务关系 knowbench:incubate）。

    没配抛 ServiceUnavailableError（incubation_unconfigured），不可达同样抛它。
    """

    def configured(self) -> bool: ...

    async def start(self, request: IncubationRequestV1) -> IncubationResultV1: ...

    async def status(self, incubation_id: str) -> IncubationResultV1: ...

    async def cancel(self, incubation_id: str) -> IncubationResultV1: ...


class KnowbaseDeliveryClient(Protocol):
    """交付、测试证据、名录要的 knowbase 接口（契约 user-data/v2）。
    knowbase 拒了抛 BadGatewayError（code 照 knowbase 给的），不可达抛
    ServiceUnavailableError。"""

    async def push_expert(
        self, expert_id: str, *, owner: str, name: str, latest_version: str | None
    ) -> None: ...

    async def freeze(
        self, expert_id: str, version: str, *, author: str, preview: bool = False
    ) -> list[dict[str, Any]]: ...

    async def binding_states(
        self, items: list[dict[str, Any]]
    ) -> list[dict[str, Any]]: ...

    async def consent_platform(
        self, user_token: str, *, kind: str, ref: str, items: list[dict[str, Any]]
    ) -> int: ...

    async def end_platform(
        self, *, subject: str, kind: str, ref: str, reason: str
    ) -> None: ...

    async def void_frozen(self, expert_id: str, version: str, *, author: str) -> int:
        """作废没交成的那次冻结；有别人请过的拒（frozen_in_use）。"""
        ...

    async def raise_frozen_license(
        self,
        expert_id: str,
        version: str,
        material_id: str,
        *,
        author: str,
        license_: str,
    ) -> dict[str, Any]:
        """把冻结的那一份许可往高改（作者勾了「我有权」）。"""
        ...

    async def platform_consent_live(self, *, subject: str, kind: str, ref: str) -> int:
        """本人给这次平台过程留的依据还有几份有效（0：撤了）。"""
        ...

    async def machine_grant(
        self,
        *,
        owner: str,
        ref: str,
        items: list[dict[str, Any]],
        ttl_seconds: int,
        platform_use: str,
    ) -> str: ...

    async def revoke_grant(self, grant_id: str) -> None:
        """撤 knowlab 签的一张授权（重跑的机器授权）。"""
        ...

    async def write_output(
        self,
        *,
        beneficiary: str,
        task_ref: str,
        run_ref: str,
        name: str,
        content: bytes,
        expert_id: str,
        expert_version: str,
    ) -> dict[str, Any]: ...


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
        category: str | None = None,
    ) -> dict[str, Any]:
        """回 {knowbot_id, version, digest, listing_status?}。请求体照契约
        `knowbot-delivery/v1`；category 是平台人员选的类目（0032 待定第一项）。"""
        ...

    async def stop(self, *, owner_subject: str, knowbot_id: str, version: str) -> None:
        """停用 knowmart 里的这一版；停过的再停也成功。"""
        ...

    async def withdraw_public(
        self, *, owner_subject: str, knowbot_id: str, version: str
    ) -> None:
        """收回 knowmart 里这一版的公开（只撤公开、保留自用）；撤过的再撤也成功。"""
        ...

    async def versions(
        self, *, owner_subject: str, knowbot_ref: str
    ) -> list[dict[str, Any]]:
        """knowmart 里这个引用各版现在怎样：{version, status, held_reason,
        public_allowed}（status：active 在售、held 待处理、stopped 停用）。"""
        ...

    async def set_unlisted(
        self, *, owner_subject: str, knowbot_ref: str, version: str, outcome: str
    ) -> None:
        """这个创意没上架的那一版的结局（declined | rejected），按版本逐条写、重发无害
        （SDD 0037 第三节：knowmart 详情页写明空号那一版）。"""
        ...
