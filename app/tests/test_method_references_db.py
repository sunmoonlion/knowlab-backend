"""Expert references are exact, author-confirmed and independent of latest heads."""

import asyncio
import copy
import uuid

import pytest

from app.application.errors.exceptions import (
    BadRequestError,
    ConcurrencyConflictError,
    NotFoundError,
)
from app.application.services.knowbot_methods import KnowbotMethodService
from app.application.services.knowbot_workdraft import (
    KnowbotWorkdraftService,
    parse_body,
)
from app.domain.knowbots.compile import decompile, pack_problems
from app.domain.knowbots.workdraft import cells
from app.infrastructure.repositories.knowbots import SqlKnowlabRepository
from tests.knowbot_fakes import OWNER
from tests.test_author_methods_db import binding, publish, source
from tests.test_knowbot_repository_db import db as db

pytestmark = pytest.mark.asyncio


async def attach(db, rid, record, revision=1, **changes):
    async with db() as session:
        return await KnowbotMethodService(SqlKnowlabRepository(session)).bind_version(
            OWNER,
            str(record.method_id),
            record.version,
            binding(rid, record, revision=revision, **changes),
        )


async def test_shared_method_upgrade_preserves_other_expert_and_released_snapshots(db):
    origin, left, right = await source(db), await source(db), await source(db)
    mid = uuid.uuid4()
    first = await publish(db, mid, origin)
    for rid in (left, right):
        saved = await attach(db, rid, first)
        assert saved.revision == 2
        assert saved.body["method_refs"]["report"]["digest"] == first.digest
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        work = KnowbotWorkdraftService(repo)
        one = await work.publish(OWNER, str(left))
        other = await work.publish(OWNER, str(right))
        assert (
            one.manifest["pack"]["runtime_contract"] == "knowbench/dynamic-methods/v2"
        )
        assert (
            one.manifest["pack"]["method_sources"]
            == other.manifest["pack"]["method_sources"]
        )
        assert not pack_problems(one.manifest["pack"])
        await work.save(
            OWNER,
            str(origin),
            revision=1,
            edits=[("steps/report/does", "列出核对结果并标明未核事项。")],
        )
    second = await publish(db, mid, origin, version=2, revision=2)
    await attach(db, left, second, revision=2)
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        work = KnowbotWorkdraftService(repo)
        new = await work.publish(OWNER, str(left))
        assert (
            new.manifest["pack"]["method_sources"]["report"]["reference"]["version"]
            == 2
        )
        assert (
            new.manifest["pack"]["method_units"][0]["contract"]["step_version"] == "2"
        )
        assert (await repo.get_draft(one.id)).manifest == one.manifest
        assert (await repo.get_draft(other.id)).manifest == other.manifest
        assert (await repo.get_workdraft(right)).body["method_refs"]["report"][
            "version"
        ] == 1
        _, problems, _ = await work.preview(OWNER, str(left))
        assert not problems  # includes exact per-cell author provenance


@pytest.mark.parametrize(
    "path,value",
    [
        ("steps/report/does", "偷偷修改"),
        ("steps/report/advanced", {"tools": ["run_sql"]}),
        (
            "steps/report/selection",
            {"applies_when": "总是", "required": False, "max_calls": 9},
        ),
        ("steps/report", None),
        ("steps/inspect/outputs", [{"name": "rows", "type": "text"}]),
    ],
)
async def test_regular_edits_cannot_mutate_reference_or_break_its_inputs(
    db, path, value
):
    rid, mid = await source(db), uuid.uuid4()
    record = await publish(db, mid, rid)
    saved = await attach(db, rid, record)
    async with db() as session:
        work = KnowbotWorkdraftService(SqlKnowlabRepository(session))
        with pytest.raises(BadRequestError):
            await work.save(OWNER, str(rid), revision=2, edits=[(path, value)])
        _, current = await work.get(OWNER, str(rid))
        assert current.body == saved.body and current.revision == 2


async def test_common_store_blocks_proposals_that_drop_references(db):
    rid, mid = await source(db), uuid.uuid4()
    record = await publish(db, mid, rid)
    saved = await attach(db, rid, record)
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        work = KnowbotWorkdraftService(repo)
        raw = copy.deepcopy(saved.body)
        raw.pop("method_refs")
        async with repo.transaction():
            with pytest.raises(BadRequestError, match="explicit version binding"):
                await work.store(saved, parse_body(raw), saved.sources)
        assert (await repo.get_workdraft(rid)).revision == 2


async def test_copy_import_preserve_refs_and_refuse_cross_owner_relabeling(db):
    origin, target, foreign = (
        await source(db),
        await source(db),
        await source(db, owner="other"),
    )
    record = await publish(db, uuid.uuid4(), origin)
    await attach(db, origin, record)
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        work = KnowbotWorkdraftService(repo)
        released = await work.publish(OWNER, str(origin))
        restored, notes = decompile(released.manifest)
        assert (
            not notes and restored.method_refs["report"].method_id == record.method_id
        )
        copied, notes = await work.copy_from(
            OWNER, str(target), revision=1, draft_id=str(released.id)
        )
        assert (
            not notes
            and copied.body["method_refs"]
            == restored.model_dump(mode="json")["method_refs"]
        )
        assert "method_refs/report" in cells(parse_body(copied.body))
        _, issues, _ = await work.preview(OWNER, str(target))
        assert not issues
        with pytest.raises(NotFoundError):
            await work.import_manifest(
                "other", str(foreign), revision=1, manifest=released.manifest
            )
        assert (await repo.get_workdraft(foreign)).revision == 1
        plain = copy.deepcopy(released.manifest)
        plain["pack"].pop("method_sources")
        plain["pack"].pop("method_compiler")
        plain["pack"]["runtime_contract"] = "knowbench/dynamic-methods/v1"
        with pytest.raises(BadRequestError, match="explicit version binding"):
            await work.import_manifest(OWNER, str(target), revision=2, manifest=plain)


async def test_binding_revision_race_and_transaction_rollback(db, monkeypatch):
    rid = await source(db)
    record = await publish(db, uuid.uuid4(), rid)
    results = await asyncio.gather(
        attach(db, rid, record), attach(db, rid, record), return_exceptions=True
    )
    assert sum(isinstance(r, ConcurrencyConflictError) for r in results) == 1
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        before = await repo.get_workdraft(rid)

        async def broken(*args, **kwargs):
            raise RuntimeError("injected write failure")

        monkeypatch.setattr(repo, "update_workdraft", broken)
        with pytest.raises(RuntimeError, match="injected"):
            await KnowbotMethodService(repo).bind_version(
                OWNER,
                str(record.method_id),
                1,
                binding(rid, record, revision=2, required=False),
            )
        after = await repo.get_workdraft(rid)
        assert after == before


@pytest.mark.parametrize(
    "kind", ["digest", "body", "contract", "inputs", "unknown_alias", "compiler"]
)
async def test_referenced_import_rejects_snapshot_and_compilation_tampering(db, kind):
    rid = await source(db)
    record = await publish(db, uuid.uuid4(), rid)
    await attach(db, rid, record)
    async with db() as session:
        work = KnowbotWorkdraftService(SqlKnowlabRepository(session))
        manifest, errors, _ = await work.preview(OWNER, str(rid))
        assert not errors
    pack = manifest["pack"]
    snap = pack["method_sources"]["report"]
    if kind == "digest":
        snap["reference"]["digest"] = "sha256:" + "0" * 64
    elif kind == "body":
        snap["definition"]["step"]["does"] = "改写原文"
    elif kind == "contract":
        pack["method_units"][0]["contract"]["tools"] = ["run_sql"]
    elif kind == "inputs":
        snap["reference"]["inputs"] = {"records": "review"}
    elif kind == "compiler":
        pack["method_compiler"] = "unknown"
    else:
        pack["method_sources"]["missing"] = pack["method_sources"].pop("report")
    assert pack_problems(pack)
    with pytest.raises(ValueError):
        decompile(manifest)
