"""Real author storage: failed composition/publication leaves no partial version."""

import uuid

import pytest

from app.application.errors.exceptions import BadRequestError
from app.application.services.knowbot_methods import KnowbotMethodService
from app.application.services.knowbot_workdraft import KnowbotWorkdraftService
from app.infrastructure.repositories.knowbots import SqlKnowlabRepository
from tests.knowbot_fakes import OWNER
from tests.test_author_methods_db import binding, publish, source
from tests.test_knowbot_repository_db import db as db  # noqa: F401

pytestmark = pytest.mark.asyncio


async def test_conflicting_workdraft_is_saved_for_correction_but_cannot_publish(db):
    rid = await source(db)
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        service = KnowbotWorkdraftService(repo)
        saved = await service.save(
            OWNER,
            str(rid),
            revision=1,
            edits=[
                (
                    "steps/report/advanced",
                    {"output_file": "report/result.md", "tools": []},
                ),
                (
                    "steps/inspect/advanced",
                    {"output_file": "inspect/RESULT.md", "tools": ["read_input"]},
                ),
            ],
        )
        _, problems, _ = await service.preview(OWNER, str(rid))
        assert any(
            p.path == "steps/inspect/advanced" and "冲突" in p.message for p in problems
        )
        with pytest.raises(BadRequestError) as caught:
            await service.publish(OWNER, str(rid))
        assert caught.value.code == "workdraft_invalid"
        assert not await repo.list_drafts(request_ids=[rid])
        current = await repo.get_workdraft(rid)
        assert current.body == saved.body and current.revision == 2
    # Reopen the storage session and correct only the filename, through save.
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        service = KnowbotWorkdraftService(repo)
        await service.save(
            OWNER,
            str(rid),
            revision=2,
            edits=[
                (
                    "steps/inspect/advanced",
                    {"output_file": "inspect/evidence.md", "tools": ["read_input"]},
                ),
            ],
        )
        draft = await service.publish(OWNER, str(rid))
        assert draft.status == "pending_test"
        assert len(await repo.list_drafts(request_ids=[rid])) == 1
        assert (await repo.get_workdraft(rid)).revision == 3


async def test_conflicting_method_binding_preserves_target_and_frozen_source(db):
    origin, target, mid = await source(db), await source(db), uuid.uuid4()
    async with db() as session:
        drafts = KnowbotWorkdraftService(SqlKnowlabRepository(session))
        await drafts.save(
            OWNER,
            str(origin),
            revision=1,
            edits=[
                (
                    "steps/report/advanced",
                    {"output_file": "report/result.md", "tools": []},
                ),
            ],
        )
        target_before = await drafts.save(
            OWNER,
            str(target),
            revision=1,
            edits=[
                (
                    "steps/inspect/advanced",
                    {"output_file": "inspect/result.md", "tools": ["read_input"]},
                ),
            ],
        )
    frozen = await publish(db, mid, origin, revision=2)
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        methods = KnowbotMethodService(repo)
        for action in (methods.preview_binding, methods.bind_version):
            with pytest.raises(BadRequestError) as caught:
                await action(OWNER, str(mid), 1, binding(target, frozen, revision=2))
            assert caught.value.code == "method_binding_invalid"
        current = await repo.get_workdraft(target)
        assert (
            current.revision == target_before.revision
            and current.body == target_before.body
        )
        assert await methods.get(OWNER, str(mid), 1) == frozen
        assert not await repo.list_drafts(request_ids=[target])
