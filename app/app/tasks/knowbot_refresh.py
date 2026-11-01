"""孵化一轮与测试的后台刷新（SDD 0046 第九节「还没做」、专家孵化方案第十八节「轮询只靠
页面刷新」）。

原来只有作者开着页面时，页面轮询才问运行方；作者不开页面，跑完的一轮、测完的测试、
冒出来的问题都要等他下次打开才记下来，「待办」与首页「在等你」也跟着晚。由 beat 每分钟
触发一次：没结束的孵化一轮、在跑的测试各问运行方一次，做法同页面轮询。都走服务关系
（knowbench:incubate、knowbench:testrun、knowbase:user-data-read），不要作者的令牌。

孵化与测试各用一个数据库会话，一边出错不挡另一边。同一轮还补做三件（都不要作者的
令牌）：
- 测完了的测试证据写进作者的产出区（knowbase:user-data-output，0043 第 4.2 节）；
- 名称或最新版本变了的专家推进 knowbase 名录（knowbase:user-data-directory，
  0044 第三节）；
- 审完了还没撤成的审核员、重跑依据补撤（knowbase:user-data-grant-test，
  0046 第五节）；
- 在审交付的审核授权作者在 knowbase 撤了没有，撤了照作者撤回处理（同上关系）；
- 上架了的各版在 knowmart 那头怎样（待处理与原因，knowmart:deliver，0043 第 4.3 节）。
"""

from __future__ import annotations

import asyncio
import logging

from app.bootstrap.knowbots import (
    knowbot_delivery_service,
    knowbot_expert_service,
    knowbot_incubation_service,
    knowbot_service,
)
from app.infrastructure.storage.postgres import get_postgres
from app.worker import celery_app

log = logging.getLogger(__name__)


async def _run() -> dict[str, int]:
    postgres = get_postgres()
    await postgres.init()
    done = {
        "incubations": 0,
        "tests": 0,
        "evidence": 0,
        "directory": 0,
        "closed": 0,
        "market": 0,
        "revoked": 0,
    }
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
        # 这几件各自一个会话、各自兜住：前面出错不挡后面（10-30 交付审查第 9 条）
        chores = (
            ("evidence", lambda s: knowbot_service(s).write_pending_evidence()),
            ("directory", lambda s: knowbot_delivery_service(s).sync_directory()),
            ("closed", lambda s: knowbot_delivery_service(s).close_pending()),
            ("revoked", lambda s: knowbot_delivery_service(s).check_consents()),
            ("market", lambda s: knowbot_expert_service(s).sync_market()),
        )
        for name, chore in chores:
            try:
                async with postgres.session_factory() as session:
                    done[name] = await chore(session)
            except Exception:
                log.exception("knowbot %s sweep failed", name)
    finally:
        await postgres.shutdown()
    return done


@celery_app.task(name="app.tasks.knowbot_refresh.sweep")
def sweep() -> dict[str, int]:
    return asyncio.run(_run())
