"""Atomic definitions and execution policy survive real immutable storage."""

from uuid import uuid4

from app.application.services.knowbot_methods import KnowbotMethodService
from app.application.services.knowbot_workdraft import KnowbotWorkdraftService
from app.domain.knowbots.workdraft import WorkdraftBody
from app.infrastructure.repositories.knowbots import SqlKnowlabRepository
from tests.knowbot_fakes import OWNER
from tests.test_atomic_methods import binding, command, source_body
from tests.test_author_methods_db import source
from tests.test_knowbot_repository_db import db as db


async def test_freeze_reopen_bind_and_preview_atomic_method(db):
    rid, mid = await source(db), uuid4()
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        saved = await KnowbotWorkdraftService(repo).save(
            OWNER,
            str(rid),
            revision=1,
            edits=[
                (key, value)
                for key, value in source_body().model_dump(mode="json").items()
                if key in {"steps", "planning", "public_capabilities"}
            ],
        )
        frozen = await KnowbotMethodService(repo).publish(
            OWNER,
            str(mid),
            1,
            command().model_copy(
                update={"request_id": rid, "revision": saved.revision}
            ),
        )
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        svc = KnowbotMethodService(repo)
        from app.domain.knowbots.methods import parse_definition

        record = await svc.get(OWNER, str(mid), 1)
        assert record.definition == frozen.definition
        assert record.digest == frozen.digest
        listing = await svc.listing(OWNER, limit=30, offset=0)
        assert listing[0]["title"] == frozen.definition["title"]
        cmd = binding(parse_definition(record.definition)).model_copy(
            update={"request_id": rid, "revision": saved.revision}
        )
        preview = await svc.preview_binding(OWNER, str(mid), 1, cmd)
        assert preview["preview_only"]
        bound = await svc.bind_version(OWNER, str(mid), 1, cmd)
        assert (
            bound.body["method_refs"]["report"]["execution"]["on_fail"]["kind"] == "ask"
        )
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        current = await repo.get_workdraft(rid)
        snapshots = await KnowbotWorkdraftService(repo).method_sources(
            OWNER,
            WorkdraftBody.model_validate(current.body),
        )
        assert snapshots["report"]["definition"] == frozen.definition


async def test_independent_step_freezes_without_inventing_an_expert_plan(db):
    from app.domain.knowbots.methods import freeze
    from tests.dynamic_method_fixtures import body

    rid = await source(db)
    raw = body()
    steps = raw["steps"]
    steps[0]["why"] = "逐条保留输入集合"
    for step in steps:
        step.pop("selection")
    # Upstream first so even the ordinary workflow satisfies dependency ordering.
    steps[0], steps[1] = steps[1], steps[0]
    cmd = command().model_copy(
        update=dict(
            request_id=rid,
            revision=2,
            applies_when="需要确认输入条目无遗漏时",
            input_slots={"inspect": "records"},
            input_fields={"records": ("rows",)},
            input_descriptions={"records": "待核对的条目"},
        )
    )
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        saved = await KnowbotWorkdraftService(repo).save(
            OWNER, str(rid), revision=1, edits=[("steps", steps), ("planning", None)]
        )
        expected = freeze(WorkdraftBody.model_validate(saved.body), cmd)
        _, issues, _ = await KnowbotWorkdraftService(repo).preview(OWNER, str(rid))
        assert len(issues) == 1 and "固定 v2 专家已退役" in issues[0].message, issues
        record = await KnowbotMethodService(repo).publish(OWNER, str(uuid4()), 1, cmd)
        assert record.digest == expected.digest()
