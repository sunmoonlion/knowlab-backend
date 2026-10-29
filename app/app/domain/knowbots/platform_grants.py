"""作者给平台的授权（SDD 0046 第四节；decisions 10-29「孵化第 5 步的三件」）。

两种：
- 孵化（incubate）：作者点「请平台孵化」记一张（pending），这一版创意正式受理后生效
  （active）；作者点「我自己来」撤销（revoked），平台把草稿交还结束（ended），不受理
  （declined）。生效后最长 30 天，到期（expired）作者一键续。
- 测试（test）：自助孵化的准专家交平台测这一次，轻受理后生效，测完结束。

运营看得到什么一律按还有效的授权算：pending 的让运营能受理，active 的让运营干活。
依据写成 `platform:knowlab:<种类>:<编号>`（「待你决定」第 4 条的写法）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import Field

from app.domain.knowbots.drafts import Strict

GrantKind = Literal["incubate", "test"]
GRANT_DAYS = 30
LIVE = ("pending", "active")
# 平台发起的 AI 一轮、平台测试的费用上限（元），作者设
MAX_BUDGET = Decimal("1000")


def basis(kind: str, grant_id: uuid.UUID) -> str:
    return f"platform:knowlab:{kind}:{grant_id}"


def expiry(now: datetime) -> datetime:
    return now + timedelta(days=GRANT_DAYS)


def state(status: str, expires_at: datetime | None, now: datetime) -> str:
    """授权现在的状态：生效了但过了期的算到期。"""
    if status == "active" and expires_at is not None and expires_at <= now:
        return "expired"
    return status


def live(status: str, expires_at: datetime | None, now: datetime) -> bool:
    return state(status, expires_at, now) in LIVE


class PlatformIncubationIn(Strict):
    """「请平台孵化」：确认框上作者点「同意」，带上平台发起 AI 一轮时的费用上限。"""

    agree: Literal[True]
    budget_limit: Decimal = Field(gt=0, le=MAX_BUDGET, decimal_places=2)


class PlatformTestIn(Strict):
    """「交平台测」：确认框上作者点「同意」，带上这一次测试的费用上限。"""

    agree: Literal[True]
    budget_limit: Decimal = Field(gt=0, le=MAX_BUDGET, decimal_places=2)


class LightAcceptanceIn(Strict):
    """轻受理：运营看一眼接不接；不接要说理由。"""

    accepted: bool
    reason: str = Field(default="", max_length=2000)
