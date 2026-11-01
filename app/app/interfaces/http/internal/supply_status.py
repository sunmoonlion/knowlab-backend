"""knowfeed 推需求状态（契约 `knowfeed-supply-status/v1`；SDD 0045 第 2.5 节）。

knowfeed 经服务关系 `supply-status`（`knowlab:supply-status`，只认 knowfeed）调这里：
测试里作者确认要补采的那张需求单状态变了，等着的用例马上重解析接着走（同页面刷新时
那一步）。
通知只当「去查一次」的信号，以单号查到的为准；重发、晚到都无害。只认集群内地址来的。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.knowbot_test_data import KnowbotTestData
from app.bootstrap.knowbots import knowbot_test_data
from app.domain.knowbots.supply_status import DemandStatusEventV1
from app.infrastructure.security.relation_auth import (
    ServiceCaller,
    require_supply_status_service,
)
from app.infrastructure.storage.postgres import get_db_session
from app.interfaces.http.internal.host_guard import internal_host_only

router = APIRouter(
    prefix="/internal/v1/knowfeed",
    tags=["内部-需求状态"],
    dependencies=[Depends(internal_host_only)],
)
NO_STORE = {"Cache-Control": "no-store"}


def test_data(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> KnowbotTestData | None:
    return knowbot_test_data(session)


@router.post("/demand-status", status_code=204)
async def demand_status(
    _: Annotated[ServiceCaller, Depends(require_supply_status_service)],
    body: DemandStatusEventV1,
    data: Annotated[KnowbotTestData | None, Depends(test_data)],
) -> Response:
    """一张需求单的状态变了。不是 knowlab 提的、没配 knowbase 的照样收下（204），
    不做事。"""
    if body.app == "knowlab" and data is not None:
        await data.on_demand_status(body.demand_id, body.owner)
    return Response(status_code=204, headers=NO_STORE)
