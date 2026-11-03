"""Runtime demand handed to its owner as a proposal, never accepted author text."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from app.domain.knowbots.drafts import RuntimeIncubationIn, Strict

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,47}$")]
Text = Annotated[str, Field(min_length=1, max_length=2000)]


class DemandRequirement(Strict):
    id: Identifier
    kind: Literal["result", "constraint", "explicit_method"]
    statement: Text
    question_quote: Text
    required: bool = Field(strict=True)
    origin: Literal["user_explicit", "goal_inferred"]
    rationale: Text


class DemandProposal(Strict):
    id: Identifier
    requirement_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=100)
    approach: Text
    reason: Text
    data_requirements: tuple[Text, ...] = Field(max_length=30)


class DemandGap(Strict):
    requirement_id: Identifier
    status: Literal["uncovered", "unknown"]
    reason: Text


class MethodDemand(Strict):
    match_artifact_version: int = Field(ge=1, strict=True)
    question_revision: int = Field(ge=1, strict=True)
    outcome: Literal["none", "clarify"]
    requirements: tuple[DemandRequirement, ...] = Field(min_length=1, max_length=100)
    method_proposals: tuple[DemandProposal, ...] = Field(max_length=30)
    gaps: tuple[DemandGap, ...] = Field(max_length=300)
    clarification: Text | None


class RuntimeIncubationV2(RuntimeIncubationIn):
    contract: Literal["knowlab/runtime-incubation/v2"]
    method_demand: MethodDemand

    @model_validator(mode="after")
    def _demand(self) -> Self:
        demand = self.method_demand
        requirements = {r.id for r in demand.requirements}
        proposals = {p.id for p in demand.method_proposals}
        if (
            not self.task_id
            or len(requirements) != len(demand.requirements)
            or len(proposals) != len(demand.method_proposals)
            or requirements & proposals
        ):
            raise ValueError("demand needs a source task and unique identifiers")
        for req in demand.requirements:
            if (
                not req.question_quote.strip()
                or req.question_quote not in self.question
            ):
                raise ValueError("demand must preserve the original question")
            if req.kind == "explicit_method" and (
                req.origin != "user_explicit" or not req.required
            ):
                raise ValueError("explicit methods cannot become optional suggestions")
        if any(
            not set(p.requirement_ids) <= requirements
            or len(set(p.requirement_ids)) != len(p.requirement_ids)
            for p in demand.method_proposals
        ) or any(g.requirement_id not in requirements for g in demand.gaps):
            raise ValueError("demand contains unknown requirement references")
        if (demand.outcome == "clarify") != bool(demand.clarification):
            raise ValueError("clarification differs from the matching outcome")
        # The existing AI-reference channel is bounded; refuse rather than truncate
        # a requirement or silently lose its unaccepted status.
        if len(self.reference()) > 20000:
            raise ValueError("method demand exceeds the incubation reference limit")
        return self

    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(
                self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False
            ).encode()
        ).hexdigest()

    def reference(self) -> str:
        return (
            "以下是需求侧AI分析与建议，尚未经作者接受，不得作为作者已定方法或验收规则。\n"
            + json.dumps(self.method_demand.model_dump(mode="json"), ensure_ascii=False)
            + (
                "\n用户附带的参考回答（同样只是参考）：\n" + self.reference_answer
                if self.reference_answer
                else ""
            )
        )


def render() -> str:
    return (
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": "https://contracts.sunmoonai.com/knowlab/runtime-incubation/v2/request.schema.json",
                **RuntimeIncubationV2.model_json_schema(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[5] / "contracts/runtime-incubation/v2"
    target.mkdir(parents=True, exist_ok=True)
    (target / "request.schema.json").write_text(render())
