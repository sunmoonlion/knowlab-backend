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
        changed["allowed_callers"] = ["host", "model"]
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
        changed["allowed_callers"] = ["host", "model"]
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
    assert runtime.registration_calls[1].draft.allowed_callers == ("host",)


@pytest.mark.parametrize("scope", ["platform", "acquired"])
async def test_author_matches_and_validates_only_granted_shared_outputs(
    db, monkeypatch, scope
):
    from app.application.errors.exceptions import BadGatewayError
    from app.application.services.knowbot_service import KnowbotCreationService
    from app.domain.knowbots.drafts import KnowbotRequestIn
    from app.domain.knowbots.formal_method_admission import ValidateFormalMethod
    from tests.knowbot_fakes import request_body
    from tests.test_formal_method import composite, frozen

    runtime, mid, command = await prepared(db)
    record = await freeze(db, runtime, mid, command)
    child = frozen(record.definition, mid=mid)
    ref = child.reference.model_dump(mode="json")
    consumer = "consumer"
    asset, cursor, parent = uuid4(), str(uuid4()), uuid4()
    grant = {"reference": ref, "scope": scope, "title": "Echo"}
    available = True

    async def grants(runtime_name, subject, *, reference=None, after=None):
        assert subject == consumer
        if not available:
            return {"items": [], "next": None}
        if reference:
            assert reference == child.reference
            return {
                "items": [
                    {
                        **grant,
                        "definition": record.definition,
                        "installations": [
                            {
                                "asset_id": str(asset),
                                "revision": 1,
                                "name": "test",
                                "account": None,
                            }
                        ],
                        "selected": str(asset),
                    }
                ],
                "next": None,
            }
        if after == cursor:
            return {"items": [grant], "next": None}
        # Same ID/version, different digest: must yield an empty, non-final page.
        return {
            "items": [{**grant, "reference": {**ref, "digest": "sha256:" + "0" * 64}}],
            "next": cursor,
        }

    monkeypatch.setattr(runtime, "incubation_tool_versions", grants, raising=False)
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        service = FormalMethodService(repo, runtime)
        request = await KnowbotCreationService(repo, runtime).submit(
            consumer, KnowbotRequestIn.model_validate(request_body())
        )
        rid = str(request.id)
        slot = draft().definition.procedure.inputs["value"]

        async def match(*, shared=True, after=None, exclude=parent, wanted=slot):
            return await service.matching_outputs(
                consumer, rid, wanted, exclude, 0, shared=shared, after=after
            )

        assert await match(shared=False) == {"items": [], "next": None}
        assert await match() == {"items": [], "next": cursor}
        assert await match(after=cursor) == {
            "items": [{"reference": ref, "output": "result", "title": "Echo"}],
            "next": None,
        }
        assert not (await match(after=cursor, exclude=mid))["items"]
        assert not (
            await match(
                after=cursor,
                wanted=slot.model_copy(update={"meaning": {"unit": "other"}}),
            )
        )["items"]
        with pytest.raises(BadGatewayError):
            await match(after="ffffffff-ffff-ffff-ffff-ffffffffffff")
        proposal = {
            "definition": composite(child),
            "source": None,
            "allowed_callers": ["host"],
        }
        await service.workdrafts.save(
            consumer, rid, revision=0, edits=[("formal_method", proposal)]
        )
        validate = ValidateFormalMethod(
            request_id=request.id, revision=1, run_id=uuid4()
        )
        assert (await service.validate(consumer, str(parent), validate))[
            "status"
        ] == "pending"
        available = False
        assert await match(after=cursor) == {"items": [], "next": None}
        retry = validate.model_copy(update={"run_id": uuid4()})
        with pytest.raises(NotFoundError):
            await service.validate(consumer, str(parent), retry)
        assert retry.run_id not in runtime.validation_requests


async def test_p1_adoption_replays_only_the_same_confirmed_revision(db):
    from copy import deepcopy

    from app.application.errors.exceptions import BadRequestError
    from tests.test_formal_method import composite, frozen

    runtime, _, cmd = await prepared(db)
    rid = str(cmd.request_id)
    plan = {
        "definition": composite(frozen(), frozen()),
        "source": None,
        "allowed_callers": ["host"],
    }

    async def save(revision, doc, *, owner=OWNER):
        async with db() as session:
            service = KnowbotWorkdraftService(SqlKnowlabRepository(session))
            got = await service.save(
                owner,
                rid,
                revision=revision,
                edits=[("formal_method", doc)],
                adopt_p1=True,
            )
            return got, await service.p1_receipt(got)

    (first, proof), (retry, duplicate) = await asyncio.gather(
        save(1, plan), save(1, plan)
    )
    assert first == retry and proof == duplicate
    assert (proof["base_revision"], proof["saved_revision"]) == (1, 2)
    assert first.sources["formal_method"] == {
        "kind": "user_confirmed",
        "ref": "record:" + proof["record_id"],
    }
    with pytest.raises(NotFoundError):
        await save(1, plan, owner="other-author")
    with pytest.raises(BadRequestError):
        await save(2, plan)
    changed = deepcopy(plan)
    changed["definition"]["procedure"]["instructions"] = (
        "Modified after the first preview"
    )
    with pytest.raises(ConcurrencyConflictError):
        await save(1, changed)
    newer, later = await save(2, changed)
    assert later["previous_record"] == first.sources["formal_method"]["ref"]
    assert newer.revision == 3 and later["digest"] != proof["digest"]
    with pytest.raises(ConcurrencyConflictError):
        await save(1, plan)
