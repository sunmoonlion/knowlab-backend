"""Read-only test process projection, owned by the test application.

The runtime resolves run/case IDs itself. No arbitrary task proxy, raw model
history, credentials, or method instructions belong in this contract.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from pydantic import Field

from app.domain.knowbots.test_run import CASE_ID, RUN_ID, CaseOutputsV1, Strict


class TestToolActivity(Strict):
    at: datetime | None = None
    name: str = Field(max_length=256)
    ok: bool


class TestStepActivity(Strict):
    step_id: str = Field(min_length=1, max_length=128)
    index: int = Field(ge=1)
    title: str = Field(max_length=200)
    status: str = Field(max_length=32)
    selection_reason: str | None = None
    attempts: int = Field(ge=0)
    spent: str
    tools: dict[str, int]
    latest_activity: tuple[TestToolActivity, ...] = Field(max_length=20)


class TestPlanView(Strict):
    version: int | None = Field(default=None, ge=1)
    planning: bool
    reason: str | None = None


class TestCaseExecution(Strict):
    case_id: str = Field(pattern=CASE_ID)
    state: str = Field(max_length=32)
    waiting_reason: str | None = None
    title: str = Field(default="", max_length=200)
    activity: str = Field(default="", max_length=1000)
    since: datetime | None = None
    last_event_at: datetime | None = None
    spent: str
    plan: TestPlanView | None = None
    steps: tuple[TestStepActivity, ...] = Field(max_length=200)
    # Only the explicitly requested case includes bounded, already projected outputs.
    outputs: CaseOutputsV1 | None = None


class TestExecutionV1(Strict):
    contract: Literal["knowbot-test-execution/v1"]
    run_id: str = Field(pattern=RUN_ID)
    status: str = Field(max_length=32)
    cases: tuple[TestCaseExecution, ...] = Field(max_length=100)


def render() -> str:
    return (
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": "https://contracts.sunmoonai.com/knowlab/knowbot-test-execution/v1/execution.schema.json",
                **TestExecutionV1.model_json_schema(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
