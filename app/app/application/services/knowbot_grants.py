"""授权与自助受理单的两个小件，申请服务与切换服务共用（避免两边互相导入）。"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from app.application.ports.knowbots import (
    AcceptanceRecord,
    DeliveryRecord,
    GrantRecord,
)
from app.domain.knowbots.platform_grants import live

SELF_ACCEPTOR = "system:self-service"


def live_incubation(grants: Sequence[GrantRecord], now: datetime) -> GrantRecord | None:
    """这个名称还有效的孵化授权（pending 或 active、没过期）。"""
    return next(
        (
            g
            for g in grants
            if g.kind == "incubate" and live(g.status, g.expires_at, now)
        ),
        None,
    )


def self_acceptance(
    request_id: uuid.UUID, version: int, now: datetime
) -> AcceptanceRecord:
    """选自助时自动记的「自助（不受理）」（SDD 0046 第二节）。"""
    return AcceptanceRecord(
        request_id=request_id,
        version=version,
        accepted=True,
        checks={},
        reason=None,
        decided_by=SELF_ACCEPTOR,
        decided_at=now,
        kind="self",
    )


def reviewing(delivery: DeliveryRecord) -> bool:
    """这次交付还在审、作者给审核员的授权还在：运营看得到这一版的清单与测试（SDD 0046
    第五节「交审时本人授权审核员看这次交付，审完撤销」）。"""
    return delivery.status == "reviewing" and delivery.consent_ended_at is None
