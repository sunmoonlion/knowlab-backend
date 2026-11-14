"""创意到专家的装配：每个请求一个数据库会话；外部客户端进程内共用（令牌缓存在里面）。"""

from __future__ import annotations

import os
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.formal_methods import FormalMethodService
from app.application.services.knowbot_board import KnowbotBoard
from app.application.services.knowbot_deliveries import KnowbotDeliveryService
from app.application.services.knowbot_experts import KnowbotExpertService
from app.application.services.knowbot_incubation import KnowbotIncubationService
from app.application.services.knowbot_library import KnowbotLibraryService
from app.application.services.knowbot_methods import KnowbotMethodService
from app.application.services.knowbot_platform import KnowbotPlatformService
from app.application.services.knowbot_proposals import KnowbotProposalService
from app.application.services.knowbot_questions import KnowbotQuestionService
from app.application.services.knowbot_service import KnowbotCreationService
from app.application.services.knowbot_test_data import KnowbotTestData
from app.application.services.knowbot_workdraft import KnowbotWorkdraftService
from app.infrastructure.external.knowbase_data_v2 import KnowbaseDataV2
from app.infrastructure.external.knowbase_delivery import KnowbaseDelivery
from app.infrastructure.external.knowbase_library import KnowbaseLibrary
from app.infrastructure.external.knowbot_clients import (
    KnowbenchIncubationClient,
    KnowmartDeliveryClient,
    RuntimeTestClients,
)
from app.infrastructure.external.knowfeed_supply import KnowfeedSupply
from app.infrastructure.repositories.knowbots import SqlKnowlabRepository


@lru_cache(maxsize=1)
def runtime_clients() -> RuntimeTestClients:
    return RuntimeTestClients()


@lru_cache(maxsize=1)
def knowmart_client() -> KnowmartDeliveryClient:
    return KnowmartDeliveryClient()


@lru_cache(maxsize=1)
def incubation_client() -> KnowbenchIncubationClient:
    """交 knowbench 跑孵化的一轮（服务关系 knowbench:incubate）。"""
    return KnowbenchIncubationClient()


def knowbot_question_service(session: AsyncSession) -> KnowbotQuestionService:
    return KnowbotQuestionService(SqlKnowlabRepository(session), runtime_clients())


@lru_cache(maxsize=1)
def _knowbase_delivery() -> KnowbaseDelivery:
    return KnowbaseDelivery()


def knowbase_delivery() -> KnowbaseDelivery | None:
    """交付、测试证据、名录要的 knowbase 接口（服务关系 user-data-directory、
    user-data-output、user-data-grant-test 与作者本人令牌）；没配 knowbase 就没有。"""
    if not os.environ.get("KNOWBASE_INTERNAL_URL", "").strip():
        return None
    return _knowbase_delivery()


def knowbot_service(session: AsyncSession) -> KnowbotCreationService:
    repo = SqlKnowlabRepository(session)
    questions = KnowbotQuestionService(repo, runtime_clients())
    return KnowbotCreationService(
        repo,
        runtime_clients(),
        on_refresh=questions.resend,
        test_data=knowbot_test_data(session),
        knowbase=knowbase_delivery(),
    )


def knowbot_delivery_service(session: AsyncSession) -> KnowbotDeliveryService:
    """交付（SDD 0046 第五节）：提交、轻审、重跑、审核、交 knowmart、名录。"""
    repo = SqlKnowlabRepository(session)
    return KnowbotDeliveryService(
        repo,
        knowmart_client(),
        knowbase_delivery(),
        knowbot_service(session),
        KnowbotExpertService(repo, knowmart_client()),
    )


def knowbot_test_data(session: AsyncSession) -> KnowbotTestData | None:
    """测试集与测试里专家提出的数据（SDD 0046 第三节）；没配 knowbase 就没有。
    补采开关 `KNOWLAB_DATA_SUPPLY_ENABLED` 默认关（fail-closed，同 knowbench）。"""
    if not os.environ.get("KNOWBASE_INTERNAL_URL", "").strip():
        return None
    from app.application.services.knowbot_service import MAX_WAIT_SECONDS

    repo = SqlKnowlabRepository(session)
    return KnowbotTestData(
        repo,
        KnowbaseDataV2(),
        KnowfeedSupply(),
        KnowbotQuestionService(repo, runtime_clients()),
        supply_enabled=os.environ.get("KNOWLAB_DATA_SUPPLY_ENABLED", "").lower()
        in ("1", "true", "yes"),
        max_wait_seconds=MAX_WAIT_SECONDS,
    )


def knowbot_expert_service(session: AsyncSession) -> KnowbotExpertService:
    """专家的交回、两道门、上架、停用（SDD 0037）。"""
    return KnowbotExpertService(
        SqlKnowlabRepository(session),
        knowmart_client(),
        knowbase=knowbase_delivery(),
        creation=knowbot_service(session),
    )


def knowbot_workdraft_service(session: AsyncSession) -> KnowbotWorkdraftService:
    """工作稿：作者直接改、导入、复制、预览、出一版（专家孵化方案）。"""
    return KnowbotWorkdraftService(SqlKnowlabRepository(session))


def knowbot_incubation_service(session: AsyncSession) -> KnowbotIncubationService:
    """AI 孵化的一轮：发起、取回、取消、接受建议（专家孵化方案第八、九节）。"""
    return KnowbotIncubationService(
        SqlKnowlabRepository(session), incubation_client(), runtime_clients()
    )


@lru_cache(maxsize=1)
def knowbase_library() -> KnowbaseLibrary:
    """凭作者本人令牌读 knowbase 的知识库栏与读法（没配 KNOWBASE_INTERNAL_URL 时调用回
    503 knowbase_unconfigured）。"""
    return KnowbaseLibrary()


def knowbot_library_service(session: AsyncSession) -> KnowbotLibraryService:
    """知识库栏、工作稿里挑数据（SDD 0046 第六节，专家孵化方案第 7.1、8.2 节）。"""
    return KnowbotLibraryService(SqlKnowlabRepository(session), knowbase_library())


def knowbot_board(session: AsyncSession) -> KnowbotBoard:
    """两端的栏、大类、详情页（只读）。"""
    return KnowbotBoard(SqlKnowlabRepository(session))


def knowbot_platform_service(session: AsyncSession) -> KnowbotPlatformService:
    """孵化、测试在自助与平台之间切换（SDD 0046 第四节）。"""
    return KnowbotPlatformService(SqlKnowlabRepository(session))


def knowbot_proposal_service(session: AsyncSession) -> KnowbotProposalService:
    """运营对工作稿的提议，作者逐条处理（专家孵化方案第十二节）。"""
    return KnowbotProposalService(SqlKnowlabRepository(session))


def knowbot_method_service(session: AsyncSession) -> KnowbotMethodService:
    return KnowbotMethodService(SqlKnowlabRepository(session))


def formal_method_service(session: AsyncSession) -> FormalMethodService:
    return FormalMethodService(SqlKnowlabRepository(session), RuntimeTestClients())
