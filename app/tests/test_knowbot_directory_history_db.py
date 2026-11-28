"""Isolated PostgreSQL with a recording directory client and SQLAlchemy load events."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import event

from app.infrastructure.models.knowbots import FormalTestSuite
from app.infrastructure.repositories.knowbots import SqlKnowlabRepository
from tests.knowbot_fakes import OWNER, historical_manifest, manifest
from tests.test_formal_method_execution import FormalTestSuiteDraft, suite
from tests.test_knowbot_repository_db import db as database  # noqa: F401
from tests.test_knowbot_repository_db import deliveries


@pytest.fixture
def db(database):  # noqa: F811
    return database


async def test_directory_refresh_uses_current_expert_drafts(db):
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        async with repo.transaction():
            requests = []
            for title, manifests in [
                ("retired", [historical_manifest()]),
                ("mixed", [manifest(), historical_manifest()]),
            ]:
                request = await repo.create_request(
                    owner_subject=OWNER,
                    title=title,
                    runtime="knowbench",
                    idea={},
                    cases=[],
                )
                requests.append(request)
                for revision, document in enumerate(manifests, 1):
                    await repo.create_draft(
                        request_id=request.id,
                        request_version=1,
                        revision=revision,
                        version=str(revision),
                        manifest=document,
                    )
        assert await repo.get_request(requests[0].id) is None
        assert await repo.get_request(requests[1].id) is not None
        service = deliveries(session)
        service.knowbase = AsyncMock()
        await service.sync_directory()
        sent = {
            call.kwargs["name"]: call.kwargs["latest_version"]
            for call in service.knowbase.push_expert.await_args_list
        }
        assert sent == {"mixed": "1.1"}


async def test_next_suite_does_not_load_every_previous_case_body(db):
    suite_id = str(uuid4())
    document = suite()

    def version(number):
        return (
            FormalTestSuiteDraft.model_validate(
                {**document, "suite_id": suite_id, "version": number}
            )
            .freeze()
            .model_dump(mode="json")
        )

    async with db() as session:
        repo = SqlKnowlabRepository(session)
        async with repo.transaction():
            request = await repo.create_request(
                owner_subject=OWNER,
                title="suite",
                runtime="knowbench",
                idea={},
                cases=[],
            )
            for number in range(1, 7):
                await repo.freeze_formal_test_suite(OWNER, request.id, version(number))
    loaded = []

    def observed(row, context):
        if "document" in row.__dict__:
            loaded.append(row.version)

    event.listen(FormalTestSuite, "load", observed)
    try:
        async with db() as session:
            repo = SqlKnowlabRepository(session)
            async with repo.transaction():
                await repo.freeze_formal_test_suite(OWNER, request.id, version(7))
    finally:
        event.remove(FormalTestSuite, "load", observed)
    assert len(loaded) <= 1, loaded


async def test_retired_request_replay_has_a_business_outcome(db):

    from app.application.errors.exceptions import AppException
    from app.domain.knowbots.method_demand import FormalHandoff
    from tests.test_knowbot_repository_db import service

    command = FormalHandoff(
        contract="knowlab/formal-handoff/v2",
        owner_subject=OWNER,
        project_id=str(uuid4()),
        task_id=str(uuid4()),
        key="knowbench:handoff:history",
        question="核对资料",
        gap="需要资料",
        review_digest="sha256:" + "a" * 64,
    )
    async with db() as session:
        record, _ = await service(session).submit_from_runtime(
            OWNER, command, runtime="knowbench"
        )
        repo = SqlKnowlabRepository(session)
        async with repo.transaction():
            workdraft = await repo.get_workdraft(record.id)
            if workdraft is not None:
                await repo.retire_method_workdraft(workdraft)
            await repo.create_draft(
                request_id=record.id,
                request_version=1,
                revision=1,
                version="1",
                manifest=historical_manifest(),
            )
        assert await repo.get_request(record.id) is None
    async with db() as session:
        with pytest.raises(AppException) as retired:
            await service(session).submit_from_runtime(
                OWNER, command, runtime="knowbench"
            )
        assert retired.value.code == "incubation_request_retired"
