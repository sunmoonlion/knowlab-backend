"""分身的提交与草稿（SDD 0027）。草稿、测试、报告、改版都在 knowlab。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
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


class KnowbotDataset(TimestampMixin, Base):
    """用户上传的一份数据（SDD 0027 决定 4）。内容在对象存储，这里只有元数据。"""

    __tablename__ = "knowbot_dataset"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    owner_subject: Mapped[str] = mapped_column(String(512), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[str] = mapped_column(String(16), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    object_key: Mapped[str] = mapped_column(String(256), nullable=False)
    version_id: Mapped[str] = mapped_column(String(256), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_knowbot_dataset_owner_subject", "owner_subject"),
        CheckConstraint("size_bytes > 0", name="ck_knowbot_dataset_size"),
    )


class KnowbotQuestion(TimestampMixin, Base):
    """测试中待人决定的问题（有人值守，SDD 0027 §3.4）。"""

    __tablename__ = "knowbot_question"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    draft_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowbot_draft.id", ondelete="RESTRICT"),
        nullable=False,
    )
    run_id: Mapped[str] = mapped_column(String(128), nullable=False)
    question_id: Mapped[str] = mapped_column(String(128), nullable=False)
    case_id: Mapped[str] = mapped_column(String(64), nullable=False)
    step_id: Mapped[str | None] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    key: Mapped[str | None] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    allow_free_text: Mapped[bool] = mapped_column(Boolean, nullable=False)
    asked_at: Mapped[str] = mapped_column(String(40), nullable=False)
    route: Mapped[str | None] = mapped_column(String(16))
    route_reason: Mapped[str | None] = mapped_column(Text)
    answer: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    answered_by: Mapped[str | None] = mapped_column(String(16))
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint(
            "draft_id", "run_id", "question_id", name="uq_knowbot_question_once"
        ),
        CheckConstraint(
            "kind IN ('tool_approval', 'question', 'ask_before')",
            name="ck_knowbot_question_kind",
        ),
        CheckConstraint(
            "(kind = 'tool_approval' AND route IS NULL)"
            " OR (kind <> 'tool_approval' AND route IS NOT NULL"
            " AND (route = 'user'"
            " OR (route = 'operator' AND route_reason IS NOT NULL)))",
            name="ck_knowbot_question_route",
        ),
        CheckConstraint(
            "answered_by IS NULL OR answered_by IN ('operator', 'user')",
            name="ck_knowbot_question_answered_by",
        ),
        Index("ix_knowbot_question_draft", "draft_id"),
    )


class KnowbotAmendment(TimestampMixin, Base):
    """用户给自己的想法写的补充方法（SDD 0035）。

    原话写下后不改、不删；只能记「不照做」。
    """

    __tablename__ = "knowbot_amendment"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowbot_request.id", ondelete="RESTRICT"),
        nullable=False,
    )
    owner_subject: Mapped[str] = mapped_column(String(512), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    declined_reason: Mapped[str | None] = mapped_column(Text)
    declined_by: Mapped[str | None] = mapped_column(String(512))
    declined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "char_length(text) BETWEEN 1 AND 4000", name="ck_knowbot_amendment_text"
        ),
        CheckConstraint(
            "(declined_at IS NULL AND declined_reason IS NULL AND declined_by IS NULL)"
            " OR (declined_at IS NOT NULL AND declined_by IS NOT NULL"
            " AND declined_reason IS NOT NULL"
            " AND char_length(declined_reason) BETWEEN 1 AND 500)",
            name="ck_knowbot_amendment_declined",
        ),
        Index("ix_knowbot_amendment_request", "request_id"),
    )


class KnowbotTestOutput(TimestampMixin, Base):
    """一次测试里一条用例的产出：成稿与各步交回物（SDD 0037）。

    运行方在用例跑完时取好，我们刷新时取一次存这里，跟着这一稿留着。运营看来验证这一稿，用户
    看自己的作验收依据。运行方没有（没取、旧版、已清空）时只记 note。
    """

    __tablename__ = "knowbot_test_output"

    draft_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowbot_draft.id", ondelete="CASCADE"),
        primary_key=True,
    )
    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    outputs: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    note: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (
        CheckConstraint(
            "outputs IS NOT NULL OR note IS NOT NULL", name="ck_knowbot_test_output_any"
        ),
    )
