"""创意、受理、准专家、测试、专家（SDD 0027、0037）。孵化与测试都在 knowlab。

代码里仍叫 request（创意的名称）、draft（准专家）；创意版本、受理单、测试、专家各
一张表，都只插、结论定了不改（SDD 0037 第十八之二节）。准专家上的 run、报告、交付
几栏是 0012 之前的，0012 起不再写，内容搬进测试与专家表，留到收缩那一次迁移再删。
"""

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
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.models.base import Base, TimestampMixin

# 'delivered' 0012 起不再用（回填成 passed），约束留到收缩迁移再收
DRAFT_STATUSES = "('pending_test', 'testing', 'passed', 'failed', 'delivered')"
TEST_STATUSES = "('queued', 'running', 'waiting', 'passed', 'failed', 'platform_error')"
ACTIVE_TESTS = "('queued', 'running', 'waiting')"


class KnowbotRequest(TimestampMixin, Base):
    __tablename__ = "knowbot_request"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    owner_subject: Mapped[str] = mapped_column(String(512), nullable=False)
    title: Mapped[str] = mapped_column(String(80), nullable=False)
    runtime: Mapped[str] = mapped_column(String(32), nullable=False)
    idea: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    cases: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    # 同意的是哪一版测试数据说明（domain/knowbots/drafts.py CONSENT_VERSION）
    consent_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    # 最新的创意版本；idea、cases 是它的内容（镜像，旧读路径用）
    latest_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    # 测试中用户答过的开步前提问：{用例: {键: 答案}}，开跑时并进用例，重测不再问
    case_answers: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )

    __table_args__ = (Index("ix_knowbot_request_owner_subject", "owner_subject"),)


class KnowbotRequestVersion(Base):
    """创意的一个版本：方法、用例、同意的测试数据说明。只插不改。"""

    __tablename__ = "knowbot_request_version"

    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowbot_request.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    idea: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    cases: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    consent_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_knowbot_request_version_positive"),
    )


class KnowbotAcceptance(Base):
    """受理单：一个创意版本一张，三样各自的结论、受理与否、理由。"""

    __tablename__ = "knowbot_acceptance"

    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    checks: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[str] = mapped_column(String(512), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    trial: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    __table_args__ = (
        ForeignKeyConstraint(
            ["request_id", "version"],
            ["knowbot_request_version.request_id", "knowbot_request_version.version"],
            ondelete="RESTRICT",
            name="fk_knowbot_acceptance_version",
        ),
        CheckConstraint(
            "accepted OR (reason IS NOT NULL AND char_length(reason) > 0)",
            name="ck_knowbot_acceptance_reason",
        ),
    )


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
    # 这次测试的运行授权（SDD 0042 第七节）：knowbase 签给运行方，测试结束撤
    run_grant_id: Mapped[str | None] = mapped_column(String(64))
    # 出自创意第几版（SDD 0037）
    request_version: Mapped[int] = mapped_column(Integer, nullable=False)
    # 请用户审核：{requested_by, requested_at, ok, note, answered_at}
    user_review: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (
        UniqueConstraint("request_id", "revision", name="uq_knowbot_draft_revision"),
        ForeignKeyConstraint(
            ["request_id", "request_version"],
            ["knowbot_request_version.request_id", "knowbot_request_version.version"],
            ondelete="RESTRICT",
            name="fk_knowbot_draft_request_version",
        ),
        CheckConstraint(f"status IN {DRAFT_STATUSES}", name="ck_knowbot_draft_status"),
        Index("ix_knowbot_draft_status", "status"),
    )


class KnowbotTest(Base):
    """一次测试：拿一个准专家按用例跑一次。编号在同一个名称里只增。"""

    __tablename__ = "knowbot_test"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowbot_request.id", ondelete="RESTRICT"),
        nullable=False,
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    draft_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowbot_draft.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    budget_limit: Mapped[str | None] = mapped_column(String(16))  # 回填的不知道
    timeout_seconds: Mapped[int | None] = mapped_column(Integer)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False)
    grant_id: Mapped[str | None] = mapped_column(String(64))
    started_by: Mapped[str | None] = mapped_column(String(512))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("request_id", "number", name="uq_knowbot_test_number"),
        UniqueConstraint("run_id", name="uq_knowbot_test_run"),
        CheckConstraint(f"status IN {TEST_STATUSES}", name="ck_knowbot_test_status"),
        CheckConstraint("number >= 1", name="ck_knowbot_test_number_positive"),
        # 同一个名称同时只有一个在跑
        Index(
            "uq_knowbot_test_one_active",
            "request_id",
            unique=True,
            postgresql_where=text(f"status IN {ACTIVE_TESTS}"),
        ),
        Index("ix_knowbot_test_draft", "draft_id"),
    )


class KnowbotExpert(Base):
    """一版专家：测试通过即是。上架前两道门（先用户、后运营）的结论都在这里。"""

    __tablename__ = "knowbot_expert"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowbot_request.id", ondelete="RESTRICT"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    draft_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowbot_draft.id", ondelete="RESTRICT"),
        nullable=False,
    )
    test_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowbot_test.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    returned_by: Mapped[str | None] = mapped_column(String(512))
    returned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_accepted: Mapped[bool | None] = mapped_column(Boolean)
    user_decided_by: Mapped[str | None] = mapped_column(String(512))
    user_decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_approved: Mapped[bool | None] = mapped_column(Boolean)
    review_listing: Mapped[str | None] = mapped_column(String(8))
    review_reason: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[str | None] = mapped_column(String(512))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    listed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    knowmart_knowbot_id: Mapped[str | None] = mapped_column(String(64))
    digest: Mapped[str | None] = mapped_column(String(80))
    # knowmart 的回执（版本号、摘要、上架状态）
    delivery: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    delivery_error: Mapped[str | None] = mapped_column(Text)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stopped_by: Mapped[str | None] = mapped_column(String(512))
    stop_reason: Mapped[str | None] = mapped_column(Text)
    trial: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    __table_args__ = (
        UniqueConstraint("request_id", "version", name="uq_knowbot_expert_version"),
        UniqueConstraint("draft_id", name="uq_knowbot_expert_draft"),
        CheckConstraint("version >= 1", name="ck_knowbot_expert_version_positive"),
        CheckConstraint(
            "review_listing IS NULL OR review_listing IN ('self', 'public')",
            name="ck_knowbot_expert_listing",
        ),
        CheckConstraint(
            "review_approved IS NOT TRUE OR review_listing IS NOT NULL",
            name="ck_knowbot_expert_approved_listing",
        ),
        CheckConstraint(
            "review_approved IS NOT FALSE"
            " OR (review_reason IS NOT NULL AND char_length(review_reason) > 0)",
            name="ck_knowbot_expert_rejected_reason",
        ),
        CheckConstraint(
            "stopped_at IS NULL"
            " OR (stopped_by IS NOT NULL AND stop_reason IS NOT NULL)",
            name="ck_knowbot_expert_stopped",
        ),
        Index("ix_knowbot_expert_knowmart", "knowmart_knowbot_id", "version"),
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
    # knowlab 自己的旧对象（2026-10-23 前传的）；新传的只在 knowbase，这两栏为空
    object_key: Mapped[str | None] = mapped_column(String(256))
    version_id: Mapped[str | None] = mapped_column(String(256))
    # knowbase 里 knowlab 分区的那份（SDD 0042 第七节）
    knowbase_item_id: Mapped[str | None] = mapped_column(String(64))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_knowbot_dataset_owner_subject", "owner_subject"),
        CheckConstraint("size_bytes > 0", name="ck_knowbot_dataset_size"),
        CheckConstraint(
            "knowbase_item_id IS NOT NULL"
            " OR (object_key IS NOT NULL AND version_id IS NOT NULL)",
            name="ck_knowbot_dataset_somewhere",
        ),
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


class KnowbotTestConsent(Base):
    """每个用户一条测试同意（SDD 0042 第七节）：范围是他 knowlab 知识库里的全部资料。"""

    __tablename__ = "knowbot_test_consent"

    owner_subject: Mapped[str] = mapped_column(String(512), primary_key=True)
    consent_id: Mapped[str] = mapped_column(String(64), nullable=False)
    items_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
