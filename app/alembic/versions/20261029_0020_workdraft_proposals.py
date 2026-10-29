"""运营对工作稿的提议（专家孵化方案第十二节；decisions 10-29「孵化第 5 步的三件」）。

平台孵化时运营在管理端用同一个编辑器改工作稿，改的都是提议：一条一格（形状同 AI 的
建议：路径、set / add / remove / move、值、理由），作者在 web端接受了才进稿、才算数
（接受记成作者的接受记录，出处照旧只认作者）。

- `knowbot_workdraft_proposal`：名称、路径、动作、值、理由、谁提的、什么时候；状态
  pending（等作者）、accepted、accepted_edited（改了再接受）、rejected（作者不要）、
  withdrawn（运营收回）、void（作者点了「我自己来」，没处理的作废）；作者处理时记下的
  工作稿记录编号。

downgrade 删表；提议不恢复。

Revision ID: 20261029_0020
Revises: 20261029_0019
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "20261029_0020"
down_revision = "20261029_0019"
branch_labels = None
depends_on = None

STATES = "('pending', 'accepted', 'accepted_edited', 'rejected', 'withdrawn', 'void')"


def upgrade() -> None:
    op.create_table(
        "knowbot_workdraft_proposal",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "request_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("knowbot_request.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("path", sa.String(200), nullable=False),
        sa.Column("op", sa.String(8), nullable=False),
        sa.Column("value", pg.JSONB()),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("proposed_by", sa.String(512), nullable=False),
        sa.Column("proposed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("records", pg.JSONB(), nullable=False, server_default="[]"),
        sa.CheckConstraint(
            "op IN ('set', 'add', 'remove', 'move')",
            name="ck_knowbot_workdraft_proposal_op",
        ),
        sa.CheckConstraint(
            f"state IN {STATES}", name="ck_knowbot_workdraft_proposal_state"
        ),
        sa.CheckConstraint(
            "(op = 'remove') = (value IS NULL)",
            name="ck_knowbot_workdraft_proposal_value",
        ),
        sa.CheckConstraint(
            "char_length(reason) > 0", name="ck_knowbot_workdraft_proposal_reason"
        ),
    )
    op.create_index(
        "ix_knowbot_workdraft_proposal_request",
        "knowbot_workdraft_proposal",
        ["request_id", "proposed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_knowbot_workdraft_proposal_request", "knowbot_workdraft_proposal")
    op.drop_table("knowbot_workdraft_proposal")
