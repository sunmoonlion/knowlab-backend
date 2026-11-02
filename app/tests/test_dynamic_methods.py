"""Author provenance, compilation and cross-app contracts for WP-10 unit 1."""

import copy
import hashlib
import json
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from app.domain.knowbots.compile import decompile, pack_problems
from app.domain.knowbots.drafts import draft_manifest_problems
from app.domain.knowbots.workdraft import WorkdraftBody, apply, cells, source_problems
from tests.dynamic_method_fixtures import body, build

PARENT = Path(__file__).resolve().parents[3]


def test_compile_catalog_not_workflow_and_preserve_author_boundaries():
    doc, errors = build()
    assert not errors
    assert doc["schema"] == "knowbot-manifest/v3"
    assert "workflow" not in doc["pack"]
    assert pack_problems(doc["pack"]) == []
    assert draft_manifest_problems(doc, runtime="knowbench", version="1") == []
    units = doc["pack"]["method_units"]
    assert [u["contract"]["step_id"] for u in units] == ["report", "inspect", "review"]
    assert units[0]["contract"]["tools"] == []
    assert units[0]["contract"]["input_refs"] == ["inspect"]
    assert any(
        r.get("source") == "inspect.rows" for r in units[0]["contract"]["acceptance"]
    )
    assert units[1]["selection"]["max_calls"] == 3


def test_dynamic_compile_import_compile_preserves_pack_and_selection():
    doc, _ = build()
    restored, notes = decompile(doc)
    assert not notes
    assert restored.planning == WorkdraftBody.model_validate(body()).planning
    rebuilt, errors = build(restored.model_dump(mode="json"))
    assert not errors
    assert rebuilt["pack"] == doc["pack"]


def test_new_author_boundaries_require_author_provenance():
    parsed = WorkdraftBody.model_validate(body())
    paths = set(cells(parsed))
    expected = {
        "planning",
        "steps/inspect/selection",
        "steps/inspect/advanced",
        "steps/inspect/uses",
        "steps/inspect/on_fail",
    }
    assert expected <= paths
    missing = source_problems(
        parsed, {}, workdraft_id=None, owner_subject="author", entries={}
    )
    assert expected <= {p.path for p in missing}
    edited = apply(
        body(),
        "steps/inspect/selection",
        {"applies_when": "需要时", "required": False, "max_calls": 1},
    )
    assert (
        cells(WorkdraftBody.model_validate(edited))["steps/inspect/selection"]
        != cells(parsed)["steps/inspect/selection"]
    )
    assert apply(body(), "planning", None)["planning"] is None


@pytest.mark.parametrize(
    "change",
    [
        {"max_steps": True},
        {"max_replans": -1},
        {"max_steps": 101},
        {"reserve": "0"},
        {"final_units": []},
        {"method_text": " "},
    ],
)
def test_invalid_planning_is_not_coerced(change):
    doc = body()
    doc["planning"].update(change)
    with pytest.raises(ValidationError):
        WorkdraftBody.model_validate(doc)


@pytest.mark.parametrize(
    "case",
    [
        "unknown",
        "cycle",
        "too_short",
        "unknown_final",
        "selection_missing",
        "undeclared_source",
    ],
)
def test_reject_broken_catalog_before_draft_creation(case):
    doc = body()
    if case == "unknown":
        doc["steps"][0]["uses"] = ["invented"]
    elif case == "cycle":
        doc["steps"][1]["uses"] = ["report"]
    elif case == "too_short":
        doc["planning"]["max_steps"] = 1
    elif case == "unknown_final":
        doc["planning"]["final_units"] = ["invented"]
    elif case == "selection_missing":
        del doc["steps"][0]["selection"]
    else:
        doc["steps"][0]["uses"] = []
    compiled, errors = build(doc)
    assert errors or pack_problems(compiled["pack"])


def test_fixed_mode_is_not_implicitly_changed_by_selection():
    doc = body()
    del doc["planning"]
    _, errors = build(doc)
    assert any(p.path.endswith("selection") for p in errors)
    compiled, _ = build()
    compiled["schema"] = "knowbot-manifest/v2"
    with pytest.raises(ValueError, match="require a v3"):
        decompile(compiled)


@pytest.mark.parametrize(
    "lock_name,part",
    [
        ("knowmart-knowbot-manifest-v3-lock.json", None),
        ("knowbench-dynamic-methods-lock.json", "pack"),
    ],
)
def test_compiler_satisfies_locked_provider_contracts(lock_name, part):
    path = PARENT / "contracts" / lock_name
    if not path.exists():
        pytest.skip("parent contract checkout required")
    lock = json.loads(path.read_text())
    schema = PARENT.parent / lock["provider"] / lock["schema"]
    if not schema.exists():
        pytest.skip("provider contract checkout required")
    assert hashlib.sha256(schema.read_bytes()).hexdigest() == lock["sha256"]
    doc, errors = build()
    assert not errors
    jsonschema.validate(doc[part] if part else doc, json.loads(schema.read_text()))
    if part:
        bad = copy.deepcopy(doc[part])
        bad["workflow"] = []
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, json.loads(schema.read_text()))


def test_runtime_fixture_is_the_actual_compiler_output():
    fixture = (
        PARENT.parent
        / "knowbench-app/knowbench-backend/app/tests/fixtures"
        / "dynamic-methods-manifest.json"
    )
    if not fixture.exists():
        pytest.skip("knowbench checkout required")
    doc, errors = build()
    assert not errors
    assert doc == json.loads(fixture.read_text())


def test_fixed_compilation_has_the_same_bytes_as_pre_dynamic_baseline():
    # Captured from 70555a64, before WP-10 changed the compiler.
    from tests.test_knowbot_workdraft_compile import build as fixed_build

    doc, errors = fixed_build()
    assert not errors
    encoded = json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert (
        hashlib.sha256(encoded.encode()).hexdigest()
        == "827a2e80cc1a4f9b8ad26dc95324433e9af3182ff20caecd123027ab29b4561d"
    )


@pytest.mark.parametrize(
    "change",
    [
        {"acceptance": None},
        {"acceptance": [{"kind": []}]},
        {"tools": [{}]},
        {"input_refs": [{}]},
        {"acceptance": []},
    ],
)
def test_malformed_dynamic_import_is_a_validation_error(change):
    doc, _ = build()
    doc["pack"]["method_units"][0]["contract"].update(change)
    assert pack_problems(doc["pack"])
    with pytest.raises(ValueError):
        decompile(doc)


def test_dynamic_import_does_not_silently_drop_frozen_tool_policy():
    doc, _ = build()
    doc["pack"]["method_units"][0]["contract"]["tool_policy"] = {"network": "deny"}
    with pytest.raises(ValueError, match="lose author constraints"):
        decompile(doc)


def test_planning_proposal_cannot_inject_method_engineering():
    from app.domain.knowbots.incubation import Suggestion

    suggestion = {
        "id": "p",
        "path": "planning",
        "op": "set",
        "value": body()["planning"],
        "reason": "待作者确认",
    }
    Suggestion.model_validate(suggestion)
    with pytest.raises(ValidationError):
        Suggestion.model_validate(
            {
                **suggestion,
                "value": {**suggestion["value"], "advanced": {"tools": ["run_sql"]}},
            }
        )
    with pytest.raises(ValidationError):
        Suggestion.model_validate(
            {
                **suggestion,
                "path": "steps/report",
                "value": {"method_text": "replace runtime text"},
            }
        )
