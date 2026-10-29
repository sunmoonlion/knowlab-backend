"""迁移 20261029_0022（工作稿记下挑的数据的格式）与真仓储：已有的工作稿补成空的；挑数据
写进 body 与 data_formats；存格子不动记下的格式；后台刷新按状态取没结束的；退回删
这一列。一次性 schema。"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

from app.application.services.knowbot_library import KnowbotLibraryService
from app.application.services.knowbot_workdraft import KnowbotWorkdraftService
from app.infrastructure.repositories.knowbots import SqlKnowlabRepository
from app.infrastructure.storage.postgres import make_session_factory
from tests.knowbot_fakes import MATERIAL, FakeLibrary
from tests.test_knowbot_repository_db import ROOT, _drop, _run, _schema

TARGET = ROOT / "alembic/versions/20261029_0022_workdraft_data_formats.py"
OWNER = "user-123"
REQUEST = uuid.UUID(int=7)


async def _token() -> str:
    return "user-token"


async def _all(c, sql: str) -> list:  # type: ignore[no-untyped-def]
    return [tuple(r) for r in (await c.execute(text(sql))).all()]


@pytest.mark.asyncio
async def test_data_formats_migrate_and_are_kept_by_the_real_repository() -> None:
    engine, schema = await _schema("20261029_0021_request_origin")
    try:
        async with engine.begin() as c:
            await c.execute(
                text(
                    "INSERT INTO knowbot_request (id, owner_subject, title, runtime,"
                    " idea, cases, incubation_mode) VALUES (:r, :o, '财务分析与预测',"
                    ' \'knowbench\', \'{"kind": "question", "question": "q"}\','
                    " '[]', 'self')"
                ),
                {"r": REQUEST, "o": OWNER},
            )
            await c.execute(
                text(
                    "INSERT INTO knowbot_request_version (request_id, version, idea,"
                    " cases, consent_version, created_by) VALUES (:r, 1, '{}', '[]',"
                    " 2, :o)"
                ),
                {"r": REQUEST, "o": OWNER},
            )
            await c.execute(
                text(
                    "INSERT INTO knowbot_workdraft (id, request_id, owner_subject,"
                    " body, revision) VALUES (gen_random_uuid(), :r, :o,"
                    " CAST(:b AS jsonb), 0)"
                ),
                {"r": REQUEST, "o": OWNER, "b": json.dumps({"name": ""})},
            )
        async with engine.begin() as c:
            await c.run_sync(lambda conn: _run(conn, [TARGET]))
        async with engine.begin() as c:
            assert await _all(c, "SELECT data_formats FROM knowbot_workdraft") == [
                ({},)
            ]
        factory = make_session_factory(engine)
        async with factory() as session:
            repo = SqlKnowlabRepository(session)
            picked = await KnowbotLibraryService(repo, FakeLibrary()).pick(
                OWNER, str(REQUEST), revision=0, picks=[MATERIAL], user_token=_token
            )
        assert picked.body["data_picks"] == [MATERIAL]
        assert picked.data_formats[MATERIAL]["readings"] == ["pages", "text"]
        async with factory() as session:
            works = KnowbotWorkdraftService(SqlKnowlabRepository(session))
            saved = await works.save(
                OWNER, str(REQUEST), revision=1, edits=[("name", "财务分析")]
            )
        # 存格子不动记下的格式
        assert saved.data_formats == picked.data_formats
        async with factory() as session:
            repo = SqlKnowlabRepository(session)
            assert await repo.list_incubations(statuses={"running"}) == []
            assert await repo.list_tests(statuses={"running", "waiting"}) == []
        async with engine.begin() as c:
            await c.run_sync(lambda conn: _run(conn, [TARGET], "downgrade"))
        async with engine.begin() as c:
            columns = await _all(
                c,
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_schema = current_schema()"
                " AND table_name = 'knowbot_workdraft'"
                " AND column_name = 'data_formats'",
            )
            assert columns == []
    finally:
        await _drop(engine, schema)
