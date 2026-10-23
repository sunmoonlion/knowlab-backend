"""创意到专家的装配：每个请求一个数据库会话；外部客户端进程内共用（令牌缓存在里面）。"""

from __future__ import annotations

import os
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports.knowbots import KnowledgeBase
from app.application.services.knowbot_board import KnowbotBoard
from app.application.services.knowbot_data import KnowbotDataService
from app.application.services.knowbot_data_read import KnowbotDataReader
from app.application.services.knowbot_experts import KnowbotExpertService
from app.application.services.knowbot_questions import KnowbotQuestionService
from app.application.services.knowbot_service import KnowbotCreationService
from app.infrastructure.documents.table_extract import CachedTableExtractor
from app.infrastructure.documents.text_extract import CachedTextExtractor
from app.infrastructure.external.knowbase_user_data import (
    KnowbaseKnowledgeBase,
    KnowbaseUserData,
)
from app.infrastructure.external.knowbot_clients import (
    KnowmartDeliveryClient,
    RuntimeTestClients,
)
from app.infrastructure.repositories.knowbots import SqlKnowlabRepository
from app.infrastructure.storage.data_store import S3DataStore


@lru_cache(maxsize=1)
def runtime_clients() -> RuntimeTestClients:
    return RuntimeTestClients()


@lru_cache(maxsize=1)
def knowmart_client() -> KnowmartDeliveryClient:
    return KnowmartDeliveryClient()


def knowbot_question_service(session: AsyncSession) -> KnowbotQuestionService:
    return KnowbotQuestionService(SqlKnowlabRepository(session), runtime_clients())


@lru_cache(maxsize=1)
def knowledge_base() -> KnowledgeBase | None:
    """knowbase 里 knowlab 的知识库分区（SDD 0042 第七节）；没配 knowbase 的地址就没有，
    知识库照旧放本应用的对象存储、测试照旧经 knowlab 读。"""
    if not os.environ.get("KNOWBASE_INTERNAL_URL", "").strip():
        return None
    return KnowbaseKnowledgeBase(KnowbaseUserData())


def knowbot_service(session: AsyncSession) -> KnowbotCreationService:
    repo = SqlKnowlabRepository(session)
    questions = KnowbotQuestionService(repo, runtime_clients())
    return KnowbotCreationService(
        repo,
        runtime_clients(),
        on_refresh=questions.resend,
        knowledge=knowledge_base(),
    )


def knowbot_expert_service(session: AsyncSession) -> KnowbotExpertService:
    """专家的交回、两道门、上架、停用（SDD 0037）。"""
    return KnowbotExpertService(SqlKnowlabRepository(session), knowmart_client())


def knowbot_board(session: AsyncSession) -> KnowbotBoard:
    """两端的栏、大类、详情页（只读）。"""
    return KnowbotBoard(SqlKnowlabRepository(session))


@lru_cache(maxsize=1)
def data_store() -> S3DataStore:
    return S3DataStore()


def knowbot_data_service(session: AsyncSession) -> KnowbotDataService:
    return KnowbotDataService(
        SqlKnowlabRepository(session), data_store(), knowledge=knowledge_base()
    )


@lru_cache(maxsize=1)
def text_extractor() -> CachedTextExtractor:
    return CachedTextExtractor()


@lru_cache(maxsize=1)
def table_extractor() -> CachedTableExtractor:
    return CachedTableExtractor()


def knowbot_data_reader(session: AsyncSession) -> KnowbotDataReader:
    """运行方读用户数据（0027 §3.5；按格子读 0030 §3.1）。"""
    return KnowbotDataReader(
        SqlKnowlabRepository(session),
        data_store(),
        text_extractor(),
        table_extractor(),
    )
