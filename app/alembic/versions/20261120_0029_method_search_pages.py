"""Account for author-approved automatic directory reads across draft revisions."""

from alembic import op

revision = "20261120_0029"
down_revision = "20261120_0028"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE method_search_page (
        request_id uuid NOT NULL REFERENCES knowbot_request(id) ON DELETE CASCADE,
        ordinal integer NOT NULL CHECK (ordinal BETWEEN 1 AND 100),
        action_id uuid NOT NULL,
        search_key text NOT NULL,
        page_cursor text NOT NULL,
        state text NOT NULL CHECK (state IN ('pending','read','failed')),
        next_cursor text,
        candidates jsonb NOT NULL DEFAULT '[]',
        started_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (request_id, ordinal),
        UNIQUE (request_id, action_id)
    )""")


def downgrade():
    op.drop_table("method_search_page")
