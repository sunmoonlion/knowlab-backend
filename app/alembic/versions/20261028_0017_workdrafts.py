"""工作稿（专家孵化方案第五、九、十一节，decisions ⑮）。

- `knowbot_workdraft`：一个名称一份；方法层的格子（body）、每格的出处（sources：格子
  路径 → {kind, ref}）、revision（每存一次加 1，存时对不上回 409）。
- `knowbot_workdraft_record`：编辑、接受建议、导入、复制的记录，只增不改；格子的出处
  引它，原话核对拿它的原文逐字比。编辑、接受一格一条（带格子路径），导入、复制是整份。
- `knowbot_draft.mode`：platform（运营建的）或 self（作者从工作稿出的一版）；已有的
  准专家都是运营建的，回填 platform。

downgrade 删掉两张表与这一列，工作稿与记录不保留。

Revision ID: 20261028_0017
Revises: 20261028_0016
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "20261028_0017"
down_revision = "20261028_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "knowbot_draft",
        sa.Column("mode", sa.String(8), nullable=False, server_default="platform"),
    )
    op.create_check_constraint(
        "ck_knowbot_draft_mode", "knowbot_draft", "mode IN ('platform', 'self')"
    )
    op.create_table(
        "knowbot_workdraft",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "request_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("knowbot_request.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("owner_subject", sa.String(512), nullable=False),
        sa.Column("body", pg.JSONB(), nullable=False),
        sa.Column(
            "sources", pg.JSONB(), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
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
        sa.UniqueConstraint("request_id", name="uq_knowbot_workdraft_request"),
        sa.CheckConstraint("revision >= 0", name="ck_knowbot_workdraft_revision"),
    )
    op.create_table(
        "knowbot_workdraft_record",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workdraft_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("knowbot_workdraft.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("actor_subject", sa.String(512), nullable=False),
        sa.Column("actor_role", sa.String(8), nullable=False),
        sa.Column("path", sa.String(512)),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("suggestion_ref", sa.String(200)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('edit', 'accept', 'import', 'copy')",
            name="ck_knowbot_workdraft_record_kind",
        ),
        sa.CheckConstraint(
            "actor_role IN ('author', 'operator')",
            name="ck_knowbot_workdraft_record_role",
        ),
        sa.CheckConstraint(
            "(kind IN ('edit', 'accept')) = (path IS NOT NULL)",
            name="ck_knowbot_workdraft_record_path",
        ),
        sa.CheckConstraint(
            "suggestion_ref IS NULL OR kind = 'accept'",
            name="ck_knowbot_workdraft_record_suggestion",
        ),
    )
    op.create_index(
        "ix_knowbot_workdraft_record_workdraft",
        "knowbot_workdraft_record",
        ["workdraft_id"],
    )


def downgrade() -> None:
    """删掉工作稿与记录（不保留）和准专家的 mode 列。"""
    op.drop_index(
        "ix_knowbot_workdraft_record_workdraft", table_name="knowbot_workdraft_record"
    )
    op.drop_table("knowbot_workdraft_record")
    op.drop_table("knowbot_workdraft")
    op.drop_constraint("ck_knowbot_draft_mode", "knowbot_draft", type_="check")
    op.drop_column("knowbot_draft", "mode")
