"""旧补充经工作稿逐字采用后的只读投影，不恢复 amendment: 写入口。

只有作者的 edit/accept 原文记录被已出稿清单引用、且内容确实进入对应方法，才算采用。
仅保存工作稿、相似改写、未接受的建议、复制/导入包都不推断为采用。
"""

from collections.abc import Mapping
from uuid import UUID

from app.domain.knowbots.workdraft import RECORD_REF, Entry


def incorporated_texts(
    manifest: object,
    *,
    workdraft_id: UUID,
    owner_subject: str,
    entries: Mapping[UUID, Entry],
) -> set[str]:
    if not isinstance(manifest, dict):
        return set()
    sources = manifest.get("rule_sources")
    pack = manifest.get("pack")
    workflow = pack.get("workflow") if isinstance(pack, dict) else None
    if not isinstance(sources, list) or not isinstance(workflow, list):
        return set()
    steps = {s.get("step_id"): s for s in workflow if isinstance(s, dict)}
    found: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            continue
        ref = source.get("ref")
        match = RECORD_REF.fullmatch(ref) if isinstance(ref, str) else None
        entry = entries.get(UUID(match.group(1))) if match else None
        if (
            entry is None
            or entry.workdraft_id != workdraft_id
            or entry.actor_subject != owner_subject
            or entry.actor_role != "author"
            or entry.kind not in ("edit", "accept")
            or source.get("source")
            != ("user_confirmed" if entry.kind == "accept" else "user_text")
            or not entry.text.strip()
        ):
            continue
        path = (entry.path or "").split("/")
        if (
            not (
                len(path) == 3
                and path[2] == "does"
                or len(path) == 4
                and path[2] == "rules"
            )
            or path[0] != "steps"
        ):
            continue
        step = steps.get(path[1], {})
        method = step.get("method_text")
        if isinstance(method, str) and entry.text in method:
            found.add(entry.text)
    return found
