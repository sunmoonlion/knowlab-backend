"""用户给自己的想法写的补充方法（SDD 0035）。

原话写下后不改、不删；运营只能记「不照做」的理由、谁、何时。补充用进哪一稿从各稿清单的
规则出处推（`amendment:<id>`），这里不另存。

CHECK 里先判非空再比长度：理由为空时长度比较得 NULL，CHECK 会当它过。
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "20261019_0008"
down_revision = "20261018_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowbot_amendment",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "request_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("knowbot_request.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("owner_subject", sa.String(512), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("declined_reason", sa.Text()),
        sa.Column("declined_by", sa.String(512)),
        sa.Column("declined_at", sa.DateTime(timezone=True)),
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
        sa.CheckConstraint(
            "char_length(text) BETWEEN 1 AND 4000", name="ck_knowbot_amendment_text"
        ),
        sa.CheckConstraint(
            "(declined_at IS NULL AND declined_reason IS NULL AND declined_by IS NULL)"
            " OR (declined_at IS NOT NULL AND declined_by IS NOT NULL"
            " AND declined_reason IS NOT NULL"
            " AND char_length(declined_reason) BETWEEN 1 AND 500)",
            name="ck_knowbot_amendment_declined",
        ),
    )
    op.create_index("ix_knowbot_amendment_request", "knowbot_amendment", ["request_id"])


def downgrade() -> None:
    op.drop_index("ix_knowbot_amendment_request", table_name="knowbot_amendment")
    op.drop_table("knowbot_amendment")
