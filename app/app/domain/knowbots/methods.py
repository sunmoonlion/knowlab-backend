"""Author-owned method definitions, independent of an expert's local aliases.

Freeze accepted workdraft cells; never infer methods from a user's runtime task.
The definition owns named input slots, while an expert supplies their bindings.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.domain.knowbots.compile import output_schema
from app.domain.knowbots.workdraft import (
    STEP_ID,
    StepCell,
    Strict,
    WorkdraftBody,
    canonical,
)

Identifier = Annotated[str, Field(pattern=STEP_ID)]


class FreezeMethod(Strict):
    request_id: UUID
    revision: int = Field(ge=1, strict=True)
    step_id: Identifier
    # Original expert alias -> independent input-slot name, explicitly confirmed.
    input_slots: dict[Identifier, Identifier] = Field(
        default_factory=dict, max_length=30
    )

    def digest(self) -> str:
        return digest(self.model_dump(mode="json"))


class MethodDefinition(Strict):
    schema_: Literal["knowlab/method-definition/v1"] = Field(alias="schema")
    step: StepCell
    input_schemas: dict[Identifier, dict[str, Any]] = Field(max_length=30)

    @model_validator(mode="after")
    def _slots(self) -> MethodDefinition:
        if self.step.id != "method" or self.step.selection is None:
            raise ValueError("a method needs a neutral identity and selection bounds")
        if self.step.selection.required:
            raise ValueError("required is an expert composition decision")
        if set(self.step.uses) != set(self.input_schemas) or "method" in self.step.uses:
            raise ValueError("method inputs must name independent slots")
        if len(set(self.step.uses)) != len(self.step.uses):
            raise ValueError("input slots must be unique")
        if (
            self.step.on_fail.kind == "back"
            and self.step.on_fail.back_to not in self.input_schemas
        ):
            raise ValueError("back target must be an input slot")
        for check in self.step.checks:
            if (
                check.template == "covers"
                and check.params.get("source_step") not in self.input_schemas
            ):
                raise ValueError("coverage source must be an input slot")
        return self

    def document(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)

    def digest(self) -> str:
        return digest(self.document())


def digest(document: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(document).encode()).hexdigest()


def _remap(step: StepCell, alias: str, inputs: dict[str, str]) -> dict[str, Any]:
    doc = step.model_dump(mode="json")
    doc["id"] = alias
    doc["uses"] = [inputs[name] for name in step.uses]
    if step.on_fail.kind == "back":
        doc["on_fail"]["back_to"] = inputs[str(step.on_fail.back_to)]
    for check in doc["checks"]:
        if check["template"] == "covers":
            name = check["params"]["source_step"]
            check["params"]["source_step"] = inputs[name]
    return doc


def freeze(body: WorkdraftBody, command: FreezeMethod) -> MethodDefinition:
    """Call only after the workdraft's compilation and author provenance pass."""
    units = {step.id: step for step in body.steps}
    step = units.get(command.step_id)
    if body.planning is None or step is None or step.selection is None:
        raise ValueError("freeze a declared method from a dynamic workdraft")
    slots = command.input_slots
    if set(slots) != set(step.uses) or len(set(slots.values())) != len(slots):
        raise ValueError("name every input slot exactly once")
    if "method" in slots.values():
        raise ValueError("method is reserved for the neutral method identity")
    doc = _remap(step, "method", slots)
    doc["selection"]["required"] = False
    return MethodDefinition.model_validate(
        {
            "schema": "knowlab/method-definition/v1",
            "step": doc,
            "input_schemas": {
                slot: output_schema(units[source]) for source, slot in slots.items()
            },
        }
    )


class BindMethod(Strict):
    request_id: UUID
    revision: int = Field(ge=1, strict=True)
    alias: Identifier
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    inputs: dict[Identifier, Identifier] = Field(default_factory=dict, max_length=30)
    required: bool = Field(strict=True)
    max_calls: int = Field(ge=1, le=100, strict=True)


def bind(
    definition: MethodDefinition, body: WorkdraftBody, command: BindMethod
) -> StepCell:
    """Resolve a pinned version without editing it or widening its boundaries."""
    if body.planning is None:
        raise ValueError("method references require a dynamic expert")
    if command.digest != definition.digest():
        raise ValueError("method digest does not match its pinned version")
    if set(command.inputs) != set(definition.input_schemas):
        raise ValueError("bind every method input slot")
    if command.alias in command.inputs.values():
        raise ValueError("a method cannot consume its own output")
    # Current execution slots identify upstream methods. Do not silently merge
    # distinct method inputs; the future reference contract must preserve them.
    if len(set(command.inputs.values())) != len(command.inputs):
        raise ValueError("distinct input slots need distinct upstream aliases")
    upstream = {step.id: step for step in body.steps}
    for slot, alias in command.inputs.items():
        source = upstream.get(alias)
        if source is None:
            raise ValueError("input binding names an unknown expert method")
        provided = output_schema(source)
        expected = definition.input_schemas[slot]
        if any(
            key not in provided or provided[key] != value
            for key, value in expected.items()
        ):
            raise ValueError("upstream output is incompatible with the method input")
    selection = definition.step.selection
    assert selection is not None
    if command.max_calls > selection.max_calls:
        raise ValueError("expert selection exceeds the method call limit")
    doc = _remap(definition.step, command.alias, command.inputs)
    doc["selection"].update(required=command.required, max_calls=command.max_calls)
    return StepCell.model_validate(doc)


def render() -> str:
    """Provider schema for author definitions; not a runtime expert manifest."""
    return (
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": "https://contracts.sunmoonai.com/knowlab/author-method/v1/definition.schema.json",
                **MethodDefinition.model_json_schema(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    print(render(), end="")
