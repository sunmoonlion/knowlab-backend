"""Persist the background poll order for active author trials."""

import sqlalchemy as sa

from alembic import op

revision = "20261128_0032"
down_revision = "20261127_0031"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "knowbot_test", sa.Column("last_polled_at", sa.DateTime(timezone=True))
    )
    op.execute(
        "CREATE INDEX ix_knowbot_test_poll ON knowbot_test "
        "(last_polled_at NULLS FIRST, id) "
        "WHERE status IN ('queued', 'running', 'waiting')"
    )


def downgrade():
    op.drop_index("ix_knowbot_test_poll", table_name="knowbot_test")
    op.drop_column("knowbot_test", "last_polled_at")
