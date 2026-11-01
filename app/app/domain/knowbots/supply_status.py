"""knowfeed 推来的需求状态（契约 `knowfeed-supply-status/v1`，真源 knowfeed 应用仓
`contracts/supply-status/v1/`；SDD 0045 第 2.5 节「需求状态只由 knowfeed 通知」）。

knowlab 是接收方：测试里作者确认要补采的那几样交给 knowfeed 后，需求单状态一变，
knowfeed 经发件箱推一条来，knowlab 收到就让等着的那条用例马上重解析接着走（原来只在
页面刷新、后台每分钟一轮时去查）。通知可能重发、晚到，以单号查到的为准：这里只拿
它当「去查一次」的信号。模型照提供方逐字写（锁
contracts/knowfeed-supply-status-lock.json，锁测试核结构）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CONTRACT = "knowfeed-supply-status/v1"

ItemStatusV1 = Literal[
    "accepted",
    "fetching",
    "ready",
    "partial",
    "failed",
    "unsatisfiable",
    "cancelled",
    "timeout",
]


class DemandStatusItemV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(min_length=1, max_length=64)
    confirmation_item: str = Field(
        min_length=1, max_length=16, description="确认里的条目编号（c1、c2……）"
    )
    status: ItemStatusV1
    reason: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "结束的原因编码，如 no_channel、channel_has_none、readings_incomplete"
        ),
    )
    updated_at: datetime


class DemandStatusEventV1(BaseModel):
    """一张需求单的状态变了。"""

    model_config = ConfigDict(extra="forbid")

    contract: Literal["knowfeed-supply-status/v1"] = CONTRACT
    event_id: str = Field(min_length=1, max_length=64, description="每条通知不同")
    demand_id: str = Field(min_length=1, max_length=64)
    app: Literal["knowbench", "knowlab"] = Field(description="提这张单的应用")
    owner: str = Field(min_length=1, max_length=255, description="归属用户")
    task_ref: str | None = Field(default=None, max_length=255)
    status: ItemStatusV1 = Field(description="需求单的汇总状态")
    items: list[DemandStatusItemV1] = Field(min_length=1, max_length=50)
    occurred_at: datetime
