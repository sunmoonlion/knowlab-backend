"""Author-owned method definitions, independent of an expert's local aliases.

Freeze accepted workdraft cells; never infer methods from a user's runtime task.
The definition owns named input slots, while an expert supplies their bindings.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import Field, TypeAdapter, model_validator

from app.domain.knowbots.atomic_methods import AtomicMethodDefinition
from app.domain.knowbots.compile import (
    DEFAULT_INPUT_MAX_CHARS,
    DEFAULT_RESERVE,
    DEFAULT_TOOLS,
    output_schema,
)
from app.domain.knowbots.workdraft import (
    STEP_ID,
    CapabilityPlanningCell,
    ConditionalStepCell,
    MethodExecution,
    OptionalStepCell,
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


class FreezeAtomicMethod(FreezeMethod):
    definition_contract: Literal["knowlab/method-definition/v3"]
    # The author selects the fields needed by this method, not the complete
    # output of whichever expert happened to supply an input originally.
    input_fields: dict[Identifier, tuple[str, ...]] = Field(max_length=30)
    input_descriptions: dict[Identifier, str] = Field(max_length=30)
    applies_when: str | None = Field(default=None, min_length=1, max_length=4000)


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


class MethodDefinitionV2(MethodDefinition):
    schema_: Literal["knowlab/method-definition/v2"] = Field(alias="schema")
    step: ConditionalStepCell
    input_required: dict[Identifier, Annotated[bool, Field(strict=True)]] = Field(
        max_length=30
    )

    @model_validator(mode="after")
    def _optional_slots(self) -> MethodDefinitionV2:
        # Reuse author-side guard validation without putting expert-local
        # bindings into the independent frozen definition.
        OptionalStepCell.model_validate(
            {
                **self.step.model_dump(mode="json"),
                "input_required": self.input_required,
            }
        )
        return self


AnyMethodDefinition = MethodDefinition | MethodDefinitionV2 | AtomicMethodDefinition
_definitions = TypeAdapter(AnyMethodDefinition)


def parse_definition(document: Any) -> AnyMethodDefinition:
    return _definitions.validate_python(document)


def digest(document: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(document).encode()).hexdigest()


def _input_schema(step: StepCell) -> dict[str, Any]:
    # Freeze the schema the compiler actually publishes, including an author's
    # explicit engineering override; never infer a different input contract.
    return (
        step.advanced.output_schema
        if step.advanced.output_schema is not None
        else output_schema(step)
    )


def _remap(step: StepCell, alias: str, inputs: dict[str, str]) -> dict[str, Any]:
    doc = step.model_dump(mode="json")
    doc["id"] = alias
    doc["uses"] = [inputs[name] for name in step.uses]
    if isinstance(step, OptionalStepCell):
        doc["input_required"] = {
            inputs[name]: required for name, required in step.input_required.items()
        }
    if step.on_fail.kind == "back":
        doc["on_fail"]["back_to"] = inputs[str(step.on_fail.back_to)]
    for check in doc["checks"]:
        if "when_input_present" in check:
            check["when_input_present"] = inputs[check["when_input_present"]]
        if check["template"] == "covers":
            name = check["params"]["source_step"]
            check["params"]["source_step"] = inputs[name]
    return doc


def freeze(body: WorkdraftBody, command: FreezeMethod) -> AnyMethodDefinition:
    """Call only after the workdraft's compilation and author provenance pass."""
    units = {step.id: step for step in body.steps}
    step = units.get(command.step_id)
    if step is None:
        raise ValueError("unknown source method")
    atomic = isinstance(command, FreezeAtomicMethod)
    if not atomic and (body.planning is None or step.selection is None):
        raise ValueError("freeze a declared method from a dynamic workdraft")
    slots = command.input_slots
    if set(slots) != set(step.uses) or len(set(slots.values())) != len(slots):
        raise ValueError("name every input slot exactly once")
    if "method" in slots.values():
        raise ValueError("method is reserved for the neutral method identity")
    doc = _remap(step, "method", slots)
    if not atomic:
        doc["selection"]["required"] = False
    # A reusable method must not inherit future platform defaults when another
    # expert binds the same version. Resolve author engineering defaults once.
    doc["advanced"].update(
        tools=list(
            DEFAULT_TOOLS if step.advanced.tools is None else step.advanced.tools
        ),
        reserve=step.advanced.reserve or DEFAULT_RESERVE,
        input_max_chars=step.advanced.input_max_chars or DEFAULT_INPUT_MAX_CHARS,
        output_schema=_input_schema(step),
    )
    requirements = doc.pop("input_required", None)
    if isinstance(command, FreezeAtomicMethod):
        if set(command.input_fields) != set(slots.values()) or set(
            command.input_descriptions
        ) != set(slots.values()):
            raise ValueError("declare each independent method input")
        schemas = {}
        for source, slot in slots.items():
            provided = _input_schema(units[source])
            fields = command.input_fields[slot]
            if (
                not fields
                or len(set(fields)) != len(fields)
                or not set(fields) <= provided.keys()
            ):
                raise ValueError("select existing input fields without duplication")
            schemas[slot] = {
                "schema": {name: provided[name] for name in fields},
                "required": (requirements or {}).get(slot, True),
                "description": command.input_descriptions[slot],
            }
        return AtomicMethodDefinition.model_validate(
            {
                "schema": command.definition_contract,
                "title": doc["title"],
                "purpose": doc["why"],
                "procedure": doc["does"],
                "applies_when": command.applies_when
                or (step.selection.applies_when if step.selection else ""),
                "inputs": schemas,
                "rules": doc["rules"],
                "outputs": doc["outputs"],
                "output_schema": _input_schema(step),
                "checks": doc["checks"],
            }
        )
    return parse_definition(
        {
            "schema": "knowlab/method-definition/v2"
            if requirements is not None
            else "knowlab/method-definition/v1",
            "step": doc,
            **({"input_required": requirements} if requirements is not None else {}),
            "input_schemas": {
                slot: _input_schema(units[source]) for source, slot in slots.items()
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
    execution: MethodExecution | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


def resolve_execution(command: BindMethod) -> BindMethod:
    if command.execution is None:
        return command
    raw = command.model_dump(mode="json")
    advanced = raw["execution"]["advanced"]
    advanced["tools"] = (
        list(DEFAULT_TOOLS) if advanced["tools"] is None else advanced["tools"]
    )
    advanced["reserve"] = advanced["reserve"] or DEFAULT_RESERVE
    advanced["input_max_chars"] = advanced["input_max_chars"] or DEFAULT_INPUT_MAX_CHARS
    return BindMethod.model_validate(raw)


def bind(
    definition: AnyMethodDefinition, body: WorkdraftBody, command: BindMethod
) -> StepCell:
    """Resolve a pinned version without editing it or widening its boundaries."""
    if body.planning is None:
        raise ValueError("method references require a dynamic expert")
    if isinstance(definition, AtomicMethodDefinition):
        return _bind_atomic(definition, body, command)
    if command.execution is not None:
        raise ValueError("legacy method execution is frozen in its original definition")
    if isinstance(definition, MethodDefinitionV2) and not isinstance(
        body.planning, CapabilityPlanningCell
    ):
        raise ValueError("optional method inputs require capability planning")
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
        provided = _input_schema(source)
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
    if isinstance(definition, MethodDefinitionV2):
        doc["input_required"] = {
            command.inputs[slot]: required
            for slot, required in definition.input_required.items()
        }
        return OptionalStepCell.model_validate(doc)
    return StepCell.model_validate(doc)


def _bind_atomic(
    definition: AtomicMethodDefinition, body: WorkdraftBody, command: BindMethod
) -> StepCell:
    if not isinstance(body.planning, CapabilityPlanningCell):
        raise ValueError("atomic methods require capability planning")
    if command.execution is None or command.digest != definition.digest():
        raise ValueError("pin the exact method and declare expert execution policy")
    if set(command.inputs) != set(definition.inputs):
        raise ValueError("bind each atomic method slot")
    if command.alias in command.inputs.values() or len(
        set(command.inputs.values())
    ) != len(command.inputs):
        raise ValueError("distinct method slots require distinct upstream aliases")
    upstream = {step.id: step for step in body.steps}
    for slot, alias in command.inputs.items():
        if alias not in upstream:
            raise ValueError("unknown upstream method")
        provided = _input_schema(upstream[alias])
        if any(
            provided.get(key) != value
            for key, value in definition.inputs[slot].schema_.items()
        ):
            raise ValueError("upstream output is incompatible with the method input")
    execution = command.execution.model_dump(mode="json")
    # All execution choices are supplied by the expert; only output semantics
    # belong to the method. Resolve platform defaults before pinning references.
    advanced = execution["advanced"]
    if any(advanced[name] is None for name in ("tools", "reserve", "input_max_chars")):
        raise ValueError("resolve execution defaults before binding an atomic method")
    advanced["output_schema"] = definition.output_schema
    doc = {
        "id": "method",
        "title": definition.title,
        "why": definition.purpose,
        "does": definition.procedure,
        "uses": sorted(definition.inputs),
        "rules": [rule.model_dump(mode="json") for rule in definition.rules],
        "outputs": [out.model_dump(mode="json") for out in definition.outputs],
        "checks": [check.model_dump(mode="json") for check in definition.checks],
        "selection": {
            "applies_when": definition.applies_when,
            "required": command.required,
            "max_calls": command.max_calls,
        },
        "input_required": {
            slot: value.required for slot, value in definition.inputs.items()
        },
        **execution,
    }
    # Execution back targets are expert aliases; _remap expects neutral slots.
    if doc["on_fail"]["kind"] == "back":
        inverse = {alias: slot for slot, alias in command.inputs.items()}
        target = doc["on_fail"]["back_to"]
        if target not in inverse:
            raise ValueError("execution back target must be an upstream input")
        doc["on_fail"]["back_to"] = inverse[target]
    return OptionalStepCell.model_validate(
        _remap(OptionalStepCell.model_validate(doc), command.alias, command.inputs)
    )


def render(*, optional: bool = False) -> str:
    """Provider schema for author definitions; not a runtime expert manifest."""
    version = "v2" if optional else "v1"
    return (
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": "https://contracts.sunmoonai.com/knowlab/author-method/"
                f"{version}/definition.schema.json",
                **(
                    MethodDefinitionV2 if optional else MethodDefinition
                ).model_json_schema(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    print(render(), end="")
