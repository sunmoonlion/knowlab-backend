"""knowbench 替用户提孵化请求（请专家方案设计第 4.9 节第 6 条、专家孵化方案第十三节）。

用户在 knowbench 让 AI 找专家、没有合适的，点了「孵化一个专家」：knowbench 经服务关系
`incubation-request`（`knowlab:incubation-request`）调这里，以这个用户的名义建一份种类为「问题」
的申请。同一个幂等钥匙重来回同一份（200），新建的回 201。只认集群内地址来的。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.knowbot_service import KnowbotCreationService
from app.bootstrap.knowbots import knowbot_service
from app.domain.knowbots.drafts import RuntimeIncubationIn
from app.infrastructure.security.relation_auth import (
    ServiceCaller,
    require_incubation_request_service,
)
from app.infrastructure.storage.postgres import get_db_session
from app.interfaces.http.internal.host_guard import internal_host_only

router = APIRouter(
    prefix="/internal/v1/knowlab",
    tags=["内部-孵化请求"],
    dependencies=[Depends(internal_host_only)],
)
NO_STORE = {"Cache-Control": "no-store"}


def service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> KnowbotCreationService:
    return knowbot_service(session)


@router.post("/incubation-requests")
async def incubation_request(
    caller: Annotated[ServiceCaller, Depends(require_incubation_request_service)],
    body: RuntimeIncubationIn,
    svc: Annotated[KnowbotCreationService, Depends(service)],
) -> JSONResponse:
    """以这个用户的名义建一份「问题」申请（自己来，只有他看得到，建了不花钱）。"""
    found, created = await svc.submit_from_runtime(
        body.owner_subject, body, runtime=caller.caller
    )
    return JSONResponse(
        status_code=201 if created else 200,
        content={
            "contract_version": 1,
            "request_id": str(found.id),
            "title": found.title,
            "created": created,
        },
        headers=NO_STORE,
    )
