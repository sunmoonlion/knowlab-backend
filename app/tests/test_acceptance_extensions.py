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


def test_report_sources_use_neutral_slots_when_bound_under_different_aliases():
    from uuid import UUID

    from app.domain.knowbots.methods import BindMethod, MethodDefinition, bind
    from app.domain.knowbots.workdraft import WorkdraftBody

    manifest, problems = build_financial_expert_manifest()
    assert not problems
    definition = MethodDefinition.model_validate(
        manifest["pack"]["method_sources"]["comprehensive_dossier_formatting"][
            "definition"
        ]
    )
    inputs = {slot: f"source_{i}" for i, slot in enumerate(definition.input_schemas)}
    body = WorkdraftBody.model_validate(
        {
            "name": "另一个专家",
            "planning": {
                "method_text": "按作者方法核对",
                "final_units": ["new_report"],
                "max_steps": 10,
                "max_replans": 1,
                "reserve": "0.05",
                "input_max_chars": 8000,
            },
            "steps": [
                {
                    "id": alias,
                    "outputs": [
                        {"name": key, "type": "table"}
                        for key in definition.input_schemas[slot]
                    ],
                    "advanced": {"output_schema": definition.input_schemas[slot]},
                }
                for slot, alias in inputs.items()
            ],
        }
    )
    bound = bind(
        definition,
        body,
        BindMethod(
            request_id=UUID(int=1),
            revision=1,
            alias="new_report",
            digest=definition.digest(),
            inputs=inputs,
            required=False,
            max_calls=1,
        ),
    )
    assert set(bound.uses) == set(inputs.values())
    for rule in bound.checks:
        if rule.template == "covers":
            assert rule.params["source_step"] in inputs.values()
        if rule.template == "each_has" and rule.params.get("field") == "source":
            assert set(rule.params["values"]) <= set(inputs)
