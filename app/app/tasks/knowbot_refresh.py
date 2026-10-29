"""孵化一轮与测试的后台刷新（SDD 0046 第九节「还没做」、专家孵化方案第十八节「轮询只靠
页面刷新」）。

原来只有作者开着页面时，页面轮询才问运行方；作者不开页面，跑完的一轮、测完的测试、
冒出来的问题都要等他下次打开才记下来，「待办」与首页「在等你」也跟着晚。由 beat 每分钟
触发一次：没结束的孵化一轮、在跑的测试各问运行方一次，做法同页面轮询。都走服务关系
（knowbench:incubate、knowbench:testrun、knowbase:user-data-read），不要作者的令牌。

孵化与测试各用一个数据库会话，一边出错不挡另一边。
"""

from __future__ import annotations

import asyncio
import logging

from app.bootstrap.knowbots import knowbot_incubation_service, knowbot_service
from app.infrastructure.storage.postgres import get_postgres
from app.worker import celery_app

log = logging.getLogger(__name__)


async def _run() -> dict[str, int]:
    postgres = get_postgres()
    await postgres.init()
    done = {"incubations": 0, "tests": 0}
    try:
        try:
            async with postgres.session_factory() as session:
                done["incubations"] = await knowbot_incubation_service(
                    session
                ).refresh_active()
        except Exception:  # 一边出错不挡另一边，下一分钟再来
            log.exception("knowbot incubation sweep failed")
        try:
            async with postgres.session_factory() as session:
                done["tests"] = await knowbot_service(session).refresh_active()
        except Exception:
            log.exception("knowbot test sweep failed")
    finally:
        await postgres.shutdown()
    return done


@celery_app.task(name="app.tasks.knowbot_refresh.sweep")
def sweep() -> dict[str, int]:
    return asyncio.run(_run())
