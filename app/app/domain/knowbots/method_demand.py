"""Runtime demand handed to its owner as a proposal, never accepted author text."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from app.domain.knowbots.drafts import Strict
from app.domain.knowbots.formal_method import MethodDraftV2, MethodRef

Text = Annotated[str, Field(min_length=1, max_length=2000)]


OriginId = Annotated[
    str,
    Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"),
]


class SuggestedPlan(Strict):
    sketch_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    methods: tuple[MethodRef, ...] = Field(min_length=1, max_length=8)
    outputs: tuple[Text, ...] = Field(max_length=100)


class FormalHandoff(Strict):
    """Approved problem and interface gap; only the method owner may seed a revision."""

    owner_subject: str = Field(min_length=1, max_length=512)
    question: Text
    key: str = Field(min_length=8, max_length=128)
    contract: Literal["knowlab/formal-handoff/v2"]
    project_id: OriginId
    task_id: OriginId

    @model_validator(mode="after")
    def check_draft_contract(self):
        if self.draft_contract is not None:
            MethodDraftV2.model_validate(self.draft_contract)
        return self

    suggested_plan: SuggestedPlan | None = None
    htn_tree: dict[str, Any] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    draft_contract: dict[str, Any] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    reference: MethodRef | None = None
    gap: Text = Field(pattern=r"\S")
    review_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")

    def digest(self):
        return hashlib.sha256(
            json.dumps(
                self.model_dump(mode="json", exclude_none=True),
                sort_keys=True,
                ensure_ascii=False,
            ).encode()
        ).hexdigest()


def render() -> str:
    return (
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": "https://contracts.sunmoonai.com/knowlab/formal-handoff/v2/request.schema.json",
                **FormalHandoff.model_json_schema(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[5] / "contracts/formal-handoff/v2"
    target.mkdir(parents=True, exist_ok=True)
    (target / "request.schema.json").write_text(render())
