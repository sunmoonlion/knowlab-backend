"""删清单 v1 与 knowlab 旧数据路（decisions ⑰：不留旧版兼容）。

10-28 起 knowlab 只收 `knowbot-manifest/v2`（逻辑 + 作者挑选的数据，测试集另记）；
用户数据在 knowbase（user-data/v2），knowlab 不再存数据、不再签 v1 测试运行授权：

- 删表：`knowbot_dataset`（用户上传的数据的元数据）、`knowbot_test_consent`（v1 测试
  同意）、`knowbot_consent_withdrawal`（待撤的旧测试同意）；
- 删列：`knowbot_draft.run_grant_id`（0012 前草稿上的运行授权，0012 起不写）、
  `knowbot_test.grant_id`（v1 的测试运行授权；v2 每条用例的授权在 `case_grants`）；
- 删行：`knowbot_draft.manifest->>'schema'` 不是 `knowbot-manifest/v2` 的准专家，连同
  它的专家、测试中的问题、测试（外键都是 RESTRICT，按这个顺序删）；产出
  （`knowbot_test_output`）随准专家级联删；测试集里「出自哪次测试」（软引用）指着被删
  测试的置空。创意（`knowbot_request`）与它的版本、受理单、补充、测试集都留着，可以照
  v2 再孵化。

downgrade 只建回空表与空列，删掉的行不恢复（数据已不在）。

Revision ID: 20261028_0016
Revises: 20261028_0015
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "20261028_0016"
down_revision = "20261028_0015"
branch_labels = None
depends_on = None

V2 = "knowbot-manifest/v2"
OLD_DRAFTS = (
    "SELECT id FROM knowbot_draft WHERE manifest->>'schema' IS DISTINCT FROM :v2"
)
OLD_TESTS = f"SELECT id FROM knowbot_test WHERE draft_id IN ({OLD_DRAFTS})"


def upgrade() -> None:
    conn = op.get_bind()
    params = {"v2": V2}
    for statement in (
        f"DELETE FROM knowbot_expert WHERE draft_id IN ({OLD_DRAFTS})"
        f" OR test_id IN ({OLD_TESTS})",
        f"DELETE FROM knowbot_question WHERE draft_id IN ({OLD_DRAFTS})",
        f"UPDATE knowbot_test_set SET from_test_id = NULL"
        f" WHERE from_test_id IN ({OLD_TESTS})",
        f"DELETE FROM knowbot_test WHERE draft_id IN ({OLD_DRAFTS})",
        "DELETE FROM knowbot_draft WHERE manifest->>'schema' IS DISTINCT FROM :v2",
    ):
        conn.execute(sa.text(statement), params)
    op.drop_column("knowbot_test", "grant_id")
    op.drop_column("knowbot_draft", "run_grant_id")
    op.drop_table("knowbot_consent_withdrawal")
    op.drop_table("knowbot_test_consent")
    op.drop_table("knowbot_dataset")


def downgrade() -> None:
    """建回 0015 时的空表与空列；删掉的 v1 准专家与旧数据记录不恢复。"""
    op.create_table(
        "knowbot_dataset",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_subject", sa.String(512), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("version", sa.String(16), nullable=False),
        sa.Column("content_type", sa.String(128), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("object_key", sa.String(256)),
        sa.Column("version_id", sa.String(256)),
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
        sa.Column("knowbase_item_id", sa.String(64)),
        sa.Column("no_resale", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint("size_bytes > 0", name="ck_knowbot_dataset_size"),
        sa.CheckConstraint(
            "knowbase_item_id IS NOT NULL"
            " OR (object_key IS NOT NULL AND version_id IS NOT NULL)",
            name="ck_knowbot_dataset_somewhere",
        ),
    )
    op.create_index(
        "ix_knowbot_dataset_owner_subject", "knowbot_dataset", ["owner_subject"]
    )
    op.create_index(
        "uq_knowbot_dataset_knowbase_item",
        "knowbot_dataset",
        ["knowbase_item_id"],
        unique=True,
        postgresql_where=sa.text("knowbase_item_id IS NOT NULL"),
    )
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
    op.add_column("knowbot_draft", sa.Column("run_grant_id", sa.String(64)))
    op.add_column("knowbot_test", sa.Column("grant_id", sa.String(64)))
