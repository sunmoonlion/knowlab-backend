"""自助测试（SDD 0046 第三节「怎么选电脑」）：测试记模式与电脑。

`mode`：platform（运营在平台的测试电脑上跑）或 self（用户本人在他自己的电脑上跑）；
`environment_id`、`workspace_root`：自助的那台电脑与放测试项目的根目录，下次默认用它。

Revision ID: 20261028_0015
Revises: 20261028_0014
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20261028_0015"
down_revision = "20261028_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "knowbot_test",
        sa.Column("mode", sa.String(8), nullable=False, server_default="platform"),
    )
    op.add_column("knowbot_test", sa.Column("environment_id", sa.String(36)))
    op.add_column("knowbot_test", sa.Column("workspace_root", sa.Text()))
    op.create_check_constraint(
        "ck_knowbot_test_mode",
        "knowbot_test",
        "mode IN ('platform', 'self')"
        " AND (mode = 'platform' OR environment_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_knowbot_test_mode", "knowbot_test", type_="check")
    op.drop_column("knowbot_test", "workspace_root")
    op.drop_column("knowbot_test", "environment_id")
    op.drop_column("knowbot_test", "mode")
