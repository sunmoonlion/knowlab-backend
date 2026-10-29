"""工作稿记下挑的数据的格式（专家孵化方案第 7.1、8.2 节）。

作者在工作稿的「数据」一栏从知识库栏（自己展示给 knowlab 的资料）挑几份，knowlab 当场
凭作者本人令牌向 knowbase 取这几份的格式（名字、文件类型、描述的几维、有哪几种读法），
记在 `knowbot_workdraft.data_formats`（资料编号 → 契约 DataFormat 的形状）。AI 起草、按
指令改的每一轮把它交给运行方；平台人员发起的那一轮没有作者的令牌，也照样交得出去。
只是格式，不是内容。

downgrade 删这一列；记着的格式不恢复（作者重挑一次就有）。

Revision ID: 20261029_0022
Revises: 20261029_0021
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "20261029_0022"
down_revision = "20261029_0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "knowbot_workdraft",
        sa.Column(
            "data_formats",
            pg.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("knowbot_workdraft", "data_formats")
