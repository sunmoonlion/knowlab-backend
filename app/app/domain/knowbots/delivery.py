"""交付（SDD 0046 第五节；0043 第 4.2、4.3 节；knowmart 0032 待定第一项）。

| 测试通过的那一次是 | 用户同意门 | 自用 | 公开 |
| --- | --- | --- | --- |
| 自助测的 | 「提交交付」即过 | 轻审 | 完整审核 + 平台发起重跑 |
| 平台测的 | 平台孵化的先交回、作者再定；自助孵化的「提交交付」即过 | 轻审 | 完整审核 |

- 轻审：能自动查的自动查（`auto_check`：成稿里有没有建议买卖、评级、目标价、荐股、
  收益承诺这类说法，财务科目与事实陈述不算；测试结果与受理单对不对得上），都过就算
  审过，查不过才交平台人员看。
- 公开：完整审核；自助测的要平台发起重跑（在作者的账号下、用作者的 key 与电脑，
  decisions ⑯），审核员看重跑的结果、不看原始数据。
- 交审时作者本人授权审核员看这次交付（`platform:knowlab:review:<交付过程>`），公开且
  自助测的另授权重跑要读的冻结输入（`platform:knowlab:rerun:<交付过程>`）；审完撤销。
- 所绑数据的许可：自用要每份还在，公开要每份都能经专家给别人用（knowbase 预查，
  knowmart 收清单时硬查）；自用转公开时重查。
- 类目由平台人员审核时选（类目表照 knowmart `domain/knowbots/catalog.py`，取值锁在
  契约 `knowbot-delivery/v1`）；公开的必须选，自动审过的自用不选（knowmart 记未分类）。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.knowbots.platform_grants import MAX_BUDGET

# knowmart 的类目表去掉「未分类」（契约 knowbot-delivery/v1 的 category 取值；锁测试核）
CATEGORY_CODES: tuple[str, ...] = (
    "finance.securities_advisory",
    "finance.public_fund",
    "finance.private_fund",
    "tools.office",
    "tools.lookup",
    "tools.calculator",
    "business.management",
)
CategoryCode = Literal[CATEGORY_CODES]  # type: ignore[valid-type]

# 成稿里的投资建议说法（C-L5：荐股、目标价、评级、收益承诺）。按「说法」认，不按单个
# 词认：
# 「买入返售金融资产」「卖出回购金融资产款」是财务科目，「股东增持」「减持计划」是事实
# 陈述，都不算（10-30 交付审查第 11 条：原来按 knowmart 条目标红的单词认，财务类专家
# 交自用基本都转人工，轻审失效）。认不出的不判违规；轻审只是「能自动查的自动查」，
# 拿不准的说法宁可放给人看，也不把科目当违规。
ADVICE_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "建议买卖",
        re.compile(r"建议(?:投资者)?(?:买入|卖出|增持|减持|持有|加仓|减仓|清仓|抄底)"),
    ),
    (
        # 只认对投资标的给的投资评级（买入、增持……评级）；「上调主体信用评级」这类
        # 评级机构的信用评级是事实陈述，不算
        "评级",
        re.compile(
            r"(?:买入|卖出|增持|减持|持有|中性|推荐|强烈推荐|谨慎推荐|跑赢大市|跑输大市"
            r"|优于大市|同步大市|弱于大市)[」”\"']?(?:的)?(?:投资)?评级"
        ),
    ),
    ("目标价", re.compile(r"目标价")),
    ("荐股", re.compile(r"荐股|强烈推荐|推荐(?:买入|关注)")),
    ("收益承诺", re.compile(r"稳赚|保证收益|承诺收益|收益承诺|保本保息")),
)
AUTO_REVIEWER = "system:auto-check"  # 自用轻审全过、自动审过的记这个
MAX_REASON_CHARS = 1000
# 所绑数据不行的原因（同 knowmart delivery.HOLD_REASONS），先说最要紧的
BINDING_PROBLEMS: tuple[str, ...] = (
    "binding_gone",
    "binding_disabled",
    "binding_deleted",
    "binding_not_shareable",
    # 冻结时只许本人用：现在许可够时作者可勾「我有权」升档，否则出新一版（转公开时）
    "binding_frozen_self_only",
)
LICENSE_RANK = {"self": 0, "via_expert": 1, "sale": 2}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DeliverySubmitIn(Strict):
    """作者「提交交付」：选自用或公开、授权审核员看这次交付；公开且自助测的要平台在作者
    的电脑上重跑，作者设这一次的费用上限（decisions ⑯）。"""

    listing: Literal["self", "public"]
    authorize_reviewer: Literal[True]
    # 自用转公开时冻结的数据只许本人用：作者勾「我有权」把冻结的那一档往高改到「可以经
    # 专家给别人用」（决定 10-30；knowbase 核现在的许可够、留痕）
    raise_frozen: bool = False
    budget_limit: Decimal | None = Field(
        default=None, gt=0, le=MAX_BUDGET, decimal_places=2
    )


class DeliveryReviewIn(Strict):
    """平台人员审一次交付：过 / 不过与理由；过的时候选类目（公开的必须选）。"""

    approved: bool
    reason: str = Field(default="", max_length=MAX_REASON_CHARS)
    category: CategoryCode | None = None

    @model_validator(mode="after")
    def _why_not(self) -> DeliveryReviewIn:
        if not self.approved and not self.reason.strip():
            raise ValueError("a rejection gives a reason")
        return self


def basis(kind: str, delivery_id: object) -> str:
    """`platform:knowlab:review:<交付过程>` 或 `platform:knowlab:rerun:<交付过程>`。"""
    return f"platform:knowlab:{kind}:{delivery_id}"


def binding_problem(states: Sequence[Mapping[str, Any]], *, public: bool) -> str | None:
    """knowbase 回的每份状态 → 不行的原因，都行回 None（同 knowmart 的规矩）。"""
    found = set()
    for s in states:
        state = s.get("state")
        frozen = s.get("frozen_license")
        current_ok = LICENSE_RANK.get(str(s.get("license") or ""), 0) >= 1
        if state in ("gone", "disabled", "deleted"):
            found.add(f"binding_{state}")
        elif state != "live":
            found.add("binding_gone")
        elif public and not current_ok:
            found.add("binding_not_shareable")
        elif public and frozen is not None and LICENSE_RANK.get(str(frozen), 0) < 1:
            found.add("binding_frozen_self_only")
        elif public and s.get("shareable") is not True:
            found.add("binding_not_shareable")
    return next((p for p in BINDING_PROBLEMS if p in found), None)


def _texts(outputs: Mapping[str, Any] | None) -> Iterable[str]:
    """一条用例的产出里给人看的文字：成稿与各步交回物（序列化后的字）。"""
    if not outputs:
        return
    deliverable = outputs.get("deliverable")
    if isinstance(deliverable, Mapping) and isinstance(deliverable.get("text"), str):
        yield deliverable["text"]
    for step in outputs.get("steps") or []:
        if isinstance(step, Mapping) and step.get("artifact") is not None:
            yield str(step["artifact"])


def auto_check(
    *,
    report: Mapping[str, Any] | None,
    test_status: str,
    accepted: bool,
    case_ids: Sequence[str],
    outputs: Mapping[str, Mapping[str, Any] | None],
) -> list[dict[str, Any]]:
    """自用轻审能自动查的（SDD 0046 第五节）；回查到的问题，空的就是都过。

    - 测试结果与受理单对得上：这次测试通过、每条用例都过；出自受理了的创意版本（自助的
      是「自助（不受理）」）；跑的用例正是那一版创意的用例，不多不少。
    - 成稿里没有投资建议的说法（ADVICE_RULES：建议买卖、评级、目标价、荐股、收益承诺；
      财务科目与事实陈述不算）；有用例没取到产出的也算查不过（没法查，交人看）。
    只记哪条用例、哪类说法、命中的那几个字，不记成稿原文（平台的人看不到自助的数据）。
    """
    findings: list[dict[str, Any]] = []
    cases = [c for c in (report or {}).get("cases") or [] if isinstance(c, Mapping)]
    if test_status != "passed" or any(c.get("status") != "passed" for c in cases):
        findings.append({"kind": "test_not_passed"})
    if not accepted:
        findings.append({"kind": "not_accepted"})
    ran = sorted(str(c.get("case_id")) for c in cases)
    if ran != sorted(case_ids):
        findings.append({"kind": "cases_mismatch"})
    for case_id in sorted(case_ids):
        found = outputs.get(case_id)
        if not found:
            findings.append({"kind": "outputs_missing", "case_id": case_id})
            continue
        joined = "\n".join(_texts(found))
        for rule, pattern in ADVICE_RULES:
            hit = pattern.search(joined)
            if hit is not None:
                findings.append(
                    {
                        "kind": "flagged_word",
                        "case_id": case_id,
                        "rule": rule,
                        "word": hit.group(0)[:20],
                    }
                )
    return findings
