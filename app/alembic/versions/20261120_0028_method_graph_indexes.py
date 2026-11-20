"""Index the external slots and direct dependencies of immutable formal versions."""

from alembic import op

revision = "20261120_0028"
down_revision = "20261115_0027"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE method_version_slots (
        method_id uuid NOT NULL, version integer NOT NULL,
        direction text NOT NULL CHECK (direction IN ('inputs','outputs')),
        name text NOT NULL, meaning jsonb NOT NULL, value_schema jsonb NOT NULL,
        guaranteed boolean NOT NULL,
        PRIMARY KEY (method_id,version,direction,name),
        FOREIGN KEY (method_id,version) REFERENCES knowbot_method_version ON DELETE CASCADE
    )""")
    op.execute("""CREATE INDEX ix_method_output_meaning ON method_version_slots
        USING hash (meaning) WHERE direction = 'outputs'""")
    op.execute("""CREATE TABLE method_dependencies (
        method_id uuid NOT NULL, version integer NOT NULL,
        child_id uuid NOT NULL, child_version integer NOT NULL, child_digest text NOT NULL,
        PRIMARY KEY (method_id,version,child_id,child_version),
        FOREIGN KEY (method_id,version) REFERENCES knowbot_method_version ON DELETE CASCADE
    )""")
    op.execute("""CREATE INDEX ix_method_dependency_child
        ON method_dependencies(child_id,child_version,child_digest)""")
    # Both the backfill and future freezes use the same projections.
    slots = """INSERT INTO method_version_slots
        SELECT v.method_id, v.version, side.key, item.key,
               item.value->'slot'->'meaning', item.value->'slot'->'schema',
               CASE WHEN side.key = 'outputs' THEN (item.value->>'always_present')::boolean
                    ELSE jsonb_array_length(item.value->'required_by') > 0 END
          FROM {source} v
          CROSS JOIN LATERAL jsonb_each(v.provenance->'interfaces') side
          CROSS JOIN LATERAL jsonb_each(side.value) item
         WHERE v.definition->>'contract' = 'knowlab/formal-method/v1'"""
    dependencies = """INSERT INTO method_dependencies
        SELECT v.method_id, v.version, (dep->>'method_id')::uuid,
               (dep->>'version')::integer, dep->>'digest'
          FROM {source} v
          CROSS JOIN LATERAL jsonb_array_elements(v.provenance->'dependencies') dep
         WHERE v.definition->>'contract' = 'knowlab/formal-method/v1'"""
    # Keep a concurrent freeze from landing between the backfill and the trigger.
    op.execute("LOCK TABLE knowbot_method_version IN SHARE ROW EXCLUSIVE MODE")
    op.execute(slots.format(source="knowbot_method_version"))
    op.execute(dependencies.format(source="knowbot_method_version"))
    op.execute(
        "CREATE FUNCTION index_formal_method_version() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
        + slots.format(source="(SELECT NEW.*)")
        + "; "
        + dependencies.format(source="(SELECT NEW.*)")
        + "; RETURN NEW; END $$"
    )
    op.execute("""CREATE TRIGGER index_formal_method AFTER INSERT ON knowbot_method_version
        FOR EACH ROW EXECUTE FUNCTION index_formal_method_version()""")


def downgrade():
    op.execute("DROP TRIGGER index_formal_method ON knowbot_method_version")
    op.execute("DROP FUNCTION index_formal_method_version()")
    op.drop_table("method_dependencies")
    op.drop_table("method_version_slots")
