"""创意记下用户同意的是哪一版测试数据说明（SDD 0037，审读 10-19）。

老创意是在第 1 版（「测试产出不给任何人看，7 天后删除」）下同意的，回填 1；新提交写 2。
产出只为第 2 版取、给人看。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20261019_0010"
down_revision = "20261019_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "knowbot_request",
        sa.Column("consent_version", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("knowbot_request", "consent_version")
