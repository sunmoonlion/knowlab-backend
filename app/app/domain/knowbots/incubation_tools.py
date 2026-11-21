"""Authorized tool contracts exchanged between the incubator and runtime."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.domain.knowbots.formal_method import (
    FormalMethodDefinition,
    MethodRef,
    Strict,
    ToolProcedure,
    ValueSlot,
)


class Installation(Strict):
    asset_id: UUID
    revision: int = Field(ge=1, strict=True)
    name: str
    account: str | None


class SharedTool(Strict):
    installations: tuple[Installation, ...] = ()
    selected: UUID | None = None
    reference: MethodRef
    scope: Literal["acquired", "platform"]
    title: str = Field(min_length=1, max_length=20000)
    definition: FormalMethodDefinition | None = None

    @model_validator(mode="after")
    def exact_tool(self):
        if self.selected is not None and not any(
            i.asset_id == self.selected for i in self.installations
        ):
            raise ValueError("Selected installation is unavailable")
        if self.definition is not None and (
            self.definition.digest() != self.reference.digest
            or not isinstance(self.definition.procedure, ToolProcedure)
        ):
            raise ValueError("Different tool definition")
        return self


class SharedToolsPage(Strict):
    items: tuple[SharedTool, ...] = Field(max_length=100)
    next: UUID | None


class AutomaticOutputSearch(Strict):
    slot: ValueSlot
    exclude_method_id: UUID
    directory: Literal["owned", "shared"]
    page_limit: int = Field(strict=True, ge=1, le=100)
    action_id: UUID


class SampleColumn(Strict):
    material_id: str = Field(min_length=1, max_length=200)
    version_no: int = Field(strict=True, ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sheet: str = Field(max_length=255)
    column: str = Field(min_length=1, max_length=200, pattern=r"\S")


class SampleColumnResult(SampleColumn):
    status: Literal["present", "missing", "unknown"]
