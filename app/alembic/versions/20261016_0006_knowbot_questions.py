"""测试中待人决定的问题（SDD 0027 §3.4，有人值守）。"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "20261016_0006"
down_revision = "20261016_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowbot_question",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "draft_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("knowbot_draft.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("question_id", sa.String(128), nullable=False),
        sa.Column("case_id", sa.String(64), nullable=False),
        sa.Column("step_id", sa.String(128)),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("key", sa.String(64)),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("options", pg.JSONB(), nullable=False),
        sa.Column("allow_free_text", sa.Boolean(), nullable=False),
        sa.Column("asked_at", sa.String(40), nullable=False),
        sa.Column("route", sa.String(16)),
        sa.Column("answer", pg.JSONB()),
        sa.Column("answered_by", sa.String(16)),
        sa.Column("answered_at", sa.DateTime(timezone=True)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
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
        sa.UniqueConstraint(
            "draft_id", "run_id", "question_id", name="uq_knowbot_question_once"
        ),
        sa.CheckConstraint(
            "kind IN ('tool_approval', 'question', 'ask_before')",
            name="ck_knowbot_question_kind",
        ),
        sa.CheckConstraint(
            "route IS NULL OR (route = 'user' AND kind <> 'tool_approval')",
            name="ck_knowbot_question_route",
        ),
        sa.CheckConstraint(
            "answered_by IS NULL OR answered_by IN ('operator', 'user')",
            name="ck_knowbot_question_answered_by",
        ),
    )
    op.create_index("ix_knowbot_question_draft", "knowbot_question", ["draft_id"])


def downgrade() -> None:
    op.drop_index("ix_knowbot_question_draft", table_name="knowbot_question")
    op.drop_table("knowbot_question")
