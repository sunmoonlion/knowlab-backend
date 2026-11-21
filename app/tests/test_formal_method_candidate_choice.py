"""Incubation-only candidate rules must be closed before an author adopts them."""

from copy import deepcopy
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.domain.knowbots.formal_method import FormalMethodDefinition, Orchestration
from app.domain.knowbots.formal_method_compiler import compile_method
from app.domain.knowbots.formal_method_plan import unique_plan
from tests.test_formal_method import composite, frozen, resolved, tool


def choice_definition():
    leaf_doc = tool()
    input_slot = leaf_doc["procedure"]["inputs"]["value"]
    input_slot["schema"] = {"type": "string", "enum": ["direct", "checked", "other"]}
    leaf = frozen(leaf_doc)
    child_doc = composite(leaf)
    child_doc["procedure"]["steps"][0]["inputs"]["value"]["slot"] = input_slot
    child_doc["procedure"].update(
        max_replans=0, max_depth=2, max_steps=1, max_expansions=2
    )
    first, second = frozen(child_doc), frozen(child_doc)
    raw = composite(first)
    graph = raw["procedure"]
    graph.update(max_depth=3, max_replans=0, max_steps=2, max_expansions=3)
    step = graph["steps"][0]
    step["inputs"]["value"]["slot"] = deepcopy(input_slot)
    step["inputs"]["value"]["binding"]["name"] = "route"
    refs = [child.reference.model_dump(mode="json") for child in (first, second)]
    step.update(
        candidates=refs,
        candidate_choice={
            "input": "value",
            "cases": [
                {"equals": value, "candidate": deepcopy(ref)}
                for value, ref in zip(("direct", "checked"), refs, strict=True)
            ],
        },
    )
    return raw, resolved(leaf, first, second)


def test_candidate_rules_match_exact_values_and_budget_only_one_subtree():
    raw, dependencies = choice_definition()
    definition = FormalMethodDefinition.model_validate(raw)
    graph = definition.procedure
    assert isinstance(graph, Orchestration)
    step = graph.steps[0]
    choice = step.candidate_choice
    assert choice is not None
    assert choice.match("direct") == step.candidates[0]
    assert choice.match("checked") == step.candidates[1]
    assert all(choice.match(v) is None for v in ("other", None, "DIRECT", ["direct"]))
    assert set(definition.interfaces()["inputs"]) == {"route"}
    compiled = compile_method(uuid4(), definition, dependencies)
    assert compiled.method_kind == "expert"
    assert (
        compiled.required_depth,
        compiled.required_expansions,
        compiled.required_steps,
    ) == (3, 3, 2)
    assert compiled.dependencies == step.candidates
    with pytest.raises(ValueError, match="conditional candidate selection"):
        unique_plan(definition)
    graph_doc = raw["procedure"]
    graph_doc["steps"][0]["candidate_choice"]["cases"].reverse()
    # Case order never decides which method wins.
    reordered = FormalMethodDefinition.model_validate(raw).procedure
    assert isinstance(reordered, Orchestration)
    assert reordered.steps[0].candidate_choice.match("direct") == step.candidates[0]


@pytest.mark.parametrize(
    ("key", "value"), [("max_depth", 2), ("max_steps", 1), ("max_expansions", 2)]
)
def test_candidate_subtree_limits_apply_to_each_alternative(key, value):
    raw, deps = choice_definition()
    raw["procedure"][key] = value
    with pytest.raises(ValueError, match="subtree limits"):
        compile_method(uuid4(), FormalMethodDefinition.model_validate(raw), deps)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("always_required",), False),
        (("inputs", "value", "binding"), {"source": "literal", "value": "checked"}),
        (("candidate_choice", "input"), "missing"),
        (("candidate_choice", "cases", 0, "equals"), "checked"),
        (("candidate_choice", "cases", 0, "equals"), "bad"),
        (("candidate_choice", "cases", 0, "candidate", "digest"), "sha256:" + "0" * 64),
        (("candidate_choice", "cases", 1, "equals"), " "),
    ],
)
def test_choice_cannot_skip_required_step_or_substitute_candidate(path, value):
    raw, _ = choice_definition()
    target = raw["procedure"]["steps"][0]
    for k in path[:-1]:
        target = target[k]
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        FormalMethodDefinition.model_validate(raw)
