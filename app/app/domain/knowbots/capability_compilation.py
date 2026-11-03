"""Versioned optional-input compilation, using the existing author compiler.

The legacy projection below is solely for validation/import. It is never a
published execution plan and cannot change the immutable source definitions.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.domain.knowbots.workdraft import (
    CapabilityPlanningCell,
    MethodReference,
    OptionalStepCell,
    Problem,
    StepCell,
    WorkdraftBody,
)


def add_input_slots(
    contract: dict[str, Any], step: StepCell, reference: MethodReference | None
) -> None:
    aliases = (
        {alias: slot for slot, alias in reference.inputs.items()}
        if reference
        else {alias: alias for alias in step.uses}
    )
    required = (
        step.input_required
        if isinstance(step, OptionalStepCell)
        else dict.fromkeys(step.uses, True)
    )
    contract["input_refs"] = [aliases[alias] for alias in step.uses]
    contract["input_slots"] = {
        aliases[alias]: {"method_id": alias, "required": required[alias]}
        for alias in step.uses
    }
    if contract.get("back_to"):
        contract["back_to"] = aliases[contract["back_to"]]
    for rule in contract["acceptance"]:
        if "when_input_present" in rule:
            rule["when_input_present"] = aliases[rule["when_input_present"]]
        if rule.get("source"):
            source, dot, field = rule["source"].partition(".")
            rule["source"] = aliases[source] + dot + field


def _plain(manifest: dict[str, Any]) -> dict[str, Any]:
    plain = deepcopy(manifest)
    pack = plain["pack"]
    if pack.pop("method_compiler") != "knowlab/workdraft-compiler/v2":
        raise ValueError("unsupported capability compiler")
    pack.pop("method_sources")
    pack.pop("public_capabilities")
    pack["planning"].pop("capability_bindings")
    # The old checker traverses all inputs, including optional ones. Its bound
    # is not the new required closure; the latter is checked separately below.
    pack["planning"]["max_steps"] = 100
    pack["runtime_contract"] = "knowbench/dynamic-methods/v1"
    for unit in pack["method_units"]:
        contract = unit["contract"]
        slots = contract.pop("input_slots")
        if set(slots) != set(contract["input_refs"]):
            raise ValueError("declare each input slot exactly once")
        for binding in slots.values():
            if (
                set(binding) != {"method_id", "required"}
                or type(binding["required"]) is not bool
            ):
                raise ValueError("invalid input binding")
        contract["input_refs"] = [
            slots[name]["method_id"] for name in contract["input_refs"]
        ]
        if len(set(contract["input_refs"])) != len(contract["input_refs"]):
            raise ValueError("distinct slots require distinct upstream aliases")
        if contract.get("back_to"):
            contract["back_to"] = slots[contract["back_to"]]["method_id"]
        for rule in contract["acceptance"]:
            if "when_input_present" in rule:
                guard = rule.pop("when_input_present")
                if guard not in slots or slots[guard]["required"]:
                    raise ValueError("conditional check must name an optional input")
                if rule["kind"] in {
                    "json_object",
                    "required_keys",
                    "no_positioning_advice",
                }:
                    raise ValueError("mandatory checks cannot be conditional")
            if rule.get("source"):
                source, dot, field = rule["source"].partition(".")
                rule["source"] = slots[source]["method_id"] + dot + field
        contract.pop("step_version", None)
    return plain


def restore(manifest: dict[str, Any]) -> WorkdraftBody:
    from app.domain.knowbots.compile import _check_of, _compile_manifest, decompile
    from app.domain.knowbots.method_composition import (
        MethodSource,
        projection,
        validate_sources,
    )

    pack = manifest["pack"]
    body, _ = decompile(_plain(manifest))
    doc = body.model_dump(mode="json")
    doc["planning"] = pack["planning"]
    doc["public_capabilities"] = pack["public_capabilities"]
    raw_sources = pack["method_sources"]
    if not isinstance(raw_sources, dict):
        raise ValueError("method_sources must be an object")
    sources = {
        alias: MethodSource.model_validate(raw) for alias, raw in raw_sources.items()
    }
    if sources:
        doc["method_refs"] = {
            alias: source.reference.model_dump(mode="json")
            for alias, source in sources.items()
        }
    for step, unit in zip(doc["steps"], pack["method_units"], strict=True):
        contract = unit["contract"]
        slots = contract["input_slots"]
        step["input_required"] = {
            value["method_id"]: value["required"] for value in slots.values()
        }
        # Reinstate author guards on exactly the corresponding decoded checks.
        for rule in contract["acceptance"]:
            if "when_input_present" not in rule:
                continue
            check = _check_of(
                rule, {name: value["method_id"] for name, value in slots.items()}
            )
            if check is None or check not in step["checks"]:
                raise ValueError("conditional check cannot be restored losslessly")
            step["checks"][step["checks"].index(check)] = {
                **check,
                "when_input_present": slots[rule["when_input_present"]]["method_id"],
            }
    body = WorkdraftBody.model_validate(doc)
    projected = {
        alias: projection(body, alias, source) for alias, source in sources.items()
    }
    body = WorkdraftBody.model_validate(
        {
            **body.model_dump(mode="json"),
            "steps": [
                projected.get(step.id, step).model_dump(mode="json")
                for step in body.steps
            ],
        }
    )
    validate_sources(body, raw_sources)
    _validate_capability_closures(body)
    rebuilt, problems = _compile_manifest(
        body,
        {},
        pack_id=pack["pack_id"],
        version=pack["version"],
        runtime=manifest.get("runtime", "knowbench"),
        fallback_name=pack["title"],
        method_sources=raw_sources,
    )
    if problems or rebuilt["pack"] != pack:
        raise ValueError("capability compilation differs from its author definitions")
    return body


def _validate_capability_closures(body: WorkdraftBody) -> None:
    planning = body.planning
    assert isinstance(planning, CapabilityPlanningCell)
    units = {step.id: step for step in body.steps}
    common = set(planning.final_units) | {
        step.id for step in body.steps if step.selection and step.selection.required
    }
    for binding in planning.capability_bindings.values():
        needed = common | set(binding.required_units)
        pending = list(needed)
        while pending:
            step = units[pending.pop()]
            for source in step.uses:
                if (
                    isinstance(step, OptionalStepCell)
                    and not step.input_required[source]
                ):
                    continue
                if source not in needed:
                    needed.add(source)
                    pending.append(source)
        if len(needed) > planning.max_steps:
            raise ValueError("step limit cannot deliver the declared capability")
        for output in binding.evidence_outputs:
            from app.domain.knowbots.compile import output_schema

            step = units[output.method]
            shape = (
                step.advanced.output_schema
                if step.advanced.output_schema is not None
                else output_schema(step)
            )
            if output.field not in shape:
                raise ValueError("capability names an unknown output field")


def catalog_problems(pack: dict[str, Any]) -> list[Problem]:
    from app.domain.knowbots.drafts import DYNAMIC_MANIFEST_SCHEMA

    try:
        restore(
            {
                "schema": DYNAMIC_MANIFEST_SCHEMA,
                "pack": pack,
                "name": pack.get("name", pack.get("title", "")),
                "tagline": pack.get("tagline", ""),
            }
        )
    except (ValueError, KeyError, TypeError, StopIteration) as exc:
        return [Problem("pack", f"invalid capability compilation: {exc}")]
    return []
