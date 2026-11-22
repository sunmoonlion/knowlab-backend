"""The transfer remains a reference after storage, reload and idempotent replay."""

import pytest

from app.application.errors.exceptions import ConcurrencyConflictError
from app.domain.knowbots.method_demand import RuntimeIncubationV2
from tests.knowbot_fakes import OWNER
from tests.test_knowbot_repository_db import db as db
from tests.test_knowbot_repository_db import service
from tests.test_runtime_method_demand import request_doc


async def test_method_demand_survives_storage_and_replay(db):
    command = RuntimeIncubationV2.model_validate(request_doc())
    async with db() as session:
        first, created = await service(session).submit_from_runtime(
            OWNER, command, runtime="knowbench"
        )
        assert created
    async with db() as session:
        again, created = await service(session).submit_from_runtime(
            OWNER, command, runtime="knowbench"
        )
        assert not created and again.id == first.id
        assert again.origin["method_demand"] == command.method_demand.model_dump(
            mode="json"
        )
        assert again.reference_answer == command.reference()
        assert again.idea == {"kind": "question", "question": command.question}
        assert again.consent_version == 0
    changed = request_doc()
    changed["method_demand"]["gaps"][0]["reason"] = "不同的缺口"
    async with db() as session:
        with pytest.raises(ConcurrencyConflictError):
            await service(session).submit_from_runtime(
                OWNER, RuntimeIncubationV2.model_validate(changed), runtime="knowbench"
            )


async def test_formal_handoff_keeps_the_buyer_out_of_the_authors_draft(db):
    from uuid import uuid4

    from app.application.errors.exceptions import BadRequestError
    from app.domain.knowbots.method_demand import FormalHandoff
    from app.infrastructure.repositories.knowbots import SqlKnowlabRepository
    from tests.test_formal_methods_db import freeze, prepared

    runtime, mid, initial = await prepared(db)
    original = await freeze(db, runtime, mid, initial)
    project, task = str(uuid4()), str(uuid4())

    def body(owner, task_id, include_method):
        return FormalHandoff.model_validate(
            {
                "contract": "knowlab/formal-handoff/v1",
                "question": "Which rows differ?",
                "project_id": project,
                "task_id": task_id,
                "owner_subject": owner,
                "key": "handoff:" + task_id,
                "gap": "Add row-level source citations.",
                "review_digest": "sha256:" + "a" * 64,
                "reference": {
                    "method_id": str(mid),
                    "version": 1,
                    "digest": original.digest,
                }
                if include_method
                else None,
            }
        )

    author = original.owner_subject
    async with db() as session:
        svc = service(session)
        repo = SqlKnowlabRepository(session)
        new, created = await svc.submit_from_runtime(
            author, body(author, task, False), runtime="knowbench"
        )
        assert (
            created and new.origin["handoff"] == "new" and new.origin["task_id"] == task
        )
        cmd = body(author, str(uuid4()), True)
        revised, created = await svc.submit_from_runtime(
            author, cmd, runtime="knowbench"
        )
        assert created and revised.origin["handoff"] == "author_revision"
        copy = await repo.get_workdraft(revised.id)
        assert copy.body["formal_method"]["definition"] == original.definition
        again, created = await svc.submit_from_runtime(author, cmd, runtime="knowbench")
        assert again.id == revised.id and not created
        assert (await repo.method_version(mid, 1)).digest == original.digest
        buyer = body("buyer", str(uuid4()), True)
        with pytest.raises(BadRequestError):
            await svc.submit_from_runtime("buyer", buyer, runtime="knowbench")
        from app.application.services.formal_methods import FormalMethodService
        from tests.test_formal_method_service import freeze_command

        validation = initial.model_copy(
            update={
                "request_id": revised.id,
                "run_id": uuid4(),
                "revision": copy.revision,
            }
        )
        methods = FormalMethodService(repo, runtime)
        await methods.validate(author, str(mid), validation)
        runtime.complete(validation.run_id)
        newer = await methods.freeze(author, str(mid), 2, freeze_command(validation))
        assert newer.source_workdraft_id == copy.id and newer.version == 2
        assert (await repo.method_version(mid, 1)).source_workdraft_id != copy.id
        # Seed a public version for consumer feedback.
        async with repo.transaction():
            await repo.insert_method_version(
                method_id=mid,
                version=3,
                definition=original.definition,
                digest=original.digest,
                command_digest=original.command_digest,
                source_workdraft_id=original.source_workdraft_id,
                provenance={**original.provenance, "visibility": "public"},
            )
        buyer = buyer.model_copy(
            update={"reference": buyer.reference.model_copy(update={"version": 3})}
        )
        sent, _ = await svc.submit_from_runtime("buyer", buyer, runtime="knowbench")
        assert (
            sent.origin["handoff"] == "user_demand"
            and await repo.get_workdraft(sent.id) is None
        )
        assert (await svc.submit_from_runtime("buyer", buyer, runtime="knowbench"))[
            0
        ].id == sent.id
        messages = await repo.list_amendments(initial.request_id)
        assert [(m.owner_subject, m.text) for m in messages] == [("buyer", buyer.gap)]


async def test_author_handoff_reopens_collaboration_in_its_editable_cell(db):
    from uuid import uuid4

    from app.domain.knowbots.formal_method import FormalMethodDefinition
    from app.domain.knowbots.method_demand import FormalHandoff
    from app.infrastructure.repositories.knowbots import SqlKnowlabRepository
    from tests.test_formal_method_plan import composite, frozen
    from tests.test_formal_methods_db import freeze, prepared

    runtime, mid, command = await prepared(db)
    original = await freeze(db, runtime, mid, command)
    definition = composite(frozen(), frozen())
    graph = definition["procedure"]
    graph["steps"][1].update(depends_on=["s0"])
    graph["steps"][1]["inputs"]["value"]["binding"] = {
        "source": "step",
        "step": "s0",
        "output": "result",
    }
    graph["collaboration"] = {
        "s0": {"isolation_group": "primary"},
        "s1": {
            "isolation_group": "review",
            "review_requirements": {"s0": "different_model_family"},
        },
    }
    frozen_graph = FormalMethodDefinition.model_validate(definition)
    owner = original.owner_subject
    task = str(uuid4())
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        async with repo.transaction():
            await repo.insert_method_version(
                method_id=mid,
                version=2,
                definition=definition,
                digest=frozen_graph.digest(),
                command_digest=original.command_digest,
                source_workdraft_id=original.source_workdraft_id,
                provenance={**original.provenance, "source": None},
            )
        handoff = FormalHandoff.model_validate(
            {
                "contract": "knowlab/formal-handoff/v1",
                "question": "Compare the evidence",
                "project_id": str(uuid4()),
                "task_id": task,
                "owner_subject": owner,
                "key": "handoff:" + task,
                "gap": "Revise the review criteria",
                "review_digest": "sha256:" + "a" * 64,
                "reference": {
                    "method_id": str(mid),
                    "version": 2,
                    "digest": frozen_graph.digest(),
                },
            }
        )
        result, created = await service(session).submit_from_runtime(
            owner, handoff, runtime="knowbench"
        )
        workdraft = await repo.get_workdraft(result.id)
        assert created and result.origin["handoff"] == "author_revision"
        assert workdraft.body["collaboration"] == graph["collaboration"]
        assert (
            "collaboration"
            not in workdraft.body["formal_method"]["definition"]["procedure"]
        )
        assert (await repo.method_version(mid, 2)).definition == definition
