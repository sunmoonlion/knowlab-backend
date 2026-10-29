"""创意、受理、准专家、测试、专家（SDD 0027、0037）。孵化与测试都在 knowlab。

代码里仍叫 request（创意的名称）、draft（准专家）；创意版本、受理单、测试、专家各
一张表，都只插、结论定了不改（SDD 0037 第十八之二节）。准专家上的 run、报告、交付
几栏是 0012 之前的，0012 起不再写，内容搬进测试与专家表，留到收缩那一次迁移再删。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import text as sql_text  # 问题表有一栏叫 text，类里用这个名字
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.models.base import Base, TimestampMixin

# 'delivered' 0012 起不再用（回填成 passed），约束留到收缩迁移再收
DRAFT_STATUSES = "('pending_test', 'testing', 'passed', 'failed', 'delivered')"
TEST_STATUSES = "('queued', 'running', 'waiting', 'passed', 'failed', 'platform_error')"
ACTIVE_TESTS = "('queued', 'running', 'waiting')"
# 孵化一轮的状态照契约 knowbot-incubation/v1 的结果状态
INCUBATION_STATUSES = (
    "('queued', 'running', 'waiting', 'succeeded', 'failed', 'over_budget',"
    " 'cancelled')"
)
ACTIVE_INCUBATIONS = "('queued', 'running', 'waiting')"


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
    # 现在的孵化是自助（self）还是平台（platform）（SDD 0046 第四节）
    incubation_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="self"
    )

    __table_args__ = (
        Index("ix_knowbot_request_owner_subject", "owner_subject"),
        CheckConstraint(
            "incubation_mode IN ('self', 'platform')",
            name="ck_knowbot_request_incubation_mode",
        ),
    )


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
    # formal：正式受理；self：「自助（不受理）」（SDD 0046 第二节）
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="formal"
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('formal', 'self')", name="ck_knowbot_acceptance_kind"
        ),
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
    # 出自创意第几版（SDD 0037）
    request_version: Mapped[int] = mapped_column(Integer, nullable=False)
    # 请用户审核：{requested_by, requested_at, ok, note, answered_at}
    user_review: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # platform（运营建的）或 self（作者从工作稿出的一版，专家孵化方案第十一节）
    mode: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default="platform"
    )

    __table_args__ = (
        UniqueConstraint("request_id", "revision", name="uq_knowbot_draft_revision"),
        CheckConstraint("mode IN ('platform', 'self')", name="ck_knowbot_draft_mode"),
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
    started_by: Mapped[str | None] = mapped_column(String(512))
    # 这次带的测试集是第几版（SDD 0046 第三节）；没带的（第一次测）是空的
    test_set_version: Mapped[int | None] = mapped_column(Integer)
    # 带测试集发起时每条用例签的测试运行授权，测试结束撤
    case_grants: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    # 自助（用户本人在他自己的电脑上跑）还是平台（SDD 0046 第三节）；
    # 自助的记电脑与根目录
    mode: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default="platform"
    )
    environment_id: Mapped[str | None] = mapped_column(String(36))
    workspace_root: Mapped[str | None] = mapped_column(Text)
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
    # 没上架的结局送到 knowmart 的时刻；空着就是还要送（定时补发，Astra 评审 F08）
    unlisted_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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


class KnowbotQuestion(TimestampMixin, Base):
    """待人决定的问题：测试中的（有人值守，SDD 0027 §3.4），挂在准专家上；孵化的缺口
    （kind gap，专家孵化方案第 8.4 节），挂在孵化的那一轮与名称上。两样只挂一样。"""

    __tablename__ = "knowbot_question"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    draft_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowbot_draft.id", ondelete="RESTRICT")
    )
    incubation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowbot_incubation.id", ondelete="RESTRICT")
    )
    request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowbot_request.id", ondelete="RESTRICT")
    )
    # 缺口附的提议（作者可以答「按提议」）
    proposal: Mapped[str | None] = mapped_column(Text)
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
    # data 问题（SDD 0046 第三节）：专家提出的几样，与在 knowbase 解析、确认、补采到哪
    requests: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    data_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (
        UniqueConstraint(
            "draft_id", "run_id", "question_id", name="uq_knowbot_question_once"
        ),
        CheckConstraint(
            "kind IN ('tool_approval', 'question', 'ask_before', 'data', 'gap')",
            name="ck_knowbot_question_kind",
        ),
        # 测试中的挂在准专家上；缺口挂在孵化的那一轮与名称上
        CheckConstraint(
            "(kind = 'gap' AND draft_id IS NULL AND incubation_id IS NOT NULL"
            " AND request_id IS NOT NULL)"
            " OR (kind <> 'gap' AND draft_id IS NOT NULL AND incubation_id IS NULL"
            " AND request_id IS NULL)",
            name="ck_knowbot_question_owner",
        ),
        Index(
            "uq_knowbot_question_gap_once",
            "incubation_id",
            "question_id",
            unique=True,
            postgresql_where=sql_text("incubation_id IS NOT NULL"),
        ),
        Index("ix_knowbot_question_request", "request_id"),
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


class KnowbotTestSet(Base):
    """测试集（SDD 0046 第三节，decisions ⑭）：测试问题 + 作者确认的数据，按用例记，
    同一名称里版本只增。cases：{用例编号: {"items": [钉住的一份]}}。"""

    __tablename__ = "knowbot_test_set"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowbot_request.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    cases: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    from_test_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("request_id", "version", name="uq_knowbot_test_set_version"),
        CheckConstraint("version >= 1", name="ck_knowbot_test_set_version"),
    )


class KnowbotWorkdraft(TimestampMixin, Base):
    """工作稿（专家孵化方案第五节）：一个名称一份，方法层的格子与每格的出处。

    body：方法层（WorkdraftBody）；sources：格子路径 → {kind, ref}；revision 每存一次
    加 1，存时带上读到的，对不上回 409。
    """

    __tablename__ = "knowbot_workdraft"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowbot_request.id", ondelete="RESTRICT"),
        nullable=False,
    )
    owner_subject: Mapped[str] = mapped_column(String(512), nullable=False)
    body: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    sources: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    __table_args__ = (
        UniqueConstraint("request_id", name="uq_knowbot_workdraft_request"),
        CheckConstraint("revision >= 0", name="ck_knowbot_workdraft_revision"),
    )


class KnowbotWorkdraftRecord(Base):
    """工作稿的一条记录：编辑、接受建议、导入、复制（专家孵化方案第 9.1 节）。只增不改。

    格子的出处引它（`record:<id>`），原话核对拿格子文字与 text 逐字比；导入、复制的 text
    记着拆出的每一格。
    """

    __tablename__ = "knowbot_workdraft_record"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    workdraft_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowbot_workdraft.id", ondelete="RESTRICT"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    actor_subject: Mapped[str] = mapped_column(String(512), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(8), nullable=False)
    path: Mapped[str | None] = mapped_column(String(512))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # 接受的是哪一条建议（以后 AI、运营的建议）
    suggestion_ref: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('edit', 'accept', 'import', 'copy')",
            name="ck_knowbot_workdraft_record_kind",
        ),
        CheckConstraint(
            "actor_role IN ('author', 'operator')",
            name="ck_knowbot_workdraft_record_role",
        ),
        # 编辑、接受是一格一条；导入、复制是整份
        CheckConstraint(
            "(kind IN ('edit', 'accept')) = (path IS NOT NULL)",
            name="ck_knowbot_workdraft_record_path",
        ),
        CheckConstraint(
            "suggestion_ref IS NULL OR kind = 'accept'",
            name="ck_knowbot_workdraft_record_suggestion",
        ),
        Index("ix_knowbot_workdraft_record_workdraft", "workdraft_id"),
    )


class KnowbotIncubation(TimestampMixin, Base):
    """AI 孵化的一轮（专家孵化方案第 8.2 节，0029 第 6.6 节）：作者点「AI 起草」或
    「让 AI 改…」一次是一轮，经 knowbench:incubate 交给运行方，在作者的电脑上、用他的
    key 跑。

    同一份工作稿同时只有一轮没结束。交出去的种子（创意第几版与拼成的原文）、带去的答复、
    工作稿的 revision 都记着，取回后照它们核出处（第九节）；handback 是交回的四样，
    review 是 knowlab 核过的每条建议，decisions 是作者对每条建议的处理。
    """

    __tablename__ = "knowbot_incubation"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowbot_request.id", ondelete="RESTRICT"),
        nullable=False,
    )
    workdraft_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowbot_workdraft.id", ondelete="RESTRICT"),
        nullable=False,
    )
    owner_subject: Mapped[str] = mapped_column(String(512), nullable=False)
    round: Mapped[int] = mapped_column(Integer, nullable=False)
    # 方式：self 自助 AI（作者自己点的）；platform 留给以后的平台孵化
    mode: Mapped[str] = mapped_column(String(8), nullable=False, server_default="self")
    action: Mapped[str] = mapped_column(String(8), nullable=False)
    instruction: Mapped[str | None] = mapped_column(Text)
    step_id: Mapped[str | None] = mapped_column(String(48))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    budget_limit: Mapped[str] = mapped_column(String(16), nullable=False)
    spent: Mapped[str | None] = mapped_column(String(24))
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    # 交给运行方的编号（契约的 incubation_id），与运行方那边的委托号
    run_id: Mapped[str] = mapped_column(String(128), nullable=False)
    task_id: Mapped[str | None] = mapped_column(String(36))
    environment_id: Mapped[str] = mapped_column(String(36), nullable=False)
    workspace_root: Mapped[str] = mapped_column(Text, nullable=False)
    waiting_for: Mapped[str | None] = mapped_column(String(32))
    request_version: Mapped[int] = mapped_column(Integer, nullable=False)
    seed: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    workdraft_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    answer_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    error: Mapped[str | None] = mapped_column(Text)
    handback: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    review: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    decisions: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("request_id", "round", name="uq_knowbot_incubation_round"),
        UniqueConstraint("run_id", name="uq_knowbot_incubation_run"),
        CheckConstraint("round >= 1", name="ck_knowbot_incubation_round"),
        CheckConstraint(
            "mode IN ('self', 'platform')", name="ck_knowbot_incubation_mode"
        ),
        CheckConstraint(
            "action IN ('draft', 'revise')", name="ck_knowbot_incubation_action"
        ),
        # 按指令改才有那句指令；起草没有
        CheckConstraint(
            "(action = 'revise') = (instruction IS NOT NULL)",
            name="ck_knowbot_incubation_instruction",
        ),
        CheckConstraint(
            f"status IN {INCUBATION_STATUSES}", name="ck_knowbot_incubation_status"
        ),
        CheckConstraint(
            "(status = 'succeeded') = (handback IS NOT NULL)",
            name="ck_knowbot_incubation_handback",
        ),
        # 同一份工作稿同时只有一轮没结束
        Index(
            "uq_knowbot_incubation_one_active",
            "workdraft_id",
            unique=True,
            postgresql_where=text(f"status IN {ACTIVE_INCUBATIONS}"),
        ),
        Index("ix_knowbot_incubation_owner", "owner_subject"),
    )


class KnowbotPlatformGrant(Base):
    """作者给平台的授权（迁移 20261029_0019；domain/knowbots/platform_grants.py）。"""

    __tablename__ = "knowbot_platform_grant"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowbot_request.id", ondelete="RESTRICT"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    draft_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowbot_draft.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    budget_limit: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    granted_by: Mapped[str] = mapped_column(String(512), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(String(512))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text)
    ended_by: Mapped[str | None] = mapped_column(String(512))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "kind IN ('incubate', 'test')", name="ck_knowbot_platform_grant_kind"
        ),
        CheckConstraint(
            "status IN ('pending', 'active', 'declined', 'revoked', 'ended',"
            " 'expired')",
            name="ck_knowbot_platform_grant_status",
        ),
        CheckConstraint(
            "(kind = 'test') = (draft_id IS NOT NULL)",
            name="ck_knowbot_platform_grant_draft",
        ),
        CheckConstraint("budget_limit > 0", name="ck_knowbot_platform_grant_budget"),
        Index("ix_knowbot_platform_grant_request", "request_id"),
        Index(
            "uq_knowbot_platform_grant_one_incubation",
            "request_id",
            unique=True,
            postgresql_where=text(
                "kind = 'incubate' AND status IN ('pending', 'active')"
            ),
        ),
        Index(
            "uq_knowbot_platform_grant_one_test",
            "draft_id",
            unique=True,
            postgresql_where=text("kind = 'test' AND status IN ('pending', 'active')"),
        ),
    )
