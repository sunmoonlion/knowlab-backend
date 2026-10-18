"""运行方读用户数据的内部接口（SDD 0027 §3.5，契约 knowbot-data/v1）。

只接受运行方的服务身份（关系 read，`knowlab:read`），身份先于数据库会话核。
入口在 `/api/internal/v1/knowlab`（traefik service-access 的内部入口）。
运行方先按自己的委托核过，这里按 knowlab 的草稿与交付记录再核一次；不符一律 404。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.errors.exceptions import AppException
from app.application.services.knowbot_data_read import KnowbotDataReader
from app.bootstrap.knowbots import knowbot_data_reader
from app.domain.knowbots.data_read import (
    CellsRequestV1,
    ListRequestV1,
    ReadRequestV1,
    TableRequestV1,
)
from app.infrastructure.security.relation_auth import (
    ServiceCaller,
    require_read_service,
)
from app.infrastructure.storage.postgres import get_db_session
from app.interfaces.http.internal.host_guard import internal_host_only

NO_STORE = {"Cache-Control": "no-store"}

router = APIRouter(
    prefix="/internal/v1/knowlab",
    tags=["Knowbot data"],
    dependencies=[Depends(internal_host_only)],  # 公网地址来的当没有（审读第 1 条）
)
Caller = Annotated[ServiceCaller, Depends(require_read_service)]


def reader(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> KnowbotDataReader:
    return knowbot_data_reader(session)


Reader = Annotated[KnowbotDataReader, Depends(reader)]


def _invalid(exc: ValidationError) -> AppException:
    return AppException(
        code="knowbot_data_request_invalid",
        status_code=422,
        msg="not a knowbot-data/v1 request",
    )


@router.post("/knowbot-data/list")
async def list_data(caller: Caller, body: dict[str, Any], svc: Reader) -> JSONResponse:
    try:
        request = ListRequestV1.model_validate(body)
    except ValidationError as exc:
        raise _invalid(exc) from exc
    result = await svc.list(caller.caller, request)
    return JSONResponse(result.model_dump(mode="json"), headers=NO_STORE)


@router.post("/knowbot-data/read")
async def read_data(caller: Caller, body: dict[str, Any], svc: Reader) -> JSONResponse:
    try:
        request = ReadRequestV1.model_validate(body)
    except ValidationError as exc:
        raise _invalid(exc) from exc
    result = await svc.read(caller.caller, request)
    return JSONResponse(result.model_dump(mode="json"), headers=NO_STORE)


@router.post("/knowbot-data/table")
async def read_table(caller: Caller, body: dict[str, Any], svc: Reader) -> JSONResponse:
    try:
        request = TableRequestV1.model_validate(body)
    except ValidationError as exc:
        raise _invalid(exc) from exc
    result = await svc.table(caller.caller, request)
    return JSONResponse(result.model_dump(mode="json"), headers=NO_STORE)


@router.post("/knowbot-data/cells")
async def read_cells(caller: Caller, body: dict[str, Any], svc: Reader) -> JSONResponse:
    try:
        request = CellsRequestV1.model_validate(body)
    except ValidationError as exc:
        raise _invalid(exc) from exc
    result = await svc.cells(caller.caller, request)
    return JSONResponse(result.model_dump(mode="json"), headers=NO_STORE)
