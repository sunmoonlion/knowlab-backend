"""Support standalone method-first Five-Element drafts and slot domain prefix lookup."""

from alembic import op

revision = "20261126_0030"
down_revision = "20261120_0029"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE knowbot_workdraft ALTER COLUMN request_id DROP NOT NULL")
    op.execute(
        """ALTER TABLE knowbot_workdraft
           ADD COLUMN method_id uuid REFERENCES knowbot_method(id) ON DELETE RESTRICT,
           ADD COLUMN draft_contract jsonb,
           ADD COLUMN htn_state jsonb,
           ADD COLUMN parent_gap jsonb"""
    )
    op.execute(
        """ALTER TABLE knowbot_workdraft
           ADD CONSTRAINT ck_knowbot_workdraft_anchor
           CHECK (request_id IS NOT NULL OR method_id IS NOT NULL)"""
    )
    op.execute(
        """CREATE UNIQUE INDEX uq_knowbot_workdraft_method
           ON knowbot_workdraft (method_id)
           WHERE method_id IS NOT NULL AND request_id IS NULL"""
    )
    op.execute("DROP INDEX IF EXISTS ix_method_output_meaning")
    op.execute(
        """CREATE INDEX ix_method_output_domain
           ON method_version_slots ((meaning->>'domain_id') text_pattern_ops)
           WHERE direction = 'outputs'"""
    )
    op.execute(
        """CREATE INDEX ix_method_output_meaning_gin
           ON method_version_slots USING gin (meaning)
           WHERE direction = 'outputs'"""
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_method_output_meaning_gin")
    op.execute("DROP INDEX IF EXISTS ix_method_output_domain")
    op.execute(
        """CREATE INDEX ix_method_output_meaning ON method_version_slots
           USING hash (meaning) WHERE direction = 'outputs'"""
    )
    op.execute("DROP INDEX IF EXISTS uq_knowbot_workdraft_method")
    op.execute(
        "ALTER TABLE knowbot_workdraft DROP CONSTRAINT ck_knowbot_workdraft_anchor"
    )
    op.execute(
        """ALTER TABLE knowbot_workdraft
           DROP COLUMN parent_gap,
           DROP COLUMN htn_state,
           DROP COLUMN draft_contract,
           DROP COLUMN method_id"""
    )
    op.execute("DELETE FROM knowbot_workdraft WHERE request_id IS NULL")
    op.execute("ALTER TABLE knowbot_workdraft ALTER COLUMN request_id SET NOT NULL")
