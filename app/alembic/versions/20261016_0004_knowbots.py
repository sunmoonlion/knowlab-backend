"""分身的提交与草稿（SDD 0027 P3）。"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "20261016_0004"
down_revision = "20260911_0003"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "knowbot_request",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),  # 由应用生成
        sa.Column("owner_subject", sa.String(512), nullable=False),
        sa.Column("title", sa.String(80), nullable=False),
        sa.Column("runtime", sa.String(32), nullable=False),
        sa.Column("idea", pg.JSONB(), nullable=False),
        sa.Column("cases", pg.JSONB(), nullable=False),
        *_timestamps(),
    )
    op.create_index(
        "ix_knowbot_request_owner_subject", "knowbot_request", ["owner_subject"]
    )
    op.create_table(
        "knowbot_draft",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),  # 由应用生成
        sa.Column(
            "request_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("knowbot_request.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("version", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("manifest", pg.JSONB(), nullable=False),
        sa.Column("rules_reviewed_by", sa.String(512), nullable=True),
        sa.Column("rules_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_id", sa.String(128), nullable=True),
        sa.Column("run_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("run_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_timeout_seconds", sa.Integer(), nullable=True),
        sa.Column("report", pg.JSONB(), nullable=True),
        sa.Column("delivery", pg.JSONB(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("request_id", "revision", name="uq_knowbot_draft_revision"),
        sa.CheckConstraint(
            "status IN ('pending_test', 'testing', 'passed', 'failed', 'delivered')",
            name="ck_knowbot_draft_status",
        ),
    )
    op.create_index("ix_knowbot_draft_status", "knowbot_draft", ["status"])


def downgrade() -> None:
    op.drop_index("ix_knowbot_draft_status", table_name="knowbot_draft")
    op.drop_table("knowbot_draft")
    op.drop_index("ix_knowbot_request_owner_subject", table_name="knowbot_request")
    op.drop_table("knowbot_request")
