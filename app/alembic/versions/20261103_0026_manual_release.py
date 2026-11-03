"""Auditable manual release without inventing a passed test."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "20261103_0026"
down_revision = "20261102_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowbot_expert", sa.Column("manual_release", pg.JSONB()))
    op.create_table(
        "knowbot_manual_release",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "request_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("knowbot_request.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "draft_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("knowbot_draft.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("owner_subject", sa.String(512), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column(
            "baseline_test_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("knowbot_test.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence", pg.JSONB(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("decided_by", sa.String(512)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decision_reason", sa.Text()),
        sa.Column(
            "expert_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("knowbot_expert.id", ondelete="RESTRICT"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="ck_manual_release_status",
        ),
        sa.CheckConstraint(
            "(status = 'pending') = (decided_at IS NULL)",
            name="ck_manual_release_decision",
        ),
        sa.CheckConstraint(
            "(status = 'approved') = (expert_id IS NOT NULL)",
            name="ck_manual_release_expert",
        ),
    )
    op.execute("""CREATE FUNCTION guard_manual_release_history() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'manual release history is immutable';
        END IF;
        IF OLD.status <> 'pending' OR
           (to_jsonb(NEW) - ARRAY['status','decided_by','decided_at',
                                     'decision_reason','expert_id'])
           IS DISTINCT FROM
           (to_jsonb(OLD) - ARRAY['status','decided_by','decided_at',
                                     'decision_reason','expert_id'])
        THEN RAISE EXCEPTION 'manual release scope or decision is immutable'; END IF;
        RETURN NEW; END; $$""")
    op.execute("""CREATE TRIGGER manual_release_immutable BEFORE UPDATE OR DELETE
        ON knowbot_manual_release FOR EACH ROW
        EXECUTE FUNCTION guard_manual_release_history()""")
    op.drop_constraint(
        "ck_knowbot_delivery_test_mode", "knowbot_delivery", type_="check"
    )
    op.create_check_constraint(
        "ck_knowbot_delivery_test_mode",
        "knowbot_delivery",
        "test_mode IN ('self','platform','unrun')",
    )


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(sa.text("SELECT EXISTS (SELECT 1 FROM knowbot_manual_release)"))
        .scalar()
    ):
        raise RuntimeError("cannot discard manual release evidence")
    op.drop_table("knowbot_manual_release")
    op.execute("DROP FUNCTION guard_manual_release_history()")
    op.drop_column("knowbot_expert", "manual_release")
    op.drop_constraint(
        "ck_knowbot_delivery_test_mode", "knowbot_delivery", type_="check"
    )
    op.create_check_constraint(
        "ck_knowbot_delivery_test_mode",
        "knowbot_delivery",
        "test_mode IN ('self','platform')",
    )
