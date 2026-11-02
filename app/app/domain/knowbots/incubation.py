"""孵化契约 `knowbot-incubation/v1`（专家孵化方案第八节，SDD 0029 第 6.3 节）。

knowlab 定，运行方实现。作者点「AI 起草」或「让 AI 改…」，knowlab 把这一轮要的
材料交给运行方；运行方在作者的账号下、他接入的电脑上、用他的模型 key 跑一件委托
（decisions ⑮⑯：钱是作者的，每轮由作者点了才跑，费用上限作者自设），交回对工作稿
的建议。一轮要几分钟，所以是异步的：

- `POST /api/internal/v1/knowbot-runtime/incubations`：提交，回 202 与当前状态。同一个
  incubation_id 重复提交是幂等的：内容相同回原状态，不同则 409。
- `GET /api/internal/v1/knowbot-runtime/incubations/{incubation_id}`：查状态与结果，
  knowlab 轮询（同测试）。
- `POST /api/internal/v1/knowbot-runtime/incubations/{incubation_id}/cancel`：取消；已经
  结束的回原状态。

交过去的只有契约列死的字段（0029 第 6.4 节）：动作与作者的那句指令、创意原文、
当前工作稿（含作者改过和确认过的格子与每格出处）、答过的缺口、作者挑的数据的
**格式**（名字、文件类型、描述、读法、表名、列标签、行标签，不给内容）、作者勾了才带
的最近一次测试报告（只有每一步的结果，第 8.4 节）、费用上限与在哪台电脑上跑。
运行方交给模型的也只有这些，不读作者数据的内容，不看别的用户的任何东西。

交回来的四样（第 8.2 节）：出处表、缺口表、建议、自查。建议**不直接改工作稿**，
作者接受了才进稿（第 8.3 节）；判断（口径、阈值、换算、预测方法、什么算对）只能
是缺口附提议，不能是建议（第四节）；建议只碰方法层的格子，不写 method_text、
不碰「高级」与挑的数据。knowlab 取回后做原话核对与出处把关（第九节，块 5 第 4 步）。
契约文件由 schema.py 生成，放父仓 contracts/knowbot-incubation/v1/。
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.knowbots.workdraft import (
    CELL_ID,
    NAME,
    STEP_ID,
    PlanningCell,
    WorkdraftBody,
)

CONTRACT = "knowbot-incubation/v1"
INCUBATION_ID = r"^[A-Za-z0-9._:-]{8,128}$"
# 答过的缺口在 knowlab 里的编号（问题编号）；出处 answer 的 ref 指它
ANSWER_ID = r"^[A-Za-z0-9._:-]{1,128}$"
# 缺口、建议在这一轮交回里的编号
ITEM_ID = r"^[A-Za-z0-9_-]{1,32}$"
MAX_REQUEST_BYTES = 1_000_000
# 读法的种类名（knowbase 的 rendition kind：text、pages、tables ……）
_READING = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

_STEP = STEP_ID[1:-1]
_CELL = CELL_ID[1:-1]
_NAME = NAME[1:-1]
# 方法层的格子（形状同 knowlab workdraft.apply 认的路径）：整个专家一处与每一步
# 一处。「高级」（工程层）、挑的数据、整份步骤列表都不在里面：AI 不写 method_text、
# 不改「高级」，数据由作者自己挑（第 8.3 节）
CELL_PATH = (
    r"^(name|tagline|planning|scope|scope/(does|does_not|elsewhere)"
    rf"|steps/{_STEP}(/(title|does|why|uses|rules|ask_before|outputs|checks|on_fail|selection))?"
    rf"|steps/{_STEP}/rules/{_CELL}"
    rf"|steps/{_STEP}/ask_before/{_CELL}"
    rf"|steps/{_STEP}/outputs/{_NAME}"
    rf"|steps/{_STEP}/checks/[0-9]{{1,2}})$"
)
# 建议的 value 里不许出现的键：工程层归程序与「高级」（第 7.2 节），不归 AI
ENGINEERING_KEYS = frozenset({"advanced", "method_text"})


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------- 交过去的 ----------
class Seed(Strict):
    """创意原文（第四节第 1 条）：一句话问题、六问（knowlab 拼成带题目的白话）
    或 skill。出处 idea 的 quote 要逐字出现在这里。"""

    kind: Literal["question", "six_questions", "skill"]
    text: str = Field(min_length=1, max_length=60_000)


class CellSource(Strict):
    """工作稿一格的出处（第 9.1 节）：作者写的 user_text，接受或答过的
    user_confirmed；ref 指编辑记录、接受记录或答过的问题。模型凭它认出哪些格子是
    作者的，只能提修改建议。"""

    kind: Literal["user_text", "user_confirmed"]
    ref: str = Field(min_length=1, max_length=200)


class Workdraft(Strict):
    """当前工作稿：方法层的格子（含「高级」，模型只读不改）与每格出处（键是格子路径）。"""

    body: WorkdraftBody = WorkdraftBody()
    sources: dict[str, CellSource] = Field(default_factory=dict, max_length=5000)


class AnsweredGap(Strict):
    """作者答过的缺口：问题、当时的提议、作者的答复。出处 answer 的 ref 指 id。"""

    id: str = Field(pattern=ANSWER_ID)
    question: str = Field(min_length=1, max_length=2000)
    proposal: str = Field(default="", max_length=4000)
    answer: str = Field(min_length=1, max_length=4000)


class TableFormat(Strict):
    """一张表的格式：表名、列标签、行标签（0030 的表格读取取表头与第一列，不取数）。"""

    name: str = Field(default="", max_length=255)
    columns: tuple[str, ...] = Field(default=(), max_length=200)
    rows: tuple[str, ...] = Field(default=(), max_length=500)

    @model_validator(mode="after")
    def _short_labels(self) -> TableFormat:
        if any(len(label) > 200 for label in (*self.columns, *self.rows)):
            raise ValueError("each label is at most 200 characters")
        return self


class DataDescription(Strict):
    """一份数据在 knowbase 里的描述（0049 的几维：主体、类型、期间、格式、语言）。是
    描述，不是内容；作者自己确认过或系统识别的，照 knowbase 给的转过来。"""

    subject: str | None = Field(default=None, max_length=64)
    doc_type: str | None = Field(default=None, max_length=64)
    period_start: str | None = Field(default=None, max_length=10)  # ISO 日期
    period_end: str | None = Field(default=None, max_length=10)
    data_format: str | None = Field(default=None, max_length=64)
    language: str | None = Field(default=None, max_length=16)


class DataFormat(Strict):
    """作者挑的一份数据的格式（0029 第 6.4 节、专家孵化方案第 8.2 节）：名字、文件类型、
    描述的几维、有哪几种读法（正文、按页文字、表格……已就绪的），各表的表名、列标签、
    行标签；不含内容。knowbase 现在不给本人读表头的接口，tables 先空着。"""

    name: str = Field(min_length=1, max_length=255)
    media_type: str = Field(default="", max_length=128)
    description: DataDescription = DataDescription()
    readings: tuple[str, ...] = Field(default=(), max_length=10)
    tables: tuple[TableFormat, ...] = Field(default=(), max_length=50)

    @model_validator(mode="after")
    def _reading_names(self) -> DataFormat:
        if any(not _READING.fullmatch(r) for r in self.readings):
            raise ValueError("a reading is a short lowercase name")
        return self


class LatestTestStep(Strict):
    """报告里一步的结果：过没过、没过的验收名、一条一句为什么没过（同测试契约）。"""

    step_id: str = Field(min_length=1, max_length=128)
    accepted: bool
    failed_checks: tuple[str, ...] = Field(default=(), max_length=64)
    details: tuple[str, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def _short(self) -> LatestTestStep:
        if any(len(x) > 200 for x in (*self.failed_checks, *self.details)):
            raise ValueError("each check name and detail is at most 200 characters")
        return self


class LatestTestExpectation(Strict):
    """作者给这条用例定的一条期望核没核上：哪个交回物的哪一项、怎么比。期望值本身
    不给：模型照着期望值改方法，等于把答案写进规则。"""

    artifact: str = Field(min_length=1, max_length=128)
    path: str = Field(min_length=1, max_length=256)
    kind: Literal["equals", "approx", "contains"]
    passed: bool


class LatestTestCase(Strict):
    case_id: str = Field(pattern=r"^[A-Za-z0-9._-]{1,64}$")
    question: str = Field(min_length=1, max_length=4000)  # 作者写的测试问题
    status: Literal["passed", "failed", "needs_human", "error"]
    steps: tuple[LatestTestStep, ...] = Field(default=(), max_length=200)
    expectations: tuple[LatestTestExpectation, ...] = Field(default=(), max_length=64)
    note: str | None = Field(default=None, max_length=1000)


class LatestTest(Strict):
    """最近一次有结论的测试的报告（专家孵化方案第 8.4 节「带上测试报告让 AI 改」，0029
    第三节第 5 步）：第几次、测的是准专家第几版、通过没有、每条用例每一步的结果。只有
    报告：成稿、各步交回物、测试数据都不给（0029 第 6.4 节，模型不看数据的内容）。"""

    number: int = Field(ge=1)
    draft_version: str = Field(min_length=1, max_length=32)
    status: Literal["passed", "failed"]
    cases: tuple[LatestTestCase, ...] = Field(default=(), max_length=50)


class Budget(Strict):
    limit: str = Field(pattern=r"^[0-9]{1,6}(\.[0-9]{1,2})?$")  # 十进制字符串，避免浮点
    currency: Literal["CNY"] = "CNY"

    @model_validator(mode="after")
    def _more_than_zero(self) -> Budget:
        # 上限是作者的钱（decisions ⑯）：零就是没给，运行方不当成「不设上限」
        if Decimal(self.limit) <= 0:
            raise ValueError("the budget limit is more than zero")
        return self


class Runner(Strict):
    """谁跑这一轮（decisions ⑯，第 8.5 节）：只有作者本人——他接入的电脑、他选的
    工作区根目录、他的模型 key，他出钱。运行方在这个根目录下建固定的「孵化」子目录
    与项目。形状同测试的 Runner。"""

    kind: Literal["user"]
    environment_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    workspace_root: str = Field(min_length=1, max_length=1024)


class IncubationRequestV1(Strict):
    contract: Literal["knowbot-incubation/v1"]
    incubation_id: str = Field(pattern=INCUBATION_ID)
    # 作者（Casdoor 用户 id）：委托开在他的账号下，花他的钱
    owner_subject: str = Field(pattern=r"^[A-Za-z0-9._:@-]{1,512}$")
    # draft：从种子起草骨架（工作稿是空的，或作者想重来）；revise：按作者的那句话改
    action: Literal["draft", "revise"]
    instruction: str | None = Field(
        default=None, min_length=1, max_length=4000, exclude_if=lambda v: v is None
    )
    # 按指令改时作者指定的那一步（可以不给）
    step_id: str | None = Field(
        default=None, pattern=STEP_ID, exclude_if=lambda v: v is None
    )
    seed: Seed
    # 用户先让自己的模型直接答过的回答（从 knowbench 转来的问题申请才有，
    # 请专家方案设计第
    # 4.9 节第 6 条）：模型写的，起草时可以参考，进工作稿只能算提议（kind proposal）
    reference: str | None = Field(
        default=None, min_length=1, max_length=20000, exclude_if=lambda v: v is None
    )
    workdraft: Workdraft = Workdraft()
    answers: tuple[AnsweredGap, ...] = Field(default=(), max_length=200)
    data_formats: tuple[DataFormat, ...] = Field(default=(), max_length=200)
    # 作者点「让 AI 改…」时勾了「带上测试报告」才有（专家孵化方案第 8.4 节）
    test_report: LatestTest | None = Field(default=None, exclude_if=lambda v: v is None)
    budget: Budget
    timeout_seconds: int = Field(ge=60, le=86400)
    runner: Runner

    @model_validator(mode="after")
    def _instruction_with_revise(self) -> IncubationRequestV1:
        if (self.action == "revise") != (self.instruction is not None):
            raise ValueError("revise needs the author's instruction, draft has none")
        if self.test_report is not None and self.action != "revise":
            raise ValueError("only revise carries a test report")
        if self.step_id is not None:
            if self.action != "revise":
                raise ValueError("only revise names a step")
            if self.step_id not in {s.id for s in self.workdraft.body.steps}:
                raise ValueError("the named step is not in the workdraft")
        ids = [a.id for a in self.answers]
        if len(ids) != len(set(ids)):
            raise ValueError("answered gap ids must be unique")
        return self

    @model_validator(mode="after")
    def _fits(self) -> IncubationRequestV1:
        if _bytes(self.model_dump(mode="json")) > MAX_REQUEST_BYTES:
            raise ValueError("the request is larger than the limit")
        return self


# ---------- 交回来的 ----------
class SourceEntry(Strict):
    """出处表的一行（第 8.2、9.1 节）：建议里的一格出自哪里。

    idea：创意里逐字的一句（quote，knowlab 核它逐字出现在这一版创意里）；
    answer：某个答过的缺口（ref 是它的 id）；proposal：AI 的提议，作者接受时页面
    照实标「这是 AI 的提议」。
    """

    path: str = Field(pattern=CELL_PATH)
    kind: Literal["idea", "answer", "proposal"]
    quote: str | None = Field(
        default=None, min_length=1, max_length=4000, exclude_if=lambda v: v is None
    )
    ref: str | None = Field(
        default=None, pattern=ANSWER_ID, exclude_if=lambda v: v is None
    )

    @model_validator(mode="after")
    def _cites_what_its_kind_says(self) -> SourceEntry:
        if (self.kind == "idea") != (self.quote is not None):
            raise ValueError("an idea source quotes the seed, and only it quotes")
        if (self.kind == "answer") != (self.ref is not None):
            raise ValueError("an answer source names the answered gap, and only it")
        return self


class Gap(Strict):
    """缺口（第 8.4 节）：要作者定的判断，写成一个问题，附提议与选项；进「待办」
    由作者答。"""

    id: str = Field(pattern=ITEM_ID)
    question: str = Field(min_length=1, max_length=2000)
    proposal: str = Field(min_length=1, max_length=4000)
    options: tuple[str, ...] = Field(default=(), max_length=10)

    @model_validator(mode="after")
    def _short_options(self) -> Gap:
        if any(not o.strip() or len(o) > 200 for o in self.options):
            raise ValueError("each option is 1 to 200 characters")
        return self


class Suggestion(Strict):
    """对工作稿一格的改动（第 8.3 节），作者接受了才进稿。

    路径与 value 的形状同工作稿格子（knowlab workdraft.apply）：set 换掉一格或一块，
    add 加一步、一条规则、一个交回项、一条验收，remove 删掉（不带 value），move 把
    一步或一条挪到第 value 位（从 0 起）。"""

    id: str = Field(pattern=ITEM_ID)
    path: str = Field(pattern=CELL_PATH)
    op: Literal["set", "add", "remove", "move"]
    value: Any = None
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def _value_fits_op(self) -> Suggestion:
        if self.op == "remove" and self.value is not None:
            raise ValueError("remove carries no value")
        if self.op == "move" and not (
            isinstance(self.value, int)
            and not isinstance(self.value, bool)
            and 0 <= self.value < 100
        ):
            raise ValueError("move carries the new position, from 0")
        if self.op in ("set", "add") and self.value is None:
            raise ValueError(f"{self.op} carries a value")
        if self.path == "planning" and self.op in ("set", "add"):
            # 这里的 method_text 是作者规划原文，不是 AI 重写单元的执行正文。
            PlanningCell.model_validate(self.value)
        elif _engineering(self.value):
            raise ValueError("a suggestion does not touch the engineering layer")
        return self


class SelfCheck(Strict):
    """自查（第 8.2 节第 4 条）：一条起草时的建议对不上出处或碰到了判断，已经改成
    了这个缺口（turned_into_gap 是缺口编号）；那条建议不再在建议里。"""

    suggestion_id: str = Field(pattern=ITEM_ID)
    turned_into_gap: str = Field(pattern=ITEM_ID)


class Handback(Strict):
    sources: tuple[SourceEntry, ...] = Field(default=(), max_length=2000)
    gaps: tuple[Gap, ...] = Field(default=(), max_length=200)
    suggestions: tuple[Suggestion, ...] = Field(default=(), max_length=500)
    self_check: tuple[SelfCheck, ...] = Field(default=(), max_length=500)

    @model_validator(mode="after")
    def _consistent(self) -> Handback:
        gaps = [g.id for g in self.gaps]
        suggestions = [s.id for s in self.suggestions]
        if len(gaps) != len(set(gaps)) or len(suggestions) != len(set(suggestions)):
            raise ValueError("gap ids and suggestion ids must be unique")
        for check in self.self_check:
            if check.turned_into_gap not in gaps:
                raise ValueError("a self check names a gap that is not in the gaps")
            if check.suggestion_id in suggestions:
                raise ValueError(
                    "a suggestion turned into a gap is no longer suggested"
                )
        return self


class Cost(Strict):
    spent: str = Field(pattern=r"^[0-9]{1,9}(\.[0-9]{1,6})?$")
    currency: Literal["CNY"] = "CNY"


IncubationStatus = Literal[
    "queued",  # 受理了，还没开跑（排在同一台电脑、同一根目录的上一轮后面）
    "running",
    # 等作者的电脑：不在线，或「孵化」目录还没建好（第 8.5 节「等你接入电脑」）
    "waiting",
    "succeeded",
    "failed",
    "over_budget",  # 到了费用上限停下（第 8.4 节）
    "cancelled",
]
FINAL_STATUSES = frozenset({"succeeded", "failed", "over_budget", "cancelled"})


class IncubationResultV1(Strict):
    contract: Literal["knowbot-incubation/v1"]
    incubation_id: str = Field(pattern=INCUBATION_ID)
    status: IncubationStatus
    cost: Cost | None = None
    error: str | None = Field(default=None, max_length=1000)  # 没成的原因，不含分析内容
    # 作者工作台里这一轮的委托（knowlab 孵化记录里记着，0029 第 6.6 节）
    task_id: str | None = Field(default=None, pattern=r"^[0-9a-f-]{36}$")
    waiting_for: Literal["computer_offline", "folder_not_ready"] | None = None
    started_at: str | None = None
    finished_at: str | None = None
    handback: Handback | None = None

    @model_validator(mode="after")
    def _states(self) -> IncubationResultV1:
        if (self.status == "succeeded") != (self.handback is not None):
            raise ValueError("a succeeded round hands back, and only it")
        if (self.status == "waiting") != (self.waiting_for is not None):
            raise ValueError("waiting says what it waits for, and only waiting")
        return self

    @property
    def final(self) -> bool:
        return self.status in FINAL_STATUSES


def _engineering(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            key in ENGINEERING_KEYS or _engineering(item) for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_engineering(item) for item in value)
    return False


def _bytes(value: Any) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
