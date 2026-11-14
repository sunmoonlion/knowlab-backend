"""创意到专家的接口（SDD 0027、0037）。代码里仍叫 request、draft、knowbot。

web端（用户会话），左边过程「申请 · 孵化 · 测试 · 交付 · 待办」、右边结果
「专家 · 知识库」（SDD 0046 第六节）：申请（出创意 v1）、改创意（出新版本）、待办（转给
自己的问题与孵化缺口、请自己审核的准专家、交回等自己定的专家）、专家（交回了的各版：
要不要、停用）、知识库（凭本人令牌现取 knowbase 里展示给 knowlab 的资料）；名称的
「孵化」栏里是工作稿（专家孵化方案：存格子、挑数据、导入专家包与 skill、复制自己的、
预览、出一版、AI 起草与按指令改的每一轮与接受建议）。不是自己的与没有的一样，都是 404。

管理端（管理员会话，我们的运营；admin、operator 现在能做的一样），左边栏
「受理 · 孵化 · 测试 · 交付」：每栏的大类列表与「等我们」的数；受理（写受理单）、孵化
（选受理了的版本贴清单、请用户审核、核出处）、测试（发起 = 新编号、刷新、判定卡住 = 平台
出错）、交付（交回、运营审核、交 knowmart 与重试、全部专家、停用）。补充方法只读。
管理端只看得到平台模式的准专家、测试与它们的问题（SDD 0046 第四节）：自助的准专家、
工作稿、AI 孵化的各轮与缺口、自助测试与报告不列，按编号来也是 404。平台测试也在作者的
账号下、他的电脑上跑（decisions ⑯）。

点任何一条进同一个名称的详情页：顶上一行摘要，下面按时间排的记录。身份依赖写在服务前面：
先认人，再开数据库会话。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.errors.exceptions import NotFoundError, ServiceUnavailableError
from app.application.ports.knowbots import DraftRecord
from app.application.services.formal_methods import FormalMethodService
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
    light_items,
    timeline,
    user_expert_items,
)
from app.application.services.knowbot_deliveries import KnowbotDeliveryService
from app.application.services.knowbot_experts import (
    KnowbotExpertService,
    shown_to_author,
    stage_of,
)
from app.application.services.knowbot_grants import live_incubation
from app.application.services.knowbot_incubation import (
    DEFAULT_BUDGET,
    KnowbotIncubationService,
)
from app.application.services.knowbot_library import KnowbotLibraryService
from app.application.services.knowbot_manual_release import (
    KnowbotManualReleaseService,
    release_view,
)
from app.application.services.knowbot_methods import (
    KnowbotMethodService,
    method_version_view,
)
from app.application.services.knowbot_platform import KnowbotPlatformService
from app.application.services.knowbot_proposals import KnowbotProposalService
from app.application.services.knowbot_questions import AnswerIn, KnowbotQuestionService
from app.application.services.knowbot_service import KnowbotCreationService, parse_id
from app.application.services.knowbot_test_data import KnowbotTestData
from app.application.services.knowbot_workdraft import KnowbotWorkdraftService
from app.bootstrap.knowbots import (
    formal_method_service,
    knowbot_board,
    knowbot_delivery_service,
    knowbot_expert_service,
    knowbot_incubation_service,
    knowbot_library_service,
    knowbot_method_service,
    knowbot_platform_service,
    knowbot_proposal_service,
    knowbot_question_service,
    knowbot_service,
    knowbot_test_data,
    knowbot_workdraft_service,
    runtime_clients,
)
from app.domain.knowbots.delivery import (
    CATEGORY_CODES,
    DeliveryReviewIn,
    DeliverySubmitIn,
)
from app.domain.knowbots.drafts import KnowbotRequestIn
from app.domain.knowbots.formal_method_admission import (
    FreezeFormalMethod,
    ValidateFormalMethod,
)
from app.domain.knowbots.lifecycle import (
    AcceptanceIn,
    RevisionIn,
    StopIn,
    UserReviewIn,
)
from app.domain.knowbots.manual_release import ManualReleaseDecision, ManualReleaseIn
from app.domain.knowbots.methods import BindMethod, FreezeAtomicMethod, FreezeMethod
from app.domain.knowbots.platform_grants import (
    LightAcceptanceIn,
    PlatformIncubationIn,
    PlatformTestIn,
)
from app.domain.knowbots.workdraft import STEP_ID
from app.domain.security import Principal
from app.infrastructure.repositories.knowbots import SqlKnowlabRepository
from app.infrastructure.storage.postgres import get_db_session
from app.interfaces.endpoints.knowbot_views import (
    amendment_view,
    answered_view,
    delivery_view,
    draft_view,
    expert_view,
    grant_view,
    incubation_view,
    life_view,
    outputs_view,
    proposal_view,
    question_view,
    test_view,
    version_view,
    workdraft_view,
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


def manual_release_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> KnowbotManualReleaseService:
    return KnowbotManualReleaseService(SqlKnowlabRepository(session))


def expert_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> KnowbotExpertService:
    return knowbot_expert_service(session)


def board_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> KnowbotBoard:
    return knowbot_board(session)


def delivery_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> KnowbotDeliveryService:
    return knowbot_delivery_service(session)


def question_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> KnowbotQuestionService:
    return knowbot_question_service(session)


def workdraft_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> KnowbotWorkdraftService:
    return knowbot_workdraft_service(session)


def method_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> KnowbotMethodService:
    return knowbot_method_service(session)


def formal_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> FormalMethodService:
    return formal_method_service(session)


def proposal_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> KnowbotProposalService:
    return knowbot_proposal_service(session)


def platform_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> KnowbotPlatformService:
    return knowbot_platform_service(session)


def incubation_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> KnowbotIncubationService:
    return knowbot_incubation_service(session)


def library_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> KnowbotLibraryService:
    return knowbot_library_service(session)


ManualReleases = Annotated[KnowbotManualReleaseService, Depends(manual_release_service)]
Service = Annotated[KnowbotCreationService, Depends(service)]
Library = Annotated[KnowbotLibraryService, Depends(library_service)]
Platform = Annotated[KnowbotPlatformService, Depends(platform_service)]
Proposals = Annotated[KnowbotProposalService, Depends(proposal_service)]
Incubations = Annotated[KnowbotIncubationService, Depends(incubation_service)]
Workdrafts = Annotated[KnowbotWorkdraftService, Depends(workdraft_service)]
Methods = Annotated[KnowbotMethodService, Depends(method_service)]
FormalMethods = Annotated[FormalMethodService, Depends(formal_service)]
Experts = Annotated[KnowbotExpertService, Depends(expert_service)]
Deliveries = Annotated[KnowbotDeliveryService, Depends(delivery_service)]
Board = Annotated[KnowbotBoard, Depends(board_service)]
Questions = Annotated[KnowbotQuestionService, Depends(question_service)]
# knowbase 用户令牌：真要动 knowbase 时才换（测试集与测试里的数据，SDD 0046 第三节）
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
    now = svc.now()
    return life_view(life, admin=admin) | {
        # 现在的孵化是自助还是平台；作者给平台的授权（SDD 0046 第四节）
        "incubation_mode": life.request.incubation_mode,
        # 从 knowbench 转来的：来源与用户的模型直接答过的回答（只算提议）。管理端只在
        # 孵化授权在效的平台孵化时给（operator_view 已清掉）；没有创意版本时不给后两项
        "origin": life.request.origin if life.versions else None,
        "reference_answer": life.request.reference_answer if life.versions else None,
        "grants": [grant_view(g, now, admin=admin) for g in life.grants],
        "versions": [version_view(v, life, admin=admin) for v in life.versions],
        "drafts": [draft_view(d, life, admin=admin) for d in life.drafts],
        "tests": [test_view(t, life, admin=admin) for t in life.tests],
        "experts": [
            expert_view(e, life, admin=admin)
            for e in life.experts
            if admin or shown_to_author(e)
        ],
        "timeline": timeline(life, for_user=not admin),
        # 旧的补充方法（SDD 0035，已取消）：只读，出处照旧可查；管理端只在孵化授权
        # 在效的平台孵化时给（纯自助作者写的运营看不到）
        "amendments": [
            amendment_view(a, admin=admin)
            for a in await svc.amendments(life.request.id, life.drafts)
        ]
        if not admin or live_incubation(life.grants, now) is not None
        else [],
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


@web_router.get("/knowbot-requests/defaults")
async def request_defaults(principal: User, svc: Service) -> JSONResponse:
    """新申请默认的模式：上次选的；第一次是空，让他自己选（SDD 0046 第四节）。"""
    return _ok({"mode": await svc.default_mode(principal.subject) or None})


@web_router.post("/knowbot-requests")
async def submit(principal: User, body: KnowbotRequestIn, svc: Service) -> JSONResponse:
    """申请：出创意 v1。选自助的直接进工作稿；请平台的同时授权平台，等运营受理。"""
    request = await svc.submit(principal.subject, body)
    return _ok(
        {
            "request_id": str(request.id),
            "version": 1,
            "incubation_mode": request.incubation_mode,
        },
        status_code=201,
    )


@web_router.post("/knowbot-requests/{request_id}/platform")
async def ask_platform(
    principal: User,
    body: PlatformIncubationIn,
    platform: Platform,
    request_id: ItemId,
) -> JSONResponse:
    """「请平台孵化」：确认框上点了同意就是本人授权（platform:knowlab:incubate:<编号>），
    名称转平台，这一版创意受理后平台接着孵化（SDD 0046 第四节）。"""
    grant = await platform.ask_platform(principal.subject, request_id, body)
    return _ok({"grant": grant_view(grant, platform.now(), admin=False)}, 201)


@web_router.post("/knowbot-requests/{request_id}/take-back", status_code=204)
async def take_back(
    principal: User, platform: Platform, request_id: ItemId
) -> Response:
    """「我自己来」：平台的授权立即撤销，运营立即看不到。"""
    await platform.take_back(principal.subject, request_id)
    return Response(status_code=204, headers=NO_STORE)


@web_router.post("/knowbot-requests/{request_id}/platform/renew")
async def renew_platform(
    principal: User, platform: Platform, request_id: ItemId
) -> JSONResponse:
    """续期：孵化授权续到 30 天后（decisions 10-29）。"""
    grant = await platform.renew(principal.subject, request_id)
    return _ok({"grant": grant_view(grant, platform.now(), admin=False)})


@web_router.post("/knowbot-drafts/{draft_id}/platform-test")
async def ask_platform_test(
    principal: User, body: PlatformTestIn, platform: Platform, draft_id: ItemId
) -> JSONResponse:
    """「交平台测」：自助孵化的准专家交平台测这一次（轻受理），上限作者设。"""
    grant = await platform.ask_platform_test(principal.subject, draft_id, body)
    return _ok({"grant": grant_view(grant, platform.now(), admin=False)}, 201)


@web_router.post("/knowbot-grants/{grant_id}/withdraw", status_code=204)
async def withdraw_platform_test(
    principal: User, platform: Platform, grant_id: ItemId
) -> Response:
    """还没开测的平台测试，作者收回。"""
    await platform.withdraw_test(principal.subject, grant_id)
    return Response(status_code=204, headers=NO_STORE)


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
    """自己的一个名称的详情页：各版创意与受理、准专家、测试、交回了的专家、记录；平台在
    问我什么（平台视图，SDD 0046 第四节）。"""
    life = await board.life(request_id, principal.subject)
    return _ok(
        await _detail(life, svc, admin=False)
        | {"platform_asks": await board.platform_asks(life)}
    )


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
            # 孵化的缺口不挂准专家（draft 是空的）
            draft = None if item.draft_id is None else life.draft(item.draft_id)
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


@web_router.get("/knowbot-tests/{test_id}/execution")
async def my_test_execution(
    principal: User,
    svc: Service,
    test_id: ItemId,
    case_id: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9._-]{1,64}$")] = None,
) -> JSONResponse:
    return _ok(
        {"execution": await svc.my_test_execution(principal.subject, test_id, case_id)}
    )


@web_router.post("/knowbot-experts/{expert_id}/deliveries")
async def submit_delivery(
    principal: User,
    body: DeliverySubmitIn,
    deliveries: Deliveries,
    token: KnowbaseToken,
    expert_id: ItemId,
) -> JSONResponse:
    """「提交交付」（SDD 0046 第五节）：选这一版、定自用或公开、授权审核员看这次交付。
    这一下就是用户同意门；自用的能自动查的自动查，都过马上进 knowmart 小店。上架了的
    自用版本再提交一次公开，就是自用转公开（重查许可）。"""
    found = await deliveries.submit(principal.subject, expert_id, body, token)
    return _ok({"delivery_id": str(found.id), "status": found.status}, 201)


@web_router.post("/knowbot-experts/{expert_id}/deliveries/withdraw")
async def withdraw_delivery(
    principal: User, deliveries: Deliveries, expert_id: ItemId
) -> JSONResponse:
    """撤回没走完的交付（在审的，或审过还没交成的）：停在跑的平台重跑、撤给审核员的
    授权；第一次交付的这一版回到「等你提交」。"""
    found = await deliveries.withdraw(principal.subject, expert_id)
    return _ok({"delivery_id": str(found.id), "status": found.status})


@web_router.post("/knowbot-experts/{expert_id}/decline")
async def decline_expert(
    principal: User, experts: Experts, expert_id: ItemId
) -> JSONResponse:
    """作者不要这一版：不上架。"""
    found = await experts.decline(principal.subject, expert_id)
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


@web_router.get("/knowbot-questions")
async def my_questions(principal: User, questions: Questions) -> JSONResponse:
    """测试中转给我回答的问题（投资判断只能我自己答，C-L1）。"""
    items = await questions.user_queue(principal.subject)
    return _ok(
        {"questions": [question_view(q, d, r, admin=False) for q, d, r in items]}
    )


@web_router.get("/knowbot-tests/{test_id}/questions")
async def my_test_questions(
    principal: User, svc: Service, questions: Questions, test_id: ItemId
) -> JSONResponse:
    test, _ = await svc.my_test(principal.subject, test_id)
    items = await questions.user_queue(principal.subject)
    return _ok(
        {
            "questions": [
                question_view(q, d, r, admin=False)
                for q, d, r in items
                if q.run_id == test.run_id
            ]
        }
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


class SelfTestIn(BaseModel):
    """自助测试（SDD 0046 第三节）：在哪台电脑、哪个根目录下跑，费用上限多少。"""

    model_config = ConfigDict(extra="forbid")
    environment_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    workspace_root: str = Field(min_length=1, max_length=1024)
    budget_limit: str = Field(pattern=r"^[0-9]{1,6}(\.[0-9]{1,2})?$")
    timeout_seconds: int = Field(default=3600, ge=60, le=86400)


@web_router.get("/knowbot-drafts/{draft_id}/computers")
async def my_computers(principal: User, svc: Service, draft_id: ItemId) -> JSONResponse:
    """自助测试选电脑：我接入过的电脑（在线与否、白名单根目录）与上次用的那台。"""
    return _ok(await svc.my_computers(principal.subject, draft_id))


@web_router.post("/knowbot-drafts/{draft_id}/self-tests")
async def start_self_test(
    principal: User, svc: Service, body: SelfTestIn, draft_id: ItemId
) -> JSONResponse:
    """自己拿这位准专家测一次：在我自己的电脑上、用我的模型 key，费用上限我自己设。"""
    test = await svc.start_self_test(
        principal.subject,
        draft_id,
        environment_id=body.environment_id,
        workspace_root=body.workspace_root,
        budget_limit=body.budget_limit,
        timeout_seconds=body.timeout_seconds,
    )
    return _ok(
        {"test_id": str(test.id), "status": test.status, "mode": test.mode},
        status_code=201,
    )


@web_router.post("/knowbot-tests/{test_id}/stop")
async def stop_my_test(principal: User, svc: Service, test_id: ItemId) -> JSONResponse:
    test = await svc.stop_mine(principal.subject, test_id)
    return _ok({"test_id": str(test.id), "status": test.status})


@web_router.post("/knowbot-tests/{test_id}/refresh")
async def refresh_my_test(
    principal: User, svc: Service, test_id: ItemId
) -> JSONResponse:
    """自己的测试刷新一次：问运行方结果、查在取的数据。"""
    test = await svc.refresh_mine(principal.subject, test_id)
    return _ok({"test_id": str(test.id), "status": test.status})


# ---------- web端：知识库栏（SDD 0046 第六节） ----------
@web_router.get("/knowbot-library")
async def my_library(
    _: User, library: Library, user_token: KnowbaseToken
) -> JSONResponse:
    """知识库栏：我在 knowbase 展示给 knowlab 的资料（凭我本人的令牌现取，knowlab
    不存）。在 knowbase 管，这里只列。"""
    return _ok({"materials": await library.shown(user_token)})


class DataPicksIn(BaseModel):
    """工作稿里挑的数据：整份换成这几份（资料编号），空的就是都不要了。"""

    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    picks: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(
        max_length=50
    )


# ---------- web端：工作稿（专家孵化方案第五至十一节） ----------
class CellIn(BaseModel):
    """存一格：path 是格子路径（domain/knowbots/workdraft.py），value 为 null 是删。"""

    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=300)
    value: Any


class SaveCellsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)  # 读到的那一版；对不上回 409
    cells: list[CellIn] = Field(min_length=1, max_length=200)


class ImportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    manifest: dict[str, Any]


class CopyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    draft_id: str = Field(min_length=1, max_length=64)


@web_router.get("/knowbot-requests/{request_id}/workdraft")
async def my_workdraft(
    principal: User, workdrafts: Workdrafts, props: Proposals, request_id: ItemId
) -> JSONResponse:
    """这个名称的工作稿：格子、每格的出处、revision（还没存过的是空的一份）；平台孵化时
    运营的提议也带上（作者逐条处理）。"""
    request, found = await workdrafts.get(principal.subject, request_id)
    _, proposals = await props.mine(principal.subject, request_id)
    return _ok(
        workdraft_view(request, found, await workdrafts.origins(found))
        | {"proposals": [proposal_view(p, admin=False) for p in proposals]}
    )


@web_router.post("/knowbot-requests/{request_id}/workdraft/proposals/{proposal_id}")
async def decide_proposal(
    principal: User,
    body: DecisionIn,
    props: Proposals,
    workdrafts: Workdrafts,
    request_id: ItemId,
    proposal_id: ItemId,
) -> JSONResponse:
    """接受、改了再接受、不要运营的一条提议（专家孵化方案第十二节）；工作稿别处改过回
    409 workdraft_revision。"""
    _, found = await props.decide(
        principal.subject,
        request_id,
        proposal_id,
        action=body.action,
        revision=body.revision,
        value=body.value,
    )
    request, workdraft = await workdrafts.get(principal.subject, request_id)
    return _ok(
        {
            "workdraft": workdraft_view(
                request, workdraft, await workdrafts.origins(workdraft)
            ),
            "proposal": proposal_view(found, admin=False),
        }
    )


@web_router.post("/knowbot-requests/{request_id}/workdraft/cells")
async def save_cells(
    principal: User, body: SaveCellsIn, workdrafts: Workdrafts, request_id: ItemId
) -> JSONResponse:
    """存几格（自动保存）：每格改了的记一条编辑记录，算我的原话。"""
    found = await workdrafts.save(
        principal.subject,
        request_id,
        revision=body.revision,
        edits=[(c.path, c.value) for c in body.cells],
    )
    request, _ = await workdrafts.get(principal.subject, request_id)
    return _ok(workdraft_view(request, found, await workdrafts.origins(found)))


@web_router.post("/knowbot-requests/{request_id}/workdraft/import")
async def import_pack(
    principal: User, body: ImportIn, workdrafts: Workdrafts, request_id: ItemId
) -> JSONResponse:
    """导入专家包（清单 v2）：整份换掉；导入的内容算我的原话，工程层留进「高级」。"""
    found, notes = await workdrafts.import_manifest(
        principal.subject, request_id, revision=body.revision, manifest=body.manifest
    )
    request, _ = await workdrafts.get(principal.subject, request_id)
    view = workdraft_view(request, found, await workdrafts.origins(found))
    return _ok(view | {"notes": notes})


@web_router.post("/knowbot-requests/{request_id}/workdraft/copy")
async def copy_mine(
    principal: User, body: CopyIn, workdrafts: Workdrafts, request_id: ItemId
) -> JSONResponse:
    """拿我自己的一位准专家作起点：整份换掉。别人的不行。"""
    found, notes = await workdrafts.copy_from(
        principal.subject, request_id, revision=body.revision, draft_id=body.draft_id
    )
    request, _ = await workdrafts.get(principal.subject, request_id)
    view = workdraft_view(request, found, await workdrafts.origins(found))
    return _ok(view | {"notes": notes})


@web_router.put("/knowbot-requests/{request_id}/workdraft/data-picks")
async def pick_data(
    principal: User,
    body: DataPicksIn,
    library: Library,
    workdrafts: Workdrafts,
    user_token: KnowbaseToken,
    request_id: ItemId,
) -> JSONResponse:
    """挑数据（专家孵化方案第 7.1 节）：只能从知识库栏里挑；挑的时候取下每份的格式
    （描述、读法），AI 一轮交给模型的就是它，不给内容（第 8.2 节）。不在栏里的回 400
    pick_not_shown；工作稿别处改过回 409 workdraft_revision。"""
    found = await library.pick(
        principal.subject,
        request_id,
        revision=body.revision,
        picks=body.picks,
        user_token=user_token,
    )
    request, _ = await workdrafts.get(principal.subject, request_id)
    return _ok(workdraft_view(request, found, await workdrafts.origins(found)))


@web_router.get("/knowbot-requests/{request_id}/workdraft/preview")
async def preview_workdraft(
    principal: User, workdrafts: Workdrafts, request_id: ItemId
) -> JSONResponse:
    """编出的清单与出不了版本的地方（按格子路径标红），不出版本。"""
    manifest, problems, version = await workdrafts.preview(
        principal.subject, request_id
    )
    return _ok(
        {
            "version": version,
            "manifest": manifest,
            "problems": [{"path": p.path, "message": p.message} for p in problems],
        }
    )


@web_router.post("/knowbot-requests/{request_id}/workdraft/publish")
async def publish_workdraft(
    principal: User, workdrafts: Workdrafts, request_id: ItemId
) -> JSONResponse:
    """出一版：把关都过了就出准专家第 n 版（自助），拿去测。工作稿不变。"""
    draft = await workdrafts.publish(principal.subject, request_id)
    return _ok(
        {
            "draft_id": str(draft.id),
            "version": draft.version,
            "revision": draft.revision,
            "request_version": draft.request_version,
            "mode": draft.mode,
        },
        status_code=201,
    )


class SeedSkillIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skill: str = Field(min_length=1, max_length=20000)


@web_router.post("/knowbot-requests/{request_id}/workdraft/seed-skill")
async def seed_skill(
    principal: User, body: SeedSkillIn, workdrafts: Workdrafts, request_id: ItemId
) -> JSONResponse:
    """导入 skill 当种子：存成创意的新一版（种类 skill），再点「AI 起草」去拆。"""
    version = await workdrafts.seed_skill(principal.subject, request_id, body.skill)
    return _ok({"request_id": request_id, "version": version}, status_code=201)


# ---------- web端：AI 孵化的一轮（专家孵化方案第八、九节） ----------
class IncubationIn(BaseModel):
    """发起一轮：起草（draft）或按我的一句话改（revise，可以指定一步）；费用上限我设，
    电脑与目录不给就用上次的。"""

    model_config = ConfigDict(extra="forbid")
    action: Literal["draft", "revise"]
    instruction: str | None = Field(default=None, min_length=1, max_length=4000)
    step_id: str | None = Field(default=None, pattern=STEP_ID)
    budget_limit: str = Field(
        default=DEFAULT_BUDGET, pattern=r"^[0-9]{1,6}(\.[0-9]{1,2})?$"
    )
    environment_id: str | None = Field(default=None, pattern=r"^[0-9a-f-]{36}$")
    workspace_root: str | None = Field(default=None, min_length=1, max_length=1024)
    # 带上最近一次测试的报告让 AI 改（只许 revise，专家孵化方案第 8.4 节）
    with_test: bool = False


class AcceptStepIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)


class DecisionIn(BaseModel):
    """对一条建议：接受、改了再接受（value 是改后的）、不要。改工作稿的带读到的
    revision，对不上回 409。"""

    model_config = ConfigDict(extra="forbid")
    action: Literal["accept", "accept_edited", "reject"]
    revision: int | None = Field(default=None, ge=0)
    value: Any = None


@web_router.get("/knowbot-requests/{request_id}/workdraft/computers")
async def incubation_computers(
    principal: User, incubations: Incubations, request_id: ItemId
) -> JSONResponse:
    """AI 孵化选电脑：我接入过的电脑与上次用的那台与目录。"""
    return _ok(await incubations.computers(principal.subject, request_id))


@web_router.get("/knowbot-requests/{request_id}/workdraft/capabilities")
async def incubation_capabilities(
    principal: User,
    incubations: Incubations,
    request_id: ItemId,
) -> JSONResponse:
    return _ok(await incubations.capabilities(principal.subject, request_id))


@web_router.get("/knowbot-requests/{request_id}/workdraft/incubations")
async def my_incubations(
    principal: User, incubations: Incubations, request_id: ItemId
) -> JSONResponse:
    """这个名称的各轮（第几轮、状态、花费），第一轮在前。"""
    found = await incubations.rounds(principal.subject, request_id)
    return _ok({"incubations": [incubation_view(r) for r in found]})


@web_router.post("/knowbot-requests/{request_id}/workdraft/incubations")
async def start_incubation(
    principal: User, body: IncubationIn, incubations: Incubations, request_id: ItemId
) -> JSONResponse:
    """发起一轮（「AI 起草」「让 AI 改…」）：在我的电脑上、用我的 key 跑，费用上限我设。
    这份工作稿已有一轮在跑回 409 incubation_running；没有可用的电脑回 409
    author_has_no_computer。"""
    record = await incubations.start(
        principal.subject,
        request_id,
        action=body.action,
        instruction=body.instruction,
        step_id=body.step_id,
        budget_limit=body.budget_limit,
        environment_id=body.environment_id,
        workspace_root=body.workspace_root,
        with_test=body.with_test,
    )
    return _ok(
        {"incubation": incubation_view(record, await incubations.gaps(record))},
        status_code=201,
    )


@web_router.get("/knowbot-requests/{request_id}/workdraft/incubations/{incubation_id}")
async def my_incubation(
    principal: User, incubations: Incubations, request_id: ItemId, incubation_id: ItemId
) -> JSONResponse:
    """一轮：状态与交回的建议（每条的出处、核得上没有、我怎么处理的）、缺口、自查。"""
    _, record = await incubations.mine(principal.subject, request_id, incubation_id)
    return _ok({"incubation": incubation_view(record, await incubations.gaps(record))})


@web_router.post(
    "/knowbot-requests/{request_id}/workdraft/incubations/{incubation_id}/refresh"
)
async def refresh_incubation(
    principal: User, incubations: Incubations, request_id: ItemId, incubation_id: ItemId
) -> JSONResponse:
    """刷新一次（页面轮询）：没结束的问一次运行方，成了的存交回、核出处、缺口进待办。"""
    record = await incubations.refresh(principal.subject, request_id, incubation_id)
    return _ok({"incubation": incubation_view(record, await incubations.gaps(record))})


@web_router.post(
    "/knowbot-requests/{request_id}/workdraft/incubations/{incubation_id}/cancel"
)
async def cancel_incubation(
    principal: User, incubations: Incubations, request_id: ItemId, incubation_id: ItemId
) -> JSONResponse:
    """取消没结束的一轮。"""
    record = await incubations.cancel(principal.subject, request_id, incubation_id)
    return _ok({"incubation": incubation_view(record, await incubations.gaps(record))})


@web_router.post(
    "/knowbot-requests/{request_id}/workdraft/incubations/{incubation_id}"
    "/suggestions/{suggestion_id}"
)
async def decide_suggestion(
    principal: User,
    body: DecisionIn,
    incubations: Incubations,
    workdrafts: Workdrafts,
    request_id: ItemId,
    incubation_id: ItemId,
    suggestion_id: Annotated[str, Path(max_length=32)],
) -> JSONResponse:
    """接受、改了再接受、不要一条建议。出处核不上的只能改了再接受（409
    suggestion_unverified）；工作稿别处改过回 409 workdraft_revision。"""
    _, record = await incubations.decide(
        principal.subject,
        request_id,
        incubation_id,
        suggestion_id,
        action=body.action,
        revision=body.revision,
        value=body.value,
    )
    request, found = await workdrafts.get(principal.subject, request_id)
    return _ok(
        {
            "workdraft": workdraft_view(
                request, found, await workdrafts.origins(found)
            ),
            "incubation": incubation_view(record, await incubations.gaps(record)),
        }
    )


@web_router.post(
    "/knowbot-requests/{request_id}/workdraft/incubations/{incubation_id}"
    "/steps/{step_id}/accept-all"
)
async def accept_step(
    principal: User,
    body: AcceptStepIn,
    incubations: Incubations,
    workdrafts: Workdrafts,
    request_id: ItemId,
    incubation_id: ItemId,
    step_id: Annotated[str, Path(pattern=STEP_ID)],
) -> JSONResponse:
    """一键接受这一步的全部建议（专家孵化方案第 8.3 节）：出处核不上的、放不上去的跳过，
    照旧待处理（skipped 里写着为什么）。工作稿别处改过回 409 workdraft_revision。"""
    _, record, accepted, skipped = await incubations.accept_step(
        principal.subject,
        request_id,
        incubation_id,
        step_id,
        revision=body.revision,
    )
    request, found = await workdrafts.get(principal.subject, request_id)
    return _ok(
        {
            "workdraft": workdraft_view(
                request, found, await workdrafts.origins(found)
            ),
            "incubation": incubation_view(record, await incubations.gaps(record)),
            "accepted": accepted,
            "skipped": skipped,
        }
    )


def test_data_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> KnowbotTestData:
    found = knowbot_test_data(session)
    if found is None:
        raise ServiceUnavailableError(
            "knowbase is not configured", code="knowbase_unconfigured"
        )
    return found


TestData = Annotated[KnowbotTestData, Depends(test_data_service)]


@web_router.get("/knowbot-tests/{test_id}/data")
async def my_test_data(
    principal: User, data: TestData, user_token: KnowbaseToken, test_id: ItemId
) -> JSONResponse:
    """这次测试里专家提出的数据（SDD 0046 第三节）：每一样解析到哪、候选有哪些、
    在取的、没取到的。只有我自己的测试。"""
    found = await data.pending(principal.subject, test_id, user_token)
    return _ok({"requests": found})


class TestDataBody(BaseModel):
    """确认测试数据：可以先改（needs 是改过的整份），换一份、去掉几样；
    都不给就照提出的。"""

    model_config = ConfigDict(extra="forbid")
    picks: dict[str, dict[str, Any]] = Field(default_factory=dict, max_length=200)
    dropped: list[str] = Field(default_factory=list, max_length=200)
    needs: list[dict[str, Any]] | None = Field(default=None, max_length=20)


@web_router.post("/knowbot-test-data/{question_id}/confirm")
async def confirm_my_test_data(
    principal: User,
    data: TestData,
    user_token: KnowbaseToken,
    question_id: ItemId,
    body: TestDataBody,
) -> JSONResponse:
    done = await data.confirm(
        principal.subject,
        question_id,
        user_token,
        picks=body.picks,
        dropped=body.dropped,
        needs=body.needs,
    )
    return _ok(done)


@web_router.post("/knowbot-test-data/{question_id}/decline")
async def decline_my_test_data(
    principal: User, data: TestData, question_id: ItemId
) -> JSONResponse:
    """不要这几样，或没取到的照现有的接着做：记「数据不全」。"""
    return _ok(await data.decline(principal.subject, question_id))


# ---------- 管理端（我们的运营） ----------
class TakeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=200)


class StartTestIn(BaseModel):
    """运营发起平台测试：只给超时；费用上限是作者在授权里设的（decisions ⑯）。"""

    model_config = ConfigDict(extra="forbid")
    timeout_seconds: int = Field(ge=60, le=86400)


Column = Literal["acceptance", "incubation", "tests", "delivery"]


@admin_router.get("/knowbot-counts")
async def counts(_: Admin, board: Board) -> JSONResponse:
    """左边栏旁的数：每栏「等我们」的条数。"""
    return _ok({"counts": admin_counts(await board.lives(operator=True))})


@admin_router.get("/knowbot-board/{column}")
async def board_column(_: Admin, board: Board, column: Column) -> JSONResponse:
    """一栏的全部条目，每条带大类（category）；两端按大类分组。"""
    lives = await board.lives(operator=True)
    items: list[dict[str, Any]] = []
    if column == "acceptance":
        for category, life, version in acceptance_items(lives):
            found = next(v for v in life.versions if v.version == version)
            items.append(
                _brief(life, admin=True)
                | {"category": category, "acceptance_kind": "formal"}
                | version_view(found, life, admin=True)
            )
        # 轻受理：作者交平台测的准专家（SDD 0046 第四节）
        now = board.now()
        for category, life, draft, grant in light_items(lives):
            items.append(
                _brief(life, admin=True)
                | {
                    "category": category,
                    "acceptance_kind": "light",
                    "draft": draft_view(draft, life, admin=True),
                    "grant": grant_view(grant, now, admin=True),
                }
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
    questions: Questions,
    request_id: ItemId,
) -> JSONResponse:
    """一个名称的详情页（管理端看的：平台模式的那几步）；孵化要看的：平台测试中答过的
    问题。"""
    life = await board.life(request_id, operator=True)
    shown_draft_ids = {d.id for d in life.drafts}
    return _ok(
        await _detail(life, svc, admin=True)
        | {
            # 测试中答过的：只取运营视图里的准专家
            "answered": [
                answered_view(q, admin=True)
                for q in await questions.answered(life.request.id)
                if q.draft_id in shown_draft_ids
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


# ---------- 管理端：平台孵化的工作稿（专家孵化方案第十二节） ----------
class ProposalItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=200)
    op: Literal["set", "add", "remove", "move"]
    value: Any = None
    reason: str = Field(min_length=1, max_length=1000)


class ProposalsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposals: list[ProposalItemIn] = Field(min_length=1, max_length=50)


class OperatorRoundIn(BaseModel):
    """运营发起一轮：起草或按一句话改；上限、电脑都按作者的（decisions ⑯）。"""

    model_config = ConfigDict(extra="forbid")
    action: Literal["draft", "revise"]
    instruction: str | None = Field(default=None, min_length=1, max_length=4000)
    step_id: str | None = Field(default=None, pattern=STEP_ID)


async def _operator_workdraft(
    request_id: str, workdrafts: KnowbotWorkdraftService, props: KnowbotProposalService
) -> dict[str, Any]:
    request, found, proposals = await props.operator_view(request_id)
    return workdraft_view(request, found, await workdrafts.origins(found)) | {
        "proposals": [proposal_view(p, admin=True) for p in proposals]
    }


@admin_router.get("/knowbot-requests/{request_id}/workdraft")
async def operator_workdraft(
    _: Admin, workdrafts: Workdrafts, props: Proposals, request_id: ItemId
) -> JSONResponse:
    """平台孵化的工作稿（孵化授权已生效才看得到）：格子、出处、运营的提议。"""
    return _ok(await _operator_workdraft(request_id, workdrafts, props))


@admin_router.post("/knowbot-requests/{request_id}/workdraft/proposals")
async def propose(
    admin: Releaser,
    body: ProposalsIn,
    workdrafts: Workdrafts,
    props: Proposals,
    request_id: ItemId,
) -> JSONResponse:
    """运营改格子：存成提议，作者在 web端接受了才进稿。套不上现稿的回 400。"""
    await props.propose(
        request_id, admin.subject, [p.model_dump(mode="json") for p in body.proposals]
    )
    return _ok(await _operator_workdraft(request_id, workdrafts, props), 201)


@admin_router.post(
    "/knowbot-requests/{request_id}/workdraft/proposals/{proposal_id}/withdraw"
)
async def withdraw_proposal(
    _: Releaser,
    workdrafts: Workdrafts,
    props: Proposals,
    request_id: ItemId,
    proposal_id: ItemId,
) -> JSONResponse:
    """收回还没处理的提议。"""
    await props.withdraw(request_id, proposal_id)
    return _ok(await _operator_workdraft(request_id, workdrafts, props))


@admin_router.get("/knowbot-requests/{request_id}/workdraft/preview")
async def operator_preview(
    _: Admin, workdrafts: Workdrafts, request_id: ItemId
) -> JSONResponse:
    """编出的清单与出不了版本的地方（不出版本）。"""
    manifest, problems, version = await workdrafts.operator_preview(request_id)
    return _ok(
        {
            "version": version,
            "manifest": manifest,
            "problems": [{"path": p.path, "message": p.message} for p in problems],
        }
    )


@admin_router.post("/knowbot-requests/{request_id}/workdraft/publish")
async def operator_publish(
    _: Releaser, workdrafts: Workdrafts, request_id: ItemId
) -> JSONResponse:
    """平台模式出一版（decisions 10-29）：准专家记平台；最新一版创意要正式受理过，
    出处照样只认作者原话与作者接受的。"""
    draft = await workdrafts.operator_publish(request_id)
    return _ok(
        {
            "draft_id": str(draft.id),
            "version": draft.version,
            "revision": draft.revision,
            "request_version": draft.request_version,
            "mode": draft.mode,
        },
        status_code=201,
    )


@admin_router.get("/knowbot-requests/{request_id}/workdraft/incubations")
async def operator_rounds(
    _: Admin, incubations: Incubations, request_id: ItemId
) -> JSONResponse:
    """平台发起的各轮（作者自助的那几轮看不到），带交回的建议，运营可以改成提议。"""
    found = await incubations.operator_rounds(request_id)
    return _ok(
        {"incubations": [incubation_view(r, await incubations.gaps(r)) for r in found]}
    )


@admin_router.post("/knowbot-requests/{request_id}/workdraft/incubations")
async def operator_round(
    _: Releaser, body: OperatorRoundIn, incubations: Incubations, request_id: ItemId
) -> JSONResponse:
    """运营发起一轮 AI：在作者的账号、作者上次用的电脑上、用作者的 key 跑，上限是作者
    请平台孵化时设的（decisions ⑯）；建议直接给作者处理。"""
    record = await incubations.operator_start(
        request_id,
        action=body.action,
        instruction=body.instruction,
        step_id=body.step_id,
    )
    return _ok(
        {"incubation": incubation_view(record, await incubations.gaps(record))},
        status_code=201,
    )


@admin_router.post("/knowbot-grants/{grant_id}/decision")
async def decide_platform_test(
    admin: Releaser, body: LightAcceptanceIn, platform: Platform, grant_id: ItemId
) -> JSONResponse:
    """轻受理：接这一次平台测试（进测试栏待发起）或不接（写理由）。"""
    grant = await platform.decide_test(grant_id, body, admin.subject)
    return _ok({"grant": grant_view(grant, platform.now(), admin=True)})


@admin_router.post("/knowbot-requests/{request_id}/hand-back", status_code=204)
async def hand_back(
    admin: Releaser, platform: Platform, request_id: ItemId
) -> Response:
    """平台孵化做完，把草稿交还作者：孵化授权结束，名称转自助。"""
    await platform.hand_back(request_id, admin.subject)
    return Response(status_code=204, headers=NO_STORE)


@admin_router.post("/knowbot-drafts/{draft_id}/review-request", status_code=204)
async def request_review(admin: Releaser, svc: Service, draft_id: ItemId) -> Response:
    """请用户审核这位准专家（web端「审核」里一条待办）。"""
    await svc.operator_draft(draft_id)
    await svc.request_user_review(draft_id, admin.subject)
    return Response(status_code=204, headers=NO_STORE)


@admin_router.get("/knowbot-drafts/{draft_id}/manifest")
async def draft_manifest(admin: Admin, svc: Service, draft_id: ItemId) -> JSONResponse:
    """这位准专家的清单原文：过程页里只读、可复制（SDD 0037）。"""
    found = await svc.operator_draft(draft_id)
    log.info("knowbot manifest read by=%s draft=%s", admin.subject, found.id)
    return _ok({"draft_id": str(found.id), "manifest": found.manifest})


@admin_router.post("/knowbot-drafts/{draft_id}/rules-review", status_code=204)
async def review_rules(admin: Releaser, svc: Service, draft_id: ItemId) -> Response:
    """逐条核过规则出处（C-L1）；记下是谁核的。"""
    await svc.operator_draft(draft_id)
    await svc.review_rules(draft_id, admin.subject)
    return Response(status_code=204, headers=NO_STORE)


@admin_router.post("/knowbot-drafts/{draft_id}/test")
async def start_test(
    admin: Admin, body: StartTestIn, svc: Service, board: Board, draft_id: ItemId
) -> JSONResponse:
    """拿这位准专家按用例跑一次：新编号。在作者上次自助测试的电脑与目录、用作者的 key
    跑（decisions ⑯），费用上限是作者在授权里设的；作者没有可用的电脑回 409
    author_has_no_computer，没授权（或授权不在了）回 409 not_authorised。"""
    test = await svc.start_test(
        draft_id,
        timeout_seconds=body.timeout_seconds,
        starter=admin.subject,
    )
    life = await board.life(str(test.request_id), operator=True)
    return _ok({"test": test_view(test, life, admin=True)}, status_code=201)


@admin_router.post("/knowbot-tests/{test_id}/refresh")
async def refresh(
    _: Admin, svc: Service, board: Board, test_id: ItemId
) -> JSONResponse:
    await svc.operator_test(test_id)
    test = await svc.refresh(test_id)
    life = await board.life(str(test.request_id), operator=True)
    return _ok({"test": test_view(test, life, admin=True)})


@admin_router.post("/knowbot-tests/{test_id}/reset", status_code=204)
async def reset(_: Releaser, svc: Service, test_id: ItemId) -> Response:
    """超时还没结论：记平台出错（超时）结束这一次，准专家回到待测。"""
    await svc.operator_test(test_id)
    await svc.reset_stuck(test_id)
    return Response(status_code=204, headers=NO_STORE)


@admin_router.get("/knowbot-tests/{test_id}/outputs")
async def test_outputs(admin: Admin, svc: Service, test_id: ItemId) -> JSONResponse:
    """这次测试每条用例的成稿与各步交回物：测试人员验证这一稿用（SDD 0037）。"""
    test, request = await svc.operator_test(test_id)
    if test.mode == "rerun":
        # 平台重跑：审核员看结论、不看原始产出（SDD 0046 第五节）
        raise NotFoundError("No such test", code="test_not_found")
    # 产出是用户的分析内容：谁、哪一次、何时读的留一行，不记正文（审读 10-19）
    log.info("knowbot outputs read by=%s test=%s", admin.subject, test.id)
    return _ok(outputs_view(test, await svc.test_outputs(test, request), admin=True))


async def _verify_operator_expert(board: KnowbotBoard, expert_id: str) -> None:
    eid = parse_id(expert_id, "expert")
    expert = await board.repo.get_expert(eid)
    if expert is None:
        raise NotFoundError("No such expert", code="expert_not_found")
    life = await board.life(str(expert.request_id), operator=True)
    if not any(e.id == eid for e in life.experts):
        raise NotFoundError("No such expert", code="expert_not_found")


async def _verify_operator_delivery(board: KnowbotBoard, delivery_id: str) -> None:
    did = parse_id(delivery_id, "delivery")
    delivery = await board.repo.get_delivery(did)
    if delivery is None:
        raise NotFoundError("No such delivery", code="delivery_not_found")
    life = await board.life(str(delivery.request_id), operator=True)
    if not any(d.id == did for d in life.deliveries):
        raise NotFoundError("No such delivery", code="delivery_not_found")


@admin_router.post("/knowbot-experts/{expert_id}/return")
async def return_expert(
    admin: Releaser, experts: Experts, board: Board, expert_id: ItemId
) -> JSONResponse:
    """交回：把这一版通知用户（用户同意门开始）。"""
    await _verify_operator_expert(board, expert_id)
    found = await experts.return_to_user(expert_id, admin.subject)
    return _ok({"expert_id": str(found.id), "stage": stage_of(found)})


async def _operator_delivery(board: KnowbotBoard, found: Any) -> dict[str, Any]:
    """回包从运营看的那一份里取：审完了的不带绑定、许可预查、自动查（同列表）。
    撤回、作废后这个名称运营已看不到了的，回只有结论的那一份。"""
    try:
        life = await board.life(str(found.request_id), operator=True)
    except NotFoundError:
        request = await board.repo.get_request(found.request_id)
        assert request is not None
        life = Life(request=request)
    shown = next(
        (d for d in life.deliveries if d.id == found.id),
        replace(found, bindings=[], binding_check=None, auto_check=None),
    )
    return delivery_view(shown, life, admin=True)


@admin_router.get("/knowbot-categories")
async def categories(_: Admin) -> JSONResponse:
    """交付时可选的类目（契约 knowbot-delivery/v1；名称由前端照 knowmart 的叫法写）。"""
    return _ok({"categories": list(CATEGORY_CODES)})


@admin_router.post("/knowbot-deliveries/{delivery_id}/rerun")
async def start_rerun(
    admin: Releaser, deliveries: Deliveries, board: Board, delivery_id: ItemId
) -> JSONResponse:
    """平台发起重跑（公开、自助测的）：在作者的账号下、作者的电脑上、用作者的 key 跑
    （decisions ⑯）。"""
    await _verify_operator_delivery(board, delivery_id)
    test = await deliveries.start_rerun(delivery_id, admin.subject)
    return _ok({"test_id": str(test.id), "status": test.status}, 201)


@admin_router.post("/knowbot-deliveries/{delivery_id}/review")
async def review_delivery(
    admin: Releaser,
    body: DeliveryReviewIn,
    deliveries: Deliveries,
    board: Board,
    delivery_id: ItemId,
) -> JSONResponse:
    """审一次交付：过（公开的选类目）即交 knowmart；不过写理由。交不成记下，可重试。"""
    await _verify_operator_delivery(board, delivery_id)
    found = await deliveries.review(delivery_id, body, admin.subject)
    return _ok(await _operator_delivery(board, found))


class VoidBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=1000)


@admin_router.post("/knowbot-deliveries/{delivery_id}/void")
async def void_delivery(
    admin: Releaser,
    body: VoidBody,
    deliveries: Deliveries,
    board: Board,
    delivery_id: ItemId,
) -> JSONResponse:
    """作废审过了却一直交不成的那一次（写理由）：撤依据，这一版回到「等作者提交」。"""
    await _verify_operator_delivery(board, delivery_id)
    found = await deliveries.void(delivery_id, admin.subject, body.reason)
    return _ok(await _operator_delivery(board, found))


@admin_router.post("/knowbot-deliveries/{delivery_id}/deliver")
async def retry_delivery(
    _: Releaser, deliveries: Deliveries, board: Board, delivery_id: ItemId
) -> JSONResponse:
    """重试交付：审过了、上次交 knowmart 没成功的。"""
    await _verify_operator_delivery(board, delivery_id)
    found = await deliveries.retry(delivery_id)
    return _ok(await _operator_delivery(board, found))


@admin_router.post("/knowbot-experts/{expert_id}/stop")
async def stop_expert(
    admin: Releaser, body: StopIn, experts: Experts, board: Board, expert_id: ItemId
) -> JSONResponse:
    """停用上架了的这一版（写理由）；knowmart 那一版跟着停。"""
    await _verify_operator_expert(board, expert_id)
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


# ---------- web端：作者独立方法版本；没有管理员代确认入口 ----------
@web_router.get("/knowbot-requests/{request_id}/formal-method-sources")
async def formal_sources(
    principal: User, methods: FormalMethods, request_id: ItemId
) -> JSONResponse:
    return _ok(await methods.sources(principal.subject, request_id))


@web_router.post("/method-units/{method_id}/validations")
async def validate_formal_method(
    principal: User,
    methods: FormalMethods,
    method_id: ItemId,
    body: ValidateFormalMethod,
) -> JSONResponse:
    return _ok(await methods.validate(principal.subject, method_id, body))


@web_router.get("/method-units/{method_id}/validations/{run_id}")
async def formal_validation(
    principal: User,
    methods: FormalMethods,
    method_id: ItemId,
    run_id: ItemId,
    request_id: str,
) -> JSONResponse:
    return _ok(
        await methods.validation(principal.subject, method_id, request_id, run_id)
    )


@web_router.put("/method-units/{method_id}/formal-versions/{version}")
async def freeze_formal_version(
    principal: User,
    methods: FormalMethods,
    method_id: ItemId,
    version: Annotated[int, Path(ge=1, le=999999)],
    body: FreezeFormalMethod,
) -> JSONResponse:
    return _ok(
        method_version_view(
            await methods.freeze(principal.subject, method_id, version, body)
        )
    )


@web_router.get("/method-units/{method_id}/formal-versions/{version}")
async def formal_version(
    principal: User,
    methods: FormalMethods,
    method_id: ItemId,
    version: Annotated[int, Path(ge=1, le=999999)],
) -> JSONResponse:
    return _ok(
        method_version_view(await methods.get(principal.subject, method_id, version))
    )


@web_router.get("/method-units")
async def my_methods(
    principal: User,
    methods: Methods,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    offset: Annotated[int, Query(ge=0)] = 0,
    formal: bool = False,
) -> JSONResponse:
    if formal:
        formal_methods = FormalMethodService(methods.repo, runtime_clients())
        return _ok(
            {
                "items": await formal_methods.listing(
                    principal.subject, limit=limit, offset=offset
                )
            }
        )
    return _ok(
        {"items": await methods.listing(principal.subject, limit=limit, offset=offset)}
    )


@web_router.put("/method-units/{method_id}/versions/{version}")
async def freeze_method_version(
    principal: User,
    methods: Methods,
    method_id: ItemId,
    version: Annotated[int, Path(ge=1, le=999999)],
    body: FreezeAtomicMethod | FreezeMethod,
) -> JSONResponse:
    """作者确认来源版本和命名输入槽；同版本同请求重放，不覆盖旧内容。"""
    return _ok(
        method_version_view(
            await methods.publish(principal.subject, method_id, version, body)
        )
    )


@web_router.get("/method-units/{method_id}/versions/{version}")
async def my_method_version(
    principal: User,
    methods: Methods,
    method_id: ItemId,
    version: Annotated[int, Path(ge=1, le=999999)],
) -> JSONResponse:
    return _ok(
        method_version_view(await methods.get(principal.subject, method_id, version))
    )


@web_router.post("/method-units/{method_id}/versions/{version}/preview-binding")
async def preview_method_binding(
    principal: User,
    methods: Methods,
    method_id: ItemId,
    version: Annotated[int, Path(ge=1, le=999999)],
    body: BindMethod,
) -> JSONResponse:
    """只核对候选引用与输入连接，不保存、不发布、不启动执行。"""
    return _ok(
        await methods.preview_binding(principal.subject, method_id, version, body)
    )


@web_router.post("/method-units/{method_id}/versions/{version}/bind")
async def bind_method_version(
    principal: User,
    methods: Methods,
    method_id: ItemId,
    version: Annotated[int, Path(ge=1, le=999999)],
    body: BindMethod,
) -> JSONResponse:
    """作者明确选定精确版本；只改自己的工作稿，不发布、不运行。"""
    found = await methods.bind_version(principal.subject, method_id, version, body)
    request, _ = await methods.workdrafts.get(principal.subject, str(body.request_id))
    return _ok(workdraft_view(request, found, await methods.workdrafts.origins(found)))


@web_router.post("/knowbot-drafts/{draft_id}/manual-release")
async def request_manual_release(
    user: User, service: ManualReleases, draft_id: ItemId, body: ManualReleaseIn
) -> JSONResponse:
    identifier = str(parse_id(draft_id, "draft"))
    return _ok(release_view(await service.request(user.subject, identifier, body)), 201)


@web_router.get("/knowbot-manual-releases")
async def my_manual_releases(user: User, service: ManualReleases) -> JSONResponse:
    return _ok(
        {
            "items": [
                release_view(r)
                for r in await service.repo.list_manual_releases(
                    owner_subject=user.subject
                )
            ]
        }
    )


@admin_router.get("/knowbot-manual-releases")
async def manual_release_queue(_: Releaser, service: ManualReleases) -> JSONResponse:
    # Only the author-opted-in package is exposed; no other private drafts/data.
    return _ok(
        {"items": [release_view(r) for r in await service.repo.list_manual_releases()]}
    )


@admin_router.get("/knowbot-manual-releases/{release_id}")
async def manual_release_detail(
    _: Releaser, service: ManualReleases, release_id: ItemId
) -> JSONResponse:
    found = await service.repo.get_manual_release(parse_id(release_id))
    if found is None:
        raise NotFoundError("No release request", code="manual_release_not_found")
    draft = await service.repo.get_draft(found.draft_id)
    return _ok(
        {
            "request": release_view(found),
            "manifest": None if draft is None else draft.manifest,
        }
    )


@admin_router.post("/knowbot-manual-releases/{release_id}/decision")
async def decide_manual_release(
    admin: Releaser,
    service: ManualReleases,
    release_id: ItemId,
    body: ManualReleaseDecision,
) -> JSONResponse:
    return _ok(
        release_view(
            await service.decide(str(parse_id(release_id)), admin.subject, body)
        )
    )
