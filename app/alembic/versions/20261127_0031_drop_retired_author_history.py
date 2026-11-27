"""Remove storage used exclusively by retired expert workflows."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from alembic import op

revision = "20261127_0031"
down_revision = "20261126_0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """UPDATE knowbot_workdraft
           SET body = '{}', sources = '{}', data_formats = '{}', htn_state = NULL, parent_gap = NULL
           WHERE method_id IS NOT NULL AND draft_contract IS NULL"""
    )
    op.drop_table("knowbot_amendment")
    op.drop_constraint(
        "ck_knowbot_request_reference_answer", "knowbot_request", type_="check"
    )
    op.drop_column("knowbot_request", "reference_answer")


def downgrade() -> None:
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
    op.add_column("knowbot_request", sa.Column("reference_answer", sa.Text()))
    op.create_check_constraint(
        "ck_knowbot_request_reference_answer",
        "knowbot_request",
        "reference_answer IS NULL OR char_length(reference_answer) <= 20000",
    )
