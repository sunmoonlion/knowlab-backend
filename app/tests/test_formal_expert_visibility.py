"""Service visibility boundary using synthetic receipts, not execution evidence.

The in-memory Runtime.complete helper deliberately supplies a complete receipt
to isolate this policy from the real execution and source-qualification paths.
"""

from uuid import uuid4

import pytest

from app.application.errors.exceptions import BadRequestError
from app.application.services.knowbot_service import KnowbotCreationService
from app.domain.knowbots.drafts import KnowbotRequestIn
from app.domain.knowbots.formal_method_admission import ValidateFormalMethod
from app.domain.knowbots.formal_method_plan import unique_plan
from tests.knowbot_fakes import OWNER, request_body
from tests.test_formal_method import composite, frozen
from tests.test_formal_method_service import draft, freeze_command, setup


@pytest.mark.asyncio
async def test_expert_cannot_publish_a_private_dependency():
    repo, runtime, service, child_id, child_command = await setup()
    child_draft = draft().model_dump(mode="json", by_alias=True)
    child_draft["definition"]["procedure"]["implementation"]["kind"] = "mcp"
    child_draft["source"].update(kind="mcp", asset_id=str(uuid4()), asset_revision=1)
    await service.workdrafts.save(
        OWNER,
        str(child_command.request_id),
        revision=1,
        edits=[("formal_method", child_draft)],
    )
    child_command = child_command.model_copy(update={"revision": 2})
    await service.validate(OWNER, str(child_id), child_command)
    runtime.complete(child_command.run_id)
    child = await service.freeze(OWNER, str(child_id), 1, freeze_command(child_command))

    definition = composite(frozen(child.definition, mid=child_id))
    definition["procedure"]["max_replans"] = 0
    definition["procedure"]["steps"][0]["on_failure"] = "fail"
    request = await KnowbotCreationService(repo, runtime).submit(
        OWNER, KnowbotRequestIn.model_validate(request_body())
    )
    await service.workdrafts.save(
        OWNER,
        str(request.id),
        revision=0,
        edits=[
            (
                "formal_method",
                {"definition": definition, "source": None, "allowed_callers": ["host"]},
            )
        ],
    )
    parent_id = uuid4()
    command = ValidateFormalMethod(request_id=request.id, revision=1, run_id=uuid4())
    await service.validate(OWNER, str(parent_id), command)
    payload = runtime.validation_requests[command.run_id]
    assert len(unique_plan(payload.draft.definition)) == 1
    receipt = runtime.complete(command.run_id, business=True)
    assert "business" in receipt["checks"]
    assert receipt["uncovered"] == []

    registrations_before = len(runtime.registration_calls)
    with pytest.raises(BadRequestError) as rejected:
        await service.freeze(
            OWNER, str(parent_id), 1, freeze_command(command, "public")
        )
    assert rejected.value.code == "formal_delivery_denied"
    assert (parent_id, 1) not in repo.method_versions
    assert parent_id not in repo.method_owners
    assert len(runtime.registration_calls) == registrations_before

    saved = await service.freeze(
        OWNER, str(parent_id), 1, freeze_command(command, "private")
    )
    assert saved.provenance["method_kind"] == "expert"
    assert saved.provenance["dispatch"] == "orchestration"
    assert saved.provenance["visibility"] == "private"
    assert saved.provenance["validation"]["run_id"] == str(command.run_id)
    assert runtime.registrations[(parent_id, 1)].visibility == "private"
    assert len(runtime.registration_calls) == registrations_before + 1


@pytest.mark.asyncio
async def test_reviewed_child_and_expert_freeze_as_public_versions():
    from app.domain.knowbots.formal_method_admission import FreezeFormalMethod
    from tests.test_formal_method_execution import complete_trial, prepared

    repo, runtime, service, child_id, command = await prepared()

    async def freeze_reviewed(mid, trial):
        await service.execute(OWNER, str(mid), trial)
        complete_trial(runtime, trial, business=True)
        return await service.freeze(
            OWNER,
            str(mid),
            1,
            FreezeFormalMethod(
                request_id=trial.request_id,
                revision=trial.revision,
                run_id=trial.run_id,
                visibility="public",
            ),
        )

    child = await freeze_reviewed(child_id, command)
    definition = composite(frozen(child.definition, mid=child_id))
    definition["procedure"]["max_replans"] = 0
    definition["procedure"]["steps"][0]["on_failure"] = "fail"
    request = await KnowbotCreationService(repo, runtime).submit(
        OWNER, KnowbotRequestIn.model_validate(request_body())
    )
    await service.workdrafts.save(
        OWNER,
        str(request.id),
        revision=0,
        edits=[
            (
                "formal_method",
                {"definition": definition, "source": None, "allowed_callers": ["host"]},
            )
        ],
    )
    trial_body = command.model_dump(mode="json")
    trial_body.update(request_id=str(request.id), revision=1, run_id=str(uuid4()))
    trial_body["execution"]["suite"]["suite_id"] = str(uuid4())
    trial = type(command).model_validate(trial_body)
    parent_id = uuid4()
    root = await freeze_reviewed(parent_id, trial)
    assert root.provenance["visibility"] == "public"
    assert runtime.registrations[(parent_id, 1)].visibility == "public"
    assert root.provenance["dependencies"] == [
        runtime.registrations[(child_id, 1)].reference.model_dump(mode="json")
    ]
