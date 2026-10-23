"""创意、受理、准专家、测试、专家：一生里各段的规矩。

SDD 0037 第四至第十节、第十八之二节。

没有退回：每个过程跑一次出一个结果，结果定了不改；要改就再跑一次、出新的。创意、准专家、
专家用版本号，测试用编号，都只增。

- 创意：改一次加一个版本，只插不改。
- 受理：一个版本一张受理单，查三样（C-L5 投资建议类不做、C-L2 测试数据有没有权用、
  C-L1 归不归这类专家能做的）；三样都过才能受理，不受理要写理由。
- 准专家：只能从受理了的版本孵化；版本号「x.y」，x 是它要冲的专家第几版。可以请用户
  审核：在等用户答的不能测，用户说不行的不再测（再孵化）。
- 测试：每次一个新编号；平台出错（含超时、开跑失败、运营判定卡住）结束这一次，准专家
  回到待测，再测是新编号。同一个名称同时只有一个在跑。
- 专家：测试通过即是专家，版本号就是准专家的 x；x 已经出过专家的准专家不许再测。
  上架前两道门：先用户同意，后运营审核；都过才交 knowmart。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.knowbots.drafts import Idea
from app.domain.knowbots.test_run import RunCase

# ---------- 受理 ----------
# 受理查的三样（所有者 10-23 按拟的）
CHECKS: tuple[str, ...] = ("C-L5", "C-L2", "C-L1")
MAX_NOTE_CHARS = 500
MAX_REASON_CHARS = 1000
TRIAL_ACCEPTOR = "system:trial"  # 回填：试行期自动受理
TRIAL_NOTE = "试行期自动受理"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CheckIn(Strict):
    ok: bool
    note: str = Field(default="", max_length=MAX_NOTE_CHARS)


class AcceptanceIn(Strict):
    """一张受理单：三样各自的结论与说明、受理与否、理由。"""

    checks: dict[str, CheckIn]
    accepted: bool
    reason: str = Field(default="", max_length=MAX_REASON_CHARS)

    @model_validator(mode="after")
    def _consistent(self) -> AcceptanceIn:
        if set(self.checks) != set(CHECKS):
            raise ValueError(f"checks must be exactly {', '.join(CHECKS)}")
        if self.accepted and not all(c.ok for c in self.checks.values()):
            raise ValueError("accept only when all three checks pass")
        if not self.accepted and not self.reason.strip():
            raise ValueError("say why it is not accepted")
        return self


class RevisionIn(Strict):
    """改创意：方法与用例，出一个新版本。名称与运行方一生不变。"""

    idea: Idea
    cases: tuple[RunCase, ...] = Field(min_length=1, max_length=50)
    # 测试数据说明的文字变了，新版本要用户重新同意（这一版的表单那一句）
    consent_test_data: Literal[True]

    @model_validator(mode="after")
    def _unique_cases(self) -> RevisionIn:
        ids = [c.case_id for c in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("case ids must be unique")
        return self


# ---------- 准专家：用户审核 ----------
MAX_REVIEW_NOTE_CHARS = 2000


class UserReviewIn(Strict):
    """用户审核一位准专家：行 / 不行（不行要说哪里不对）。"""

    ok: bool
    note: str = Field(default="", max_length=MAX_REVIEW_NOTE_CHARS)

    @model_validator(mode="after")
    def _why_not(self) -> UserReviewIn:
        if not self.ok and not self.note.strip():
            raise ValueError("say what is wrong")
        return self


def review_state(user_review: Mapping[str, Any] | None) -> str | None:
    """None 没请用户审核；pending 等用户答；ok 用户说行；not_ok 用户说不行。"""
    if not user_review:
        return None
    if user_review.get("answered_at") is None:
        return "pending"
    return "ok" if user_review.get("ok") else "not_ok"


# ---------- 测试 ----------
TestStatus = Literal[
    "queued", "running", "waiting", "passed", "failed", "platform_error"
]
TEST_STATUSES: tuple[str, ...] = (
    "queued",
    "running",
    "waiting",
    "passed",
    "failed",
    "platform_error",
)
ACTIVE_TESTS = frozenset({"queued", "running", "waiting"})
# 运行方的状态 → 测试的状态（契约 knowbot-test-run/v1）
_FROM_RUN = {
    "accepted": "queued",
    "running": "running",
    "waiting": "waiting",
    "passed": "passed",
    "failed": "failed",
    "error": "platform_error",
    "timeout": "platform_error",
    "cancelled": "platform_error",
}


def status_from_run(run_status: str) -> str:
    return _FROM_RUN[run_status]


# ---------- 专家 ----------
MAX_STOP_CHARS = 500
TRIAL_NOTE_GATES = "两道门之前交付（试行期）"


class ExpertDecisionIn(Strict):
    """用户同意门：要这一版 / 不要。"""

    accept: bool


class ExpertReviewIn(Strict):
    """运营审核门：通过（自用 / 公开，公开要发布主体有资质）/ 不通过与理由。"""

    approved: bool
    listing: Literal["self", "public"] | None = None
    reason: str = Field(default="", max_length=MAX_REASON_CHARS)

    @model_validator(mode="after")
    def _consistent(self) -> ExpertReviewIn:
        if self.approved and self.listing is None:
            raise ValueError("an approval says self or public")
        if not self.approved and (self.listing is not None or not self.reason.strip()):
            raise ValueError("a rejection gives a reason and no listing")
        return self


class StopIn(Strict):
    reason: str = Field(min_length=1, max_length=MAX_STOP_CHARS)


ExpertStage = Literal[
    "to_return",  # 测试通过、还没交回用户
    "awaiting_user",  # 交回了，等用户要不要
    "declined",  # 用户不要
    "to_review",  # 用户要了，等运营审核
    "rejected",  # 运营审核不通过
    "to_deliver",  # 审过了，还没交成 knowmart（交付失败可重试）
    "listed",  # 上架了
    "stopped",  # 停用了
]


@dataclass(frozen=True)
class ExpertGates:
    """算专家走到哪一步要的那几项。"""

    returned_at: datetime | None
    user_accepted: bool | None
    review_approved: bool | None
    listed_at: datetime | None
    stopped_at: datetime | None


def expert_stage(e: ExpertGates) -> str:
    if e.stopped_at is not None:
        return "stopped"
    if e.listed_at is not None:
        return "listed"
    if e.returned_at is None:
        return "to_return"
    if e.user_accepted is None:
        return "awaiting_user"
    if not e.user_accepted:
        return "declined"
    if e.review_approved is None:
        return "to_review"
    if not e.review_approved:
        return "rejected"
    return "to_deliver"


# ---------- 两端的栏与大类 ----------
# 管理端「交付」栏：大类 → 哪几个阶段（全部专家另列）
DELIVERY_CATEGORIES: dict[str, frozenset[str]] = {
    "to_return": frozenset({"to_return"}),
    "awaiting_user": frozenset({"awaiting_user"}),
    "to_review": frozenset({"to_review", "to_deliver"}),
    "listed": frozenset({"listed"}),
    "not_listed": frozenset({"declined", "rejected"}),
}
OUR_EXPERT_STAGES = frozenset({"to_return", "to_review", "to_deliver"})
# web端「专家」栏：用户只看交回了的
USER_EXPERT_CATEGORIES: dict[str, frozenset[str]] = {
    "new": frozenset({"awaiting_user"}),
    "accepted": frozenset({"to_review", "to_deliver"}),
    "usable": frozenset({"listed"}),
    "stopped": frozenset({"stopped"}),
    "not_listed": frozenset({"declined", "rejected"}),
}


def category_of(stage: str, categories: Mapping[str, Iterable[str]]) -> str | None:
    for name, stages in categories.items():
        if stage in stages:
            return name
    return None


def draft_label(version: str, revision: int) -> str:
    """准专家的版本号「x.y」。"""
    return f"{version}.{revision}"
