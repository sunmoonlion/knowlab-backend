"""分身的创建与测试接口（SDD 0027）。

网页端（用户会话）：提交想法、运行方、验收用例；上传、列出、删除自己的数据；看自己的
提交、各版草稿与运行正确性报告。管理端（管理员会话，我们的运营）：看全部提交、建草稿（过渡做法：上传清单）、
核规则出处、发起测试、刷新结果、重试交付、超时退回。身份依赖写在服务前面：先认人，
再开数据库会话。报告只有每条用例通过与否和出错处，不含分析内容（契约本身就不带）。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Path, Query, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports.knowbots import (
    DatasetRecord,
    DraftRecord,
    QuestionRecord,
    RequestRecord,
)
from app.application.services.knowbot_data import KnowbotDataService
from app.application.services.knowbot_questions import AnswerIn, KnowbotQuestionService
from app.application.services.knowbot_service import KnowbotCreationService
from app.bootstrap.knowbots import (
    knowbot_data_service,
    knowbot_question_service,
    knowbot_service,
    runtime_clients,
)
from app.domain.knowbots.datasets import MAX_FILE_BYTES, QUOTA_BYTES
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


def data_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> KnowbotDataService:
    return knowbot_data_service(session)


def question_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> KnowbotQuestionService:
    return knowbot_question_service(session)


Service = Annotated[KnowbotCreationService, Depends(service)]
Questions = Annotated[KnowbotQuestionService, Depends(question_service)]
DataService = Annotated[KnowbotDataService, Depends(data_service)]
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


def _dataset_view(found: DatasetRecord) -> dict[str, Any]:
    return {
        "dataset_id": str(found.id),
        "name": found.name,
        "version": found.version,
        "content_type": found.content_type,
        "size_bytes": found.size_bytes,
        "sha256": found.sha256,
        "created_at": _iso(found.created_at),
    }


def _question_view(
    q: QuestionRecord, draft: DraftRecord, request: RequestRecord, *, admin: bool
) -> dict[str, Any]:
    view: dict[str, Any] = {
        "question_id": str(q.id),
        "kind": q.kind,
        "text": q.text,
        "options": q.options,
        "allow_free_text": q.allow_free_text,
        "case_id": q.case_id,
        "step_id": q.step_id,
        "asked_at": q.asked_at,
        "title": request.title,
        "version": draft.version,
        "revision": draft.revision,
    }
    if admin:
        view |= {
            "route": q.route,
            "owner_subject": request.owner_subject,
            "draft_id": str(draft.id),
            "last_error": q.last_error,
        }
    return view


def _answered_view(q: QuestionRecord) -> dict[str, Any]:
    return {
        "question_id": str(q.id),
        "kind": q.kind,
        "case_id": q.case_id,
        "key": q.key,
        "text": q.text,
        "answer": q.answer,
        "answered_by": q.answered_by,
        "answered_at": _iso(q.answered_at),
    }


class AnswerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str | None = Field(default=None, pattern=r"^(approve|deny)$")
    option_id: str | None = Field(default=None, max_length=64)
    text: str | None = Field(default=None, max_length=4000)

    def given(self) -> AnswerIn:
        return AnswerIn(
            decision=self.decision, option_id=self.option_id, text=self.text
        )


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


@web_router.get("/knowbot-datasets")
async def my_datasets(principal: User, data: DataService) -> JSONResponse:
    """我的数据：只有自己的、没删的，和用了多少。"""
    found, used = await data.mine(principal.subject)
    return _ok(
        {
            "datasets": [_dataset_view(d) for d in found],
            "used_bytes": used,
            "quota_bytes": QUOTA_BYTES,
            "max_file_bytes": MAX_FILE_BYTES,
        }
    )


@web_router.post("/knowbot-datasets")
async def upload_dataset(
    principal: User,
    data: DataService,
    file: Annotated[UploadFile, File()],
) -> JSONResponse:
    """上传一份数据。只收 PDF、Excel、CSV、文本；单个不超过 50 MB，每人不超过 1 GB。"""
    body = await file.read(MAX_FILE_BYTES + 1)
    found = await data.upload(principal.subject, file.filename, body)
    return _ok({"dataset": _dataset_view(found)}, status_code=201)


@web_router.delete("/knowbot-datasets/{dataset_id}", status_code=204)
async def delete_dataset(
    principal: User, data: DataService, dataset_id: ItemId
) -> Response:
    """删一份数据。在做的或已交付的分身绑着的不能删（先停用那一版）。"""
    await data.delete(principal.subject, dataset_id)
    return Response(status_code=204, headers=NO_STORE)


@web_router.get("/knowbot-questions")
async def my_questions(principal: User, questions: Questions) -> JSONResponse:
    """测试中转给我回答的问题（投资判断只能我自己答，C-L1）。"""
    items = await questions.user_queue(principal.subject)
    return _ok(
        {"questions": [_question_view(q, d, r, admin=False) for q, d, r in items]}
    )


@web_router.post("/knowbot-questions/{question_id}/answer")
async def answer_my_question(
    principal: User, body: AnswerBody, questions: Questions, question_id: ItemId
) -> JSONResponse:
    found = await questions.answer_as_user(principal.subject, question_id, body.given())
    return _ok({"question_id": str(found.id), "sent": found.sent_at is not None})


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
async def request_detail(
    _: Admin,
    svc: Service,
    data: DataService,
    questions: Questions,
    request_id: ItemId,
) -> JSONResponse:
    """建草稿要看的：用户的方法原文（六问或 skill）、验收用例、他的数据（只有元数据，
    清单的 data_bindings 照这里写：数据 id + 版本 + sha256）。"""
    request = await svc.request_detail(request_id)
    found, _used = await data.mine(request.owner_subject)
    return _ok(
        {
            "request_id": str(request.id),
            "title": request.title,
            "owner_subject": request.owner_subject,
            "runtime": request.runtime,
            "idea": request.idea,
            "cases": request.cases,
            "datasets": [_dataset_view(d) for d in found],
            # 测试中答过的：用户确认的判断写进下一版草稿的规则出处（user_confirmed）
            "answered": [
                _answered_view(q) for q in await questions.answered(request.id)
            ],
        }
    )


@admin_router.post("/knowbot-requests/{request_id}/drafts")
async def add_draft(
    _: Admin, body: DraftIn, svc: Service, request_id: ItemId
) -> JSONResponse:
    """过渡做法：管理员上传清单建草稿（等「后端自动生成」定了再接）。"""
    draft = await svc.add_draft(request_id, body.manifest)
    return _ok({"draft_id": str(draft.id), "version": draft.version}, status_code=201)


@admin_router.get("/knowbot-questions")
async def open_questions(_: Admin, questions: Questions) -> JSONResponse:
    """测试中还没回答的问题：在我们这里的，和转给用户、用户还没答的。"""
    items = await questions.operator_queue()
    return _ok(
        {"questions": [_question_view(q, d, r, admin=True) for q, d, r in items]}
    )


@admin_router.post("/knowbot-questions/{question_id}/answer")
async def answer_question(
    _: Admin, body: AnswerBody, questions: Questions, question_id: ItemId
) -> JSONResponse:
    """工具审批与跑法层面的问题由我们答；投资判断要转给用户（C-L1）。"""
    found = await questions.answer_as_operator(question_id, body.given())
    return _ok({"question_id": str(found.id), "sent": found.sent_at is not None})


@admin_router.post("/knowbot-questions/{question_id}/to-user", status_code=204)
async def question_to_user(
    _: Admin, questions: Questions, question_id: ItemId
) -> Response:
    await questions.to_user(question_id)
    return Response(status_code=204, headers=NO_STORE)


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
