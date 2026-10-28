"""迁移 20261028_0017（工作稿）与工作稿在真 PostgreSQL 上：已有准专家回填 platform；
两张表与约束；存格子、出一版走真仓储；退回删掉两张表与 mode 列。一次性 schema。"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.application.errors.exceptions import ConcurrencyConflictError
from app.application.services.knowbot_workdraft import KnowbotWorkdraftService
from app.infrastructure.repositories.knowbots import SqlKnowlabRepository
from app.infrastructure.storage.postgres import make_session_factory
from tests.test_knowbot_repository_db import ROOT, _drop, _run, _schema, _versions

TARGET = ROOT / "alembic/versions/20261028_0017_workdrafts.py"
# 之后的迁移：真仓储按最新的模型读写，走服务前先升上去，退回时先退它们
LATER = [p for p in _versions() if p.stem > TARGET.stem]
OWNER = "user-123"
REQUEST, DRAFT = uuid.UUID(int=1), uuid.UUID(int=11)
TABLES = ("knowbot_workdraft", "knowbot_workdraft_record")
STEP = {
    "title": "数据摸底",
    "does": "看清数据有哪些表、哪些报告期。",
    "rules": [{"id": "r1", "text": "合并、母公司都有时问用户。"}],
    "outputs": [{"name": "periods", "type": "list"}],
}


async def _rows(c) -> None:  # type: ignore[no-untyped-def]
    await c.execute(
        text(
            "INSERT INTO knowbot_request (id, owner_subject, title, runtime, idea,"
            " cases) VALUES (:r, :o, '财务分析与预测', 'knowbench',"
            " '{\"kind\": \"skill\"}', '[]')"
        ),
        {"r": REQUEST, "o": OWNER},
    )
    await c.execute(
        text(
            "INSERT INTO knowbot_request_version (request_id, version, idea, cases,"
            " consent_version, created_by) VALUES (:r, 1, '{}', '[]', 2, :o)"
        ),
        {"r": REQUEST, "o": OWNER},
    )
    await c.execute(
        text(
            "INSERT INTO knowbot_draft (id, request_id, revision, version, status,"
            " manifest, request_version) VALUES (:d, :r, 1, '1', 'pending_test',"
            " CAST(:m AS jsonb), 1)"
        ),
        {"d": DRAFT, "r": REQUEST, "m": json.dumps({"schema": "knowbot-manifest/v2"})},
    )


async def _all(c, sql: str) -> list:  # type: ignore[no-untyped-def]
    return [tuple(r) for r in (await c.execute(text(sql))).all()]


@pytest.mark.asyncio
async def test_workdrafts_migrate_round_trip_and_publish_for_real() -> None:
    engine, schema = await _schema("20261028_0016_drop_manifest_v1")
    try:
        async with engine.begin() as c:
            await _rows(c)
        async with engine.begin() as c:
            await c.run_sync(lambda conn: _run(conn, [TARGET]))
        async with engine.begin() as c:
            assert await _all(c, "SELECT mode FROM knowbot_draft") == [("platform",)]
        # mode 只能是两种之一；编辑记录要带格子，导入不带
        for bad in (
            "UPDATE knowbot_draft SET mode = 'other'",
            "INSERT INTO knowbot_workdraft (id, request_id, owner_subject, body,"
            " revision) VALUES (gen_random_uuid(), :r, 'u', '{}', -1)",
        ):
            with pytest.raises(DBAPIError):
                async with engine.begin() as c:
                    await c.execute(text(bad), {"r": REQUEST})

        async with engine.begin() as c:
            await c.run_sync(lambda conn: _run(conn, LATER))
        factory = make_session_factory(engine)
        async with factory() as session:
            works = KnowbotWorkdraftService(SqlKnowlabRepository(session))
            saved = await works.save(
                OWNER,
                str(REQUEST),
                revision=0,
                edits=[("scope/does", "分析财报"), ("steps/overview", STEP)],
            )
        assert saved.revision == 1
        async with factory() as session:
            works = KnowbotWorkdraftService(SqlKnowlabRepository(session))
            with pytest.raises(ConcurrencyConflictError):
                await works.save(OWNER, str(REQUEST), revision=0, edits=[("name", "x")])
        async with factory() as session:
            works = KnowbotWorkdraftService(SqlKnowlabRepository(session))
            draft = await works.publish(OWNER, str(REQUEST))
        assert (draft.mode, draft.version, draft.revision) == ("self", "1", 2)
        async with engine.begin() as c:
            assert await _all(
                c, "SELECT revision, mode FROM knowbot_draft ORDER BY revision"
            ) == [(1, "platform"), (2, "self")]
            kinds = await _all(
                c, "SELECT DISTINCT kind, actor_role FROM knowbot_workdraft_record"
            )
            assert kinds == [("edit", "author")]
            with pytest.raises(DBAPIError):
                async with c.begin_nested():
                    await c.execute(
                        text(
                            "INSERT INTO knowbot_workdraft_record (id, workdraft_id,"
                            " kind, actor_subject, actor_role, text) SELECT"
                            " gen_random_uuid(), id, 'edit', 'u', 'author', 'x'"
                            " FROM knowbot_workdraft"
                        )
                    )
            with pytest.raises(DBAPIError):
                async with c.begin_nested():
                    await c.execute(
                        text(
                            "INSERT INTO knowbot_workdraft_record (id, workdraft_id,"
                            " kind, actor_subject, actor_role, path, text) SELECT"
                            " gen_random_uuid(), id, 'import', 'u', 'author', 'name',"
                            " 'x' FROM knowbot_workdraft"
                        )
                    )
        # 退回：两张表与 mode 列都没了
        async with engine.begin() as c:
            await c.run_sync(
                lambda conn: _run(conn, [*reversed(LATER), TARGET], "downgrade")
            )
        async with engine.begin() as c:
            for table in TABLES:
                found = await c.execute(text(f"SELECT to_regclass('{table}')"))
                assert found.scalar() is None, table
            columns = await _all(
                c,
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_schema = current_schema()"
                " AND table_name = 'knowbot_draft' AND column_name = 'mode'",
            )
            assert columns == []
    finally:
        await _drop(engine, schema)
