"""Bind each formal trial to its immutable author request before dispatch."""

from alembic import op

revision = "20261128_0033"
down_revision = "20261128_0032"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE formal_trial_bindings (
        run_id uuid PRIMARY KEY, request_digest varchar(71) NOT NULL,
        suite_id uuid NOT NULL, suite_version integer NOT NULL,
        FOREIGN KEY (suite_id,suite_version)
            REFERENCES formal_test_suites(suite_id,version)
    )""")


def downgrade():
    op.drop_table("formal_trial_bindings")
