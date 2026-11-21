"""Closed deterministic planning; these tests do not certify dependency admission."""

import copy
import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.domain.knowbots.formal_method import (
    FormalMethodDefinition,
    Orchestration,
    ResourceValue,
)
from app.domain.knowbots.formal_method_execution import (
    FormalExecutionInput,
    FormalMethodExecutionRequest,
)
from app.domain.knowbots.formal_method_plan import (
    bind_step_inputs,
    export_outputs,
    unique_plan,
    validate_plan_inputs,
    validate_step_outputs,
)
from scripts.export_formal_method import generate
from tests.test_formal_method import composite, frozen
from tests.test_formal_method_execution import execution


def definition():
    raw = composite(frozen(), frozen(), frozen())
    p = raw["procedure"]
    p.update(max_replans=0, max_steps=3, max_depth=2, max_expansions=4)
    for step in p["steps"]:
        step["on_failure"] = "fail"
    # Serialized order deliberately differs from graph order. Literal and caller
    # values feed two roots, and the final step reads an accepted predecessor.
    p["steps"][1]["inputs"]["value"]["binding"] = {"source": "literal", "value": 7}
    p["steps"][2]["depends_on"] = ["s0", "s1"]
    p["steps"][2]["inputs"]["value"]["binding"] = {
        "source": "step",
        "step": "s0",
        "output": "result",
    }
    p["exports"]["result"]["step"] = "s2"
    p["steps"].reverse()
    return FormalMethodDefinition.model_validate(raw)


def changed(**changes):
    raw = definition().document()
    raw["procedure"].update(changes)
    return FormalMethodDefinition.model_validate(raw)


def request_document(value=None):
    value = value or definition()
    return {
        "contract": "knowlab/formal-method-execution/v1",
        "run_id": str(uuid4()),
        "request_id": str(uuid4()),
        "workdraft_id": str(uuid4()),
        "method_id": str(uuid4()),
        "owner_subject": "author",
        "revision": 1,
        "draft": {
            "definition": value.document(),
            "source": None,
            "allowed_callers": ["host"],
        },
        "dependencies": [
            s.candidates[0].model_dump(mode="json") for s in unique_plan(value)
        ],
        "execution": FormalExecutionInput.model_validate(execution())
        .freeze()
        .model_dump(mode="json"),
    }


def test_topological_plan_is_stable_and_text_is_not_an_executable_condition():
    value = definition()
    expected = unique_plan(value)
    assert [step.id for step in expected] == ["s0", "s1", "s2"]
    raw = value.document()
    raw["procedure"]["steps"].reverse()
    reordered = FormalMethodDefinition.model_validate(raw)
    assert unique_plan(reordered) == expected
    assert reordered.digest() != value.digest()
    for step in raw["procedure"]["steps"]:
        step["select_when"] = "Never evaluate this descriptive sentence"
        step["required_when"] = "An explanation, not executable code"
    assert [s.id for s in unique_plan(FormalMethodDefinition.model_validate(raw))] == [
        "s0",
        "s1",
        "s2",
    ]


@pytest.mark.parametrize(
    "limits",
    [{"max_depth": 1}, {"max_steps": 2}, {"max_expansions": 3}, {"max_replans": 1}],
)
def test_declared_limits_are_enforced(limits):
    with pytest.raises(ValueError):
        unique_plan(changed(**limits))


def test_repeated_method_calls_count_as_separate_expansions():
    value = frozen()
    raw = composite(value, value)
    for step in raw["procedure"]["steps"]:
        step["on_failure"] = "fail"
    raw["procedure"].update(max_replans=0, max_expansions=2)
    with pytest.raises(ValueError, match="limits"):
        unique_plan(FormalMethodDefinition.model_validate(raw))
    raw["procedure"]["max_expansions"] = 3
    assert len(unique_plan(FormalMethodDefinition.model_validate(raw))) == 2


@pytest.mark.parametrize(
    "change", ["optional", "candidate", "conflicting_digest", "resource", "pause"]
)
def test_non_unique_or_resource_plans_are_rejected(change):
    value = definition()
    p = value.procedure
    assert isinstance(p, Orchestration)
    steps = list(p.steps)
    step = steps[-1]
    if change == "pause":
        step = step.model_copy(update={"on_failure": "pause"})
    elif change == "optional":
        step = step.model_copy(update={"always_required": False})
    elif change == "candidate":
        step = step.model_copy(
            update={"candidates": (*step.candidates, frozen().reference)}
        )
    elif change == "conflicting_digest":
        step = step.model_copy(
            update={
                "candidates": (
                    steps[0]
                    .candidates[0]
                    .model_copy(update={"digest": "sha256:" + "f" * 64}),
                )
            }
        )
    else:
        item = step.inputs["value"].model_copy(
            update={
                "binding": ResourceValue(
                    source="resource", requirement_id="read", role="source"
                )
            }
        )
        step = step.model_copy(update={"inputs": {"value": item}})
    steps[-1] = step
    with pytest.raises(ValueError):
        unique_plan(
            value.model_copy(
                update={"procedure": p.model_copy(update={"steps": tuple(steps)})}
            )
        )


def test_closed_bindings_and_exports_require_every_accepted_step():
    value = definition()
    caller = validate_plan_inputs(value, {"value": 3})
    first, second, third = unique_plan(value)
    assert bind_step_inputs(first, caller, {}) == {"value": 3}
    assert bind_step_inputs(second, caller, {}) == {"value": 7}
    accepted = {
        "s0": validate_step_outputs(first, {"result": 3, "private_child_output": 99})
    }
    with pytest.raises(ValueError, match="predecessor"):
        bind_step_inputs(third, caller, accepted)
    accepted["s1"] = validate_step_outputs(second, {"result": 7})
    assert bind_step_inputs(third, caller, accepted) == {"value": 3}
    with pytest.raises(ValueError, match="every required step"):
        export_outputs(value, accepted)
    accepted["s2"] = validate_step_outputs(third, {"result": 3})
    assert export_outputs(value, accepted) == {"result": 3}
    for malformed in (
        {**accepted, "invented": {}},
        {**accepted, "s1": {"result": 7, "hidden": 1}},
    ):
        with pytest.raises(ValueError):
            export_outputs(value, malformed)


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"value": True},
        {"value": "3"},
        {"value": 3, "extra": 4},
        {"value": float("nan")},
        {"value": float("inf")},
        {"value": (3,)},
        {1: 3},
        [],
    ],
)
def test_plan_inputs_reject_missing_extra_or_non_json_values(values):
    with pytest.raises(ValueError):
        validate_plan_inputs(definition(), values)


@pytest.mark.parametrize(
    "values", [{}, {"result": True}, {"result": "wrong"}, {"result": float("nan")}]
)
def test_step_outputs_require_the_declared_typed_value(values):
    with pytest.raises(ValueError):
        validate_step_outputs(unique_plan(definition())[0], values)


def test_bindings_validate_values_and_do_not_mutate_sources():
    first, _, third = unique_plan(definition())
    for values in ({}, {"value": False}):
        with pytest.raises(ValueError):
            bind_step_inputs(first, values, {})
    with pytest.raises(ValueError):
        bind_step_inputs(
            third, {"value": 3}, {"s0": {"result": "bad"}, "s1": {"result": 7}}
        )
    raw = definition().document()
    for step in raw["procedure"]["steps"]:
        step["inputs"]["value"]["slot"]["schema"] = {"type": "object"}
        step["outputs"]["result"]["schema"] = {"type": "object"}
    value = FormalMethodDefinition.model_validate(raw)
    caller = {"value": {"nested": [1]}}
    copied = validate_plan_inputs(value, caller)
    copied["value"]["nested"].append(2)
    assert caller == {"value": {"nested": [1]}}


def test_unfinished_schema_evaluation_cannot_validate_a_plan_output(monkeypatch):
    from jsonschema import Draft202012Validator

    step = unique_plan(definition())[0]

    def too_deep(self, value):
        raise RecursionError

    monkeypatch.setattr(Draft202012Validator, "is_valid", too_deep)
    with pytest.raises(ValueError, match="Plan value does not match"):
        validate_step_outputs(step, {"result": 1})


def test_json_size_and_depth_are_bounded():
    first = unique_plan(definition())[0]
    for value in ("x" * 64001,):
        with pytest.raises(ValueError, match="64000"):
            validate_step_outputs(first, {"result": value})
    nested = 1
    for _ in range(33):
        nested = [nested]
    with pytest.raises(ValueError, match="depth"):
        validate_step_outputs(first, {"result": nested})


def test_execution_accepts_host_unique_plan_and_exact_dependencies_only():
    raw = request_document()
    request = FormalMethodExecutionRequest.model_validate(raw)
    assert request.admission_request().dependencies == request.dependencies
    assert (
        FormalMethodExecutionRequest.model_validate_json(
            request.model_dump_json(by_alias=True)
        )
        == request
    )
    for dependencies in (
        [],
        raw["dependencies"][:-1],
        raw["dependencies"] + raw["dependencies"][:1],
    ):
        with pytest.raises(ValidationError, match="dependencies"):
            FormalMethodExecutionRequest.model_validate(
                {**raw, "dependencies": dependencies}
            )
    model = copy.deepcopy(raw)
    model["draft"]["allowed_callers"] = ["model"]
    with pytest.raises(ValidationError, match="host caller"):
        FormalMethodExecutionRequest.model_validate(model)


def test_export_includes_plan_provider_source_and_rewrites_imports(tmp_path):
    outputs = generate(tmp_path)
    generated = outputs[
        tmp_path / "app/domain/workbench/formal_method_plan_generated.py"
    ]
    assert "from app.domain.workbench.formal_method_generated import" in generated
    assert "app.domain.knowbots" not in generated
    assert "def unique_plan(" in generated
    lock = json.loads(outputs[tmp_path / "contracts/formal-method-lock.json"])
    assert "formal_method_plan.py" in lock["source_sha256"]


def comparison_graph():
    raw = composite(frozen(), frozen())
    graph = raw["procedure"]
    graph.update(
        max_replans=0,
        max_steps=4,
        max_expansions=5,
        object_batch={"max_objects": 3, "on_missing": "stop"},
    )
    worker, collector = graph["steps"]
    item = worker["inputs"]["value"]
    item["slot"]["schema"] = {"type": "string"}
    item["binding"] = {"source": "object_item", "name": "objects"}
    rows = collector["inputs"]["value"]
    rows["slot"]["schema"] = {
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["object_id", "value"],
            "properties": {
                "object_id": item["slot"]["schema"],
                "value": worker["outputs"]["result"]["schema"],
            },
        },
    }
    rows["binding"] = {
        "source": "object_rows",
        "step": worker["id"],
        "output": "result",
    }
    collector["depends_on"] = [worker["id"]]
    graph["exports"]["result"]["step"] = collector["id"]
    return raw


def test_object_batch_policy_changes_identity_and_cannot_enter_fixed_executor():
    raw = comparison_graph()
    batch = FormalMethodDefinition.model_validate(raw)
    assert set(batch.interfaces()["inputs"]) == {"objects"}
    with pytest.raises(ValueError, match="bounded object fanout"):
        unique_plan(batch)
    raw["procedure"]["object_batch"]["on_missing"] = "request_partial"
    assert FormalMethodDefinition.model_validate(raw).digest() != batch.digest()


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("object_batch",), None),
        (("object_batch", "max_objects"), 0),
        (("object_batch", "max_objects"), 100),
        (("max_expansions",), 4),
        (("exports", "result", "step"), "s0"),
        (("steps", 0, "outputs", "result", "required"), False),
        (
            ("steps", 0, "outputs", "result", "schema"),
            {"$defs": {"n": {"type": "number"}}, "$ref": "#/$defs/n"},
        ),
        (("steps", 0, "always_required"), False),
        (("steps", 1, "depends_on"), []),
        (("steps", 1, "inputs", "value", "binding", "output"), "missing"),
        (("steps", 1, "inputs", "value", "slot", "meaning"), {"unit": "incomparable"}),
        (
            (
                "steps",
                1,
                "inputs",
                "value",
                "slot",
                "schema",
                "items",
                "properties",
                "value",
            ),
            {"type": "null"},
        ),
    ],
)
def test_object_batch_requires_closed_typed_collection(path, replacement):
    raw = comparison_graph()
    target = raw["procedure"]
    for field in path[:-1]:
        target = target[field]
    target[path[-1]] = replacement
    with pytest.raises(ValidationError):
        FormalMethodDefinition.model_validate(raw)


def test_object_list_cannot_alias_an_ordinary_input():
    raw = comparison_graph()
    collector = raw["procedure"]["steps"][1]
    collector["inputs"]["extra"] = {
        **collector["inputs"]["value"],
        "binding": {"source": "caller", "name": "objects"},
    }
    with pytest.raises(ValidationError, match="distinct list input"):
        FormalMethodDefinition.model_validate(raw)
