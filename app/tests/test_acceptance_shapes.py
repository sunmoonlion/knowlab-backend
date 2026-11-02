"""Authoring rejects impossible coverage without rewriting frozen evidence."""

import copy
import json
from pathlib import Path

import pytest

from app.domain.knowbots.compile import compile_manifest, decompile, pack_problems
from tests.dynamic_method_fixtures import body, build
from tests.test_knowbot_workdraft_compile import build as fixed_build


def schemas(source, target, **params):
    doc = body()
    doc["steps"][1]["advanced"]["output_schema"] = {"rows": source}
    doc["steps"][0]["advanced"]["output_schema"] = {"rows": target}
    doc["steps"][0]["checks"][0]["params"].update(params)
    return doc


@pytest.mark.parametrize(
    "source,target,params,why",
    [
        ("number", [{"id": "string"}], {}, "source must declare a list"),
        (
            {"value": "number", "compute_id": "string"},
            [{"id": "string"}],
            {},
            "source must declare a list",
        ),
        ([], [{"id": "string"}], {}, "source must declare a list"),
        ([{"missing": "string"}], [{"id": "string"}], {}, "source_field"),
        ([{"id": "string"}], ["string"], {}, "objects containing id"),
        ([{"id": "string"}], [{"other": "string"}], {}, "objects containing id"),
        ([{"id": "number"}], [{"id": "string"}], {}, "source value type"),
        (
            [{"id": "string"}],
            [{"id": "string"}],
            {"source_path": "rows.id"},
            "source must declare a list",
        ),
    ],
)
def test_new_dynamic_compilation_refuses_incompatible_coverage(
    source, target, params, why
):
    _, problems = build(schemas(source, target, **params))
    assert any(p.path == "steps/report/checks/0" and why in p.message for p in problems)


@pytest.mark.parametrize(
    "source,target,params",
    [
        ([{"id": "string"}], [{"id": "string"}], {}),
        ([{"id": "number"}], {"id": "integer"}, {}),
        (["string"], [{"id": "string"}], {"source_field": None}),
    ],
)
def test_valid_scalar_and_object_list_sources(source, target, params):
    doc = schemas(source, target)
    if params.get("source_field", "") is None:
        doc["steps"][0]["checks"][0]["params"].pop("source_field")
    manifest, problems = build(doc)
    assert not problems
    assert not pack_problems(manifest["pack"])


def test_nested_source_and_every_also_target_use_effective_author_schema():
    doc = schemas(
        {"nested": [{"id": "string"}]},
        [{"id": "string"}],
        source_path="rows.nested",
        also=["other"],
    )
    doc["steps"][0]["outputs"].append({"name": "other", "type": "list"})
    doc["steps"][0]["advanced"]["output_schema"]["other"] = {"id": "string"}
    assert not build(doc)[1]
    doc["steps"][0]["advanced"]["output_schema"]["other"] = {"id": "number"}
    assert any("covers other.id" in p.message for p in build(doc)[1])


def test_fixed_workflow_gets_the_same_authoring_guard():
    doc = body()
    doc.pop("planning")
    for step in doc["steps"]:
        step.pop("selection")
    doc["steps"] = [doc["steps"][1], doc["steps"][0]]
    doc["steps"][0]["outputs"] = [{"name": "rows", "type": "number"}]
    assert any("source must declare a list" in p.message for p in fixed_build(doc)[1])


def test_historical_snapshot_replays_unchanged_but_new_authoring_is_rejected():
    # Captured from a published-era v1 compiler: implicit string rows and an id
    # coverage rule. Read/replay must remain exact; reusing it needs author edits.
    old = json.loads(
        (
            Path(__file__).parent
            / "fixtures/referenced-methods-before-shape-check.json"
        ).read_text()
    )
    unchanged = copy.deepcopy(old)
    assert not pack_problems(old["pack"])
    draft, notes = decompile(old)
    assert not notes
    assert old == unchanged
    _, problems = compile_manifest(
        draft,
        {},
        pack_id=old["pack"]["pack_id"],
        version="1",
        runtime="knowbench",
        fallback_name="synthetic",
        method_sources=old["pack"]["method_sources"],
    )
    assert any("source_field" in p.message for p in problems)
    # Historical replay still verifies the exact digest and compiled projection.
    altered = copy.deepcopy(old)
    altered["pack"]["method_units"][0]["contract"]["acceptance"] = []
    assert pack_problems(altered["pack"])
