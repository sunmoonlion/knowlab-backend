"""New author workdrafts diagnose unavoidable runtime file collisions early."""

import copy

import pytest

from app.domain.knowbots.compile import compile_manifest, decompile
from tests.dynamic_method_fixtures import body, build
from tests.referenced_method_fixtures import build as referenced


@pytest.mark.parametrize("root", ["required", "final", "dependency"])
@pytest.mark.parametrize(
    "paths",
    [
        ("analysis/report.md", "forecast/Report.MD"),
        ("reports", "reports/final.md"),
    ],
)
def test_unavoidable_methods_and_their_inputs_cannot_collide(root, paths):
    raw = body()
    raw["steps"][0]["advanced"]["output_file"] = paths[0]
    raw["steps"][1]["advanced"]["output_file"] = paths[1]
    if root in {"final", "dependency"}:
        for step in raw["steps"]:
            step["selection"]["required"] = False
    if root == "dependency":
        raw["planning"]["final_units"] = ["review"]
        raw["steps"][2]["uses"] = ["report"]
    _, problems = build(raw)
    assert any(
        p.path == "steps/inspect/advanced" and "冲突" in p.message for p in problems
    )


def test_optional_collision_remains_a_task_selection_decision():
    raw = body()
    raw["steps"][0]["advanced"]["output_file"] = "analysis/report.md"
    raw["steps"][2]["advanced"]["output_file"] = "forecast/report.md"
    _, problems = build(raw)
    assert not problems
    raw["steps"][2]["selection"]["required"] = True
    _, problems = build(raw)
    assert any(
        p.path == "steps/review/advanced" and "冲突" in p.message for p in problems
    )


@pytest.mark.parametrize("path", ["交回.md", "reports/交回.MD"])
def test_reserved_handback_name_is_reported_on_the_author_cell(path):
    raw = body()
    raw["steps"][2]["advanced"]["output_file"] = path
    _, problems = build(raw)
    assert any(
        p.path == "steps/review/advanced" and "交回.md" in p.message for p in problems
    )


def test_distinct_files_and_manifest_bytes_are_not_rewritten():
    raw = body()
    raw["steps"][0]["advanced"]["output_file"] = "analysis/report.md"
    raw["steps"][1]["advanced"]["output_file"] = "source/evidence.md"
    original = copy.deepcopy(raw)
    manifest, problems = build(raw)
    assert not problems and raw == original
    assert [
        u["contract"].get("output_file") for u in manifest["pack"]["method_units"]
    ] == [
        "analysis/report.md",
        "source/evidence.md",
        None,
    ]


def test_historical_referenced_snapshot_stays_readable_but_cannot_republish_conflict():
    # build returns the exact compiled evidence even when current authoring
    # checks reject publication; replay must not rewrite immutable evidence.
    manifest, problems = referenced(output_file="reports/交回.md")
    assert any("交回.md" in p.message for p in problems)
    original = copy.deepcopy(manifest)
    restored, notes = decompile(manifest)
    assert not notes and manifest == original
    rebuilt, problems = compile_manifest(
        restored,
        {},
        pack_id=manifest["pack"]["pack_id"],
        version="1",
        runtime="knowbench",
        fallback_name="synthetic",
        method_sources=manifest["pack"]["method_sources"],
    )
    assert any(p.path == "steps/report/advanced" for p in problems)
    assert rebuilt["pack"] == manifest["pack"]
