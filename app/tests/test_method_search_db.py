"""Durable page budgets and gap identity, using independent PostgreSQL sessions."""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.application.errors.exceptions import NotFoundError
from app.application.services.formal_methods import FormalMethodService
from app.application.services.method_search import scan_outputs
from app.domain.knowbots.incubation_tools import AutomaticOutputSearch
from app.infrastructure.repositories.knowbots import SqlKnowlabRepository
from tests.test_formal_method_service import OWNER, draft, setup
from tests.test_knowbot_repository_db import db as db


async def project(db):
    async with db() as session:
        _, runtime, _, mid, command = await setup(SqlKnowlabRepository(session))
    query = AutomaticOutputSearch(
        slot=draft().definition.procedure.inputs["value"],
        exclude_method_id=mid,
        directory="owned",
        page_limit=3,
        action_id=uuid4(),
    )
    return runtime, str(command.request_id), query


async def test_budget_and_progress_survive_revision_and_connections(db, monkeypatch):
    runtime, rid, query = await project(db)
    calls = []
    ref = {"method_id": str(uuid4()), "version": 1, "digest": "sha256:" + "a" * 64}
    item = {"reference": ref, "output": "out", "title": "A"}

    async def match(self, owner, request, slot, exclude, offset, **kwargs):
        assert not self.repo.session.in_transaction()
        calls.append(offset)
        return {
            "items": [item] if offset else [],
            "next": {0: 20, 20: 40, 40: None}[offset],
        }

    monkeypatch.setattr(FormalMethodService, "matching_outputs", match)

    async def scan(cmd, owner=OWNER):
        async with db() as session:
            return await scan_outputs(
                FormalMethodService(SqlKnowlabRepository(session), runtime),
                owner,
                rid,
                cmd,
            )

    with pytest.raises(NotFoundError):
        await scan(query, "another-author")
    first = await scan(query)
    assert (first["reason"], first["used"]) == ("continue", 1)
    assert (await scan(query))["reason"] == "already_read"
    async with db() as session:
        await FormalMethodService(
            SqlKnowlabRepository(session), runtime
        ).workdrafts.save(
            OWNER,
            rid,
            revision=1,
            edits=[("formal_method", draft().model_dump(mode="json", by_alias=True))],
        )
    renamed = query.model_copy(
        update={
            "slot": query.slot.model_copy(update={"description": "new wording"}),
            "action_id": uuid4(),
        }
    )
    result = await scan(renamed)
    assert result["items"] == [item] and result["used"] == 2
    assert (await scan(renamed.model_copy(update={"action_id": uuid4()})))[
        "reason"
    ] == "no_progress"
    assert (await scan(renamed.model_copy(update={"action_id": uuid4()})))["used"] == 3
    other = query.model_copy(update={"action_id": uuid4(), "directory": "shared"})
    assert (await scan(other))["reason"] == "budget"
    assert calls == [0, 20, 40]
    assert (await scan(other.model_copy(update={"page_limit": 4})))["used"] == 4


async def test_concurrent_last_page_and_abandoned_read(db, monkeypatch):
    runtime, rid, query = await project(db)
    started, finish = asyncio.Event(), asyncio.Event()
    sends = 0

    async def match(self, *args, **kwargs):
        nonlocal sends
        assert not self.repo.session.in_transaction()
        sends += 1
        if sends == 1:
            started.set()
            await asyncio.wait_for(finish.wait(), 10)
        return {"items": [], "next": None}

    monkeypatch.setattr(FormalMethodService, "matching_outputs", match)

    async def scan(**extra):
        async with db() as session:
            return await scan_outputs(
                FormalMethodService(SqlKnowlabRepository(session), runtime),
                OWNER,
                rid,
                query.model_copy(
                    update={"action_id": uuid4(), "page_limit": 1, **extra}
                ),
            )

    active = asyncio.create_task(scan())
    try:
        await asyncio.wait_for(started.wait(), 10)
        same, other = await asyncio.gather(scan(), scan(directory="shared"))
        assert (same["reason"], other["reason"], sends) == ("busy", "budget", 1)
        async with db() as session, session.begin():
            await session.execute(
                text(
                    "UPDATE method_search_page SET started_at=now()-interval '1 hour' "
                    "WHERE request_id=:id"
                ),
                {"id": rid},
            )
        retry = await scan(page_limit=2)
        assert (retry["used"], retry["reason"], sends) == (2, "no_progress", 2)
    finally:
        finish.set()
    assert (await active)["reason"] == "already_read"


async def test_failed_directory_read_can_retry_same_page_with_remaining_budget(
    db, monkeypatch
):
    runtime, rid, query = await project(db)
    async with db() as session:
        service = FormalMethodService(SqlKnowlabRepository(session), runtime)
        attempts = []

        async def sometimes_unavailable(*args, **kwargs):
            attempts.append(args[4])
            if len(attempts) == 1:
                raise TimeoutError("test directory unavailable")
            return {"items": [], "next": None}

        monkeypatch.setattr(service, "matching_outputs", sometimes_unavailable)
        with pytest.raises(TimeoutError):
            await scan_outputs(service, OWNER, rid, query)
        denied = await scan_outputs(
            service,
            OWNER,
            rid,
            query.model_copy(update={"action_id": uuid4(), "page_limit": 1}),
        )
        assert (denied["reason"], denied["pages_read"], denied["used"]) == (
            "budget",
            0,
            1,
        )
        retry = await scan_outputs(
            service, OWNER, rid, query.model_copy(update={"action_id": uuid4()})
        )
        assert (retry["reason"], retry["pages_read"], retry["used"]) == (
            "no_progress",
            1,
            2,
        )
        assert attempts == [0, 0]
