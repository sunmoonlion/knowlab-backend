"""一次测试里每条用例的产出：成稿与各步交回物（SDD 0037）。

运行方在用例跑完时取好；我们刷新时每条用例取一次存这里，跟着这一稿留着。同一稿重测（超时
退回后再测）换了 run_id，所以主键带 run_id。运行方没有的只记 note。
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "20261019_0009"
down_revision = "20261019_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowbot_test_output",
        sa.Column(
            "draft_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("knowbot_draft.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("run_id", sa.String(128), primary_key=True),
        sa.Column("case_id", sa.String(64), primary_key=True),
        sa.Column("outputs", pg.JSONB()),
        sa.Column("note", sa.String(500)),
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
            "outputs IS NOT NULL OR note IS NOT NULL", name="ck_knowbot_test_output_any"
        ),
    )


def downgrade() -> None:
    op.drop_table("knowbot_test_output")
