"""AI 孵化的一轮与孵化的缺口（专家孵化方案第八、九节，第十五节第 4 步；0029 第 6.6 节）。

- `knowbot_incubation`：作者点「AI 起草」或「让 AI 改…」一次是一轮（这个名称里第几轮、
  方式、动作与那句指令、状态照契约 knowbot-incubation/v1 的结果状态、费用上限与花费、交给
  运行方的编号与那边的委托号、在哪台电脑哪个目录跑、交出去的种子与带去的答复、交回的四样、
  knowlab 核过的每条建议、作者对每条建议的处理）。同一份工作稿同时只有一轮没结束。
- `knowbot_question`：问题表也收孵化的缺口（kind gap）：缺口不挂准专家，挂在那一轮与名称
  上，`draft_id` 改成可空，加 `incubation_id`、`request_id`、`proposal`（AI 附的提议）；
  两样只挂一样。已有的问题都挂在准专家上，不用回填。
- 创意加种类「问题」（question）不用改库：创意的种类在 JSONB 里，库里没有约束它。

downgrade 删掉孵化的缺口（问题表里 kind gap 的行）与孵化表，问题表退回只挂准专家；删掉的
行不恢复。

Revision ID: 20261028_0018
Revises: 20261028_0017
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "20261028_0018"
down_revision = "20261028_0017"
branch_labels = None
depends_on = None

STATUSES = "('queued', 'running', 'waiting', 'succeeded', 'failed', 'over_budget', 'cancelled')"
ACTIVE = "('queued', 'running', 'waiting')"
OLD_KINDS = "kind IN ('tool_approval', 'question', 'ask_before', 'data')"
NEW_KINDS = "kind IN ('tool_approval', 'question', 'ask_before', 'data', 'gap')"
OWNER = (
    "(kind = 'gap' AND draft_id IS NULL AND incubation_id IS NOT NULL"
    " AND request_id IS NOT NULL)"
    " OR (kind <> 'gap' AND draft_id IS NOT NULL AND incubation_id IS NULL"
    " AND request_id IS NULL)"
)


def upgrade() -> None:
    op.create_table(
        "knowbot_incubation",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "request_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("knowbot_request.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "workdraft_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("knowbot_workdraft.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("owner_subject", sa.String(512), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(8), nullable=False, server_default="self"),
        sa.Column("action", sa.String(8), nullable=False),
        sa.Column("instruction", sa.Text()),
        sa.Column("step_id", sa.String(48)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("budget_limit", sa.String(16), nullable=False),
        sa.Column("spent", sa.String(24)),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("task_id", sa.String(36)),
        sa.Column("environment_id", sa.String(36), nullable=False),
        sa.Column("workspace_root", sa.Text(), nullable=False),
        sa.Column("waiting_for", sa.String(32)),
        sa.Column("request_version", sa.Integer(), nullable=False),
        sa.Column("seed", pg.JSONB(), nullable=False),
        sa.Column("workdraft_revision", sa.Integer(), nullable=False),
        sa.Column(
            "answer_ids", pg.JSONB(), nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column("error", sa.Text()),
        sa.Column("handback", pg.JSONB()),
        sa.Column("review", pg.JSONB()),
        sa.Column(
            "decisions", pg.JSONB(), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
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
        sa.UniqueConstraint("request_id", "round", name="uq_knowbot_incubation_round"),
        sa.UniqueConstraint("run_id", name="uq_knowbot_incubation_run"),
        sa.CheckConstraint("round >= 1", name="ck_knowbot_incubation_round"),
        sa.CheckConstraint(
            "mode IN ('self', 'platform')", name="ck_knowbot_incubation_mode"
        ),
        sa.CheckConstraint(
            "action IN ('draft', 'revise')", name="ck_knowbot_incubation_action"
        ),
        sa.CheckConstraint(
            "(action = 'revise') = (instruction IS NOT NULL)",
            name="ck_knowbot_incubation_instruction",
        ),
        sa.CheckConstraint(
            f"status IN {STATUSES}", name="ck_knowbot_incubation_status"
        ),
        sa.CheckConstraint(
            "(status = 'succeeded') = (handback IS NOT NULL)",
            name="ck_knowbot_incubation_handback",
        ),
    )
    op.create_index(
        "uq_knowbot_incubation_one_active",
        "knowbot_incubation",
        ["workdraft_id"],
        unique=True,
        postgresql_where=sa.text(f"status IN {ACTIVE}"),
    )
    op.create_index(
        "ix_knowbot_incubation_owner", "knowbot_incubation", ["owner_subject"]
    )

    op.alter_column("knowbot_question", "draft_id", nullable=True)
    op.add_column(
        "knowbot_question",
        sa.Column(
            "incubation_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("knowbot_incubation.id", ondelete="RESTRICT"),
        ),
    )
    op.add_column(
        "knowbot_question",
        sa.Column(
            "request_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("knowbot_request.id", ondelete="RESTRICT"),
        ),
    )
    op.add_column("knowbot_question", sa.Column("proposal", sa.Text()))
    op.drop_constraint("ck_knowbot_question_kind", "knowbot_question", type_="check")
    op.create_check_constraint(
        "ck_knowbot_question_kind", "knowbot_question", NEW_KINDS
    )
    op.create_check_constraint("ck_knowbot_question_owner", "knowbot_question", OWNER)
    op.create_index(
        "uq_knowbot_question_gap_once",
        "knowbot_question",
        ["incubation_id", "question_id"],
        unique=True,
        postgresql_where=sa.text("incubation_id IS NOT NULL"),
    )
    op.create_index("ix_knowbot_question_request", "knowbot_question", ["request_id"])


def downgrade() -> None:
    """删掉孵化的缺口与孵化表（不保留），问题表退回只挂准专家。"""
    op.execute("DELETE FROM knowbot_question WHERE kind = 'gap'")
    op.drop_index("ix_knowbot_question_request", table_name="knowbot_question")
    op.drop_index("uq_knowbot_question_gap_once", table_name="knowbot_question")
    op.drop_constraint("ck_knowbot_question_owner", "knowbot_question", type_="check")
    op.drop_constraint("ck_knowbot_question_kind", "knowbot_question", type_="check")
    op.create_check_constraint(
        "ck_knowbot_question_kind", "knowbot_question", OLD_KINDS
    )
    op.drop_column("knowbot_question", "proposal")
    op.drop_column("knowbot_question", "request_id")
    op.drop_column("knowbot_question", "incubation_id")
    op.alter_column("knowbot_question", "draft_id", nullable=False)
    op.drop_index("ix_knowbot_incubation_owner", table_name="knowbot_incubation")
    op.drop_index("uq_knowbot_incubation_one_active", table_name="knowbot_incubation")
    op.drop_table("knowbot_incubation")
