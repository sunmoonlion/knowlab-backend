"""New optional rules compile, round-trip and survive author method freezing."""

import pytest

from app.domain.knowbots.compile import _check_of, check_rule
from app.domain.knowbots.workdraft import CheckCell, OutputCell, StepCell
from tests.financial_author_method_fixtures import (
    ALL_20_SPECS,
    build_financial_expert_manifest,
)


@pytest.mark.parametrize(
    "template, params",
    [
        (
            "computed",
            {
                "expected_value": 0,
                "input_keys": ["left", "right"],
                "expression": "abs(left-right)",
            },
        ),
        (
            "each_has",
            {"non_empty_keys": ["period", "metric"], "unique_by": ["period", "metric"]},
        ),
    ],
)
def test_extensions_roundtrip(template, params):
    check = CheckCell.model_validate(
        {"template": template, "params": {"path": "rows", **params}}
    )
    step = StepCell(id="sample", outputs=(OutputCell(name="rows", type="table"),))
    rule, error = check_rule(check, step, {})
    assert error is None
    restored = _check_of(rule, {})
    assert restored is not None
    for key, value in check.params.items():
        assert restored["params"][key] == value


@pytest.mark.parametrize(
    "template, params",
    [
        ("computed", {"expected_value": True}),
        ("computed", {"expected_value": float("nan")}),
        ("computed", {"expected_value": "0"}),
        ("computed", {"input_keys": ["x", "x"]}),
        ("each_has", {"unique_by": [" "]}),
        ("each_has", {"non_empty_keys": [1]}),
        ("each_has", {"expected_value": 0}),
    ],
)
def test_extensions_reject_malformed_authored_params(template, params):
    check = CheckCell.model_validate(
        {"template": template, "params": {"path": "rows", **params}}
    )
    rule, error = check_rule(
        check,
        StepCell(id="sample", outputs=(OutputCell(name="rows", type="table"),)),
        {},
    )
    assert rule is None and error


def test_method_boundaries_survive_freeze_bind_and_compile():
    manifest, problems = build_financial_expert_manifest()
    assert problems == []
    units = {u["contract"]["step_id"]: u for u in manifest["pack"]["method_units"]}
    for spec in ALL_20_SPECS:
        unit = units[spec["id"]]
        assert spec["does_not"] in unit["selection"]["applies_when"]
        for key in ("does_not", "elsewhere"):
            assert spec[key] in unit["contract"]["method_text"]
            definition = manifest["pack"]["method_sources"][spec["id"]]["definition"]
            assert any(r["text"] == spec[key] for r in definition["step"]["rules"])
