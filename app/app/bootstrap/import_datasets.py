"""一次性：把知识库里还在本应用对象存储的旧数据搬进 knowbase（SDD 0042 第七节）。

`python -m app.bootstrap.import_datasets`。按数据编号幂等，中途断了重跑接着搬；
旧对象不删，留到「财务分析与预测」第 1 版停用。要配 `KNOWBASE_INTERNAL_URL` 与服务关系
`KNOWBASE_USER_DATA_IMPORT_SERVICE_*`（knowbase 那边把这个服务身份登记成 knowlab）。
搬完以后各人的测试同意在他下次打开知识库页时补上；补上之前，绑的都是搬过来的旧数据的
测试照旧经 knowlab 读旧对象（Fable 评审 P2-4）。要在没有在跑的测试时跑：跑着的测试中途
换了读法会读不到。
"""

from __future__ import annotations

import asyncio
import logging

from app.bootstrap.knowbots import knowbot_data_service
from app.infrastructure.logging.logging import setup_logging
from app.infrastructure.storage.postgres import get_postgres

logger = logging.getLogger(__name__)
BATCH = 50


async def main() -> int:
    setup_logging()
    await get_postgres().init()
    total = 0
    try:
        while True:
            async with get_postgres().session_factory() as session:
                moved = await knowbot_data_service(session).import_old(BATCH)
            total += moved
            logger.info("datasets imported batch=%s total=%s", moved, total)
            if moved < BATCH:
                return 0
    finally:
        await get_postgres().shutdown()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
