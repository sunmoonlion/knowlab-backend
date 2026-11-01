"""迁移 20261030_0023（交付过程、专家要不要交回、重跑的测试、测试证据、名录）与真仓储。

旧专家按测试与准专家的模式补「要不要交回」；同一位专家只能有一个在审的交付；退回删表与列、
重跑的测试改记平台测试。一次性 schema。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.application.errors.exceptions import ConcurrencyConflictError
from app.application.ports.knowbots import DeliveryRecord
from app.infrastructure.repositories.knowbots import SqlKnowlabRepository
from app.infrastructure.storage.postgres import make_session_factory
from tests.test_knowbot_repository_db import ROOT, _drop, _run, _schema, _versions

TARGET = ROOT / "alembic/versions/20261030_0023_deliveries.py"
OWNER = "user-123"
REQUEST = uuid.UUID(int=11)
AT = datetime(2026, 10, 30, tzinfo=UTC)


async def _rows(c) -> dict[str, uuid.UUID]:  # type: ignore[no-untyped-def]
    ids = {k: uuid.uuid4() for k in ("pd", "sd", "pt", "st", "pe", "se")}
    await c.execute(
        text(
            "INSERT INTO knowbot_request (id, owner_subject, title, runtime, idea,"
            " cases) VALUES (:r, :o, '财务分析与预测', 'knowbench', '{}', '[]')"
        ),
        {"r": REQUEST, "o": OWNER},
    )
    await c.execute(
        text(
            "INSERT INTO knowbot_request_version (request_id, version, idea, cases,"
            " consent_version, created_by) VALUES (:r, 1, '{}', '[]', 3, :o)"
        ),
        {"r": REQUEST, "o": OWNER},
    )
    for n, (draft, mode) in enumerate(((ids["pd"], "platform"), (ids["sd"], "self"))):
        await c.execute(
            text(
                "INSERT INTO knowbot_draft (id, request_id, revision, version, status,"
                " manifest, request_version, mode) VALUES (:d, :r, :n, :v, 'passed',"
                " '{}', 1, :m)"
            ),
            {"d": draft, "r": REQUEST, "n": n + 1, "v": str(n + 1), "m": mode},
        )
    for n, (test, draft, mode) in enumerate(
        ((ids["pt"], ids["pd"], "platform"), (ids["st"], ids["sd"], "self"))
    ):
        await c.execute(
            text(
                "INSERT INTO knowbot_test (id, request_id, number, draft_id, status,"
                " run_id, started_at, mode, environment_id) VALUES (:t, :r, :n, :d,"
                " 'passed', :run, now(), :m, :e)"
            ),
            {
                "t": test,
                "r": REQUEST,
                "n": n + 1,
                "d": draft,
                "run": f"knowlab-test-{test}",
                "m": mode,
                "e": None if mode == "platform" else "e-1",
            },
        )
    for n, (expert, draft, test) in enumerate(
        ((ids["pe"], ids["pd"], ids["pt"]), (ids["se"], ids["sd"], ids["st"]))
    ):
        await c.execute(
            text(
                "INSERT INTO knowbot_expert (id, request_id, version, draft_id,"
                " test_id, created_at) VALUES (:e, :r, :n, :d, :t, now())"
            ),
            {"e": expert, "r": REQUEST, "n": n + 1, "d": draft, "t": test},
        )
    return ids


def _delivery(expert: uuid.UUID, **over) -> DeliveryRecord:  # type: ignore[no-untyped-def]
    return DeliveryRecord(
        **{
            "id": uuid.uuid4(),
            "expert_id": expert,
            "request_id": REQUEST,
            "listing": "public",
            "test_mode": "self",
            "needs_rerun": True,
            "budget_limit": Decimal("5.00"),
            "submitted_by": OWNER,
            "submitted_at": AT,
            "bindings": [{"material_id": "m-1", "version_no": 1}],
            **over,
        }
    )


@pytest.mark.asyncio
async def test_deliveries_migrate_round_trip_and_hold() -> None:
    engine, schema = await _schema("20261029_0022_workdraft_data_formats")
    try:
        async with engine.begin() as c:
            ids = await _rows(c)
            # 旧流程里作者已经要了的：平台测的那版在审、自助测的那版审过了待交
            await c.execute(
                text(
                    "UPDATE knowbot_expert SET user_accepted = true,"
                    " user_decided_by = 'user-123', user_decided_at = now()"
                )
            )
            await c.execute(
                text(
                    "UPDATE knowbot_expert SET review_approved = true,"
                    " review_listing = 'public', reviewed_by = 'ops-1',"
                    " reviewed_at = now() WHERE id = :e"
                ),
                {"e": ids["se"]},
            )
            await c.run_sync(lambda conn: _run(conn, [TARGET]))
        async with engine.begin() as c:
            made = {
                r[0]: r[1:]
                for r in (
                    await c.execute(
                        text(
                            "SELECT expert_id, status, listing, test_mode,"
                            " consent_ended_at IS NULL FROM knowbot_delivery"
                        )
                    )
                ).all()
            }
            # 照 ⑰：未审的旧行放回「等作者提交」；已审过的照审核结论补交付
            assert made == {
                ids["se"]: ("approved", "public", "self", False),
            }
            pe_row = (
                await c.execute(
                    text(
                        "SELECT user_accepted, returned_at IS NOT NULL"
                        " FROM knowbot_expert WHERE id = :e"
                    ),
                    {"e": ids["pe"]},
                )
            ).one()
            assert pe_row == (None, True)
            await c.execute(text("DELETE FROM knowbot_delivery"))
        async with engine.begin() as c:
            rows = dict(
                (
                    await c.execute(text("SELECT id, needs_return FROM knowbot_expert"))
                ).all()
            )
            # 平台孵化、平台测的要交回；自助测的不用
            assert rows == {ids["pe"]: True, ids["se"]: False}
        later = [p for p in _versions() if p.stem > TARGET.stem]
        async with engine.begin() as c:
            await c.run_sync(lambda conn: _run(conn, later))
        factory = make_session_factory(engine)
        first = _delivery(ids["se"])
        async with factory() as session:
            repo = SqlKnowlabRepository(session)
            async with repo.transaction():
                await repo.create_delivery(first)
                await repo.set_directory_sent(
                    REQUEST, {"name": "财务分析与预测", "latest_version": "2.2"}
                )
                await repo.update_test(ids["st"], evidence={"material_id": "o-1"})
        async with factory() as session:
            repo = SqlKnowlabRepository(session)
            with pytest.raises(ConcurrencyConflictError) as raised:
                async with repo.transaction():
                    await repo.create_delivery(_delivery(ids["se"]))
            assert raised.value.code == "delivery_open"
        async with factory() as session:
            repo = SqlKnowlabRepository(session)
            [found] = await repo.list_deliveries([REQUEST])
            assert found == first
            assert (await repo.get_expert(ids["se"])).needs_return is False  # type: ignore[union-attr]
            assert (await repo.get_request(REQUEST)).directory_sent == {  # type: ignore[union-attr]
                "name": "财务分析与预测",
                "latest_version": "2.2",
            }
            assert [t.id for t in await repo.tests_without_evidence(AT)] == [ids["pt"]]
            # 写不成的到了再试的时候才再取
            async with repo.transaction():
                await repo.update_test(
                    ids["pt"],
                    evidence={
                        "failures": 1,
                        "retry_after": "2026-10-30T01:00:00+00:00",
                    },
                )
            assert await repo.tests_without_evidence(AT) == []
            due = datetime(2026, 10, 30, 2, tzinfo=UTC)
            assert [t.id for t in await repo.tests_without_evidence(due)] == [ids["pt"]]
            assert await repo.deliveries_to_close() == []
            # 名录只取名称与最新准专家的版本号（不读大栏位）
            [row] = await repo.directory_rows()
            assert (row["request_id"], row["version"], row["revision"]) == (
                REQUEST,
                "2",
                2,
            )
            assert row["directory_sent"]["latest_version"] == "2.2"
            async with repo.transaction():
                await repo.update_delivery(
                    first.id, status="rejected", reason="不清楚", decided_at=AT
                )
            assert [d.id for d in await repo.deliveries_to_close()] == [first.id]
            async with repo.transaction():  # 不过的那次之后可以再交
                await repo.create_delivery(_delivery(ids["se"], listing="self"))
            with pytest.raises(ValueError):
                await repo.update_delivery(first.id, listing="self")
        async with factory() as session:  # 撤回的要记撤回时刻
            repo = SqlKnowlabRepository(session)
            with pytest.raises(IntegrityError):
                async with repo.transaction():
                    [*_, last] = await repo.list_deliveries([REQUEST])
                    await repo.update_delivery(last.id, status="withdrawn")
        async with engine.begin() as c:
            await c.execute(
                text("UPDATE knowbot_test SET mode = 'rerun' WHERE id = :t"),
                {"t": ids["st"]},
            )
            await c.run_sync(
                lambda conn: _run(conn, [*reversed(later), TARGET], "downgrade")
            )
        async with engine.begin() as c:
            mode = (
                await c.execute(
                    text("SELECT mode FROM knowbot_test WHERE id = :t"),
                    {"t": ids["st"]},
                )
            ).scalar_one()
            assert mode == "platform"
            gone = (
                await c.execute(
                    text(
                        "SELECT count(*) FROM information_schema.tables"
                        " WHERE table_schema = current_schema()"
                        " AND table_name = 'knowbot_delivery'"
                    )
                )
            ).scalar_one()
            assert gone == 0
    finally:
        await _drop(engine, schema)
