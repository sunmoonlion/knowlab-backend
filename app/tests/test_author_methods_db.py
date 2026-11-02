"""Real method/version storage, author isolation, concurrency and rollback."""

import asyncio
import uuid
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.application.errors.exceptions import (
    BadRequestError,
    ConcurrencyConflictError,
    NotFoundError,
)
from app.application.services.knowbot_methods import KnowbotMethodService
from app.application.services.knowbot_service import KnowbotCreationService
from app.application.services.knowbot_workdraft import KnowbotWorkdraftService
from app.domain.knowbots.drafts import KnowbotRequestIn
from app.domain.knowbots.methods import BindMethod, FreezeMethod
from app.infrastructure.repositories.knowbots import SqlKnowlabRepository
from tests.dynamic_method_fixtures import body
from tests.knowbot_fakes import OWNER, FakeRuntime, request_body
from tests.test_knowbot_repository_db import _run
from tests.test_knowbot_repository_db import db as db

pytestmark = pytest.mark.asyncio


async def source(db, owner=OWNER):
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        req = await KnowbotCreationService(repo, FakeRuntime()).submit(
            owner, KnowbotRequestIn.model_validate(request_body())
        )
        await KnowbotWorkdraftService(repo).save(
            owner, str(req.id), revision=0, edits=list(body().items())
        )
        return req.id


def command(rid, **changes):
    return FreezeMethod.model_validate(
        {
            "request_id": str(rid),
            "revision": 1,
            "step_id": "report",
            "input_slots": {"inspect": "records"},
            **changes,
        }
    )


async def publish(db, mid, rid, version=1, owner=OWNER, **changes):
    async with db() as session:
        return await KnowbotMethodService(SqlKnowlabRepository(session)).publish(
            owner, str(mid), version, command(rid, **changes)
        )


def binding(rid, record, **changes):
    return BindMethod.model_validate(
        {
            "request_id": str(rid),
            "revision": 1,
            "alias": "report",
            "digest": record.digest,
            "inputs": {"records": "inspect"},
            "required": True,
            "max_calls": 1,
            **changes,
        }
    )


async def test_freeze_reopen_version_upgrade_and_old_references_are_stable(db):
    rid, other = await source(db), await source(db)
    mid = uuid.uuid4()
    first = await publish(db, mid, rid)
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        service = KnowbotMethodService(repo)
        left = await service.preview_binding(OWNER, str(mid), 1, binding(rid, first))
        right = await service.preview_binding(OWNER, str(mid), 1, binding(other, first))
        assert left["step"] == right["step"] and left["digest"] == right["digest"]
        # A preview cannot secretly attach or publish a different expert.
        assert (await repo.get_workdraft(rid)).revision == 1
        assert (await repo.get_workdraft(other)).revision == 1
        await KnowbotWorkdraftService(repo).save(
            OWNER,
            str(rid),
            revision=1,
            edits=[("steps/report/does", "列出每条核对结果并注明未核事项。")],
        )
    second = await publish(db, mid, rid, version=2, revision=2)
    assert second.digest != first.digest
    # Replay the exact old command after the source changes; don't read latest.
    assert (await publish(db, mid, rid)).definition == first.definition
    async with db() as session:
        service = KnowbotMethodService(SqlKnowlabRepository(session))
        old = await service.get(OWNER, str(mid), 1)
        assert old.definition == first.definition
        stable = await service.preview_binding(
            OWNER, str(mid), 1, binding(other, first)
        )
        assert stable == right
        upgraded = await service.preview_binding(
            OWNER, str(mid), 2, binding(rid, second, revision=2)
        )
        assert upgraded["step"]["does"] != stable["step"]["does"]
        listed = (await service.listing(OWNER, limit=10, offset=0))[0]
        assert listed["latest_version"] == 2
        assert listed["title"] == second.definition["step"]["title"]
        assert listed["summary"] == second.definition["step"]["does"]
        assert not await service.listing("someone-else", limit=10, offset=0)
        assert not await service.listing(OWNER, limit=10, offset=1)


async def test_owner_checks_hide_versions_and_source_workdrafts(db):
    rid, mid = await source(db), uuid.uuid4()
    record = await publish(db, mid, rid)
    with pytest.raises(NotFoundError):
        await publish(db, mid, rid, owner="intruder")
    with pytest.raises(NotFoundError):
        await publish(db, uuid.uuid4(), rid, owner="intruder")
    other = await source(db, owner="intruder")
    async with db() as session:
        service = KnowbotMethodService(SqlKnowlabRepository(session))
        with pytest.raises(NotFoundError):
            await service.get("intruder", str(mid), 1)
        with pytest.raises(NotFoundError):
            await service.preview_binding(OWNER, str(mid), 1, binding(other, record))


async def test_invalid_source_and_stale_revision_leave_no_orphan_method(db):
    rid, mid = await source(db), uuid.uuid4()
    with pytest.raises(ConcurrencyConflictError):
        await publish(db, mid, rid, revision=2)
    with pytest.raises(BadRequestError):
        await publish(db, mid, rid, input_slots={})
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        # Remove a provenance record link, simulating an unaccepted proposal.
        async with repo.transaction():
            await session.execute(
                text(
                    "UPDATE knowbot_workdraft "
                    "SET sources=sources - 'steps/report/does' "
                    "WHERE request_id=:rid"
                ),
                {"rid": rid},
            )
        with pytest.raises(BadRequestError, match="author/compile"):
            await KnowbotMethodService(repo).publish(OWNER, str(mid), 1, command(rid))
        assert await repo.method_owner(mid) is None


async def test_duplicate_freezes_are_atomic_and_conflicting_same_version_is_rejected(
    db,
):
    rid, mid = await source(db), uuid.uuid4()
    first, repeated = await asyncio.gather(publish(db, mid, rid), publish(db, mid, rid))
    assert first == repeated
    with pytest.raises(ConcurrencyConflictError):
        await publish(db, mid, rid, step_id="inspect", input_slots={})
    with pytest.raises(ConcurrencyConflictError):
        await publish(db, mid, rid, version=3)
    async with db() as session:
        assert (
            await session.execute(text("SELECT count(*) FROM knowbot_method_version"))
        ).scalar_one() == 1


async def test_failed_version_insert_rolls_back_new_method_identity(db, monkeypatch):
    rid, mid = await source(db), uuid.uuid4()
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        original = repo.insert_method_version

        async def fail(**kwargs):
            await original(**kwargs)
            raise RuntimeError("injected after insert")

        monkeypatch.setattr(repo, "insert_method_version", fail)
        with pytest.raises(RuntimeError, match="injected"):
            await KnowbotMethodService(repo).publish(OWNER, str(mid), 1, command(rid))
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        assert await repo.method_owner(mid) is None
    assert (await publish(db, mid, rid)).version == 1


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE knowbot_method_version SET definition='{}'::jsonb",
        "DELETE FROM knowbot_method_version",
    ],
)
async def test_database_rejects_mutating_accepted_method_history(db, sql):
    rid, mid = await source(db), uuid.uuid4()
    await publish(db, mid, rid)
    async with db() as session:
        with pytest.raises(DBAPIError, match="immutable"):
            async with session.begin():
                await session.execute(text(sql))


async def test_empty_migration_roundtrip_and_nonempty_downgrade_guard(db):
    migration = (
        Path(__file__).parents[1] / "alembic/versions/20261102_0025_author_methods.py"
    )
    async with db() as session:
        async with session.begin():
            conn = await session.connection()
            await conn.run_sync(lambda c: _run(c, [migration], "downgrade"))
            await conn.run_sync(lambda c: _run(c, [migration], "upgrade"))
    await publish(db, uuid.uuid4(), await source(db))
    async with db() as session:
        with pytest.raises(RuntimeError, match="cannot discard"):
            async with session.begin():
                await (await session.connection()).run_sync(
                    lambda c: _run(c, [migration], "downgrade")
                )


async def test_competing_different_freezes_cannot_claim_one_version(db):
    rid, mid = await source(db), uuid.uuid4()
    results = await asyncio.gather(
        publish(db, mid, rid),
        publish(db, mid, rid, step_id="inspect", input_slots={}),
        return_exceptions=True,
    )
    assert sum(isinstance(r, ConcurrencyConflictError) for r in results) == 1
    assert sum(not isinstance(r, BaseException) for r in results) == 1
    async with db() as session:
        record = await KnowbotMethodService(SqlKnowlabRepository(session)).get(
            OWNER, str(mid), 1
        )
        assert record.provenance["step_id"] in {"inspect", "report"}


async def test_binding_preview_rejects_cycles_and_stale_digest_without_saving(db):
    rid, mid = await source(db), uuid.uuid4()
    record = await publish(db, mid, rid)
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        service = KnowbotMethodService(repo)
        for change in (
            {"alias": "inspect", "inputs": {"records": "report"}},
            {"digest": "sha256:" + "f" * 64},
        ):
            with pytest.raises(BadRequestError):
                await service.preview_binding(
                    OWNER, str(mid), 1, binding(rid, record, **change)
                )
        current = await repo.get_workdraft(rid)
        assert current.revision == 1 and current.body["steps"][1]["uses"] == []


async def test_corrupt_definition_is_not_returned_as_a_valid_version(db, monkeypatch):
    rid, mid = await source(db), uuid.uuid4()
    record = await publish(db, mid, rid)
    async with db() as session:
        repo = SqlKnowlabRepository(session)

        async def corrupted(*args):
            return replace(record, digest="sha256:" + "f" * 64)

        monkeypatch.setattr(repo, "method_version", corrupted)
        with pytest.raises(ConcurrencyConflictError, match="integrity"):
            await KnowbotMethodService(repo).get(OWNER, str(mid), 1)
