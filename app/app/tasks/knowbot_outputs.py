"""测试产出到期清理（SDD 0037，审读 10-19）。

分身交付或被下一个分身取代满 90 天，清掉成稿与交回物的正文，留一句原因。
由 beat 每小时触发一次。
"""

from __future__ import annotations

import asyncio

from app.bootstrap.knowbots import knowbot_service
from app.infrastructure.storage.postgres import get_postgres
from app.worker import celery_app


async def _run() -> int:
    postgres = get_postgres()
    await postgres.init()
    try:
        async with postgres.session_factory() as session:
            return await knowbot_service(session).expire_test_outputs()
    finally:
        await postgres.shutdown()


@celery_app.task(name="app.tasks.knowbot_outputs.expire")
def expire() -> int:
    return asyncio.run(_run())
