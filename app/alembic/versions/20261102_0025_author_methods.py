"""Independent author methods with append-only versions.

Revision ID: 20261102_0025
Revises: 20261101_0024
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20261102_0025"
down_revision = "20261101_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowbot_method",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_subject", sa.String(512), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_knowbot_method_owner",
        "knowbot_method",
        ["owner_subject", "created_at", "id"],
    )
    op.create_table(
        "knowbot_method_version",
        sa.Column(
            "method_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowbot_method.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("digest", sa.String(71), nullable=False),
        sa.Column("command_digest", sa.String(71), nullable=False),
        sa.Column("definition", postgresql.JSONB(), nullable=False),
        sa.Column("provenance", postgresql.JSONB(), nullable=False),
        sa.Column(
            "source_workdraft_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowbot_workdraft.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("version > 0", name="ck_knowbot_method_version_positive"),
        sa.CheckConstraint(
            "digest ~ '^sha256:[0-9a-f]{64}$' "
            "AND command_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_knowbot_method_version_digests",
        ),
    )
    op.execute("""CREATE FUNCTION reject_method_version_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
        RAISE EXCEPTION 'author method versions are immutable'; END; $$""")
    op.execute("""CREATE TRIGGER knowbot_method_version_immutable
        BEFORE UPDATE OR DELETE ON knowbot_method_version
        FOR EACH ROW EXECUTE FUNCTION reject_method_version_mutation()""")


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(sa.text("SELECT EXISTS (SELECT 1 FROM knowbot_method)"))
        .scalar()
    ):
        raise RuntimeError("cannot discard author method history")
    op.drop_table("knowbot_method_version")
    op.execute("DROP FUNCTION reject_method_version_mutation()")
    op.drop_index("ix_knowbot_method_owner", table_name="knowbot_method")
    op.drop_table("knowbot_method")
