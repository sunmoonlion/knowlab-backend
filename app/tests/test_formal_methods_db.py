"""Real storage/concurrency for author formal-method admission (isolated schemas)."""

import asyncio
from uuid import uuid4

import pytest

from app.application.errors.exceptions import (
    ConcurrencyConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from app.application.services.formal_methods import FormalMethodService
from app.application.services.knowbot_methods import KnowbotMethodService
from app.application.services.knowbot_workdraft import KnowbotWorkdraftService
from app.infrastructure.repositories.knowbots import SqlKnowlabRepository
from tests.test_formal_method_service import (
    OWNER,
    Runtime,
    draft,
    freeze_command,
    setup,
)
from tests.test_knowbot_repository_db import db as db

pytestmark = pytest.mark.asyncio


async def prepared(db):
    runtime = Runtime()
    async with db() as session:
        _, _, service, mid, command = await setup(
            SqlKnowlabRepository(session), runtime
        )
        await service.validate(OWNER, str(mid), command)
    runtime.complete(command.run_id)
    return runtime, mid, command


async def freeze(db, runtime, mid, command):
    async with db() as session:
        return await FormalMethodService(SqlKnowlabRepository(session), runtime).freeze(
            OWNER, str(mid), 1, freeze_command(command)
        )


async def test_concurrent_formal_freeze_replays_one_immutable_version(db):
    runtime, mid, command = await prepared(db)
    first, retry = await asyncio.gather(
        freeze(db, runtime, mid, command), freeze(db, runtime, mid, command)
    )
    assert first == retry
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        service = FormalMethodService(repo, runtime)
        assert (
            await service.get(OWNER, str(mid), 1)
        ).definition == draft().definition.document()
        assert await repo.method_head(mid) == 1
        formal = await service.listing(OWNER, limit=30, offset=0)
        assert len(formal) == 1 and formal[0]["method_kind"] == "tool"
        assert formal[0]["title"] == "Echo"
        assert await KnowbotMethodService(repo).listing(OWNER, limit=30, offset=0) == []
        with pytest.raises(NotFoundError):
            await service.get("other-user", str(mid), 1)


async def test_changed_workdraft_cannot_freeze_using_earlier_runtime_receipt(db):
    runtime, mid, command = await prepared(db)
    async with db() as session:
        service = KnowbotWorkdraftService(SqlKnowlabRepository(session))
        changed = draft().model_dump(mode="json", by_alias=True)
        changed["allowed_callers"] = ["host"]
        await service.save(
            OWNER,
            str(command.request_id),
            revision=1,
            edits=[("formal_method", changed)],
        )
    with pytest.raises(ConcurrencyConflictError):
        await freeze(db, runtime, mid, command)
    with pytest.raises(ConcurrencyConflictError):
        await freeze(db, runtime, mid, command.model_copy(update={"revision": 2}))
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        assert await repo.method_owner(mid) is None
        assert await repo.method_head(mid) == 0


async def test_formal_version_rejects_another_freeze_identity(db):
    runtime, mid, command = await prepared(db)
    original = await freeze(db, runtime, mid, command)
    newer = command.model_copy(update={"run_id": uuid4()})
    async with db() as session:
        service = FormalMethodService(SqlKnowlabRepository(session), runtime)
        await service.validate(OWNER, str(mid), newer)
    runtime.complete(newer.run_id)
    with pytest.raises(ConcurrencyConflictError) as error:
        await freeze(db, runtime, mid, newer)
    assert error.value.code == "method_version_conflict"
    async with db() as session:
        record = await FormalMethodService(SqlKnowlabRepository(session), runtime).get(
            OWNER, str(mid), 1
        )
        assert record == original


async def test_registration_failure_retries_committed_version_without_transaction(
    db,
):
    runtime, mid, command = await prepared(db)
    runtime.fail_register = True
    async with db() as session:

        async def registered_after_commit(frozen):
            assert not session.in_transaction()
            async with db() as observer:
                stored = await SqlKnowlabRepository(observer).method_version(mid, 1)
                assert stored is not None and stored.digest == frozen.reference.digest

        runtime.on_register = registered_after_commit
        service = FormalMethodService(SqlKnowlabRepository(session), runtime)
        with pytest.raises(ServiceUnavailableError):
            await service.freeze(OWNER, str(mid), 1, freeze_command(command))
    async with db() as session:
        changed = draft().model_dump(mode="json", by_alias=True)
        changed["allowed_callers"] = ["host"]
        await KnowbotWorkdraftService(SqlKnowlabRepository(session)).save(
            OWNER,
            str(command.request_id),
            revision=1,
            edits=[("formal_method", changed)],
        )
    runtime.fail_register = False
    await freeze(db, runtime, mid, command)
    assert len(runtime.registrations) == 1
    assert runtime.registration_calls[0] == runtime.registration_calls[1]
    assert runtime.registration_calls[1].draft.allowed_callers == ("host", "model")
