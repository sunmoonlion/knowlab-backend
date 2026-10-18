"""测试契约 `knowbot-test-run/v1`（SDD 0027 决定 12）：knowlab 定，运行方实现。

knowlab 管理端后端把一份草稿清单、验收用例、数据绑定、预算与超时交给运行方；运行方
在我们的测试账号上无人值守跑。测试一次要几分钟到几十分钟，所以是异步的：

- `POST /api/internal/v1/knowbot-runtime/test-runs`：提交，回 202 与当前状态。
  同一个 run_id 重复提交是幂等的：内容相同回原状态，不同则 409。
- `GET /api/internal/v1/knowbot-runtime/test-runs/{run_id}`：查状态与结果，
  knowlab 轮询。
- `POST /api/internal/v1/knowbot-runtime/test-runs/{run_id}/answers`：回答待回答的
  问题。有人值守（所有者 2026-10-16）：跑到要人决定的地方，状态是 waiting，结果里
  列出问题；knowlab 这边运营答跑法、用户答投资判断，答完运行方接着跑。同一个问题
  重复回答：内容相同回原状态，不同则 409。等人的时间不计入超时，最长 max_wait_seconds。

结果只有每条用例通过与否、每步哪条验收没过、用户给的期望是否满足、花了多少；不含任何
分析内容（测试产出不给用户，`C-L5`、0026「不替用户跑出结果」）。契约文件由 schema.py
生成，放父仓 contracts/knowbot-test-run/v1/。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONTRACT = "knowbot-test-run/v1"
RUN_ID = r"^[A-Za-z0-9._:-]{8,128}$"
CASE_ID = r"^[A-Za-z0-9._-]{1,64}$"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Expectation(Strict):
    """用户定的「怎样算对」：在最后的交回物里，某一项应当等于、约等于或包含什么。"""

    artifact: str = Field(min_length=1, max_length=128)  # 哪个交回物
    path: str = Field(min_length=1, max_length=256)  # 交回物里的哪一项，点号路径
    kind: Literal["equals", "approx", "contains"]
    value: str | int | float | bool
    # approx 的相对误差，例如 0.01 是 1%；别的 kind 不用
    tolerance: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def _tolerance_only_for_approx(self) -> Expectation:
        if (self.kind == "approx") != (self.tolerance is not None):
            raise ValueError("tolerance is required for approx and only for approx")
        if self.kind == "approx" and isinstance(self.value, (str, bool)):
            raise ValueError("approx needs a number")
        return self


class CaseInput(Strict):
    question: str = Field(min_length=1, max_length=4000)
    # 开步前的提问（分身清单里各步的 ask_before）预先给好的答案：提问 id → 答案
    answers: dict[str, str] = Field(default_factory=dict, max_length=64)


class RunCase(Strict):
    case_id: str = Field(pattern=CASE_ID)
    input: CaseInput
    expectations: tuple[Expectation, ...] = Field(default=(), max_length=64)


class Budget(Strict):
    limit: str = Field(pattern=r"^[0-9]{1,6}(\.[0-9]{1,2})?$")  # 十进制字符串，避免浮点
    currency: Literal["CNY"] = "CNY"


class RunRequestV1(Strict):
    contract: Literal["knowbot-test-run/v1"]
    run_id: str = Field(pattern=RUN_ID)
    # 数据的所有者（Casdoor 用户 id）：运行方只按清单 data_bindings 读他的数据
    owner_subject: str = Field(pattern=r"^[A-Za-z0-9._:@-]{1,512}$")
    manifest: dict[str, Any]  # knowbot-manifest/v1 草稿；运行方核 runtime 是自己
    cases: tuple[RunCase, ...] = Field(min_length=1, max_length=50)
    budget: Budget
    timeout_seconds: int = Field(ge=60, le=86400)  # 不含等人回答的时间
    # 一个问题最多等多久；过了没人答，那条用例记 needs_human（所有者定 3 天）
    max_wait_seconds: int = Field(default=259200, ge=60, le=604800)

    @model_validator(mode="after")
    def _unique_cases(self) -> RunRequestV1:
        ids = [c.case_id for c in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("case ids must be unique")
        return self


class StepResult(Strict):
    step_id: str = Field(min_length=1, max_length=128)
    accepted: bool
    failed_checks: tuple[str, ...] = Field(default=(), max_length=64)  # 没过的验收名
    # 没过的为什么没过，一条一句（哪一格、说了多少、实际多少）；v1 冻结后加的可选字段
    # （审读第 3 条）。数是用户自己数据里的，不是分析内容（C-L5）
    details: tuple[Annotated[str, Field(max_length=200)], ...] = Field(
        default=(), max_length=64
    )


class CaseResult(Strict):
    case_id: str = Field(pattern=CASE_ID)
    # needs_human：跑到需要人决定的地方（没有预设答案的提问、必须人审批、返工用完）
    status: Literal["passed", "failed", "needs_human", "error"]
    steps: tuple[StepResult, ...] = Field(default=(), max_length=200)
    expectations: tuple[bool, ...] = Field(default=(), max_length=64)  # 按请求里的顺序
    note: str | None = Field(default=None, max_length=1000)  # 出错处，不含分析内容


class Cost(Strict):
    spent: str = Field(pattern=r"^[0-9]{1,9}(\.[0-9]{1,6})?$")
    currency: Literal["CNY"] = "CNY"


class Option(Strict):
    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=200)


class PendingQuestion(Strict):
    """跑到要人决定的地方。tool_approval：能不能做这件事（运营答）；question：Codex
    反问；ask_before：开步前的提问（key 是清单里提问的 id，答案可记回用例）。"""

    question_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    case_id: str = Field(pattern=CASE_ID)
    step_id: str | None = Field(default=None, max_length=128)
    kind: Literal["tool_approval", "question", "ask_before"]
    key: str | None = Field(default=None, max_length=64)
    text: str = Field(min_length=1, max_length=4000)
    options: tuple[Option, ...] = Field(default=(), max_length=20)
    allow_free_text: bool = True
    asked_at: str = Field(min_length=1, max_length=40)


def first_route(kind: str) -> str | None:
    """问题先归谁（C-L1，审读第 2 条）：工具审批归我们（空）；投资判断与开步前的提问
    归用户本人，运营要写理由改派给自己才能答。"""
    return None if kind == "tool_approval" else "user"


class Progress(Strict):
    """跑着的那条用例走到哪了（待办账 135）。

    只有编号、计数、步名和钱，不含交回物内容（C-L5）。
    v1 冻结后加的可选字段：运行方不给也收。
    """

    case_id: str = Field(pattern=CASE_ID)
    cases_done: int = Field(ge=0, le=50)  # 已经跑完几条
    cases_total: int = Field(ge=1, le=50)
    step: int = Field(ge=0, le=200)  # 第几步（从 1 起；0 = 还没开第一步）
    steps_total: int = Field(ge=0, le=200)
    step_title: str = Field(default="", max_length=200)
    attempt: int = Field(default=0, ge=0, le=100)  # 这一步第几次做（重做会加）
    spent: str = Field(
        pattern=r"^[0-9]{1,9}(\.[0-9]{1,6})?$"
    )  # 到现在花了多少，含跑着的这条


RunStatus = Literal[
    "accepted",
    "running",
    "waiting",
    "passed",
    "failed",
    "error",
    "timeout",
    "cancelled",
]
FINAL_STATUSES = frozenset({"passed", "failed", "error", "timeout", "cancelled"})


class RunResultV1(Strict):
    contract: Literal["knowbot-test-run/v1"]
    run_id: str = Field(pattern=RUN_ID)
    status: RunStatus
    cases: tuple[CaseResult, ...] = Field(default=(), max_length=50)
    cost: Cost | None = None
    started_at: str | None = None
    finished_at: str | None = None
    questions: tuple[PendingQuestion, ...] = Field(default=(), max_length=50)
    waited_seconds: int = Field(default=0, ge=0)  # 到现在一共等了人多久
    progress: Progress | None = None  # 没结论时：跑着的那条走到哪了

    @model_validator(mode="after")
    def _waiting_means_questions(self) -> RunResultV1:
        if (self.status == "waiting") != bool(self.questions):
            raise ValueError("waiting has questions and only waiting has questions")
        return self

    @model_validator(mode="after")
    def _passed_means_every_case_passed(self) -> RunResultV1:
        if self.status == "passed" and (
            not self.cases or any(c.status != "passed" for c in self.cases)
        ):
            raise ValueError("a passed run needs every case passed")
        return self

    @property
    def final(self) -> bool:
        return self.status in FINAL_STATUSES


class Answer(Strict):
    """回答一个问题：tool_approval 给 decision；别的给选项或自己写的话。"""

    question_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    decision: Literal["approve", "deny"] | None = None
    option_id: str | None = Field(default=None, max_length=64)
    text: str | None = Field(default=None, min_length=1, max_length=4000)
    answered_by: Literal["operator", "user"]

    @model_validator(mode="after")
    def _one_kind_of_answer(self) -> Answer:
        given = [v for v in (self.decision, self.option_id, self.text) if v is not None]
        if len(given) != 1 and not (self.option_id and self.text):
            raise ValueError("give a decision, an option, or text")
        return self


class AnswersV1(Strict):
    contract: Literal["knowbot-test-run/v1"]
    answers: tuple[Answer, ...] = Field(min_length=1, max_length=50)
