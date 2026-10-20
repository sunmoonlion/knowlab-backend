"""分身的创建与测试接口（SDD 0027）。

网页端（用户会话）：提交想法、运行方、验收用例；给自己的想法写补充方法（SDD 0035）；
上传、列出、删除自己的数据；看自己的
提交、各版草稿与运行正确性报告。管理端（管理员会话，我们的运营）：看全部提交、建草稿（过渡做法：上传清单）、
核规则出处、发起测试、刷新结果、重试交付、超时退回。身份依赖写在服务前面：先认人，
再开数据库会话。报告只有每条用例通过与否和出错处，不含分析内容（契约本身就不带）。

创意、分身、专家（SDD 0037；代码里仍叫 request、draft、knowbot）：每条用例
的成稿与各步交回物单独一个接口——管理端看全部（测试人员验证这一稿），web端
只看自己的（验收依据，C-L3）；不是自己的与没有的一样，都是 404。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Path, Query, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports.knowbots import (
    AmendmentRecord,
    DatasetRecord,
    DraftRecord,
    QuestionRecord,
    RequestRecord,
    TestOutputRecord,
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
from app.domain.knowbots.drafts import (
    CONSENT_VERSION,
    MAX_AMENDMENT_CHARS,
    MAX_DECLINE_CHARS,
    KnowbotRequestIn,
)
from app.domain.security import Principal
from app.infrastructure.storage.postgres import get_db_session
from app.interfaces.http.middleware.auth import (
    get_web_current_user,
    require_knowlab_admin,
    require_knowlab_release,
)

CONTRACT_VERSION = 1
log = logging.getLogger(__name__)
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
# 运营（operator）只能看、发起测试、答跑法问题；下面这几步只有 admin 能做
Releaser = Annotated[Principal, Depends(require_knowlab_release)]
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
        # 用户什么时候点的「要这一版」；还没要的是 None（所有者 2026-10-20「按丙来」）
        "accepted_at": (draft.delivery or {}).get("accepted_at"),
    }


def _draft_for_admin(
    draft: DraftRecord,
    request: RequestRecord,
    amendments: tuple[list[str], int] = ([], 0),
) -> dict[str, Any]:
    blocking, newer = amendments
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
        # 测试中：跑着的那条走到哪了（待办账 135）。只给管理端，用户的结果格式不变
        "progress": _while_testing(draft, "progress"),
        # 上一次刷新时运行方怎么说：已受理（还没开跑）、在跑、等人。
        # 开跑时间以运行方的为准，不拿我们提交的时间冒充
        # （账 136 第 ②：还没开跑却显示「已跑 x 分钟」）
        "run_status": _while_testing(draft, "status"),
        "run_running_since": _while_testing(draft, "started_at"),
        # 补充方法（SDD 0035）：核对出处前要处理的补充；这一稿建好后才提交的，下一稿处理
        "amendments_open": blocking,
        "amendments_newer": newer,
        # 交付时间（SDD 0037 专家栏）；之前交付的没有
        "delivered_at": (draft.delivery or {}).get("delivered_at"),
    }


def _while_testing(draft: DraftRecord, key: str) -> Any:
    return (draft.report or {}).get(key) if draft.status == "testing" else None


def _rule_sources_for_user(draft: DraftRecord) -> list[dict[str, Any]]:
    """规则出处给用户看的那两项：哪条规则、出自你的哪句话。

    SDD 0037「我的话用在了哪」。
    """
    return [
        {"rule": str(s.get("rule") or ""), "ref": str(s.get("ref") or "")}
        for s in draft.manifest.get("rule_sources", [])
        if isinstance(s, dict)
    ]


def _outputs_view(
    draft: DraftRecord, found: Sequence[TestOutputRecord] | None
) -> dict[str, Any]:
    return {
        "draft_id": str(draft.id),
        "run_id": draft.run_id,
        # 创意是在旧说明下同意的：不取、不显示，用户同意新说明后才有（审读 10-19）
        "consent_needed": found is None,
        "cases": [
            {
                "case_id": o.case_id,
                "fetched_at": _iso(o.fetched_at),
                "note": o.note,
                "outputs": o.outputs,
            }
            for o in found or ()
        ],
    }


def _knowbot_view(draft: DraftRecord, request: RequestRecord) -> dict[str, Any]:
    delivery = draft.delivery or {}
    return {
        "knowbot_id": delivery.get("knowbot_id"),
        "name": draft.manifest.get("name"),
        "version": draft.version,
        "revision": draft.revision,
        "draft_id": str(draft.id),
        "request_id": str(request.id),
        "title": request.title,
        "delivered_at": delivery.get("delivered_at"),
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
        # 是哪个分身的（用户自己的分身，web端按它对上，不按同名标题猜；SDD 0037）
        "draft_id": str(draft.id),
    }
    if admin:
        view |= {
            "route": q.route,
            "route_reason": q.route_reason,
            "owner_subject": request.owner_subject,
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
        "route_reason": q.route_reason,
        "answered_at": _iso(q.answered_at),
    }


def _amendment_view(
    item: tuple[AmendmentRecord, str, int | None], *, admin: bool
) -> dict[str, Any]:
    amendment, status, revision = item
    view: dict[str, Any] = {
        "amendment_id": str(amendment.id),
        "text": amendment.text,
        "created_at": _iso(amendment.created_at),
        "status": status,
        "used_in_revision": revision,
        "declined_reason": amendment.declined_reason,
        "declined_at": _iso(amendment.declined_at),
    }
    if admin:
        view["declined_by"] = amendment.declined_by
    return view


class AnswerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str | None = Field(default=None, pattern=r"^(approve|deny)$")
    option_id: str | None = Field(default=None, max_length=64)
    text: str | None = Field(default=None, max_length=4000)

    def given(self) -> AnswerIn:
        return AnswerIn(
            decision=self.decision, option_id=self.option_id, text=self.text
        )


class TakeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=200)


class AmendBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=MAX_AMENDMENT_CHARS)


class DeclineBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=MAX_DECLINE_CHARS)


def _request_view(
    request: RequestRecord,
    drafts: Sequence[DraftRecord],
    amendments: Sequence[tuple[AmendmentRecord, str, int | None]],
    *,
    admin: bool,
) -> dict[str, Any]:
    view: dict[str, Any] = {
        "request_id": str(request.id),
        "title": request.title,
        "runtime": request.runtime,
        "created_at": _iso(request.created_at),
        "drafts": [_draft_for_user(d) for d in drafts],
        "amendments": [_amendment_view(a, admin=admin) for a in amendments],
        # 同意的是第几版测试数据说明；低于现行版的，产出不取不显示（SDD 0037）
        "consent_version": request.consent_version,
        "consent_current": CONSENT_VERSION,
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
    return _ok(
        {
            "requests": [
                _request_view(r, d, await svc.amendments(r.id, d), admin=False)
                for r, d in items
            ]
        }
    )


@web_router.get("/knowbot-requests/{request_id}")
async def my_request(principal: User, svc: Service, request_id: ItemId) -> JSONResponse:
    """自己的一个创意：方法原文、验收用例、它的分身、补充方法（SDD 0037）。"""
    request, drafts = await svc.my_request(principal.subject, request_id)
    return _ok(
        _request_view(
            request, drafts, await svc.amendments(request.id, drafts), admin=False
        )
        | {"idea": request.idea, "cases": request.cases}
    )


@web_router.get("/knowbot-drafts/{draft_id}")
async def my_draft(principal: User, svc: Service, draft_id: ItemId) -> JSONResponse:
    """自己的一个分身：状态、测试结果、规则出处（只给规则与出自哪句话）。"""
    draft, request = await svc.my_draft(principal.subject, draft_id)
    return _ok(
        _draft_for_user(draft)
        | {
            "request_id": str(request.id),
            "title": request.title,
            "rule_sources": _rule_sources_for_user(draft),
        }
    )


@web_router.get("/knowbot-drafts/{draft_id}/outputs")
async def my_draft_outputs(
    principal: User, svc: Service, draft_id: ItemId
) -> JSONResponse:
    """自己的分身最近一次测试每条用例的成稿与各步交回物（验收依据，C-L3）。"""
    draft, request = await svc.my_draft(principal.subject, draft_id)
    return _ok(_outputs_view(draft, await svc.test_outputs(draft, request)))


@web_router.post("/knowbot-drafts/{draft_id}/accept")
async def accept(principal: User, svc: Service, draft_id: ItemId) -> JSONResponse:
    """测试通过的分身，用户看过成稿说要这一版：交付成专家。交付失败报错，再点一次即可。"""
    draft = await svc.accept(principal.subject, draft_id)
    return _ok(_draft_for_user(draft))


@web_router.post("/knowbot-requests/{request_id}/consent", status_code=204)
async def consent(principal: User, svc: Service, request_id: ItemId) -> Response:
    """同意新一版测试数据说明：之后测试的成稿与各步结果才取、才显示（SDD 0037）。"""
    await svc.consent(principal.subject, request_id)
    return Response(status_code=204, headers=NO_STORE)


@web_router.get("/knowbots")
async def my_knowbots(principal: User, svc: Service) -> JSONResponse:
    """做出来的专家：交付了的各版（列表直接写专家名，不叫「我的专家」）。"""
    return _ok(
        {
            "knowbots": [
                _knowbot_view(d, r) for d, r in await svc.my_knowbots(principal.subject)
            ]
        }
    )


@web_router.post("/knowbot-requests/{request_id}/amendments")
async def amend(
    principal: User, body: AmendBody, svc: Service, request_id: ItemId
) -> JSONResponse:
    """给自己的想法写一条补充方法：哪一步、改成什么、为什么。

    写下后不能改（SDD 0035）。
    """
    found = await svc.amend(principal.subject, request_id, body.text)
    return _ok({"amendment_id": str(found.id)}, status_code=201)


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
    return _ok(
        {
            "requests": [
                _request_view(r, d, await svc.amendments(r.id, d), admin=True)
                for r, d in items
            ]
        }
    )


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
    drafts = await svc.request_drafts(request.id)
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
            # 用户写的补充方法：用进出处（ref 写 amendment:<id>），或写理由不照做
            "amendments": [
                _amendment_view(a, admin=True)
                for a in await svc.amendments(request.id, drafts)
            ],
            "consent_version": request.consent_version,
            "consent_current": CONSENT_VERSION,
        }
    )


@admin_router.post("/knowbot-amendments/{amendment_id}/decline", status_code=204)
async def decline_amendment(
    admin: Releaser, body: DeclineBody, svc: Service, amendment_id: ItemId
) -> Response:
    """照不了的补充写理由不照做，用户看得到（SDD 0035）。"""
    await svc.decline_amendment(amendment_id, body.reason, admin.subject)
    return Response(status_code=204, headers=NO_STORE)


@admin_router.post("/knowbot-requests/{request_id}/drafts")
async def add_draft(
    _: Releaser, body: DraftIn, svc: Service, request_id: ItemId
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
    """工具审批，和写了理由改派给我们的跑法问题，由我们答；别的归用户（C-L1）。"""
    found = await questions.answer_as_operator(question_id, body.given())
    return _ok({"question_id": str(found.id), "sent": found.sent_at is not None})


@admin_router.post("/knowbot-questions/{question_id}/take", status_code=204)
async def question_take(
    _: Admin, body: TakeBody, questions: Questions, question_id: ItemId
) -> Response:
    """运营写一句理由，把一条归用户的问题改派给自己（只限跑法问题）。"""
    await questions.take(question_id, body.reason)
    return Response(status_code=204, headers=NO_STORE)


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
    return _ok(
        {
            "drafts": [
                _draft_for_admin(d, r, await svc.amendments_to_handle(d))
                for d, r in items
            ]
        }
    )


@admin_router.get("/knowbot-drafts/{draft_id}/manifest")
async def draft_manifest(admin: Admin, svc: Service, draft_id: ItemId) -> JSONResponse:
    """这一稿的清单原文：分身页对照材料里只读、可复制（SDD 0037）。"""
    found = await svc.draft(draft_id)
    log.info("knowbot manifest read by=%s draft=%s", admin.subject, found.id)
    return _ok({"draft_id": str(found.id), "manifest": found.manifest})


@admin_router.get("/knowbot-drafts/{draft_id}/outputs")
async def draft_outputs(admin: Admin, svc: Service, draft_id: ItemId) -> JSONResponse:
    """这一稿最近一次测试每条用例的成稿与各步交回物。

    测试人员验证这一稿用（SDD 0037）。
    """
    found, request = await svc.draft_with_request(draft_id)
    # 产出是用户的分析内容：谁、哪一稿、何时读的留一行，不记正文（审读 10-19）
    log.info("knowbot outputs read by=%s draft=%s", admin.subject, found.id)
    return _ok(_outputs_view(found, await svc.test_outputs(found, request)))


@admin_router.post("/knowbot-drafts/{draft_id}/rules-review", status_code=204)
async def review_rules(admin: Releaser, svc: Service, draft_id: ItemId) -> Response:
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
async def deliver(_: Releaser, svc: Service, draft_id: ItemId) -> JSONResponse:
    """重试交付：用户已经要了、上次交 knowmart 没成功的。用户没要的不交（409）。"""
    draft = await svc.deliver(draft_id)
    return _ok(_draft_for_user(draft))


@admin_router.post("/knowbot-drafts/{draft_id}/reset", status_code=204)
async def reset(_: Releaser, svc: Service, draft_id: ItemId) -> Response:
    await svc.reset_stuck(draft_id)
    return Response(status_code=204, headers=NO_STORE)
