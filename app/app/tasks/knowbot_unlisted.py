"""没上架那几版的结局补发给 knowmart（SDD 0037 第三节，Astra 评审 F08）。

用户不要、运营审核没过的那一版，当场送不到 knowmart 就留着；由 beat 每 5 分钟触发
一次，逐条再送，送到为止。
"""

from __future__ import annotations

import asyncio

from app.bootstrap.knowbots import knowbot_expert_service
from app.infrastructure.storage.postgres import get_postgres
from app.worker import celery_app


async def _run() -> int:
    postgres = get_postgres()
    await postgres.init()
    try:
        async with postgres.session_factory() as session:
            return await knowbot_expert_service(session).resend_unlisted()
    finally:
        await postgres.shutdown()


@celery_app.task(name="app.tasks.knowbot_unlisted.resend")
def resend() -> int:
    return asyncio.run(_run())
