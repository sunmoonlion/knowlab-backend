"""用户上传的数据（SDD 0027 决定 4）：元数据在这里，内容在本应用自己的对象存储桶。"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "20261016_0005"
down_revision = "20261016_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowbot_dataset",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_subject", sa.String(512), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("version", sa.String(16), nullable=False),
        sa.Column("content_type", sa.String(128), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("object_key", sa.String(256), nullable=False),
        sa.Column("version_id", sa.String(256), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
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
        sa.CheckConstraint("size_bytes > 0", name="ck_knowbot_dataset_size"),
    )
    op.create_index(
        "ix_knowbot_dataset_owner_subject", "knowbot_dataset", ["owner_subject"]
    )


def downgrade() -> None:
    op.drop_index("ix_knowbot_dataset_owner_subject", table_name="knowbot_dataset")
    op.drop_table("knowbot_dataset")
