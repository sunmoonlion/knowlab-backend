"""Freeze author test questions and assertions independently of method versions."""

from alembic import op

revision = "20261115_0027"
down_revision = "20261103_0026"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE formal_test_suites (
      suite_id uuid NOT NULL,
      version integer NOT NULL CHECK (version > 0),
      owner_subject varchar(512) NOT NULL,
      request_id uuid NOT NULL REFERENCES knowbot_request(id) ON DELETE RESTRICT,
      digest varchar(71) NOT NULL CHECK (digest ~ '^sha256:[0-9a-f]{64}$'),
      document jsonb NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY (suite_id,version)
    )""")


def downgrade():
    op.execute("""DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM formal_test_suites) THEN
        RAISE EXCEPTION 'frozen test evidence requires forward repair';
      END IF;
    END $$""")
    op.drop_table("formal_test_suites")
