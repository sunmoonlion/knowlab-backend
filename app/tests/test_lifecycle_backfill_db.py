"""Replay historical KnowLab transitions in a fresh, transaction-local schema."""

import os
import runpy
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine


@pytest_asyncio.fixture
async def db():
    url = os.environ.get("DELIVERY_TEST_DATABASE_URL")
    if not url:
        pytest.skip("set DELIVERY_TEST_DATABASE_URL to a disposable *_tests database")
    parsed = make_url(url)
    assert parsed.database and parsed.database.endswith("_tests")
    engine = create_async_engine(parsed.set(drivername="postgresql+asyncpg"))
    try:
        async with engine.connect() as conn, conn.begin():
            schema = "lifecycle_" + uuid4().hex
            await conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
            await conn.exec_driver_sql(f'SET LOCAL search_path TO "{schema}",public')

            def setup(c):
                with Operations.context(MigrationContext.configure(c)):
                    for path in sorted(
                        (Path(__file__).resolve().parents[1] / "alembic/versions").glob(
                            "20*.py"
                        )
                    ):
                        module = runpy.run_path(str(path))
                        if module["revision"] == "20261024_0012":
                            return module
                        module["upgrade"]()

            module = await conn.run_sync(setup)

            async def run(fn):
                def step(c):
                    with Operations.context(MigrationContext.configure(c)):
                        fn(c, module)

                await conn.run_sync(step)

            yield run
            await conn.rollback()
    finally:
        await engine.dispose()


def insert_drafts(conn, versions):
    request = uuid4()
    conn.execute(
        text("""INSERT INTO knowbot_request
        (id,owner_subject,title,runtime,idea,cases)
        VALUES (:id,'author','synthetic','knowbench','{}','[]')"""),
        {"id": request},
    )
    for revision, version in enumerate(versions, 1):
        conn.execute(
            text("""INSERT INTO knowbot_draft
            (id,request_id,revision,version,status,manifest)
            VALUES (:id,:request,:revision,:version,'passed','{}')"""),
            dict(id=uuid4(), request=request, revision=revision, version=version),
        )


async def test_backfilled_unlisted_expert_can_downgrade(db):
    def replay(conn, migration):
        insert_drafts(conn, ["1"])
        migration["upgrade"]()
        assert (
            conn.scalar(text("select count(*) from knowbot_expert where not trial"))
            == 1
        )
        migration["downgrade"]()
        assert conn.scalar(text("select status from knowbot_draft")) == "passed"

    await db(replay)


@pytest.mark.parametrize("versions", [["1", "01"], ["0"], ["2147483648"], ["v1"]])
async def test_preflight_reports_unrepresentable_versions(db, versions):
    def replay(conn, migration):
        insert_drafts(conn, versions)
        with pytest.raises(RuntimeError, match="0012 cannot backfill"):
            migration["_preflight"]()

    await db(replay)


async def test_knowbase_only_dataset_downgrade_explains_recovery(db):
    def replay(conn, _):
        conn.execute(
            text("""INSERT INTO knowbot_dataset
            (id,owner_subject,name,version,content_type,size_bytes,sha256,knowbase_item_id)
            VALUES (:id,'author','synthetic','1','text/plain',1,:sha,'item-1')"""),
            {"id": uuid4(), "sha": "0" * 64},
        )
        module = runpy.run_path(
            str(
                next(
                    (Path(__file__).resolve().parents[1] / "alembic/versions").glob(
                        "*_0011_*.py"
                    )
                )
            )
        )
        with pytest.raises(RuntimeError, match="knowbase"):
            module["downgrade"]()
        assert (
            conn.scalar(text("select knowbase_item_id from knowbot_dataset"))
            == "item-1"
        )

    await db(replay)
