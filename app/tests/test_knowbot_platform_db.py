"""迁移 20261029_0019（孵化的模式、受理的类型、给平台的授权）与授权在真 PostgreSQL 上：
已有的名称记自助、已有的受理单记正式受理；一个名称同时一张没结束的孵化授权，一位准专家
同时一张没结束的测试授权；downgrade 往返。"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.application.errors.exceptions import ConcurrencyConflictError
from app.application.ports.knowbots import GrantRecord
from app.infrastructure.repositories.knowbots import SqlKnowlabRepository
from app.infrastructure.storage.postgres import make_session_factory
from tests.knowbot_fakes import request_body
from tests.test_knowbot_repository_db import ROOT, _drop, _run, _schema

TARGET = ROOT / "alembic/versions/20261029_0019_platform_grants.py"
OWNER = "user-123"
REQUEST, DRAFT = uuid.UUID(int=1), uuid.UUID(int=11)
IDEA = request_body()["idea"]
AT = datetime(2026, 10, 29, 12, tzinfo=UTC)


async def _rows(c) -> None:  # type: ignore[no-untyped-def]
    await c.execute(
        text(
            "INSERT INTO knowbot_request (id, owner_subject, title, runtime, idea,"
            " cases) VALUES (:r, :o, '财务分析与预测', 'knowbench', CAST(:i AS jsonb),"
            " '[]')"
        ),
        {"r": REQUEST, "o": OWNER, "i": json.dumps(IDEA)},
    )
    await c.execute(
        text(
            "INSERT INTO knowbot_request_version (request_id, version, idea, cases,"
            " consent_version, created_by) VALUES (:r, 1, CAST(:i AS jsonb), '[]', 3,"
            " :o)"
        ),
        {"r": REQUEST, "o": OWNER, "i": json.dumps(IDEA)},
    )
    await c.execute(
        text(
            "INSERT INTO knowbot_acceptance (request_id, version, accepted, checks,"
            " decided_by, decided_at) VALUES (:r, 1, true, '{}', 'admin-1', now())"
        ),
        {"r": REQUEST},
    )
    await c.execute(
        text(
            "INSERT INTO knowbot_draft (id, request_id, revision, version, status,"
            " manifest, request_version) VALUES (:d, :r, 1, '1', 'pending_test',"
            " '{}', 1)"
        ),
        {"d": DRAFT, "r": REQUEST},
    )


def _grant(kind: str, draft: uuid.UUID | None = None) -> GrantRecord:
    return GrantRecord(
        id=uuid.uuid4(),
        request_id=REQUEST,
        kind=kind,
        draft_id=draft,
        status="pending",
        budget_limit=Decimal("20.00"),
        granted_by=OWNER,
        granted_at=AT,
    )


async def test_platform_grants_migrate_round_trip_and_hold() -> None:
    engine, schema = await _schema("20261028_0018_incubations")
    try:
        async with engine.begin() as c:
            await _rows(c)
            await c.run_sync(lambda conn: _run(conn, [TARGET]))
        async with engine.begin() as c:
            mode = (
                await c.execute(text("SELECT incubation_mode FROM knowbot_request"))
            ).scalar_one()
            kind = (
                await c.execute(text("SELECT kind FROM knowbot_acceptance"))
            ).scalar_one()
            assert (mode, kind) == ("self", "formal")

        factory = make_session_factory(engine)
        incubate = _grant("incubate")
        async with factory() as session:
            repo = SqlKnowlabRepository(session)
            async with repo.transaction():
                await repo.add_grant(incubate)
                await repo.add_grant(_grant("test", DRAFT))
                await repo.set_incubation_mode(REQUEST, "platform")
        async with factory() as session:
            repo = SqlKnowlabRepository(session)
            with pytest.raises(ConcurrencyConflictError) as raised:
                async with repo.transaction():
                    await repo.add_grant(_grant("incubate"))
            assert raised.value.code == "grant_exists"
            with pytest.raises(ConcurrencyConflictError):
                async with repo.transaction():
                    await repo.add_grant(_grant("test", DRAFT))
        async with factory() as session:
            repo = SqlKnowlabRepository(session)
            async with repo.transaction():
                await repo.update_grant(
                    incubate.id, status="revoked", ended_by=OWNER, ended_at=AT
                )
                # 撤了以后可以再请
                await repo.add_grant(_grant("incubate"))
            grants = await repo.list_grants([REQUEST])
            assert [g.status for g in grants if g.kind == "incubate"] == [
                "revoked",
                "pending",
            ]
            assert (await repo.get_request(REQUEST)).incubation_mode == "platform"
            with pytest.raises(ValueError):
                await repo.update_grant(incubate.id, kind="test")

        for bad in (
            # 测试授权要挂准专家，孵化授权不挂
            "INSERT INTO knowbot_platform_grant (id, request_id, kind, status,"
            " budget_limit, granted_by, granted_at) VALUES (gen_random_uuid(), :r,"
            " 'test', 'pending', 1, 'o', now())",
            "INSERT INTO knowbot_platform_grant (id, request_id, kind, status,"
            " budget_limit, granted_by, granted_at) VALUES (gen_random_uuid(), :r,"
            " 'incubate', 'nope', 1, 'o', now())",
            "INSERT INTO knowbot_platform_grant (id, request_id, kind, status,"
            " budget_limit, granted_by, granted_at) VALUES (gen_random_uuid(), :r,"
            " 'incubate', 'ended', 0, 'o', now())",
            "UPDATE knowbot_request SET incubation_mode = 'both'",
            "UPDATE knowbot_acceptance SET kind = 'light'",
        ):
            with pytest.raises(DBAPIError):
                async with engine.begin() as c:
                    await c.execute(text(bad), {"r": REQUEST})

        async with engine.begin() as c:
            await c.run_sync(lambda conn: _run(conn, [TARGET], "downgrade"))
        async with engine.begin() as c:
            tables = {
                r[0]
                for r in await c.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables"
                        " WHERE table_schema = current_schema()"
                    )
                )
            }
            assert "knowbot_platform_grant" not in tables
        async with engine.begin() as c:
            await c.run_sync(lambda conn: _run(conn, [TARGET]))
    finally:
        await _drop(engine, schema)
