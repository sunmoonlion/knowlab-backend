"""分身创建与测试的装配：每个请求一个数据库会话；外部客户端进程内共用（令牌缓存在里面）。"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.knowbot_data import KnowbotDataService
from app.application.services.knowbot_data_read import KnowbotDataReader
from app.application.services.knowbot_questions import KnowbotQuestionService
from app.application.services.knowbot_service import KnowbotCreationService
from app.infrastructure.documents.text_extract import CachedTextExtractor
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


def knowbot_service(session: AsyncSession) -> KnowbotCreationService:
    repo = SqlKnowlabRepository(session)
    questions = KnowbotQuestionService(repo, runtime_clients())
    return KnowbotCreationService(
        repo, runtime_clients(), knowmart_client(), on_refresh=questions.resend
    )


@lru_cache(maxsize=1)
def data_store() -> S3DataStore:
    return S3DataStore()


def knowbot_data_service(session: AsyncSession) -> KnowbotDataService:
    return KnowbotDataService(SqlKnowlabRepository(session), data_store())


@lru_cache(maxsize=1)
def text_extractor() -> CachedTextExtractor:
    return CachedTextExtractor()


def knowbot_data_reader(session: AsyncSession) -> KnowbotDataReader:
    """运行方读用户数据（0027 §3.5）。"""
    return KnowbotDataReader(
        SqlKnowlabRepository(session), data_store(), text_extractor()
    )
