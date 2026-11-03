"""A release exception is not a model test verdict."""

import hashlib
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.knowbots.workdraft import canonical


class ManualReleaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    baseline_test_id: UUID
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=1, max_length=2000)
    change_summary: str = Field(min_length=1, max_length=4000)
    verification: str = Field(min_length=1, max_length=4000)
    limitations: str = Field(min_length=1, max_length=4000)
    authorize_reviewer: Literal[True]
    acknowledge_unretested: Literal[True]

    @field_validator("reason", "change_summary", "verification", "limitations")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("describe the release evidence and limitations")
        return value


class ManualReleaseDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    approved: bool
    reason: str = Field(min_length=1, max_length=2000)
    acknowledge_unretested: Literal[True]

    @field_validator("reason")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("a manual decision requires a reason")
        return value


def manifest_digest(manifest: dict) -> str:
    return hashlib.sha256(canonical(manifest).encode()).hexdigest()
