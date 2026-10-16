"""分身创建与测试的装配：每个请求一个数据库会话；外部客户端进程内共用（令牌缓存在里面）。"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.knowbot_data import KnowbotDataService
from app.application.services.knowbot_service import KnowbotCreationService
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


def knowbot_service(session: AsyncSession) -> KnowbotCreationService:
    return KnowbotCreationService(
        SqlKnowlabRepository(session), runtime_clients(), knowmart_client()
    )


@lru_cache(maxsize=1)
def data_store() -> S3DataStore:
    return S3DataStore()


def knowbot_data_service(session: AsyncSession) -> KnowbotDataService:
    return KnowbotDataService(SqlKnowlabRepository(session), data_store())
