"""分身的提交与草稿（SDD 0027）。草稿、测试、报告、改版都在 knowlab。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.models.base import Base, TimestampMixin

DRAFT_STATUSES = "('pending_test', 'testing', 'passed', 'failed', 'delivered')"


class KnowbotRequest(TimestampMixin, Base):
    __tablename__ = "knowbot_request"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    owner_subject: Mapped[str] = mapped_column(String(512), nullable=False)
    title: Mapped[str] = mapped_column(String(80), nullable=False)
    runtime: Mapped[str] = mapped_column(String(32), nullable=False)
    idea: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    cases: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (Index("ix_knowbot_request_owner_subject", "owner_subject"),)


class KnowbotDraft(TimestampMixin, Base):
    __tablename__ = "knowbot_draft"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowbot_request.id", ondelete="RESTRICT"),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    rules_reviewed_by: Mapped[str | None] = mapped_column(String(512))
    rules_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    run_id: Mapped[str | None] = mapped_column(String(128))
    run_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    run_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    run_timeout_seconds: Mapped[int | None] = mapped_column(Integer)
    report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    delivery: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    last_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("request_id", "revision", name="uq_knowbot_draft_revision"),
        CheckConstraint(f"status IN {DRAFT_STATUSES}", name="ck_knowbot_draft_status"),
        Index("ix_knowbot_draft_status", "status"),
    )
