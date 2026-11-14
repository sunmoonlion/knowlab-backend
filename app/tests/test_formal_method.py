"""Five authored elements; generated interfaces and all-candidate classification."""

from uuid import uuid4

import pytest

from app.domain.knowbots.formal_method import FormalMethodDefinition, MethodRef
from app.domain.knowbots.formal_method_compiler import FrozenMethod, compile_method


def slot():
    return {
        "description": "A dimensionless value",
        "schema": {"type": "number"},
        "required": True,
        "meaning": {"unit": "dimensionless"},
        "on_missing": "pause",
    }


def tool():
    return {
        "contract": "knowlab/formal-method/v1",
        "capability_declaration": {
            "title": "Echo",
            "purpose": "Return the supplied value",
            "applies_when": ["A finite number is supplied"],
            "excludes": ["Inferring missing values"],
            "promises": {"value": "Return the unchanged number"},
        },
        "access_requirements": [],
        "procedure": {
            "kind": "direct",
            "implementation": {
                "kind": "platform",
                "source": "generic-test-echo",
                "version": "1",
                "digest": "sha256:" + "a" * 64,
                "entry": "echo",
                "requirement_ids": [],
            },
            "inputs": {"value": slot()},
            "outputs": {"result": slot()},
            "instructions": "Return the supplied value unchanged",
            "stop_conditions": ["One result returned or invalid input rejected"],
            "max_model_turns": 0,
        },
        "how_to_validate": [
            {
                "promise_id": "value",
                "outputs": ["result"],
                "check": "Compare exact values",
                "evidence": "Input and result",
                "on_failure": "fail",
            }
        ],
        "what_requires_approval": [],
    }


def frozen(doc=None, *, mid=None, version=1):
    definition = FormalMethodDefinition.model_validate(doc or tool())
    ref = MethodRef(
        method_id=mid or uuid4(), version=version, digest=definition.digest()
    )
    return FrozenMethod(ref, definition)


def composite(*children):
    doc = tool()
    doc["procedure"] = {
        "kind": "orchestration",
        "instructions": "Execute the selected methods",
        "steps": [
            {
                "id": "s" + str(i),
                "candidates": [child.reference.model_dump(mode="json")],
                "select_when": "This candidate is suitable",
                "required_when": "Always",
                "always_required": True,
                "depends_on": [],
                "inputs": {
                    "value": {
                        "slot": slot(),
                        "binding": {"source": "caller", "name": "value"},
                    }
                },
                "outputs": {"result": slot()},
                "on_failure": "pause",
            }
            for i, child in enumerate(children)
        ],
        "exports": {
            "result": {"step": "s0", "output": "result", "always_present": True}
        },
        "max_depth": 16,
        "max_expansions": 1000,
        "max_steps": 100,
        "max_replans": 3,
    }
    return doc


def resolved(*items):
    return {(i.reference.method_id, i.reference.version): i for i in items}


def test_tool_generates_views_and_has_no_extra_authored_views():
    item = frozen()
    result = compile_method(item.reference.method_id, item.definition, {})
    assert result.method_kind == "tool" and result.dispatch == "direct"
    assert result.interfaces["inputs"]["value"]["required_by"] == ["implementation"]
    assert set(result.interfaces["outputs"]) == {"result"}
    raw = tool()
    raw["inputs"] = {}
    with pytest.raises(ValueError):
        FormalMethodDefinition.model_validate(raw)
    raw = tool()
    del raw["what_requires_approval"]
    with pytest.raises(ValueError):
        FormalMethodDefinition.model_validate(raw)


def test_classification_counts_expert_identities_across_every_allowed_candidate():
    base = frozen()
    expert = frozen(composite(base))
    another_version = frozen(composite(base), mid=expert.reference.method_id, version=2)
    other_expert = frozen(composite(base))
    sources = resolved(base, expert, another_version, other_expert)
    for children, expected in [
        ((base, base), "expert"),
        ((expert, another_version), "expert"),
        ((expert, other_expert), "expert_team"),
    ]:
        doc = FormalMethodDefinition.model_validate(composite(*children))
        assert compile_method(uuid4(), doc, sources).method_kind == expected
    # Even a one-step choice between two expert identities has team scope.
    doc = composite(expert)
    doc["procedure"]["steps"][0]["candidates"].append(
        other_expert.reference.model_dump(mode="json")
    )
    team = frozen(doc)
    assert (
        compile_method(team.reference.method_id, team.definition, sources).method_kind
        == "expert_team"
    )
    sources.update(resolved(team))
    top = frozen(composite(team))
    assert (
        compile_method(top.reference.method_id, top.definition, sources).method_kind
        == "expert_team"
    )


def test_output_mapping_hides_intermediates_and_does_not_copy_all_results():
    child = frozen()
    raw = composite(child, child)
    raw["procedure"]["steps"][1]["depends_on"] = ["s0"]
    raw["procedure"]["steps"][1]["inputs"]["value"]["binding"] = {
        "source": "step",
        "step": "s0",
        "output": "result",
    }
    raw["procedure"]["exports"]["result"]["step"] = "s1"
    definition = FormalMethodDefinition.model_validate(raw)
    result = compile_method(uuid4(), definition, resolved(child))
    assert set(result.interfaces["inputs"]) == {"value"}
    assert result.interfaces["inputs"]["value"]["used_by"] == ["s0"]
    assert set(result.interfaces["outputs"]) == {"result"}


@pytest.mark.parametrize(
    "change", ["cycle", "missing_output", "conversion", "unconditional", "code"]
)
def test_invalid_orchestration_is_rejected(change):
    raw = composite(frozen(), frozen())
    steps = raw["procedure"]["steps"]
    if change == "cycle":
        steps[0]["depends_on"] = ["s1"]
        steps[1]["depends_on"] = ["s0"]
    elif change == "missing_output":
        raw["procedure"]["exports"]["result"]["output"] = "private_notes"
    elif change == "conversion":
        steps[1]["depends_on"] = ["s0"]
        steps[1]["inputs"]["value"]["binding"] = {
            "source": "step",
            "step": "s0",
            "output": "result",
        }
        steps[1]["inputs"]["value"]["slot"]["meaning"] = {"unit": "currency"}
    elif change == "unconditional":
        steps[0]["always_required"] = False
    else:
        steps[0]["inputs"]["value"]["binding"] = {"source": "script", "code": "x * 2"}
    with pytest.raises(ValueError):
        FormalMethodDefinition.model_validate(raw)


def test_frozen_sources_and_input_interfaces_are_checked_not_just_names():
    child = frozen()
    raw = composite(child)
    value = FormalMethodDefinition.model_validate(raw)
    with pytest.raises(ValueError, match="Missing or changed"):
        compile_method(uuid4(), value, {})
    altered = tool()
    altered["capability_declaration"]["purpose"] = "A different responsibility"
    with pytest.raises(ValueError, match="Missing or changed"):
        compile_method(
            uuid4(),
            value,
            {
                (child.reference.method_id, 1): FrozenMethod(
                    child.reference, FormalMethodDefinition.model_validate(altered)
                )
            },
        )
    raw["procedure"]["steps"][0]["inputs"] = {}
    with pytest.raises(ValueError, match="input interface"):
        compile_method(
            uuid4(), FormalMethodDefinition.model_validate(raw), resolved(child)
        )


def test_recursion_and_expansion_limits_are_separate_from_replanning():
    child = frozen()
    expert = frozen(composite(child))
    top = FormalMethodDefinition.model_validate(composite(expert, expert))
    for options in ({"max_depth": 2}, {"max_expansions": 2}):
        with pytest.raises(ValueError, match="expansion limit"):
            compile_method(uuid4(), top, resolved(child, expert), **options)
    with pytest.raises(ValueError, match="cycle"):
        compile_method(expert.reference.method_id, top, resolved(child, expert))


def test_optional_branch_input_is_not_turned_into_unconditional_requirement():
    child = frozen()
    raw = composite(child, child)
    raw["procedure"]["steps"][1]["always_required"] = False
    raw["procedure"]["steps"][1]["inputs"]["value"]["binding"]["name"] = "conditional"
    value = FormalMethodDefinition.model_validate(raw)
    assert value.interfaces()["inputs"]["conditional"]["required_by"] == []
    assert value.interfaces()["inputs"]["conditional"]["used_by"] == ["s1"]


@pytest.mark.parametrize(
    "schema", [{"type": "not-a-type"}, {"$ref": "https://example.test/schema"}]
)
def test_schemas_are_valid_and_never_fetch_remote_content(schema):
    raw = tool()
    raw["procedure"]["inputs"]["value"]["schema"] = schema
    with pytest.raises(ValueError):
        FormalMethodDefinition.model_validate(raw)
