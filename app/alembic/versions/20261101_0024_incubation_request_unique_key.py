"""incubation request origin key unique index

Revision ID: 20261101_0024
Revises: 20261030_0023
"""

from alembic import op
import sqlalchemy as sa


revision = "20261101_0024"
down_revision = "20261030_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX uq_knowbot_request_owner_origin_key "
        "ON knowbot_request (owner_subject, (origin->>'key')) "
        "WHERE origin IS NOT NULL AND (origin->>'key') IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_knowbot_request_owner_origin_key")
