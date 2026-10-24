"""knowlab 知识库搬进 knowbase（SDD 0042 第七节）。

- 数据记录加 knowbase 的资料编号；新传的只在 knowbase，没有 knowlab 自己的对象（对象键、版本可空）。
  旧对象留到「财务分析与预测」第 1 版停用（第 1 版运行时照旧经 knowlab 读）。
- 测试同意：每个用户一条，范围是他 knowlab 知识库里的全部资料；记 knowbase 的同意编号与那时的范围摘要。
  换下来的旧同意记进待撤，撤成了才划掉（knowbase 有没撤的同意含着一份就不让删它）。
- 草稿记这次测试的运行授权编号，测试结束撤。

Revision ID: 20261023_0011
Revises: 20261019_0010
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20261023_0011"
down_revision = "20261019_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowbot_dataset", sa.Column("knowbase_item_id", sa.String(64)))
    op.create_index(
        "uq_knowbot_dataset_knowbase_item",
        "knowbot_dataset",
        ["knowbase_item_id"],
        unique=True,
        postgresql_where=sa.text("knowbase_item_id IS NOT NULL"),
    )
    op.alter_column("knowbot_dataset", "object_key", nullable=True)
    op.alter_column("knowbot_dataset", "version_id", nullable=True)
    op.create_check_constraint(
        "ck_knowbot_dataset_somewhere",
        "knowbot_dataset",
        "knowbase_item_id IS NOT NULL OR (object_key IS NOT NULL AND version_id IS NOT NULL)",
    )
    op.add_column("knowbot_draft", sa.Column("run_grant_id", sa.String(64)))
    op.create_table(
        "knowbot_test_consent",
        sa.Column("owner_subject", sa.String(512), primary_key=True),
        sa.Column("consent_id", sa.String(64), nullable=False),
        sa.Column("items_digest", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_table(
        "knowbot_consent_withdrawal",
        sa.Column("consent_id", sa.String(64), primary_key=True),
        sa.Column("owner_subject", sa.String(512), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_knowbot_consent_withdrawal_owner_subject",
        "knowbot_consent_withdrawal",
        ["owner_subject"],
    )


def downgrade() -> None:
    op.drop_table("knowbot_consent_withdrawal")
    op.drop_table("knowbot_test_consent")
    op.drop_column("knowbot_draft", "run_grant_id")
    op.drop_constraint("ck_knowbot_dataset_somewhere", "knowbot_dataset", type_="check")
    op.alter_column("knowbot_dataset", "version_id", nullable=False)
    op.alter_column("knowbot_dataset", "object_key", nullable=False)
    op.drop_index("uq_knowbot_dataset_knowbase_item", table_name="knowbot_dataset")
    op.drop_column("knowbot_dataset", "knowbase_item_id")
