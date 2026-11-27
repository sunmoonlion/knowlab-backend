"""创意到专家的接口（SDD 0027、0037）。代码里仍叫 request、draft、knowbot。

用户可在工坊孵化方法，并查看本人展示给 KnowLab 的知识库资料。

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
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.errors.exceptions import (
    NotFoundError,
)
from app.application.ports.knowbots import DraftRecord
from app.application.services.formal_methods import FormalMethodService
from app.application.services.knowbot_board import (
    KnowbotBoard,
    Life,
    acceptance_items,
    admin_counts,
    delivery_items,
    incubation_items,
    lab_test_items,
    light_items,
    timeline,
)
from app.application.services.knowbot_deliveries import KnowbotDeliveryService
from app.application.services.knowbot_experts import (
    KnowbotExpertService,
    shown_to_author,
    stage_of,
)
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
    method_version_view,
)
from app.application.services.knowbot_platform import KnowbotPlatformService
from app.application.services.knowbot_proposals import KnowbotProposalService
from app.application.services.knowbot_questions import AnswerIn, KnowbotQuestionService
from app.application.services.knowbot_service import KnowbotCreationService, parse_id
from app.application.services.knowbot_workdraft import KnowbotWorkdraftService
from app.application.services.method_drafts import (
    BindGapResolutionIn,
    CreateMethodDraftIn,
    HandoffSeedIn,
    MethodDraftService,
    PatchMethodDraftIn,
    SpawnGapChildIn,
)
from app.bootstrap.knowbots import (
    formal_method_service,
    knowbot_board,
    knowbot_delivery_service,
    knowbot_expert_service,
    knowbot_incubation_service,
    knowbot_library_service,
    knowbot_platform_service,
    knowbot_proposal_service,
    knowbot_question_service,
    knowbot_service,
    knowbot_workdraft_service,
    method_draft_service,
)
from app.domain.knowbots.delivery import (
    CATEGORY_CODES,
    DeliveryReviewIn,
)
from app.domain.knowbots.formal_method_admission import (
    FreezeFormalMethod,
    ValidateFormalMethod,
)
from app.domain.knowbots.formal_method_execution import (
    CancelFormalExecution,
    DecideFormalExecution,
    ExecuteFormalMethod,
    PrepareBranchReview,
    ReceiveBranch,
)
from app.domain.knowbots.lifecycle import (
    AcceptanceIn,
    StopIn,
)
from app.domain.knowbots.manual_release import ManualReleaseDecision
from app.domain.knowbots.platform_grants import (
    LightAcceptanceIn,
)
from app.domain.knowbots.selection import NodeEdit
from app.domain.knowbots.workdraft import STEP_ID, MethodTemplate
from app.domain.security import Principal
from app.infrastructure.repositories.knowbots import SqlKnowlabRepository
from app.infrastructure.storage.postgres import get_db_session
from app.interfaces.endpoints.knowbot_views import (
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


def formal_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> FormalMethodService:
    return formal_method_service(session)


def draft_v2_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> MethodDraftService:
    return method_draft_service(session)


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
FormalMethods = Annotated[FormalMethodService, Depends(formal_service)]
MethodDrafts = Annotated[MethodDraftService, Depends(draft_v2_service)]
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
    }


# ---------- web端（用户） ----------


class AnswerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str | None = Field(default=None, pattern=r"^(approve|deny)$")
    option_id: str | None = Field(default=None, max_length=64)
    text: str | None = Field(default=None, max_length=4000)

    def given(self) -> AnswerIn:
        return AnswerIn(
            decision=self.decision, option_id=self.option_id, text=self.text
        )


# ---------- web端：知识库栏（SDD 0046 第六节） ----------
@web_router.get("/knowbot-library")
async def my_library(
    _: User, library: Library, user_token: KnowbaseToken
) -> JSONResponse:
    """知识库栏：我在 knowbase 展示给 knowlab 的资料（凭我本人的令牌现取，knowlab
    不存）。在 knowbase 管，这里只列。"""
    return _ok({"materials": await library.shown(user_token)})


# ---------- web端：工作稿（专家孵化方案第五至十一节） ----------


# ---------- web端：AI 孵化的一轮（专家孵化方案第八、九节） ----------
class IncubationIn(BaseModel):
    """发起一轮：起草（draft）或按我的一句话改（revise，可以指定一步）；费用上限我设，
    电脑与目录不给就用上次的。"""

    model_config = ConfigDict(extra="forbid")
    selection_revision: int = Field(ge=1)
    node_edit: NodeEdit | None = None
    action: Literal["draft", "revise"]
    instruction: str | None = Field(default=None, min_length=1, max_length=4000)
    budget_limit: str = Field(
        default=DEFAULT_BUDGET, pattern=r"^[0-9]{1,6}(\.[0-9]{1,2})?$"
    )
    environment_id: str | None = Field(default=None, pattern=r"^[0-9a-f-]{36}$")
    workspace_root: str | None = Field(default=None, min_length=1, max_length=1024)


async def studio_request(
    principal: User, incubations: Incubations, request_id: ItemId
) -> str:
    row = await incubations.repo.get_workdraft(parse_id(request_id, "request"))
    if (
        row is None
        or row.owner_subject != principal.subject
        or row.method_id is None
        or row.draft_contract is None
    ):
        raise NotFoundError("No such studio draft", code="method_draft_not_found")
    return request_id


StudioRequest = Annotated[str, Depends(studio_request)]


@web_router.get("/knowbot-requests/{request_id}/workdraft/computers")
async def incubation_computers(
    principal: User, incubations: Incubations, request_id: StudioRequest
) -> JSONResponse:
    """AI 孵化选电脑：我接入过的电脑与上次用的那台与目录。"""
    return _ok(await incubations.computers(principal.subject, request_id))


@web_router.get("/knowbot-requests/{request_id}/workdraft/incubations")
async def my_incubations(
    principal: User, incubations: Incubations, request_id: StudioRequest
) -> JSONResponse:
    """这个名称的各轮（第几轮、状态、花费），第一轮在前。"""
    found = await incubations.rounds(principal.subject, request_id)
    return _ok({"incubations": [incubation_view(r) for r in found]})


@web_router.post("/knowbot-requests/{request_id}/workdraft/incubations")
async def start_incubation(
    principal: User,
    body: IncubationIn,
    incubations: Incubations,
    request_id: StudioRequest,
) -> JSONResponse:
    """发起一轮（「AI 起草」「让 AI 改…」）：在我的电脑上、用我的 key 跑，费用上限我设。
    这份工作稿已有一轮在跑回 409 incubation_running；没有可用的电脑回 409
    author_has_no_computer。"""
    record = await incubations.start(
        principal.subject,
        request_id,
        action=body.action,
        instruction=body.instruction,
        budget_limit=body.budget_limit,
        environment_id=body.environment_id,
        workspace_root=body.workspace_root,
        selection_revision=body.selection_revision,
        node_edit=body.node_edit,
    )
    return _ok(
        {"incubation": incubation_view(record, await incubations.gaps(record))},
        status_code=201,
    )


class ConfirmSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    method_id: UUID
    cases: dict[str, list[str]] = Field(max_length=16)
    template: MethodTemplate
    step_text: dict[str, str] = Field(max_length=16)
    confirmed: Literal[True]
    confirm_brief: bool = False


@web_router.post(
    "/knowbot-requests/{request_id}/workdraft/incubations/{incubation_id}/confirm-selection"
)
async def confirm_selection(
    principal: User,
    request_id: StudioRequest,
    incubation_id: ItemId,
    body: ConfirmSelection,
    incubations: Incubations,
):
    from app.application.services.incubation_selection import SelectionService

    request, record = await incubations.mine(
        principal.subject, request_id, incubation_id
    )
    service = SelectionService(incubations.repo, incubations.runtimes)
    found = await service.confirm(principal.subject, request, record, body)
    return _ok({"revision": found.revision})


@web_router.get("/knowbot-requests/{request_id}/workdraft/incubations/{incubation_id}")
async def my_incubation(
    principal: User,
    incubations: Incubations,
    request_id: StudioRequest,
    incubation_id: ItemId,
) -> JSONResponse:
    """一轮：状态与交回的建议（每条的出处、核得上没有、我怎么处理的）、缺口、自查。"""
    _, record = await incubations.mine(principal.subject, request_id, incubation_id)
    return _ok({"incubation": incubation_view(record, await incubations.gaps(record))})


@web_router.post(
    "/knowbot-requests/{request_id}/workdraft/incubations/{incubation_id}/refresh"
)
async def refresh_incubation(
    principal: User,
    incubations: Incubations,
    request_id: StudioRequest,
    incubation_id: ItemId,
) -> JSONResponse:
    """刷新一次（页面轮询）：没结束的问一次运行方，成了的存交回、核出处、缺口进待办。"""
    record = await incubations.refresh(principal.subject, request_id, incubation_id)
    return _ok({"incubation": incubation_view(record, await incubations.gaps(record))})


@web_router.post(
    "/knowbot-requests/{request_id}/workdraft/incubations/{incubation_id}/cancel"
)
async def cancel_incubation(
    principal: User,
    incubations: Incubations,
    request_id: StudioRequest,
    incubation_id: ItemId,
) -> JSONResponse:
    """取消没结束的一轮。"""
    record = await incubations.cancel(principal.subject, request_id, incubation_id)
    return _ok({"incubation": incubation_view(record, await incubations.gaps(record))})


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
@web_router.get(
    "/knowbot-requests/{request_id}/formal-test-suites/{suite_id}/versions/{version}"
)
async def formal_test_suite(
    principal: User,
    methods: FormalMethods,
    request_id: ItemId,
    suite_id: ItemId,
    version: Annotated[int, Path(ge=1)],
) -> JSONResponse:
    return _ok(
        await methods.test_suite(principal.subject, request_id, suite_id, version)
    )


@web_router.post("/method-units/{method_id}/executions")
async def execute_formal_method(
    principal: User,
    methods: FormalMethods,
    method_id: ItemId,
    body: ExecuteFormalMethod,
) -> JSONResponse:
    return _ok(await methods.execute(principal.subject, method_id, body))


@web_router.get("/method-units/{method_id}/executions/{run_id}")
async def formal_execution(
    principal: User,
    methods: FormalMethods,
    method_id: ItemId,
    run_id: ItemId,
    request_id: str,
) -> JSONResponse:
    return _ok(
        await methods.execution(principal.subject, method_id, request_id, run_id)
    )


@web_router.post("/method-units/{method_id}/executions/{run_id}/step-deliveries")
async def receive_trial_branch(
    principal: User,
    methods: FormalMethods,
    method_id: ItemId,
    run_id: ItemId,
    body: ReceiveBranch,
) -> JSONResponse:
    return _ok(
        await methods.receive_trial_branch(principal.subject, method_id, run_id, body)
    )


@web_router.post("/method-units/{method_id}/executions/{run_id}/branch-review")
async def prepare_trial_review(
    principal: User,
    methods: FormalMethods,
    method_id: ItemId,
    run_id: ItemId,
    body: PrepareBranchReview,
) -> JSONResponse:
    return _ok(
        await methods.receive_trial_branch(principal.subject, method_id, run_id, body)
    )


@web_router.post("/method-units/{method_id}/executions/{run_id}/cancel")
async def cancel_formal_execution(
    principal: User,
    methods: FormalMethods,
    method_id: ItemId,
    run_id: ItemId,
    body: CancelFormalExecution,
) -> JSONResponse:
    return _ok(
        await methods.cancel_execution(
            principal.subject, method_id, str(body.request_id), run_id
        )
    )


@web_router.post("/method-units/{method_id}/executions/{run_id}/actions/{call_id}")
async def decide_formal_execution(
    principal: User,
    methods: FormalMethods,
    method_id: ItemId,
    run_id: ItemId,
    call_id: ItemId,
    body: DecideFormalExecution,
) -> JSONResponse:
    return _ok(
        await methods.decide_execution(
            principal.subject, method_id, run_id, call_id, body
        )
    )


@web_router.get("/knowbot-requests/{request_id}/planning-capabilities")
async def planning_capabilities(
    principal: User, methods: FormalMethods, request_id: ItemId
):
    return _ok(await methods.planning_capabilities(principal.subject, request_id))


@web_router.get("/method-directory/browse/{scene}")
async def browse_directory(
    principal: User,
    methods: FormalMethods,
    scene: Literal["owned", "shared"],
    kind: Literal["expert", "tool"],
    category: Annotated[str | None, Query(max_length=64, pattern=r"^[a-z_.]+$")] = None,
    offset: Annotated[int, Query(ge=0, le=2147483647)] = 0,
    request_id: UUID | None = None,
):
    runtime, declaration = await methods._incubation_basis(
        principal.subject, str(request_id) if request_id else None
    )
    body = {"kind": kind, "offset": offset, "category": category}
    if declaration:
        body["question"] = " ".join([declaration["title"], declaration["purpose"]])[
            :2000
        ]
    return _ok(
        await methods.runtimes.browse_directory(runtime, principal.subject, scene, body)
    )


@web_router.get("/method-directory/sources")
async def directory_sources(principal: User, methods: FormalMethods):
    return _ok(await methods.sources(principal.subject))


class InlineSourceRegistrationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["script", "instruction"]
    name: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=262144)
    request_id: UUID | None = None


@web_router.put("/method-directory/sources/{source_id}/versions/{version}")
async def register_directory_source(
    principal: User,
    methods: FormalMethods,
    source_id: ItemId,
    version: Annotated[int, Path(ge=1, le=2147483647)],
    body: InlineSourceRegistrationIn,
) -> JSONResponse:
    item = await methods.register_source(
        principal.subject,
        source_id,
        version,
        {
            "kind": body.kind,
            "name": body.name,
            "content": body.content,
        },
        request_id=str(body.request_id) if body.request_id else None,
    )
    return _ok({"source": item})


@web_router.get("/slot-vocabulary")
async def slot_vocabulary(_: User) -> JSONResponse:
    from app.domain.knowbots.slot_vocabulary import SLOT_MEANING_CATALOG

    return _ok(
        {
            "vocab_version": "1",
            "domains": [
                {"domain_id": key, "label": domain["label"]}
                for key, domain in SLOT_MEANING_CATALOG.items()
            ],
            "presets": [
                "plain_question",
                "structured_record",
                "data_table",
                "cited_markdown",
                "material_ref",
                "entity_key_list",
            ],
            "archetypes": [
                "S1",
                "S2",
                "S3",
                "S4",
            ],
        }
    )


@web_router.get("/method-drafts")
async def list_method_drafts(principal: User, drafts: MethodDrafts) -> JSONResponse:
    return _ok({"items": await drafts.list_drafts(principal.subject)})


@web_router.post("/method-drafts", status_code=201)
async def create_method_draft(
    principal: User, body: CreateMethodDraftIn, drafts: MethodDrafts
) -> JSONResponse:
    return _ok(await drafts.create(principal.subject, body), status_code=201)


@web_router.post("/method-drafts/from-handoff", status_code=201)
async def create_method_draft_from_handoff(
    principal: User, body: HandoffSeedIn, drafts: MethodDrafts
) -> JSONResponse:
    return _ok(
        await drafts.create_from_handoff(principal.subject, body), status_code=201
    )


@web_router.get("/method-drafts/{method_id}")
async def get_method_draft(
    principal: User, method_id: ItemId, drafts: MethodDrafts
) -> JSONResponse:
    return _ok(await drafts.get(principal.subject, method_id))


@web_router.delete("/method-drafts/{method_id}", status_code=204)
async def delete_method_draft(
    principal: User,
    method_id: ItemId,
    revision: Annotated[int, Query(ge=0)],
    drafts: MethodDrafts,
) -> Response:
    await drafts.delete(principal.subject, method_id, revision)
    return Response(status_code=204, headers=NO_STORE)


@web_router.patch("/method-drafts/{method_id}")
async def patch_method_draft(
    principal: User,
    method_id: ItemId,
    body: PatchMethodDraftIn,
    drafts: MethodDrafts,
) -> JSONResponse:
    return _ok(await drafts.patch(principal.subject, method_id, body))


@web_router.post("/method-drafts/{method_id}/runtime-anchor")
async def ensure_method_draft_runtime_anchor(
    principal: User, method_id: ItemId, drafts: MethodDrafts
) -> JSONResponse:
    req = await drafts.ensure_runtime_anchor(principal.subject, method_id)
    return _ok({"request_id": str(req.id), "runtime": req.runtime})


@web_router.post("/method-drafts/{method_id}/gaps/spawn", status_code=201)
async def spawn_method_draft_gap_child(
    principal: User,
    method_id: ItemId,
    body: SpawnGapChildIn,
    drafts: MethodDrafts,
) -> JSONResponse:
    return _ok(
        await drafts.spawn_gap_child(principal.subject, method_id, body),
        status_code=201,
    )


@web_router.post("/method-drafts/{method_id}/gaps/bind")
async def bind_method_draft_gap_resolution(
    principal: User,
    method_id: ItemId,
    body: BindGapResolutionIn,
    drafts: MethodDrafts,
) -> JSONResponse:
    return _ok(await drafts.bind_gap_resolution(principal.subject, method_id, body))


@web_router.get("/method-directory/shared-tools")
async def directory_shared_tools(
    principal: User, methods: FormalMethods, after: UUID | None = None
):
    return _ok(
        await methods.shared_tools(
            principal.subject, None, after=str(after) if after else None
        )
    )


@web_router.get("/knowbot-requests/{request_id}/formal-method-sources")
async def formal_sources(
    principal: User, methods: FormalMethods, request_id: ItemId
) -> JSONResponse:
    return _ok(await methods.sources(principal.subject, request_id))


class IncubationAssetSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: UUID
    revision: int = Field(strict=True, ge=1)
    digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")


@web_router.put(
    "/knowbot-requests/{request_id}/incubation-tools/{method_id}/versions/{version}/installation"
)
async def select_incubation_binding(
    principal: User,
    methods: FormalMethods,
    request_id: ItemId,
    method_id: UUID,
    version: Annotated[int, Path(ge=1)],
    body: IncubationAssetSelection,
):
    from app.domain.knowbots.formal_method import MethodRef

    return _ok(
        await methods.select_incubation_binding(
            principal.subject,
            request_id,
            MethodRef(method_id=method_id, version=version, digest=body.digest),
            body.asset_id,
            body.revision,
        )
    )


@web_router.get("/knowbot-requests/{request_id}/incubation-tools")
async def shared_incubation_tools(
    principal: User,
    methods: FormalMethods,
    request_id: ItemId,
    after: UUID | None = None,
):
    return _ok(
        await methods.shared_tools(
            principal.subject, request_id, after=str(after) if after else None
        )
    )


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


class FormalListingConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")


@web_router.put("/method-units/{method_id}/formal-versions/{version}/self-listing")
async def confirm_formal_self_listing(
    principal: User,
    methods: FormalMethods,
    method_id: UUID,
    version: Annotated[int, Path(ge=1, le=999999)],
    body: FormalListingConfirmation,
):
    from app.domain.knowbots.formal_method import MethodRef

    return _ok(
        await methods.list_for_self(
            principal.subject,
            MethodRef(method_id=method_id, version=version, digest=body.digest),
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
    methods: FormalMethods,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    offset: Annotated[int, Query(ge=0)] = 0,
    request_id: UUID | None = None,
) -> JSONResponse:
    return _ok(
        {
            "items": await methods.listing(
                principal.subject,
                limit=limit,
                offset=offset,
                request_id=str(request_id) if request_id else None,
            )
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
