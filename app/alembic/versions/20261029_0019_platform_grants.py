"""孵化的模式、受理的类型、给平台的授权（SDD 0046 第二、四节；专家孵化方案第十五节第 5 步）。

- `knowbot_request.incubation_mode`：这个名称现在的孵化是自助（self）还是平台（platform）。
  已有的名称一律记自助（产品没上线，不留旧做法，decisions ⑰）：要平台孵化的，作者点一次
  「请平台孵化」。
- `knowbot_acceptance.kind`：正式受理（formal，运营照 0037 写三样）或自助（self，「自助
  （不受理）」：选自助时自动记，不过运营）。已有的受理单都是正式受理。
- `knowbot_platform_grant`：作者给平台的授权。两种：孵化（incubate，作者点「请平台孵化」
  记一张，正式受理后生效；到作者点「我自己来」或平台把草稿交还为止，最长 30 天，到期作者
  一键续，decisions 10-29）；测试（test，自助孵化的准专家交平台测这一次，轻受理后生效，
  费用上限由作者设，测完结束）。依据写成 `platform:knowlab:<种类>:<编号>`。运营看得到什么
  一律按这张表里还有效的授权算。

downgrade 删掉授权表和两列；删掉的授权、自助受理单记的类型不恢复。

Revision ID: 20261029_0019
Revises: 20261028_0018
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "20261029_0019"
down_revision = "20261028_0018"
branch_labels = None
depends_on = None

STATUSES = "('pending', 'active', 'declined', 'revoked', 'ended', 'expired')"


def upgrade() -> None:
    op.add_column(
        "knowbot_request",
        sa.Column(
            "incubation_mode",
            sa.String(16),
            nullable=False,
            server_default="self",
        ),
    )
    op.create_check_constraint(
        "ck_knowbot_request_incubation_mode",
        "knowbot_request",
        "incubation_mode IN ('self', 'platform')",
    )
    op.add_column(
        "knowbot_acceptance",
        sa.Column("kind", sa.String(16), nullable=False, server_default="formal"),
    )
    op.create_check_constraint(
        "ck_knowbot_acceptance_kind", "knowbot_acceptance", "kind IN ('formal', 'self')"
    )
    op.create_table(
        "knowbot_platform_grant",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "request_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("knowbot_request.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        # 测试的授权是给哪一位准专家的
        sa.Column(
            "draft_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("knowbot_draft.id", ondelete="RESTRICT"),
        ),
        sa.Column("status", sa.String(16), nullable=False),
        # 作者设的费用上限（元）：平台发起的 AI 一轮、平台测试都不能超
        sa.Column("budget_limit", sa.Numeric(12, 2), nullable=False),
        sa.Column("granted_by", sa.String(512), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("decided_by", sa.String(512)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("reason", sa.Text()),
        sa.Column("ended_by", sa.String(512)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "kind IN ('incubate', 'test')", name="ck_knowbot_platform_grant_kind"
        ),
        sa.CheckConstraint(
            f"status IN {STATUSES}", name="ck_knowbot_platform_grant_status"
        ),
        sa.CheckConstraint(
            "(kind = 'test') = (draft_id IS NOT NULL)",
            name="ck_knowbot_platform_grant_draft",
        ),
        sa.CheckConstraint("budget_limit > 0", name="ck_knowbot_platform_grant_budget"),
    )
    op.create_index(
        "ix_knowbot_platform_grant_request", "knowbot_platform_grant", ["request_id"]
    )
    # 一个名称同时只有一张没结束的孵化授权；一位准专家同时只有一张没结束的测试授权
    op.create_index(
        "uq_knowbot_platform_grant_one_incubation",
        "knowbot_platform_grant",
        ["request_id"],
        unique=True,
        postgresql_where=sa.text(
            "kind = 'incubate' AND status IN ('pending', 'active')"
        ),
    )
    op.create_index(
        "uq_knowbot_platform_grant_one_test",
        "knowbot_platform_grant",
        ["draft_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'test' AND status IN ('pending', 'active')"),
    )


def downgrade() -> None:
    op.drop_index("uq_knowbot_platform_grant_one_test", "knowbot_platform_grant")
    op.drop_index("uq_knowbot_platform_grant_one_incubation", "knowbot_platform_grant")
    op.drop_index("ix_knowbot_platform_grant_request", "knowbot_platform_grant")
    op.drop_table("knowbot_platform_grant")
    op.drop_constraint(
        "ck_knowbot_acceptance_kind", "knowbot_acceptance", type_="check"
    )
    op.drop_column("knowbot_acceptance", "kind")
    op.drop_constraint(
        "ck_knowbot_request_incubation_mode", "knowbot_request", type_="check"
    )
    op.drop_column("knowbot_request", "incubation_mode")
