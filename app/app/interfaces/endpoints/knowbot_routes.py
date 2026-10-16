"""分身的创建与测试接口（SDD 0027）。

网页端（用户会话）：提交想法、运行方、验收用例；看自己的提交、各版草稿与运行正确性
报告。管理端（管理员会话，我们的运营）：看全部提交、建草稿（过渡做法：上传清单）、
核规则出处、发起测试、刷新结果、重试交付、超时退回。身份依赖写在服务前面：先认人，
再开数据库会话。报告只有每条用例通过与否和出错处，不含分析内容（契约本身就不带）。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports.knowbots import DraftRecord, RequestRecord
from app.application.services.knowbot_service import KnowbotCreationService
from app.bootstrap.knowbots import knowbot_service, runtime_clients
from app.domain.knowbots.drafts import KnowbotRequestIn
from app.domain.security import Principal
from app.infrastructure.storage.postgres import get_db_session
from app.interfaces.http.middleware.auth import (
    get_web_current_user,
    require_knowlab_admin,
)

CONTRACT_VERSION = 1
NO_STORE = {"Cache-Control": "no-store"}

web_router = APIRouter(prefix="/web/v1", tags=["Knowbot creation"])
admin_router = APIRouter(prefix="/admin/v1", tags=["Knowbot creation (admin)"])


def service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> KnowbotCreationService:
    return knowbot_service(session)


Service = Annotated[KnowbotCreationService, Depends(service)]
User = Annotated[Principal, Depends(get_web_current_user)]
Admin = Annotated[Principal, Depends(require_knowlab_admin)]
ItemId = Annotated[str, Path(max_length=64)]


def _ok(body: dict[str, Any], status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"contract_version": CONTRACT_VERSION, **body},
        headers=NO_STORE,
    )


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _report(draft: DraftRecord) -> dict[str, Any] | None:
    if draft.report is None:
        return None
    return {
        "status": draft.report.get("status"),
        "cases": draft.report.get("cases", []),
        "cost": draft.report.get("cost"),
        "finished_at": draft.report.get("finished_at"),
    }


def _draft_for_user(draft: DraftRecord) -> dict[str, Any]:
    return {
        "draft_id": str(draft.id),
        "revision": draft.revision,
        "version": draft.version,
        "status": draft.status,
        "name": draft.manifest.get("name"),
        "report": _report(draft),
        "knowbot_id": (draft.delivery or {}).get("knowbot_id"),
    }


def _draft_for_admin(draft: DraftRecord, request: RequestRecord) -> dict[str, Any]:
    return {
        **_draft_for_user(draft),
        "request_id": str(request.id),
        "title": request.title,
        "owner_subject": request.owner_subject,
        "runtime": request.runtime,
        "rule_sources": draft.manifest.get("rule_sources", []),
        "rules_reviewed_by": draft.rules_reviewed_by,
        "rules_reviewed_at": _iso(draft.rules_reviewed_at),
        "run_id": draft.run_id,
        "run_attempts": draft.run_attempts,
        "run_started_at": _iso(draft.run_started_at),
        "last_error": draft.last_error,
    }


def _request_view(
    request: RequestRecord, drafts: Sequence[DraftRecord], *, admin: bool
) -> dict[str, Any]:
    view: dict[str, Any] = {
        "request_id": str(request.id),
        "title": request.title,
        "runtime": request.runtime,
        "created_at": _iso(request.created_at),
        "drafts": [_draft_for_user(d) for d in drafts],
    }
    if admin:
        view["owner_subject"] = request.owner_subject
    return view


# ---------- 网页端（用户） ----------
@web_router.get("/knowbot-runtimes")
async def runtimes(_: User) -> JSONResponse:
    """可选的运行方（knowlab 配置里登记了的）。"""
    return _ok({"runtimes": sorted(runtime_clients().registered())})


@web_router.post("/knowbot-requests")
async def submit(principal: User, body: KnowbotRequestIn, svc: Service) -> JSONResponse:
    request = await svc.submit(principal.subject, body)
    return _ok({"request_id": str(request.id)}, status_code=201)


@web_router.get("/knowbot-requests")
async def my_requests(principal: User, svc: Service) -> JSONResponse:
    """我在做的分身：只有自己的提交，各版草稿的状态与运行正确性报告。"""
    items = await svc.my_requests(principal.subject)
    return _ok({"requests": [_request_view(r, d, admin=False) for r, d in items]})


# ---------- 管理端（我们的运营） ----------
class DraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    manifest: dict[str, Any]


class StartTestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    budget_limit: str = Field(pattern=r"^[0-9]{1,6}(\.[0-9]{1,2})?$")
    timeout_seconds: int = Field(ge=60, le=86400)


@admin_router.get("/knowbot-requests")
async def all_requests(_: Admin, svc: Service) -> JSONResponse:
    items = await svc.all_requests()
    return _ok({"requests": [_request_view(r, d, admin=True) for r, d in items]})


@admin_router.get("/knowbot-requests/{request_id}")
async def request_detail(_: Admin, svc: Service, request_id: ItemId) -> JSONResponse:
    """建草稿要看的：用户的方法原文（六问或 skill）与验收用例。"""
    request = await svc.request_detail(request_id)
    return _ok(
        {
            "request_id": str(request.id),
            "title": request.title,
            "owner_subject": request.owner_subject,
            "runtime": request.runtime,
            "idea": request.idea,
            "cases": request.cases,
        }
    )


@admin_router.post("/knowbot-requests/{request_id}/drafts")
async def add_draft(
    _: Admin, body: DraftIn, svc: Service, request_id: ItemId
) -> JSONResponse:
    """过渡做法：管理员上传清单建草稿（等「后端自动生成」定了再接）。"""
    draft = await svc.add_draft(request_id, body.manifest)
    return _ok({"draft_id": str(draft.id), "version": draft.version}, status_code=201)


@admin_router.get("/knowbot-drafts")
async def drafts(
    _: Admin,
    svc: Service,
    status: Annotated[
        str | None,
        Query(pattern=r"^(pending_test|testing|passed|failed|delivered)$"),
    ] = None,
) -> JSONResponse:
    items = await svc.drafts(status)
    return _ok({"drafts": [_draft_for_admin(d, r) for d, r in items]})


@admin_router.post("/knowbot-drafts/{draft_id}/rules-review", status_code=204)
async def review_rules(admin: Admin, svc: Service, draft_id: ItemId) -> Response:
    """逐条核过规则出处（C-L1）；记下是谁核的。"""
    await svc.review_rules(draft_id, admin.subject)
    return Response(status_code=204, headers=NO_STORE)


@admin_router.post("/knowbot-drafts/{draft_id}/test")
async def start_test(
    _: Admin, body: StartTestIn, svc: Service, draft_id: ItemId
) -> JSONResponse:
    result = await svc.start_test(
        draft_id, budget_limit=body.budget_limit, timeout_seconds=body.timeout_seconds
    )
    return _ok({"run_id": result.run_id, "run_status": result.status})


@admin_router.post("/knowbot-drafts/{draft_id}/refresh")
async def refresh(_: Admin, svc: Service, draft_id: ItemId) -> JSONResponse:
    draft = await svc.refresh(draft_id)
    return _ok(_draft_for_user(draft) | {"last_error": draft.last_error})


@admin_router.post("/knowbot-drafts/{draft_id}/deliver")
async def deliver(_: Admin, svc: Service, draft_id: ItemId) -> JSONResponse:
    draft = await svc.deliver(draft_id)
    return _ok(_draft_for_user(draft))


@admin_router.post("/knowbot-drafts/{draft_id}/reset", status_code=204)
async def reset(_: Admin, svc: Service, draft_id: ItemId) -> Response:
    await svc.reset_stuck(draft_id)
    return Response(status_code=204, headers=NO_STORE)
