"""迁移 20261028_0016（decisions ⑰）：清单 v1 的准专家连同专家、问题、测试、产出删掉，
测试集的软引用置空，创意留着；旧数据三张表与两列删掉。真 PostgreSQL、一次性 schema。"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

from tests.test_knowbot_repository_db import ROOT, _drop, _run, _schema

TARGET = ROOT / "alembic/versions/20261028_0016_drop_manifest_v1.py"
OWNER = "user-123"
REQUEST = uuid.UUID(int=1)
OLD, NEW, BLANK = uuid.UUID(int=11), uuid.UUID(int=12), uuid.UUID(int=13)
OLD_TEST, NEW_TEST = uuid.UUID(int=21), uuid.UUID(int=22)
LEGACY = ("knowbot_dataset", "knowbot_test_consent", "knowbot_consent_withdrawal")


async def _rows(c) -> None:  # type: ignore[no-untyped-def]
    """一个创意：1.1 是清单 v1 测过出了专家，1.2 是 v2 在测，1.3 清单没写 schema。"""
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
    for draft, revision, status, doc in (
        (OLD, 1, "passed", {"schema": "knowbot-manifest/v1", "data_bindings": []}),
        (NEW, 2, "testing", {"schema": "knowbot-manifest/v2"}),
        (BLANK, 3, "pending_test", {}),
    ):
        await c.execute(
            text(
                "INSERT INTO knowbot_draft (id, request_id, revision, version, status,"
                " manifest, request_version, run_grant_id) VALUES (:d, :r, :rev, '1',"
                " :s, CAST(:m AS jsonb), 1, 'old-grant')"
            ),
            {
                "d": draft,
                "r": REQUEST,
                "rev": revision,
                "s": status,
                "m": json.dumps(doc),
            },
        )
    for test, draft, number, status in (
        (OLD_TEST, OLD, 1, "passed"),
        (NEW_TEST, NEW, 2, "running"),
    ):
        await c.execute(
            text(
                "INSERT INTO knowbot_test (id, request_id, number, draft_id, status,"
                " run_id, grant_id, started_at) VALUES (:t, :r, :n, :d, :s, :run,"
                " 'grant-1', now())"
            ),
            {
                "t": test,
                "r": REQUEST,
                "n": number,
                "d": draft,
                "s": status,
                "run": f"knowlab-test-{test}",
            },
        )
    await c.execute(
        text(
            "INSERT INTO knowbot_expert (id, request_id, version, draft_id, test_id,"
            " created_at) VALUES (gen_random_uuid(), :r, 1, :d, :t, now())"
        ),
        {"r": REQUEST, "d": OLD, "t": OLD_TEST},
    )
    for draft, test in ((OLD, OLD_TEST), (NEW, NEW_TEST)):
        run = f"knowlab-test-{test}"
        await c.execute(
            text(
                "INSERT INTO knowbot_question (id, draft_id, run_id, question_id,"
                " case_id, kind, text, options, allow_free_text, asked_at) VALUES"
                " (gen_random_uuid(), :d, :run, 'q1', 'c1', 'tool_approval', '跑吗？',"
                " '[]', true, '2026-10-28T00:00:00Z')"
            ),
            {"d": draft, "run": run},
        )
        await c.execute(
            text(
                "INSERT INTO knowbot_test_output (draft_id, run_id, case_id, note)"
                " VALUES (:d, :run, 'c1', '没有产出')"
            ),
            {"d": draft, "run": run},
        )
    for version, source in ((1, OLD_TEST), (2, NEW_TEST)):
        await c.execute(
            text(
                "INSERT INTO knowbot_test_set (id, request_id, version, cases,"
                " from_test_id, created_at) VALUES (gen_random_uuid(), :r, :v, '{}',"
                " :t, now())"
            ),
            {"r": REQUEST, "v": version, "t": source},
        )
    await c.execute(
        text(
            "INSERT INTO knowbot_dataset (id, owner_subject, name, version,"
            " content_type, size_bytes, sha256, knowbase_item_id) VALUES"
            " (gen_random_uuid(), :o, '年报.pdf', '1', 'application/pdf', 1, :h, 'i1')"
        ),
        {"o": OWNER, "h": "0" * 64},
    )
    await c.execute(
        text(
            "INSERT INTO knowbot_test_consent (owner_subject, consent_id, items_digest)"
            " VALUES (:o, 'c1', :h)"
        ),
        {"o": OWNER, "h": "0" * 64},
    )


async def _all(c, sql: str) -> list:  # type: ignore[no-untyped-def]
    return [tuple(r) for r in (await c.execute(text(sql))).all()]


async def _columns(c, table: str) -> set[str]:  # type: ignore[no-untyped-def]
    found = await c.execute(
        text(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = current_schema() AND table_name = :t"
        ),
        {"t": table},
    )
    return {r[0] for r in found}


@pytest.mark.asyncio
async def test_v1_drafts_and_the_old_data_tables_go_and_v2_stays() -> None:
    engine, schema = await _schema("20261028_0015_self_tests")
    try:
        async with engine.begin() as c:
            await _rows(c)
        async with engine.begin() as c:
            await c.run_sync(lambda conn: _run(conn, [TARGET]))
        async with engine.begin() as c:
            # 只剩 v2 的那一稿与它的测试、问题、产出；v1 与没写 schema 的都删了
            assert await _all(c, "SELECT id FROM knowbot_draft") == [(NEW,)]
            assert await _all(c, "SELECT id FROM knowbot_test") == [(NEW_TEST,)]
            assert await _all(c, "SELECT draft_id FROM knowbot_question") == [(NEW,)]
            assert await _all(c, "SELECT draft_id FROM knowbot_test_output") == [(NEW,)]
            assert await _all(c, "SELECT count(*) FROM knowbot_expert") == [(0,)]
            # 创意与测试集留着；指着删掉的测试的软引用置空
            assert await _all(c, "SELECT id FROM knowbot_request") == [(REQUEST,)]
            assert await _all(
                c, "SELECT version, from_test_id FROM knowbot_test_set ORDER BY version"
            ) == [(1, None), (2, NEW_TEST)]
            for table in LEGACY:
                found = await c.execute(text(f"SELECT to_regclass('{table}')"))
                assert found.scalar() is None, table
            assert "run_grant_id" not in await _columns(c, "knowbot_draft")
            assert "grant_id" not in await _columns(c, "knowbot_test")
        # 退回：空表、空列建回来，删掉的行不恢复
        async with engine.begin() as c:
            await c.run_sync(lambda conn: _run(conn, [TARGET], "downgrade"))
        async with engine.begin() as c:
            for table in LEGACY:
                assert await _all(c, f"SELECT count(*) FROM {table}") == [(0,)]
            assert "run_grant_id" in await _columns(c, "knowbot_draft")
            assert "grant_id" in await _columns(c, "knowbot_test")
            assert await _all(c, "SELECT id FROM knowbot_draft") == [(NEW,)]
    finally:
        await _drop(engine, schema)
