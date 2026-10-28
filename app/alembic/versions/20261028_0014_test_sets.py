"""测试集与测试里的 data 问题（SDD 0046 第三节，decisions ⑭）。

- `knowbot_question`：种类加 `data`（专家提出要哪些数据，作者本人确认）；
  `requests` 记提出的几样，`data_state` 记在 knowbase 解析、确认、补采到哪。
- `knowbot_test_set`：测试集 = 测试问题 + 作者确认的数据，按用例记，版本只增。
- `knowbot_test.test_set_version`、`case_grants`：这次带的是第几版测试集、
  每条用例签的授权。

Revision ID: 20261028_0014
Revises: 20261025_0013
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "20261028_0014"
down_revision = "20261025_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "knowbot_question",
        sa.Column("requests", pg.JSONB(), nullable=False, server_default="[]"),
    )
    op.add_column("knowbot_question", sa.Column("data_state", pg.JSONB()))
    op.drop_constraint("ck_knowbot_question_kind", "knowbot_question", type_="check")
    op.create_check_constraint(
        "ck_knowbot_question_kind",
        "knowbot_question",
        "kind IN ('tool_approval', 'question', 'ask_before', 'data')",
    )
    op.add_column("knowbot_test", sa.Column("test_set_version", sa.Integer()))
    # 带测试集发起时每条用例签的测试运行授权，测试结束撤
    op.add_column(
        "knowbot_test",
        sa.Column("case_grants", pg.JSONB(), nullable=False, server_default="[]"),
    )
    op.create_table(
        "knowbot_test_set",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "request_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("knowbot_request.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("cases", pg.JSONB(), nullable=False),
        sa.Column("from_test_id", pg.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "request_id", "version", name="uq_knowbot_test_set_version"
        ),
        sa.CheckConstraint("version >= 1", name="ck_knowbot_test_set_version"),
    )


def downgrade() -> None:
    op.drop_table("knowbot_test_set")
    op.drop_column("knowbot_test", "case_grants")
    op.drop_column("knowbot_test", "test_set_version")
    op.execute("DELETE FROM knowbot_question WHERE kind = 'data'")
    op.drop_constraint("ck_knowbot_question_kind", "knowbot_question", type_="check")
    op.create_check_constraint(
        "ck_knowbot_question_kind",
        "knowbot_question",
        "kind IN ('tool_approval', 'question', 'ask_before')",
    )
    op.drop_column("knowbot_question", "data_state")
    op.drop_column("knowbot_question", "requests")
