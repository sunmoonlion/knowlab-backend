"""有人值守：投资判断默认归用户，运营要写理由才能改由自己答（审读第 2 条，C-L1）。

开步前的提问（ask_before）与要人判断的提问（question）建出来就转给用户（route = user）；
运营只能写一句理由把它改派给自己（route = operator）才能答。工具审批（tool_approval）
照旧只归我们（route 为空）。已有的行：没答的转给用户；运营直接答过的记成「运营答的」。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20261018_0007"
down_revision = "20261016_0006"
branch_labels = None
depends_on = None

# 先判空再比较：route 为空时 route = 'user' 是 NULL，CHECK 会当它过
ROUTE = (
    "(kind = 'tool_approval' AND route IS NULL)"
    " OR (kind <> 'tool_approval' AND route IS NOT NULL"
    " AND (route = 'user' OR (route = 'operator' AND route_reason IS NOT NULL)))"
)


def upgrade() -> None:
    op.add_column("knowbot_question", sa.Column("route_reason", sa.Text()))
    op.drop_constraint("ck_knowbot_question_route", "knowbot_question", type_="check")
    op.execute(
        "UPDATE knowbot_question SET route = 'operator',"
        " route_reason = '迁移前运营直接答的'"
        " WHERE route IS NULL AND kind <> 'tool_approval' AND answered_by = 'operator'"
    )
    op.execute(
        "UPDATE knowbot_question SET route = 'user'"
        " WHERE route IS NULL AND kind <> 'tool_approval'"
    )
    op.create_check_constraint("ck_knowbot_question_route", "knowbot_question", ROUTE)


def downgrade() -> None:
    op.drop_constraint("ck_knowbot_question_route", "knowbot_question", type_="check")
    op.execute("UPDATE knowbot_question SET route = NULL WHERE route = 'operator'")
    op.create_check_constraint(
        "ck_knowbot_question_route",
        "knowbot_question",
        "route IS NULL OR (route = 'user' AND kind <> 'tool_approval')",
    )
    op.drop_column("knowbot_question", "route_reason")
