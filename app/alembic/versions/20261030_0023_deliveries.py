"""交付（SDD 0046 第五节、0043 第 4.2、4.3 节）与测试证据、名录（0043 第 2.6 节、
0044 第三节）。

- `knowbot_delivery`：一次交付过程。作者「提交交付」时建（选定自用或公开、授权审核员），
  记冻结下来的绑定、许可预查、自动查的结果、要不要平台重跑与那一次重跑、审核结论与平台
  人员选的类目、交 knowmart 的回执；重跑发起过几次、前几次已花多少（费用上限按一次交付
  累计，平台出错后再发起最多 3 次）；审完撤授权（`consent_ended_at`）。
  作者撤回、平台人员作废记撤回（`withdrawn_*`）。同一位专家同时只有一个在审的；自用转
  公开再交一次就是新的一条（`conversion`）。旧流程里作者已经要了、还没上架的照当时的
  状态补一条。
- `knowbot_expert.needs_return`：平台孵化、平台测的那一版才要平台先交回、作者再定
  （先用户、后运营照旧）；自助测的、自助孵化的作者「提交交付」即过同意门。旧数据按测试
  与准专家的模式补。
- `knowbot_test.mode` 多一种 `rerun`：公开交付时平台发起的重跑，在作者的账号下、作者的
  电脑上跑（decisions ⑯），不出专家、不改准专家。`knowbot_test.evidence`：测试证据
  写进作者 knowbase 产出区的那一份（资料、版本），写不成的记次数与再试的时候。
- `knowbot_request.directory_sent`：推给 knowbase 名录的最近一份（名称、最新版本），
  对不上就再推。

downgrade 删表与列；重跑的测试改记成平台测试（只为过约束，内容不动）。

Revision ID: 20261030_0023
Revises: 20261029_0022
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "20261030_0023"
down_revision = "20261029_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "knowbot_expert",
        sa.Column(
            "needs_return", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.execute(
        "UPDATE knowbot_expert e SET needs_return = true"
        " FROM knowbot_draft d WHERE d.id = e.draft_id AND ("
        " e.returned_at IS NOT NULL OR (d.mode = 'platform' AND EXISTS ("
        " SELECT 1 FROM knowbot_test t WHERE t.id = e.test_id"
        " AND t.mode = 'platform')))"
    )
    op.drop_constraint("ck_knowbot_test_mode", "knowbot_test", type_="check")
    op.create_check_constraint(
        "ck_knowbot_test_mode",
        "knowbot_test",
        "mode IN ('platform', 'self', 'rerun')"
        " AND (mode = 'platform' OR environment_id IS NOT NULL)",
    )
    op.add_column("knowbot_test", sa.Column("evidence", pg.JSONB()))
    op.add_column("knowbot_request", sa.Column("directory_sent", pg.JSONB()))
    op.create_table(
        "knowbot_delivery",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "expert_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("knowbot_expert.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "request_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("knowbot_request.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("listing", sa.String(8), nullable=False),
        sa.Column(
            "conversion", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("test_mode", sa.String(8), nullable=False),
        sa.Column("needs_rerun", sa.Boolean(), nullable=False),
        sa.Column("budget_limit", sa.Numeric(12, 2)),
        sa.Column("submitted_by", sa.String(512), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "bindings", pg.JSONB(), nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column("binding_check", pg.JSONB()),
        sa.Column("auto_check", pg.JSONB()),
        sa.Column(
            "consents", pg.JSONB(), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column("rerun_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rerun_spent", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column(
            "rerun_test_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("knowbot_test.id", ondelete="RESTRICT"),
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("decided_by", sa.String(512)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("reason", sa.Text()),
        sa.Column("category", sa.String(64)),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("delivery_error", sa.Text()),
        sa.Column("receipt", pg.JSONB()),
        sa.Column("consent_ended_at", sa.DateTime(timezone=True)),
        sa.Column("withdrawn_by", sa.String(512)),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True)),
        sa.Column("withdraw_reason", sa.Text()),
        sa.CheckConstraint(
            "listing IN ('self', 'public')", name="ck_knowbot_delivery_listing"
        ),
        sa.CheckConstraint(
            "test_mode IN ('self', 'platform')", name="ck_knowbot_delivery_test_mode"
        ),
        sa.CheckConstraint(
            "status IN ('reviewing', 'approved', 'rejected', 'withdrawn')",
            name="ck_knowbot_delivery_status",
        ),
        sa.CheckConstraint(
            "(status = 'withdrawn') = (withdrawn_at IS NOT NULL)",
            name="ck_knowbot_delivery_withdrawn",
        ),
        sa.CheckConstraint(
            "status <> 'rejected' OR (reason IS NOT NULL AND char_length(reason) > 0)",
            name="ck_knowbot_delivery_rejected_reason",
        ),
        sa.CheckConstraint(
            "NOT needs_rerun OR budget_limit IS NOT NULL",
            name="ck_knowbot_delivery_rerun_budget",
        ),
    )
    op.create_index("ix_knowbot_delivery_request", "knowbot_delivery", ["request_id"])
    op.create_index(
        "uq_knowbot_delivery_one_open",
        "knowbot_delivery",
        ["expert_id"],
        unique=True,
        postgresql_where=sa.text("status = 'reviewing'"),
    )
    # 决定 ⑰：旧的「等运营审核」（user_accepted IS TRUE AND review_approved IS NULL）
    # 当时作者从没授权过审核员看清单与测试，放回「等作者提交」，由作者按新规矩再交；
    # 只有已审完还没交 knowmart 的（review_approved IS TRUE）补一条已审过的交付。
    op.execute(
        "UPDATE knowbot_expert"
        " SET returned_at = COALESCE(returned_at, user_decided_at, created_at),"
        " user_accepted = NULL, user_decided_by = NULL, user_decided_at = NULL"
        " WHERE user_accepted IS TRUE AND review_approved IS NULL"
        " AND listed_at IS NULL AND stopped_at IS NULL"
    )
    op.execute(
        "INSERT INTO knowbot_delivery (id, expert_id, request_id, listing,"
        " conversion, test_mode, needs_rerun, submitted_by, submitted_at, bindings,"
        " consents, status, decided_by, decided_at, consent_ended_at)"
        " SELECT gen_random_uuid(), e.id, e.request_id,"
        " COALESCE(e.review_listing, 'self'), false,"
        " CASE WHEN t.mode = 'platform' THEN 'platform' ELSE 'self' END, false,"
        " COALESCE(e.user_decided_by, 'system:migration'),"
        " COALESCE(e.user_decided_at, e.created_at), '[]', '{}',"
        " 'approved', e.reviewed_by, e.reviewed_at, now()"
        " FROM knowbot_expert e LEFT JOIN knowbot_test t ON t.id = e.test_id"
        " WHERE e.user_accepted IS TRUE AND e.review_approved IS TRUE"
        " AND e.listed_at IS NULL AND e.stopped_at IS NULL"
    )


def downgrade() -> None:
    op.drop_index("uq_knowbot_delivery_one_open", table_name="knowbot_delivery")
    op.drop_index("ix_knowbot_delivery_request", table_name="knowbot_delivery")
    op.drop_table("knowbot_delivery")
    op.drop_column("knowbot_request", "directory_sent")
    op.drop_column("knowbot_test", "evidence")
    op.execute("UPDATE knowbot_test SET mode = 'platform' WHERE mode = 'rerun'")
    op.drop_constraint("ck_knowbot_test_mode", "knowbot_test", type_="check")
    op.create_check_constraint(
        "ck_knowbot_test_mode",
        "knowbot_test",
        "mode IN ('platform', 'self')"
        " AND (mode = 'platform' OR environment_id IS NOT NULL)",
    )
    op.drop_column("knowbot_expert", "needs_return")
