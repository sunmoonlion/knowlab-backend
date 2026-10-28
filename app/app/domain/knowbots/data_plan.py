"""测试集：按 knowbase 的解析清单定一条用例用哪几份（SDD 0046 第三节，decisions ⑭）。

纯函数，不碰网络。输入是 knowbase 回的解析清单（在 `app:knowlab` 的范围里）、专家在
第 0 步或运行中提出的几样（作者可能改过）、作者换过的（条目 → 资料与版本）与去掉的
条目；输出：

- pins：要钉进这条用例、签进测试运行授权的那几份（每份带依据）；
- confirm：要作者确认、交 knowfeed 补采的条目（待确认的）；
- problems：确认不了的原因（有歧义没选、换选的不在候选里……），白话；
- incomplete：缺了的、作者去掉的（照做、记「数据不全」）；
- missing：补采结束后还没取到的。

数据是作者的，由作者定：哪一样都能去掉，标了必需的缺了也照做；待确认的在补采开着时进确认。
与 knowbench 的 `data_plan` 同一套规矩，测试授权的展示依据是 `app:knowlab`。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from app.domain.knowbots.data_request import DataRequest

DISPLAY_REF = "app:knowlab"
PUBLIC_OWNER = "public:disclosure"

_REASONS = {
    "not_shown": "在 knowbase 里还没放开给 knowlab",
    "not_collectable": "采不来，要本人到 knowbase 上传",
    "unusable": "这份资料不能用（已禁用或许可不允许）",
    "ambiguous": "有几份对得上，要选一份",
    "waiting": "正在取这一份",
    "to_confirm": "暂不能补采",
}


def item_ref(item: dict[str, Any]) -> str:
    """清单里一个条目的编号：哪一样 + 描述键（同一修订里唯一，跨修订稳定）。"""
    body = json.dumps(item.get("key") or {}, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
    return f"{item.get('need_key')}:{digest}"


@dataclass
class Plan:
    pins: list[dict[str, Any]] = field(default_factory=list)
    confirm: list[dict[str, Any]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    incomplete: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def grant_items(self) -> list[dict[str, Any]]:
        """签测试运行授权要的那几项（knowbase GrantItemIn 的形状）。"""
        keys = (
            "material_id",
            "version_no",
            "sha256",
            "rendition_kind",
            "rendition_no",
            "rendition_sha256",
            "basis",
            "basis_ref",
        )
        return [
            {k: p[k] for k in keys if p.get(k) is not None or k == "basis"}
            for p in self.pins
        ]


def plan(
    resolution: dict[str, Any],
    needs: list[DataRequest],
    *,
    picks: dict[str, dict[str, Any]] | None = None,
    dropped: list[str] | None = None,
    supply_enabled: bool = False,
    after_supply: bool = False,
) -> Plan:
    picks = picks or {}
    gone = set(dropped or ())
    by_key = {n.key: n for n in needs}
    out = Plan()
    seen: set[tuple[str, int]] = set()
    known = {item_ref(i) for i in resolution.get("items") or []}
    if any(ref not in known for ref in list(picks) + list(gone)):
        out.problems.append("换选或去掉的条目不在这张清单里")
    for item in resolution.get("items") or []:
        ref = item_ref(item)
        need = by_key.get(str(item.get("need_key")))
        if need is None:
            continue
        label = _label(need, item)
        if ref in gone:
            out.incomplete.append(f"{label}：你没要")
            continue
        status = str(item.get("status"))
        chosen = _chosen(item, picks.get(ref))
        if chosen is None:
            out.problems.append(f"{label}：换选的那一份不在候选里")
            continue
        if status == "resolved" or (status == "ambiguous" and chosen):
            for candidate in chosen:
                pin = _pin(candidate)
                key = (pin["material_id"], pin["version_no"])
                if key not in seen:
                    seen.add(key)
                    out.pins.append(pin)
            continue
        if status == "to_confirm" and supply_enabled and not after_supply:
            out.confirm.append(
                {
                    "key": item.get("key"),
                    "need_key": need.key,
                    "ref": ref,
                    "label": label,
                }
            )
            continue
        if status == "ambiguous":
            out.problems.append(f"{label}：{_REASONS['ambiguous']}")
            continue
        if after_supply and status in ("to_confirm", "waiting"):
            reason = "没取到"
            out.missing.append(f"{label}：{reason}")
        elif status == "not_collectable" and item.get("reason") == "no_subject":
            reason = "不限主体的没找到，也补采不了"
        else:
            reason = _REASONS.get(status) or str(item.get("reason") or status)
        out.incomplete.append(f"{label}：{reason}")
    return out


def _label(need: DataRequest, item: dict[str, Any]) -> str:
    key = item.get("key") or {}
    parts = [need.name, str(key.get("subject") or "")]
    if key.get("period_end"):
        parts.append(f"{key.get('period_start')}–{key.get('period_end')}")
    return " ".join(p for p in parts if p)


def _chosen(
    item: dict[str, Any], pick: dict[str, Any] | None
) -> list[dict[str, Any]] | None:
    chosen = list(item.get("chosen") or [])
    if pick is None:
        return chosen
    pool = chosen + list(item.get("candidates") or [])
    found = next(
        (
            c
            for c in pool
            if c.get("material_id") == pick.get("material_id")
            and int(c.get("version_no") or 0) == int(pick.get("version_no") or 0)
        ),
        None,
    )
    return [found] if found is not None else None


def _pin(c: dict[str, Any]) -> dict[str, Any]:
    """钉住的一份（测试契约 PinnedInput 的形状）。"""
    basis = str(c.get("basis") or "display")
    ref = {"display": DISPLAY_REF, "reference": "reference", "public": PUBLIC_OWNER}
    return {
        "material_id": str(c["material_id"]),
        "version_no": int(c["version_no"]),
        "sha256": str(c["sha256"]),
        "rendition_kind": c.get("rendition_kind"),
        "rendition_no": c.get("rendition_no"),
        "rendition_sha256": c.get("rendition_sha256"),
        "basis": basis,
        "basis_ref": ref.get(basis, DISPLAY_REF),
        "name": str(c.get("name") or c["material_id"])[:255],
        "media_type": str(c.get("media_type") or "")[:128],
        "size_bytes": int(c.get("size_bytes") or 0),
    }
