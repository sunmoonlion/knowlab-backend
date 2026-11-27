"""A versioned overall template survives the ordinary-to-formal authoring path."""

import copy
from uuid import uuid4

import pytest

from app.application.errors.exceptions import BadRequestError, ConcurrencyConflictError
from app.application.services.knowbot_workdraft import KnowbotWorkdraftService
from app.domain.knowbots.formal_method_admission import (
    ValidateFormalMethod,
)
from tests.knowbot_fakes import OWNER
from tests.test_formal_method_execution import prepared
from tests.test_knowbot_workdraft_service import STEP

BINDING = dict(
    template="checked_delivery/v1",
    goal="核对明细",
    deliverable="交回总数",
    check="总数等于带符号明细之和",
    evidence="逐行底稿",
)


@pytest.mark.asyncio
async def test_cell_edits_cannot_override_the_studio_contract():
    repo, runtime, service, mid, old = await prepared()
    works = KnowbotWorkdraftService(repo)
    rid = str(old.request_id)
    _, original = await works.get(OWNER, rid)
    method = copy.deepcopy(original.body["formal_method"])
    await works.save(
        OWNER, rid, revision=2, edits=[("formal_method", None), ("steps", [STEP])]
    )
    saved = await works.save(
        OWNER, rid, revision=3, edits=[("method_template", BINDING)]
    )
    assert saved.sources["method_template"]["kind"] == "user_text"
    with pytest.raises(BadRequestError, match="formal method incubation"):
        await works.publish(OWNER, rid)
    with pytest.raises(ConcurrencyConflictError):
        await works.save(OWNER, rid, revision=3, edits=[("method_template", None)])
    with pytest.raises(BadRequestError) as mismatch:
        await works.save(OWNER, rid, revision=4, edits=[("formal_method", method)])
    assert mismatch.value.code == "workdraft_invalid"
    definition = method["definition"]
    decl = definition["capability_declaration"]
    decl["purpose"] = BINDING["goal"]
    decl["promises"]["template_delivery"] = BINDING["deliverable"]
    definition["how_to_validate"].append(
        dict(
            promise_id="template_delivery",
            outputs=["result"],
            check=BINDING["check"],
            evidence=BINDING["evidence"],
            on_failure="fail",
        )
    )
    with pytest.raises(BadRequestError) as brief:
        await works.save(OWNER, rid, revision=4, edits=[("formal_method", method)])
    assert brief.value.code == "method_template_brief"
    await works.save(
        OWNER, rid, revision=4, edits=[("formal_method", method)], confirm_brief=True
    )
    _, reopened = await works.get(OWNER, rid)
    assert reopened.body["steps"][0]["title"] == STEP["title"]
    assert reopened.body["method_template"] == BINDING and reopened.revision == 5
    bad = copy.deepcopy(method)
    bad["definition"]["how_to_validate"][-1]["on_failure"] = "pause"
    with pytest.raises(BadRequestError):
        await works.save(OWNER, rid, revision=5, edits=[("formal_method", bad)])
    with pytest.raises(BadRequestError):
        await works.save(OWNER, rid, revision=5, edits=[("method_template", None)])
    validation = ValidateFormalMethod(
        request_id=old.request_id, revision=5, run_id=uuid4()
    )
    with pytest.raises(BadRequestError) as retired:
        await service.validate(OWNER, str(mid), validation)
    assert retired.value.code == "formal_method_invalid"
    assert not runtime.submitted
