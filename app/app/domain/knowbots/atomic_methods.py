"""Method meaning is independent of expert composition and execution policy.

The existing step compiler remains the execution backend. Its StepCell is a
projection at the boundary, not the persisted identity of an atomic method.
Neither version freezing nor this schema asserts maturity in production.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal, Self

from pydantic import Field, model_validator

from app.domain.knowbots.workdraft import (
    STEP_ID,
    CheckCell,
    ConditionalCheckCell,
    OutputCell,
    RuleCell,
    Strict,
    canonical,
)

Identifier = Annotated[str, Field(pattern=STEP_ID)]
Nonblank = Annotated[str, Field(min_length=1, max_length=20000)]


class MethodInput(Strict):
    """A method consumes an explicitly declared shape, not an upstream workflow."""

    schema_: dict[str, Any] = Field(alias="schema", min_length=1)
    required: bool = Field(strict=True)
    description: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _description(self) -> Self:
        if not self.description.strip():
            raise ValueError("method input needs a nonblank description")
        return self


class AtomicMethodDefinition(Strict):
    schema_: Literal["knowlab/method-definition/v3"] = Field(alias="schema")
    title: str = Field(min_length=1, max_length=80)
    purpose: str = Field(min_length=1, max_length=1000)
    procedure: Nonblank
    applies_when: str = Field(min_length=1, max_length=4000)
    inputs: dict[Identifier, MethodInput] = Field(max_length=30)
    rules: tuple[RuleCell, ...] = Field(max_length=50)
    outputs: tuple[OutputCell, ...] = Field(min_length=1, max_length=30)
    output_schema: dict[str, Any] = Field(min_length=1)
    checks: tuple[CheckCell | ConditionalCheckCell, ...] = Field(max_length=50)

    @model_validator(mode="after")
    def _method(self) -> Self:
        if any(
            not text.strip()
            for text in (self.title, self.purpose, self.procedure, self.applies_when)
        ):
            raise ValueError("method meaning cannot be blank")
        if "method" in self.inputs:
            raise ValueError("method is reserved")
        for names in ([o.name for o in self.outputs], [r.id for r in self.rules]):
            if len(set(names)) != len(names):
                raise ValueError("method field identifiers must be unique")
        if set(self.output_schema) != {o.name for o in self.outputs}:
            raise ValueError("output schema must describe the declared outputs")
        optional = {name for name, slot in self.inputs.items() if not slot.required}
        for check in self.checks:
            guard = getattr(check, "when_input_present", None)
            if guard is not None and (
                guard not in optional or check.template == "required"
            ):
                raise ValueError("conditional checks must guard optional inputs")
            if check.template == "covers":
                source = check.params.get("source_step")
                if source not in self.inputs:
                    raise ValueError("coverage must name an independent input slot")
                source_path = check.params.get("source_path")
                if (
                    not isinstance(source_path, str)
                    or source_path.split(".")[0] not in self.inputs[source].schema_
                ):
                    raise ValueError(
                        "coverage field must be declared in its input contract"
                    )
                if source in optional and guard != source:
                    raise ValueError("optional coverage requires its input guard")
                if guard is not None and guard != source:
                    raise ValueError("coverage guard must name its own source")
        return self

    def document(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)

    def digest(self) -> str:
        return (
            "sha256:" + hashlib.sha256(canonical(self.document()).encode()).hexdigest()
        )


def render() -> str:
    return (
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": "https://contracts.sunmoonai.com/knowlab/author-method/v3/definition.schema.json",
                **AtomicMethodDefinition.model_json_schema(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
