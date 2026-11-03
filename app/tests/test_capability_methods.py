"""Optional author inputs survive freeze, reuse, compilation and import."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from uuid import UUID

import jsonschema
import pytest

from app.domain.knowbots.compile import compile_manifest, decompile, pack_problems
from app.domain.knowbots.methods import (
    BindMethod,
    FreezeMethod,
    MethodDefinition,
    bind,
    freeze,
    parse_definition,
    render,
)
from app.domain.knowbots.workdraft import WorkdraftBody, apply, cells
from tests.dynamic_method_fixtures import body


def test_new_schemas_and_consumer_fixture_match_the_compiler():
    parent = Path(__file__).resolve().parents[3]
    assert (
        parent / "contracts/author-method/v2/definition.schema.json"
    ).read_text() == render(optional=True)
    lock = json.loads(
        (parent / "contracts/knowbench-dynamic-methods-v3-lock.json").read_text()
    )
    provider = parent.parent / lock["provider"]
    schema = provider / lock["schema"]
    assert hashlib.sha256(schema.read_bytes()).hexdigest() == lock["sha256"]
    manifest, errors = compile_body(capability_body())
    assert not errors
    jsonschema.validate(manifest["pack"], json.loads(schema.read_text()))
    fixture = (
        provider
        / "knowbench-backend/app/tests/fixtures/capability-methods-manifest.json"
    )
    assert json.loads(fixture.read_text()) == manifest


def capability_body():
    doc = body()
    doc["planning"]["capability_bindings"] = {
        "check": {
            "required_units": ["inspect"],
            "evidence_outputs": [{"method": "inspect", "field": "rows"}],
        },
        "double_check": {
            "required_units": ["review"],
            "evidence_outputs": [{"method": "review", "field": "notes"}],
        },
    }
    doc["public_capabilities"] = [
        {
            "id": name,
            "statement": name,
            "conditions": [],
            "exclusions": [],
            "delivers": ["核对结果"],
        }
        for name in ("check", "double_check")
    ]
    report = doc["steps"][0]
    report["uses"].append("review")
    report["input_required"] = {"inspect": True, "review": False}
    report["outputs"].append({"name": "review_summary", "type": "text"})
    report["checks"].append(
        {
            "template": "non_empty",
            "params": {"path": "review_summary"},
            "when_input_present": "review",
        }
    )
    return doc


def compile_body(doc, **kwargs):
    return compile_manifest(
        WorkdraftBody.model_validate(doc),
        {},
        pack_id="synthetic-capabilities",
        version="1",
        runtime="knowbench",
        fallback_name="synthetic",
        **kwargs,
    )


def test_capabilities_compile_import_and_keep_guards():
    manifest, errors = compile_body(capability_body())
    assert not errors
    pack = manifest["pack"]
    assert pack["runtime_contract"] == "knowbench/dynamic-methods/v3"
    assert not pack_problems(pack)
    restored, notes = decompile(manifest)
    assert not notes
    rebuilt, errors = compile_body(restored.model_dump(mode="json"))
    assert not errors and rebuilt == manifest
    report = pack["method_units"][0]["contract"]
    assert report["input_slots"]["review"] == {"method_id": "review", "required": False}
    assert [
        r["when_input_present"]
        for r in report["acceptance"]
        if "when_input_present" in r
    ] == ["review"]


def test_optional_definition_reuses_neutral_slots_and_exact_digest():
    original = WorkdraftBody.model_validate(capability_body())
    definition = freeze(
        original,
        FreezeMethod(
            request_id=UUID(int=1),
            revision=1,
            step_id="report",
            input_slots={"inspect": "records", "review": "verification"},
        ),
    )
    frozen = definition.document()
    assert frozen["schema"] == "knowlab/method-definition/v2"
    assert frozen["input_required"] == {"records": True, "verification": False}
    assert frozen["step"]["checks"][1]["when_input_present"] == "verification"
    assert parse_definition(frozen).digest() == definition.digest()
    with pytest.raises(ValueError):
        MethodDefinition.model_validate(frozen)
    binding = BindMethod(
        request_id=UUID(int=1),
        revision=1,
        alias="report",
        digest=definition.digest(),
        inputs={"records": "inspect", "verification": "review"},
        required=True,
        max_calls=1,
    )
    bound = bind(definition, original, binding)
    doc = original.model_dump(mode="json")
    doc["steps"][0] = bound.model_dump(mode="json")
    ref = {
        "method_id": str(UUID(int=10)),
        "version": 1,
        "digest": definition.digest(),
        "inputs": binding.inputs,
    }
    doc["method_refs"] = {"report": ref}
    sources = {"report": {"reference": ref, "definition": frozen}}
    manifest, errors = compile_body(doc, method_sources=sources)
    assert not errors and not pack_problems(manifest["pack"])
    contract = manifest["pack"]["method_units"][0]["contract"]
    assert contract["input_refs"] == ["records", "verification"]
    assert contract["input_slots"]["verification"] == {
        "method_id": "review",
        "required": False,
    }
    restored, notes = decompile(manifest)
    assert not notes and restored.method_refs
    assert restored.method_refs["report"].digest == definition.digest()
    assert definition.document() == frozen
    # A legacy expert cannot silently adopt the new input semantics.
    with pytest.raises(ValueError, match="capability planning"):
        bind(definition, WorkdraftBody.model_validate(body()), binding)


@pytest.mark.parametrize(
    "damage",
    [
        "guard",
        "source",
        "max_steps",
        "cap_output",
        "unknown_cap_method",
        "required_guard",
    ],
)
def test_published_capability_tampering_is_rejected(damage):
    manifest, errors = compile_body(capability_body())
    assert not errors
    pack = deepcopy(manifest["pack"])
    report = pack["method_units"][0]["contract"]
    if damage == "guard":
        next(r for r in report["acceptance"] if "when_input_present" in r)[
            "when_input_present"
        ] = "inspect"
    elif damage == "source":
        report["input_slots"]["review"]["method_id"] = "unknown"
    elif damage == "max_steps":
        pack["planning"]["max_steps"] = 2
    elif damage == "cap_output":
        pack["planning"]["capability_bindings"]["check"]["evidence_outputs"][0][
            "field"
        ] = "unknown"
    elif damage == "unknown_cap_method":
        pack["planning"]["capability_bindings"]["check"]["required_units"].append(
            "unknown"
        )
    else:
        report["acceptance"][0]["when_input_present"] = "review"
    assert pack_problems(pack)


def test_new_author_cells_participate_in_provenance_and_edits():
    raw = capability_body()
    draft = WorkdraftBody.model_validate(raw)
    found = cells(draft)
    assert "public_capabilities" in found
    assert "steps/report/input_required" in found
    assert '"when_input_present":"review"' in found["steps/report/checks/1"]
    changed = apply(raw, "public_capabilities", raw["public_capabilities"])
    changed = apply(
        changed, "steps/report/input_required", {"inspect": True, "review": True}
    )
    with pytest.raises(ValueError, match="optional input"):
        WorkdraftBody.model_validate(changed)
