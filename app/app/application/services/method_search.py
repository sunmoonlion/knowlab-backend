"""One authorized directory page per automatic planning action."""

import hashlib
import json

from app.application.services.formal_methods import FormalMethodService
from app.domain.knowbots.incubation_tools import AutomaticOutputSearch


def _fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def scan_outputs(
    methods: FormalMethodService,
    owner: str,
    request_id: str,
    query: AutomaticOutputSearch,
):
    repo = methods.repo
    shared = query.directory == "shared"
    slot = query.slot
    key = _fingerprint(
        [
            str(query.exclude_method_id),
            query.directory,
            slot.meaning,
            slot.schema_,
            slot.required,
        ]
    )
    cursor = "" if shared else "0"

    def result(reason, used, items=(), *, has_more=False, completed=False):
        return {
            "reason": reason,
            "has_more": has_more,
            "used": used,
            "pages_read": len(read) + int(completed),
            "limit": query.page_limit,
            "items": list(items),
        }

    async with repo.transaction():
        rid = (await methods.workdrafts._request(owner, request_id)).id
        history = await repo.lock_search_history(rid)
        used = len(history)
        previous = [r for r in history if r["search_key"] == key]
        read = [r for r in previous if r["state"] == "read"]
        if any(r["action_id"] == query.action_id for r in history):
            return result("already_read", used)
        if previous and previous[-1]["state"] == "pending":
            pending = previous[-1]
            if not pending["expired"]:
                return result("busy", used)
            if not await repo.finish_search_page(rid, pending["action_id"], "failed"):
                return result("busy", used)
        if read:
            cursor = read[-1]["next_cursor"]
            if cursor is None or any(r["page_cursor"] == cursor for r in read):
                return result("no_progress", used)
        if used >= query.page_limit:
            return result("budget", used)
        await repo.reserve_search_page(rid, used + 1, query.action_id, key, cursor)

    # Hold no transaction while checking the current author's runtime/market grants.
    try:
        page = await methods.matching_outputs(
            owner,
            request_id,
            slot,
            query.exclude_method_id,
            0 if shared else int(cursor),
            shared=shared,
            after=cursor if shared and cursor else None,
        )
        by_output = {
            _fingerprint([item["reference"], item["output"]]): item
            for item in page["items"]
        }
        following = str(page["next"]) if page["next"] is not None else None
        async with repo.transaction():
            seen = (
                await repo.seen_search_outputs(rid, key, list(by_output))
                if by_output
                else set()
            )
            fingerprints = [
                fingerprint for fingerprint in by_output if fingerprint not in seen
            ]
            items = [by_output[fingerprint] for fingerprint in fingerprints]
            saved = await repo.finish_search_page(
                rid, query.action_id, "read", following, fingerprints
            )
    except Exception:
        async with repo.transaction():
            await repo.finish_search_page(rid, query.action_id, "failed")
        raise
    if not saved:
        return result("already_read", used + 1)
    more = (
        following is not None
        and following != cursor
        and all(row["page_cursor"] != following for row in read)
    )
    if items:
        return result("matches", used + 1, items, has_more=more, completed=True)
    if not more:
        return result("no_progress", used + 1, completed=True)
    return result(
        "budget" if used + 1 >= query.page_limit else "continue",
        used + 1,
        has_more=True,
        completed=True,
    )
