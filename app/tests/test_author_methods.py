"""Independent definitions keep slot names and version digests across experts."""

import copy
import json
import uuid
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from app.domain.knowbots.methods import (
    BindMethod,
    FreezeMethod,
    MethodDefinition,
    bind,
    freeze,
    render,
)
from app.domain.knowbots.workdraft import WorkdraftBody
from tests.dynamic_method_fixtures import body


def definition():
    return freeze(
        WorkdraftBody.model_validate(body()),
        FreezeMethod(
            request_id=uuid.uuid4(),
            revision=1,
            step_id="report",
            input_slots={"inspect": "records"},
        ),
    )


def test_provider_schema_is_generated_from_the_author_definition():
    path = (
        Path(__file__).resolve().parents[3]
        / "contracts/author-method/v1/definition.schema.json"
    )
    assert path.read_text() == render()
    jsonschema.Draft202012Validator(json.loads(render())).validate(
        definition().document()
    )


def binding(value, **overrides):
    return BindMethod.model_validate(
        {
            "request_id": str(uuid.uuid4()),
            "revision": 1,
            "alias": "report",
            "digest": value.digest(),
            "inputs": {"records": "inspect"},
            "required": True,
            "max_calls": 1,
            **overrides,
        }
    )


def test_definition_has_no_expert_alias_or_required_position():
    value = definition()
    assert value.step.id == "method" and value.step.uses == ("records",)
    assert value.step.on_fail.back_to == "records"
    assert value.step.checks[0].params["source_step"] == "records"
    assert value.step.selection and not value.step.selection.required
    assert value.input_schemas.keys() == {"records"}
    assert MethodDefinition.model_validate(value.document()).digest() == value.digest()


def test_two_experts_bind_same_version_to_different_aliases_without_mutating_it():
    value = definition()
    pinned = value.document()
    first = bind(value, WorkdraftBody.model_validate(body()), binding(value))
    other = body()
    other["steps"][1]["id"] = "evidence"
    second = bind(
        value,
        WorkdraftBody.model_validate(other),
        binding(value, alias="summary", inputs={"records": "evidence"}, required=False),
    )
    assert first.id == "report" and first.uses == ("inspect",)
    assert second.id == "summary" and second.uses == ("evidence",)
    assert second.checks[0].params["source_step"] == "evidence"
    assert second.on_fail.back_to == "evidence"
    assert value.document() == pinned
    assert first.rules == second.rules and first.advanced == second.advanced


@pytest.mark.parametrize(
    "changes",
    [
        {"digest": "sha256:" + "f" * 64},
        {"inputs": {}},
        {"inputs": {"records": "unknown"}},
        {"inputs": {"records": "report"}},
        {"max_calls": 2},
    ],
)
def test_reference_validation_rejects_wrong_version_slots_or_wider_bounds(changes):
    value = definition()
    with pytest.raises(ValueError):
        bind(value, WorkdraftBody.model_validate(body()), binding(value, **changes))


def test_upstream_shape_must_satisfy_frozen_input_contract():
    value = definition()
    raw = body()
    raw["steps"][1]["outputs"][0]["type"] = "number"
    with pytest.raises(ValueError, match="incompatible"):
        bind(value, WorkdraftBody.model_validate(raw), binding(value))


def test_method_changes_create_different_digest_but_not_a_new_binding_digest():
    value = definition()
    raw = copy.deepcopy(value.document())
    raw["step"]["does"] += "最后注明未核事项。"
    newer = MethodDefinition.model_validate(raw)
    assert newer.digest() != value.digest()
    with pytest.raises(ValueError, match="digest"):
        bind(newer, WorkdraftBody.model_validate(body()), binding(value))


@pytest.mark.parametrize("slots", [{}, {"inspect": "method"}, {"other": "records"}])
def test_freeze_requires_explicit_complete_independent_input_names(slots):
    with pytest.raises(ValueError):
        freeze(
            WorkdraftBody.model_validate(body()),
            FreezeMethod(
                request_id=uuid.uuid4(), revision=1, step_id="report", input_slots=slots
            ),
        )


def test_planner_cannot_submit_replacement_method_or_coerced_revision():
    raw = binding(definition()).model_dump(mode="json")
    raw["method_text"] = "replace the author"
    with pytest.raises(ValidationError):
        BindMethod.model_validate(raw)
    raw.pop("method_text")
    raw["revision"] = True
    with pytest.raises(ValidationError):
        BindMethod.model_validate(raw)
