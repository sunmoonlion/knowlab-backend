"""申请记来源与参考回答（请专家方案设计第 4.9 节第 6 条；专家孵化方案第十三节）。

用户在 knowbench 找不到合适的专家、点了「孵化一个专家」，knowbench 经服务关系
`incubation-request` 以这个用户的名义建一份种类为「问题」的申请：

- `knowbot_request.origin`：
从哪来（`{"app": "knowbench", "project_id", "task_id", "key"}`，
  key 是 knowbench 那头的幂等钥匙，同一个钥匙只建一份）；用户自己在 knowlab 交的为空。
- `knowbot_request.reference_answer`：用户先让自己的模型直接答过的，
那份回答附来作参考。它是
  模型写的，只算提议，进工作稿要作者接受（decisions ⑮）。

这样建的申请先不带用例、未同意测试数据说明（consent_version 0），用户补用例、
点同意才能测。

downgrade 删两列；记着的来源与参考回答不恢复。

Revision ID: 20261029_0021
Revises: 20261029_0020
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "20261029_0021"
down_revision = "20261029_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowbot_request", sa.Column("origin", pg.JSONB()))
    op.add_column("knowbot_request", sa.Column("reference_answer", sa.Text()))
    op.create_check_constraint(
        "ck_knowbot_request_reference_answer",
        "knowbot_request",
        "reference_answer IS NULL OR char_length(reference_answer) <= 20000",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_knowbot_request_reference_answer", "knowbot_request", type_="check"
    )
    op.drop_column("knowbot_request", "reference_answer")
    op.drop_column("knowbot_request", "origin")
