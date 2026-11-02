"""Idea shortlists pin real owner versions, without implicitly binding or publishing."""

import uuid

import pytest
from pydantic import ValidationError

from app.application.errors.exceptions import ConcurrencyConflictError, NotFoundError
from app.application.services.knowbot_methods import KnowbotMethodService
from app.application.services.knowbot_service import KnowbotCreationService
from app.application.services.knowbot_workdraft import KnowbotWorkdraftService
from app.domain.knowbots.drafts import Idea, KnowbotRequestIn
from app.domain.knowbots.lifecycle import RevisionIn
from app.infrastructure.repositories.knowbots import SqlKnowlabRepository
from tests.dynamic_method_fixtures import body
from tests.knowbot_fakes import OWNER, FakeRuntime, request_body
from tests.test_author_methods_db import binding, publish, source
from tests.test_knowbot_repository_db import db as db


def selected(record):
    return {
        "method_id": str(record.method_id),
        "version": record.version,
        "digest": record.digest,
    }


def request(choices):
    raw = request_body()
    raw["idea"]["method_choices"] = choices
    return KnowbotRequestIn.model_validate(raw)


@pytest.mark.asyncio
async def test_choices_survive_reopen_and_revision_without_changing_old_idea(db):
    source_id = await source(db)
    method = await publish(db, uuid.uuid4(), source_id)
    choice = selected(method)
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        service = KnowbotCreationService(repo, FakeRuntime())
        created = await service.submit(OWNER, request([choice]))
        assert await repo.get_workdraft(created.id) is None
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        versions = await repo.list_versions([created.id])
        assert versions[0].idea["method_choices"] == [choice]
        revised = await KnowbotCreationService(repo, FakeRuntime()).revise(
            OWNER,
            str(created.id),
            RevisionIn.model_validate(
                {
                    "idea": {"kind": "question", "question": "改为另一个问题"},
                    "cases": request_body()["cases"],
                    "consent_test_data": True,
                }
            ),
        )
        assert "method_choices" not in revised.idea
        old = next(v for v in await repo.list_versions([created.id]) if v.version == 1)
        assert old.idea["method_choices"] == [choice]
        assert await repo.get_workdraft(created.id) is None


@pytest.mark.asyncio
async def test_cross_owner_unknown_and_forged_versions_are_refused(db):
    source_id = await source(db)
    method = await publish(db, uuid.uuid4(), source_id)
    async with db() as session:
        service = KnowbotCreationService(SqlKnowlabRepository(session), FakeRuntime())
        with pytest.raises(NotFoundError):
            await service.submit("different-author", request([selected(method)]))
        with pytest.raises(NotFoundError):
            await service.submit(OWNER, request([selected(method) | {"version": 99}]))
        with pytest.raises(ConcurrencyConflictError):
            await service.submit(
                OWNER, request([selected(method) | {"digest": "sha256:" + "0" * 64}])
            )


@pytest.mark.asyncio
async def test_idea_choice_binds_and_compiles_exact_version_after_author_upgrade(db):
    source_id, mid = await source(db), uuid.uuid4()
    first = await publish(db, mid, source_id)
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        created = await KnowbotCreationService(repo, FakeRuntime()).submit(
            OWNER, request([selected(first)])
        )
        drafts = KnowbotWorkdraftService(repo)
        await drafts.save(
            OWNER, str(created.id), revision=0, edits=list(body().items())
        )
        await drafts.save(
            OWNER,
            str(source_id),
            revision=1,
            edits=[("steps/report/does", "作者发布新做法")],
        )
    second = await publish(db, mid, source_id, version=2, revision=2)
    assert second.digest != first.digest
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        methods = KnowbotMethodService(repo)
        command = binding(created.id, first)
        await methods.preview_binding(OWNER, str(mid), 1, command)
        await methods.bind_version(OWNER, str(mid), 1, command)
        current = await repo.get_workdraft(created.id)
        assert current is not None
        manifest, problems = await KnowbotWorkdraftService(repo)._compile(
            created, current, "1"
        )
        assert problems == []
        reference = manifest["pack"]["method_sources"]["report"]["reference"]
        assert {key: reference[key] for key in selected(first)} == selected(first)
        assert manifest["schema"] == "knowbot-manifest/v3"
        versions = await repo.list_versions([created.id])
        assert versions[0].idea["method_choices"] == [selected(first)]


@pytest.mark.asyncio
async def test_invalid_choice_revision_does_not_add_a_version(db):
    source_id = await source(db)
    method = await publish(db, uuid.uuid4(), source_id)
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        service = KnowbotCreationService(repo, FakeRuntime())
        created = await service.submit(OWNER, request([selected(method)]))
        revision = RevisionIn.model_validate(
            {
                "idea": {
                    "kind": "question",
                    "question": "继续",
                    "method_choices": [selected(method) | {"version": 99}],
                },
                "cases": request_body()["cases"],
                "consent_test_data": True,
            }
        )
        with pytest.raises(NotFoundError):
            await service.revise(OWNER, str(created.id), revision)
        assert len(await repo.list_versions([created.id])) == 1


def test_duplicate_choices_are_rejected_and_old_ideas_keep_their_shape():
    choice = {
        "method_id": str(uuid.uuid4()),
        "version": 1,
        "digest": "sha256:" + "a" * 64,
    }
    with pytest.raises(ValidationError):
        Idea.model_validate(
            {"kind": "question", "question": "看看", "method_choices": [choice, choice]}
        )
    assert Idea(kind="question", question="看看").model_dump(
        mode="json", exclude_none=True
    ) == {
        "kind": "question",
        "question": "看看",
    }
