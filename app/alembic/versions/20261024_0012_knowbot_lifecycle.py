"""创意版本、受理单、测试、专家（SDD 0037 第十节、第十八之二节）。

只扩不缩（constraints.md「做数据迁移时」）：新加四张表与几栏、回填；准专家上的 run、报告、
交付几栏与状态约束里的 delivered 留着、不再写，收缩那一次迁移再删。

- 创意版本：每个创意回填 v1（就是现在的内容）；创意记最新版本号，用例里答过的开步前提问
  以后另记（case_answers），版本只插不改。
- 受理单：每个 v1 回填「受理（试行期自动受理）」。
- 准专家：加出自创意第几版（回填 1）与请用户审核。
- 测试：每个测过的准专家回填一次测试（编号按开跑时间排，状态按准专家与报告：测试中的按
  报告是排队 / 进行中 / 等用户答；通过、已交付的是通过；没通过的是没通过；退回待测试的是
  平台出错）。之前被盖掉的几次找不回。
- 专家：测试通过、已交付的准专家回填专家，版本号就是准专家的版本号。已交付的标试行期、
  上架信息照搬；通过还没交付的（旧 web端已给用户看了、能点「要这一版」）记成已交回，
  用户点过的记成用户要了。之后准专家的「已交付」改「通过」。

Revision ID: 20261024_0012
Revises: 20261023_0011
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "20261024_0012"
down_revision = "20261023_0011"
branch_labels = None
depends_on = None

TRIAL = "system:trial"
TRIAL_CHECKS = json.dumps(
    {code: {"ok": True, "note": "试行期自动受理"} for code in ("C-L5", "C-L2", "C-L1")},
    ensure_ascii=False,
)
ACTIVE = "status IN ('queued', 'running', 'waiting')"


def _now() -> sa.sql.elements.TextClause:
    return sa.text("now()")


def upgrade() -> None:
    op.add_column(
        "knowbot_request",
        sa.Column("latest_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "knowbot_request",
        sa.Column(
            "case_answers",
            pg.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_table(
        "knowbot_request_version",
        sa.Column(
            "request_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("knowbot_request.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("idea", pg.JSONB(), nullable=False),
        sa.Column("cases", pg.JSONB(), nullable=False),
        sa.Column("consent_version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(512), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=_now(),
        ),
        sa.CheckConstraint("version >= 1", name="ck_knowbot_request_version_positive"),
    )
    op.create_table(
        "knowbot_acceptance",
        sa.Column("request_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("checks", pg.JSONB(), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("decided_by", sa.String(512), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trial", sa.Boolean(), nullable=False, server_default="false"),
        sa.ForeignKeyConstraint(
            ["request_id", "version"],
            ["knowbot_request_version.request_id", "knowbot_request_version.version"],
            ondelete="RESTRICT",
            name="fk_knowbot_acceptance_version",
        ),
        sa.CheckConstraint(
            "accepted OR (reason IS NOT NULL AND char_length(reason) > 0)",
            name="ck_knowbot_acceptance_reason",
        ),
    )
    op.add_column("knowbot_draft", sa.Column("request_version", sa.Integer()))
    op.add_column("knowbot_draft", sa.Column("user_review", pg.JSONB()))
    op.create_table(
        "knowbot_test",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "request_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("knowbot_request.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column(
            "draft_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("knowbot_draft.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("budget_limit", sa.String(16)),
        sa.Column("timeout_seconds", sa.Integer()),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("grant_id", sa.String(64)),
        sa.Column("started_by", sa.String(512)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("report", pg.JSONB()),
        sa.Column("error", sa.Text()),
        sa.UniqueConstraint("request_id", "number", name="uq_knowbot_test_number"),
        sa.UniqueConstraint("run_id", name="uq_knowbot_test_run"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'waiting', 'passed', 'failed',"
            " 'platform_error')",
            name="ck_knowbot_test_status",
        ),
        sa.CheckConstraint("number >= 1", name="ck_knowbot_test_number_positive"),
    )
    op.create_index(
        "uq_knowbot_test_one_active",
        "knowbot_test",
        ["request_id"],
        unique=True,
        postgresql_where=sa.text(ACTIVE),
    )
    op.create_index("ix_knowbot_test_draft", "knowbot_test", ["draft_id"])
    op.create_table(
        "knowbot_expert",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "request_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("knowbot_request.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "draft_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("knowbot_draft.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "test_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("knowbot_test.id", ondelete="RESTRICT"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("returned_by", sa.String(512)),
        sa.Column("returned_at", sa.DateTime(timezone=True)),
        sa.Column("user_accepted", sa.Boolean()),
        sa.Column("user_decided_by", sa.String(512)),
        sa.Column("user_decided_at", sa.DateTime(timezone=True)),
        sa.Column("review_approved", sa.Boolean()),
        sa.Column("review_listing", sa.String(8)),
        sa.Column("review_reason", sa.Text()),
        sa.Column("reviewed_by", sa.String(512)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("listed_at", sa.DateTime(timezone=True)),
        sa.Column("knowmart_knowbot_id", sa.String(64)),
        sa.Column("digest", sa.String(80)),
        sa.Column("delivery", pg.JSONB()),
        sa.Column("delivery_error", sa.Text()),
        sa.Column("stopped_at", sa.DateTime(timezone=True)),
        sa.Column("stopped_by", sa.String(512)),
        sa.Column("stop_reason", sa.Text()),
        sa.Column("trial", sa.Boolean(), nullable=False, server_default="false"),
        # 没上架的结局送到 knowmart 的时刻；空着就是还要送（定时补发，Astra 评审 F08）
        sa.Column("unlisted_sent_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("request_id", "version", name="uq_knowbot_expert_version"),
        sa.UniqueConstraint("draft_id", name="uq_knowbot_expert_draft"),
        sa.CheckConstraint("version >= 1", name="ck_knowbot_expert_version_positive"),
        sa.CheckConstraint(
            "review_listing IS NULL OR review_listing IN ('self', 'public')",
            name="ck_knowbot_expert_listing",
        ),
        sa.CheckConstraint(
            "review_approved IS NOT TRUE OR review_listing IS NOT NULL",
            name="ck_knowbot_expert_approved_listing",
        ),
        sa.CheckConstraint(
            "review_approved IS NOT FALSE"
            " OR (review_reason IS NOT NULL AND char_length(review_reason) > 0)",
            name="ck_knowbot_expert_rejected_reason",
        ),
        sa.CheckConstraint(
            "stopped_at IS NULL"
            " OR (stopped_by IS NOT NULL AND stop_reason IS NOT NULL)",
            name="ck_knowbot_expert_stopped",
        ),
    )
    op.create_index(
        "ix_knowbot_expert_knowmart",
        "knowbot_expert",
        ["knowmart_knowbot_id", "version"],
    )
    _backfill()
    op.alter_column("knowbot_draft", "request_version", nullable=False)
    op.create_foreign_key(
        "fk_knowbot_draft_request_version",
        "knowbot_draft",
        "knowbot_request_version",
        ["request_id", "request_version"],
        ["request_id", "version"],
        ondelete="RESTRICT",
    )


def _backfill() -> None:
    op.execute(
        """INSERT INTO knowbot_request_version
             (request_id, version, idea, cases, consent_version, created_by, created_at)
           SELECT id, 1, idea, cases, consent_version, owner_subject, created_at
           FROM knowbot_request"""
    )
    op.execute(
        sa.text(
            """INSERT INTO knowbot_acceptance
                 (request_id, version, accepted, checks, reason, decided_by,
                  decided_at, trial)
               SELECT id, 1, true, CAST(:checks AS jsonb), NULL, :by, created_at, true
               FROM knowbot_request"""
        ).bindparams(checks=TRIAL_CHECKS, by=TRIAL)
    )
    op.execute("UPDATE knowbot_draft SET request_version = 1")
    op.execute(
        """INSERT INTO knowbot_test
             (id, request_id, number, draft_id, status, budget_limit, timeout_seconds,
              run_id, grant_id, started_by, started_at, finished_at, report, error)
           SELECT gen_random_uuid(), d.request_id,
             row_number() OVER (
               PARTITION BY d.request_id
               ORDER BY coalesce(d.run_started_at, d.updated_at), d.revision),
             d.id,
             CASE
               WHEN d.status = 'testing' THEN
                 CASE coalesce(d.report ->> 'status', 'accepted')
                   WHEN 'running' THEN 'running'
                   WHEN 'waiting' THEN 'waiting'
                   ELSE 'queued' END
               WHEN d.status IN ('passed', 'delivered') THEN 'passed'
               WHEN d.status = 'failed' THEN 'failed'
               ELSE 'platform_error' END,
             NULL, d.run_timeout_seconds, d.run_id, d.run_grant_id, NULL,
             coalesce(d.run_started_at, d.updated_at),
             CASE WHEN d.status = 'testing' THEN NULL
               ELSE coalesce((d.report ->> 'finished_at')::timestamptz, d.updated_at)
             END,
             d.report,
             CASE WHEN d.status = 'pending_test'
               THEN coalesce(d.last_error, '0012 之前退回待测试') END
           FROM knowbot_draft d
           WHERE d.run_id IS NOT NULL"""
    )
    op.execute(
        sa.text(
            """INSERT INTO knowbot_expert
                 (id, request_id, version, draft_id, test_id, created_at,
                  returned_by, returned_at, user_accepted, user_decided_by,
                  user_decided_at, listed_at, knowmart_knowbot_id, digest, delivery,
                  trial)
               SELECT gen_random_uuid(), d.request_id, CAST(d.version AS integer), d.id,
                 t.id, coalesce(t.finished_at, d.updated_at),
                 :by, coalesce(t.finished_at, d.updated_at),
                 CASE WHEN d.delivery ->> 'accepted_at' IS NOT NULL THEN true END,
                 d.delivery ->> 'accepted_by',
                 (d.delivery ->> 'accepted_at')::timestamptz,
                 CASE WHEN d.status = 'delivered'
                   THEN coalesce((d.delivery ->> 'delivered_at')::timestamptz,
                                 d.updated_at) END,
                 CASE WHEN d.status = 'delivered' THEN d.delivery ->> 'knowbot_id' END,
                 CASE WHEN d.status = 'delivered' THEN d.delivery ->> 'digest' END,
                 CASE WHEN d.status = 'delivered' THEN d.delivery END,
                 d.status = 'delivered'
               FROM knowbot_draft d
               LEFT JOIN knowbot_test t ON t.draft_id = d.id
               WHERE d.status IN ('passed', 'delivered')"""
        ).bindparams(by=TRIAL)
    )
    op.execute("UPDATE knowbot_draft SET status = 'passed' WHERE status = 'delivered'")


def downgrade() -> None:
    # 上架了的专家，准专家改回「已交付」，回执并回 delivery（旧读路径按它找）
    op.execute(
        """UPDATE knowbot_draft d
           SET status = 'delivered',
               delivery = coalesce(e.delivery, '{}'::jsonb)
                 || jsonb_build_object(
                      'knowbot_id', e.knowmart_knowbot_id,
                      'version', d.version,
                      'digest', e.digest,
                      'delivered_at', e.listed_at)
           FROM knowbot_expert e
           WHERE e.draft_id = d.id AND e.listed_at IS NOT NULL"""
    )
    op.drop_constraint(
        "fk_knowbot_draft_request_version", "knowbot_draft", type_="foreignkey"
    )
    op.drop_index("ix_knowbot_expert_knowmart", table_name="knowbot_expert")
    op.drop_table("knowbot_expert")
    op.drop_index("ix_knowbot_test_draft", table_name="knowbot_test")
    op.drop_index("uq_knowbot_test_one_active", table_name="knowbot_test")
    op.drop_table("knowbot_test")
    op.drop_column("knowbot_draft", "user_review")
    op.drop_column("knowbot_draft", "request_version")
    op.drop_table("knowbot_acceptance")
    op.drop_table("knowbot_request_version")
    op.drop_column("knowbot_request", "case_answers")
    op.drop_column("knowbot_request", "latest_version")
