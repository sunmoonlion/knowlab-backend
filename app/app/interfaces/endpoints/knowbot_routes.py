"""创意到专家的接口（SDD 0027、0037）。代码里仍叫 request、draft、knowbot。

web端（用户会话），左边栏「申请 · 审核 · 专家」：申请（出创意 v1）、改创意（出新版本）、
审核待办箱（转给自己的问题、请自己审核的准专家、交回等自己定的专家）、专家（交回了的各
版：要不要、停用）；知识库照旧。不是自己的与没有的一样，都是 404。

管理端（管理员会话，我们的运营；admin、operator 现在能做的一样），左边栏
「受理 · 孵化 · 测试 · 交付」：每栏的大类列表与「等我们」的数；受理（写受理单）、孵化
（选受理了的版本贴清单、请用户审核、核出处）、测试（发起 = 新编号、刷新、判定卡住 = 平台
出错）、交付（交回、运营审核、交 knowmart 与重试、全部专家、停用）。补充方法只读。

点任何一条进同一个名称的详情页：顶上一行摘要，下面按时间排的记录。身份依赖写在服务前面：
先认人，再开数据库会话。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Path, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports.knowbots import DraftRecord
from app.application.services.knowbot_board import (
    KnowbotBoard,
    Life,
    acceptance_items,
    admin_counts,
    application_category,
    delivery_items,
    inbox_items,
    incubation_items,
    lab_test_items,
    timeline,
    user_expert_items,
)
from app.application.services.knowbot_data import KnowbotDataService
from app.application.services.knowbot_experts import KnowbotExpertService, stage_of
from app.application.services.knowbot_questions import AnswerIn, KnowbotQuestionService
from app.application.services.knowbot_service import KnowbotCreationService
from app.bootstrap.knowbots import (
    knowbot_board,
    knowbot_data_service,
    knowbot_expert_service,
    knowbot_question_service,
    knowbot_service,
    runtime_clients,
)
from app.domain.knowbots.datasets import MAX_FILE_BYTES, QUOTA_BYTES
from app.domain.knowbots.drafts import KnowbotRequestIn
from app.domain.knowbots.lifecycle import (
    AcceptanceIn,
    ExpertDecisionIn,
    ExpertReviewIn,
    RevisionIn,
    StopIn,
    UserReviewIn,
)
from app.domain.security import Principal
from app.infrastructure.storage.postgres import get_db_session
from app.interfaces.endpoints.knowbot_views import (
    amendment_view,
    answered_view,
    dataset_view,
    draft_view,
    expert_view,
    life_view,
    outputs_view,
    question_view,
    test_view,
    version_view,
)
from app.interfaces.http.middleware.auth import (
    get_web_current_user,
    get_web_knowbase_token_provider,
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


def expert_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> KnowbotExpertService:
    return knowbot_expert_service(session)


def board_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> KnowbotBoard:
    return knowbot_board(session)


def data_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> KnowbotDataService:
    return knowbot_data_service(session)


def question_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> KnowbotQuestionService:
    return knowbot_question_service(session)


Service = Annotated[KnowbotCreationService, Depends(service)]
Experts = Annotated[KnowbotExpertService, Depends(expert_service)]
Board = Annotated[KnowbotBoard, Depends(board_service)]
Questions = Annotated[KnowbotQuestionService, Depends(question_service)]
DataService = Annotated[KnowbotDataService, Depends(data_service)]
# knowbase 用户令牌：真要动 knowbase 时才换（SDD 0042 第七节）
KnowbaseToken = Annotated[
    Callable[[], Awaitable[str]], Depends(get_web_knowbase_token_provider)
]
User = Annotated[Principal, Depends(get_web_current_user)]
Admin = Annotated[Principal, Depends(require_knowlab_admin)]
# 受理、孵化、交付、判定卡住这几步多要一项 knowlab:release（现在 operator 也有）
Releaser = Annotated[Principal, Depends(require_knowlab_release)]
ItemId = Annotated[str, Path(max_length=64)]
VersionNo = Annotated[int, Path(ge=1, le=100000)]


def _ok(body: dict[str, Any], status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"contract_version": CONTRACT_VERSION, **body},
        headers=NO_STORE,
    )


def _brief(life: Life, *, admin: bool) -> dict[str, Any]:
    """列表里每一条都带上它属于哪个名称。"""
    brief: dict[str, Any] = {
        "request_id": str(life.request.id),
        "title": life.request.title,
    }
    if admin:
        brief["owner_subject"] = life.request.owner_subject
    return brief


async def _detail(
    life: Life, svc: KnowbotCreationService, *, admin: bool
) -> dict[str, Any]:
    return life_view(life, admin=admin) | {
        "versions": [version_view(v, life, admin=admin) for v in life.versions],
        "drafts": [draft_view(d, life, admin=admin) for d in life.drafts],
        "tests": [test_view(t, life, admin=admin) for t in life.tests],
        "experts": [
            expert_view(e, life, admin=admin)
            for e in life.experts
            if admin or e.returned_at is not None
        ],
        "timeline": timeline(life, for_user=not admin),
        # 旧的补充方法（SDD 0035，已取消）：只读，出处照旧可查
        "amendments": [
            amendment_view(a, admin=admin)
            for a in await svc.amendments(life.request.id, life.drafts)
        ],
    }


# ---------- web端（用户） ----------
class ReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    note: str = Field(default="", max_length=2000)


@web_router.get("/knowbot-runtimes")
async def runtimes(_: User) -> JSONResponse:
    """可选的运行方（knowlab 配置里登记了的）。"""
    return _ok({"runtimes": sorted(runtime_clients().registered())})


@web_router.post("/knowbot-requests")
async def submit(principal: User, body: KnowbotRequestIn, svc: Service) -> JSONResponse:
    """申请：出创意 v1。"""
    request = await svc.submit(principal.subject, body)
    return _ok({"request_id": str(request.id), "version": 1}, status_code=201)


@web_router.post("/knowbot-requests/{request_id}/versions")
async def revise(
    principal: User, body: RevisionIn, svc: Service, request_id: ItemId
) -> JSONResponse:
    """改创意：出一个新版本（名称不变，旧版本不变）。"""
    found = await svc.revise(principal.subject, request_id, body)
    return _ok(
        {"request_id": str(found.request_id), "version": found.version},
        status_code=201,
    )


@web_router.get("/knowbot-requests/{request_id}")
async def my_request(
    principal: User, svc: Service, board: Board, request_id: ItemId
) -> JSONResponse:
    """自己的一个名称的详情页：各版创意与受理、准专家、测试、交回了的专家、记录。"""
    life = await board.life(request_id, principal.subject)
    return _ok(await _detail(life, svc, admin=False))


@web_router.post("/knowbot-requests/{request_id}/consent", status_code=204)
async def consent(principal: User, svc: Service, request_id: ItemId) -> Response:
    """同意新一版测试数据说明：之后测试的成稿与各步结果才取、才显示（SDD 0037）。"""
    await svc.consent(principal.subject, request_id)
    return Response(status_code=204, headers=NO_STORE)


@web_router.get("/knowbot-counts")
async def my_counts(
    principal: User, board: Board, questions: Questions
) -> JSONResponse:
    """左边栏旁的数：审核数「要你做的」，专家数「新交回」。"""
    lives = await board.lives(principal.subject)
    asked = [q for q, _, _ in await questions.user_queue(principal.subject)]
    inbox = inbox_items(lives, asked)
    return _ok(
        {
            "inbox": sum(state == "todo" for state, _, _, _ in inbox),
            "experts_new": sum(c == "new" for c, _, _ in user_expert_items(lives)),
        }
    )


@web_router.get("/knowbot-board/applications")
async def my_applications(principal: User, board: Board) -> JSONResponse:
    """申请栏：在做 / 已出专家 / 没受理。"""
    lives = await board.lives(principal.subject)
    return _ok(
        {
            "items": [
                life_view(life, admin=False) | {"category": application_category(life)}
                for life in lives
            ]
        }
    )


@web_router.get("/knowbot-board/inbox")
async def my_inbox(principal: User, board: Board, questions: Questions) -> JSONResponse:
    """审核栏（用户的待办箱）：要你做的（todo）与你答过、定过的（done）。"""
    lives = await board.lives(principal.subject)
    asked = [q for q, _, _ in await questions.user_queue(principal.subject)]
    items = []
    for state, kind, life, item in inbox_items(lives, asked):
        if kind == "question":
            draft = life.draft(item.draft_id)
            assert draft is not None
            view = question_view(item, draft, life.request, admin=False)
        elif kind == "draft":
            view = draft_view(item, life, admin=False)
        else:
            view = expert_view(item, life, admin=False)
        # type：问题 / 准专家 / 专家（问题自己的 kind 是审批、开步前提问、判断）
        items.append(view | _brief(life, admin=False) | {"state": state, "type": kind})
    answered = [
        answered_view(q, admin=False)
        for q in await questions.answered_by_user(principal.subject)
    ]
    return _ok({"items": items, "answered": answered})


@web_router.get("/knowbot-board/experts")
async def my_experts(principal: User, board: Board) -> JSONResponse:
    """专家栏：交回了的各版。新交回 / 你已同意等我们审核 / 能用 / 停用了 / 没上架。"""
    lives = await board.lives(principal.subject)
    return _ok(
        {
            "items": [
                _brief(life, admin=False)
                | {"category": category}
                | expert_view(expert, life, admin=False)
                for category, life, expert in user_expert_items(lives)
            ]
        }
    )


@web_router.post("/knowbot-drafts/{draft_id}/review")
async def review_draft(
    principal: User, body: ReviewBody, svc: Service, draft_id: ItemId
) -> JSONResponse:
    """审核一位准专家：行 / 不行（不行要说哪里不对）。"""
    found = await svc.answer_user_review(
        principal.subject, draft_id, UserReviewIn(ok=body.ok, note=body.note)
    )
    return _ok({"draft_id": str(found.id), "status": found.status})


@web_router.get("/knowbot-tests/{test_id}/outputs")
async def my_test_outputs(
    principal: User, svc: Service, test_id: ItemId
) -> JSONResponse:
    """自己的一次测试每条用例的成稿与各步交回物（验收依据，C-L3）。"""
    test, request = await svc.my_test(principal.subject, test_id)
    return _ok(outputs_view(test, await svc.test_outputs(test, request), admin=False))


@web_router.post("/knowbot-experts/{expert_id}/decision")
async def decide(
    principal: User, body: ExpertDecisionIn, experts: Experts, expert_id: ItemId
) -> JSONResponse:
    """用户同意门：要这一版 / 不要。要了的等我们审核，审过才上架。"""
    found = await experts.decide(principal.subject, expert_id, body)
    return _ok({"expert_id": str(found.id), "stage": stage_of(found)})


@web_router.post("/knowbot-experts/{expert_id}/stop")
async def stop_mine(
    principal: User, body: StopIn, experts: Experts, expert_id: ItemId
) -> JSONResponse:
    """停用自己上架了的这一版：之后请不了；已经请去在跑的不受影响。"""
    found = await experts.stop(
        expert_id, body.reason, principal.subject, owner_subject=principal.subject
    )
    return _ok({"expert_id": str(found.id), "stage": stage_of(found)})


@web_router.get("/knowbot-datasets")
async def my_datasets(
    principal: User, data: DataService, user_token: KnowbaseToken
) -> JSONResponse:
    """知识库：只有自己的、没删的，和用了多少。

    顺带让测试同意跟上（SDD 0042 第七节）。
    """
    await data.ensure_consent(principal.subject, user_token)
    found, used = await data.mine(principal.subject)
    return _ok(
        {
            "datasets": [dataset_view(d) for d in found],
            "used_bytes": used,
            "quota_bytes": QUOTA_BYTES,
            "max_file_bytes": MAX_FILE_BYTES,
        }
    )


@web_router.post("/knowbot-datasets")
async def upload_dataset(
    principal: User,
    data: DataService,
    user_token: KnowbaseToken,
    file: Annotated[UploadFile, File()],
) -> JSONResponse:
    """上传一份数据。只收 PDF、Excel、CSV、文本。

    单个不超过 50 MB，每人不超过 1 GB、1000 份。
    """
    body = await file.read(MAX_FILE_BYTES + 1)
    found = await data.upload(principal.subject, file.filename, body, user_token)
    return _ok({"dataset": dataset_view(found)}, status_code=201)


@web_router.delete("/knowbot-datasets/{dataset_id}", status_code=204)
async def delete_dataset(
    principal: User, data: DataService, user_token: KnowbaseToken, dataset_id: ItemId
) -> Response:
    """删一份数据。待测、测试中的准专家或还没走完的专家绑着的不能删（先停用那一版）。"""
    await data.delete(principal.subject, dataset_id, user_token)
    return Response(status_code=204, headers=NO_STORE)


@web_router.get("/knowbot-questions")
async def my_questions(principal: User, questions: Questions) -> JSONResponse:
    """测试中转给我回答的问题（投资判断只能我自己答，C-L1）。"""
    items = await questions.user_queue(principal.subject)
    return _ok(
        {"questions": [question_view(q, d, r, admin=False) for q, d, r in items]}
    )


class AnswerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str | None = Field(default=None, pattern=r"^(approve|deny)$")
    option_id: str | None = Field(default=None, max_length=64)
    text: str | None = Field(default=None, max_length=4000)

    def given(self) -> AnswerIn:
        return AnswerIn(
            decision=self.decision, option_id=self.option_id, text=self.text
        )


@web_router.post("/knowbot-questions/{question_id}/answer")
async def answer_my_question(
    principal: User, body: AnswerBody, questions: Questions, question_id: ItemId
) -> JSONResponse:
    found = await questions.answer_as_user(principal.subject, question_id, body.given())
    return _ok({"question_id": str(found.id), "sent": found.sent_at is not None})


# ---------- 管理端（我们的运营） ----------
class TakeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=200)


class DraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    manifest: dict[str, Any]
    # 出自创意第几版；不给就是最新受理了的
    request_version: int | None = Field(default=None, ge=1, le=100000)


class StartTestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    budget_limit: str = Field(pattern=r"^[0-9]{1,6}(\.[0-9]{1,2})?$")
    timeout_seconds: int = Field(ge=60, le=86400)


Column = Literal["acceptance", "incubation", "tests", "delivery"]


@admin_router.get("/knowbot-counts")
async def counts(_: Admin, board: Board) -> JSONResponse:
    """左边栏旁的数：每栏「等我们」的条数。"""
    return _ok({"counts": admin_counts(await board.lives())})


@admin_router.get("/knowbot-board/{column}")
async def board_column(_: Admin, board: Board, column: Column) -> JSONResponse:
    """一栏的全部条目，每条带大类（category）；两端按大类分组。"""
    lives = await board.lives()
    items: list[dict[str, Any]] = []
    if column == "acceptance":
        for category, life, version in acceptance_items(lives):
            found = next(v for v in life.versions if v.version == version)
            items.append(
                _brief(life, admin=True)
                | {"category": category}
                | version_view(found, life, admin=True)
            )
    elif column == "incubation":
        for category, life, draft in incubation_items(lives):
            items.append(
                _brief(life, admin=True)
                | {
                    "category": category,
                    "latest_version": life.request.latest_version,
                    "latest_accepted": life.latest_accepted(),
                    "draft": None
                    if draft is None
                    else draft_view(draft, life, admin=True),
                }
            )
    elif column == "tests":
        for category, life, item in lab_test_items(lives):
            view = (
                {"draft": draft_view(item, life, admin=True), "test": None}
                if isinstance(item, DraftRecord)
                else {"draft": None, "test": test_view(item, life, admin=True)}
            )
            items.append(_brief(life, admin=True) | {"category": category} | view)
    else:
        for category, life, expert in delivery_items(lives):
            items.append(
                _brief(life, admin=True)
                | {"category": category}
                | expert_view(expert, life, admin=True)
            )
    return _ok({"column": column, "items": items})


@admin_router.get("/knowbot-requests/{request_id}")
async def request_detail(
    _: Admin,
    svc: Service,
    board: Board,
    data: DataService,
    questions: Questions,
    request_id: ItemId,
) -> JSONResponse:
    """一个名称的详情页（管理端看全的）；孵化要看的：用户的数据（只有元数据，清单的
    data_bindings 照这里写：数据 id + 版本 + sha256）、测试中答过的问题。"""
    life = await board.life(request_id)
    found, _used = await data.mine(life.request.owner_subject)
    return _ok(
        await _detail(life, svc, admin=True)
        | {
            "datasets": [dataset_view(d) for d in found],
            # 测试中答过的：用户确认的判断写进下一位准专家的规则出处（user_confirmed）
            "answered": [
                answered_view(q, admin=True)
                for q in await questions.answered(life.request.id)
            ],
        }
    )


@admin_router.post("/knowbot-requests/{request_id}/versions/{version}/acceptance")
async def accept(
    admin: Releaser,
    body: AcceptanceIn,
    svc: Service,
    request_id: ItemId,
    version: VersionNo,
) -> JSONResponse:
    """给创意的这一版写受理单：三样逐条记；不受理写理由。写了不改。"""
    found = await svc.accept(request_id, version, body, admin.subject)
    return _ok(
        {"request_id": str(found.request_id), "version": found.version},
        status_code=201,
    )


@admin_router.post("/knowbot-requests/{request_id}/drafts")
async def add_draft(
    _: Releaser, body: DraftIn, svc: Service, request_id: ItemId
) -> JSONResponse:
    """孵化（过渡做法：贴清单）：选受理了的创意版本建准专家。"""
    draft = await svc.add_draft(request_id, body.manifest, body.request_version)
    return _ok(
        {
            "draft_id": str(draft.id),
            "version": draft.version,
            "revision": draft.revision,
            "request_version": draft.request_version,
        },
        status_code=201,
    )


@admin_router.post("/knowbot-drafts/{draft_id}/review-request", status_code=204)
async def request_review(admin: Releaser, svc: Service, draft_id: ItemId) -> Response:
    """请用户审核这位准专家（web端「审核」里一条待办）。"""
    await svc.request_user_review(draft_id, admin.subject)
    return Response(status_code=204, headers=NO_STORE)


@admin_router.get("/knowbot-drafts/{draft_id}/manifest")
async def draft_manifest(admin: Admin, svc: Service, draft_id: ItemId) -> JSONResponse:
    """这位准专家的清单原文：过程页里只读、可复制（SDD 0037）。"""
    found = await svc.draft(draft_id)
    log.info("knowbot manifest read by=%s draft=%s", admin.subject, found.id)
    return _ok({"draft_id": str(found.id), "manifest": found.manifest})


@admin_router.post("/knowbot-drafts/{draft_id}/rules-review", status_code=204)
async def review_rules(admin: Releaser, svc: Service, draft_id: ItemId) -> Response:
    """逐条核过规则出处（C-L1）；记下是谁核的。"""
    await svc.review_rules(draft_id, admin.subject)
    return Response(status_code=204, headers=NO_STORE)


@admin_router.post("/knowbot-drafts/{draft_id}/test")
async def start_test(
    admin: Admin, body: StartTestIn, svc: Service, board: Board, draft_id: ItemId
) -> JSONResponse:
    """拿这位准专家按用例跑一次：新编号。"""
    test = await svc.start_test(
        draft_id,
        budget_limit=body.budget_limit,
        timeout_seconds=body.timeout_seconds,
        starter=admin.subject,
    )
    life = await board.life(str(test.request_id))
    return _ok({"test": test_view(test, life, admin=True)}, status_code=201)


@admin_router.post("/knowbot-tests/{test_id}/refresh")
async def refresh(
    _: Admin, svc: Service, board: Board, test_id: ItemId
) -> JSONResponse:
    test = await svc.refresh(test_id)
    life = await board.life(str(test.request_id))
    return _ok({"test": test_view(test, life, admin=True)})


@admin_router.post("/knowbot-tests/{test_id}/reset", status_code=204)
async def reset(_: Releaser, svc: Service, test_id: ItemId) -> Response:
    """超时还没结论：记平台出错（超时）结束这一次，准专家回到待测。"""
    await svc.reset_stuck(test_id)
    return Response(status_code=204, headers=NO_STORE)


@admin_router.get("/knowbot-tests/{test_id}/outputs")
async def test_outputs(admin: Admin, svc: Service, test_id: ItemId) -> JSONResponse:
    """这次测试每条用例的成稿与各步交回物：测试人员验证这一稿用（SDD 0037）。"""
    test, request = await svc.test_with_request(test_id)
    # 产出是用户的分析内容：谁、哪一次、何时读的留一行，不记正文（审读 10-19）
    log.info("knowbot outputs read by=%s test=%s", admin.subject, test.id)
    return _ok(outputs_view(test, await svc.test_outputs(test, request), admin=True))


@admin_router.post("/knowbot-experts/{expert_id}/return")
async def return_expert(
    admin: Releaser, experts: Experts, expert_id: ItemId
) -> JSONResponse:
    """交回：把这一版通知用户（用户同意门开始）。"""
    found = await experts.return_to_user(expert_id, admin.subject)
    return _ok({"expert_id": str(found.id), "stage": stage_of(found)})


@admin_router.post("/knowbot-experts/{expert_id}/review")
async def review_expert(
    admin: Releaser, body: ExpertReviewIn, experts: Experts, expert_id: ItemId
) -> JSONResponse:
    """运营审核门：通过（自用 / 公开）即交 knowmart；不通过写理由。交付失败记在
    delivery_error，可重试。"""
    found = await experts.review(expert_id, body, admin.subject)
    return _ok(
        {
            "expert_id": str(found.id),
            "stage": stage_of(found),
            "delivery_error": found.delivery_error,
        }
    )


@admin_router.post("/knowbot-experts/{expert_id}/deliver")
async def deliver(_: Releaser, experts: Experts, expert_id: ItemId) -> JSONResponse:
    """重试交付：审过了、上次交 knowmart 没成功的。"""
    found = await experts.deliver(expert_id)
    return _ok({"expert_id": str(found.id), "stage": stage_of(found)})


@admin_router.post("/knowbot-experts/{expert_id}/stop")
async def stop_expert(
    admin: Releaser, body: StopIn, experts: Experts, expert_id: ItemId
) -> JSONResponse:
    """停用上架了的这一版（写理由）；knowmart 那一版跟着停。"""
    found = await experts.stop(expert_id, body.reason, admin.subject)
    return _ok({"expert_id": str(found.id), "stage": stage_of(found)})


@admin_router.get("/knowbot-questions")
async def open_questions(_: Admin, questions: Questions) -> JSONResponse:
    """测试中还没回答的问题：在我们这里的，和转给用户、用户还没答的。"""
    items = await questions.operator_queue()
    return _ok({"questions": [question_view(q, d, r, admin=True) for q, d, r in items]})


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
