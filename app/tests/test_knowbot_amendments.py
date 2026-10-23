"""补充方法（SDD 0035）已取消（SDD 0037 第三节）：旧的留作历史，出处照旧可查、只读。

新准专家不许再引补充（见 test_knowbot_creation_service）；这里核旧的状态照旧推得出。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.application.services.knowbot_service import KnowbotCreationService
from app.domain.knowbots.drafts import (
    KnowbotRequestIn,
    amendment_refs,
    amendment_status,
)
from tests.knowbot_fakes import (
    OWNER,
    FakeRuntime,
    MemoryKnowlab,
    manifest,
    request_body,
)


def citing(*amendment_ids: str) -> dict:
    """清单：原有的一条出处，再加引这几条补充的出处。"""
    doc = manifest()
    doc["rule_sources"] = doc["rule_sources"] + [
        {
            "rule": f"按补充 {i} 改第 6 步",
            "source": "user_text",
            "ref": f"amendment:{i}：「拿分析结论对照管理层讨论」",
        }
        for i in amendment_ids
    ]
    return doc


@pytest.mark.asyncio
async def test_old_amendments_still_show_their_status() -> None:
    repo = MemoryKnowlab()
    svc = KnowbotCreationService(repo, FakeRuntime())
    request = await svc.submit(OWNER, KnowbotRequestIn.model_validate(request_body()))
    # 0012 之前写下的补充与引它们的旧稿（仓储直接建：服务已不再收）
    used = await repo.create_amendment(
        request_id=request.id, owner_subject=OWNER, text="第 6 步改成对照管理层讨论"
    )
    waiting = await repo.create_amendment(
        request_id=request.id, owner_subject=OWNER, text="第 7 步多给一种假设"
    )
    dropped = await repo.create_amendment(
        request_id=request.id, owner_subject=OWNER, text="不要了"
    )
    await repo.decline_amendment(
        dropped.id, reason="重复", by="admin-1", at=datetime(2026, 10, 19, tzinfo=UTC)
    )
    for revision, doc in ((1, citing(str(used.id))), (2, citing(str(used.id)))):
        await repo.create_draft(
            request_id=request.id,
            request_version=1,
            revision=revision,
            version="1",
            manifest=doc,
        )
    views = {
        a.id: (status, revision)
        for a, status, revision in await svc.amendments(
            request.id, await repo.list_drafts(request_id=request.id)
        )
    }
    assert views == {
        used.id: ("used", 2),
        waiting.id: ("open", None),
        dropped.id: ("declined", None),
    }


def test_refs_need_user_text_and_a_whole_uuid() -> None:
    good = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"
    doc = {
        "rule_sources": [
            {"rule": "a", "source": "user_text", "ref": f"amendment:{good}：「原话」"},
            {"rule": "b", "source": "user_text", "ref": "想法原文第 4 问"},
            {"rule": "c", "source": "user_text", "ref": f"amendment:{good}0"},
            {"rule": "d", "source": "user_confirmed", "ref": f"amendment:{good}"},
        ]
    }
    cited, broken = amendment_refs(doc)
    assert cited == {good}
    assert broken == [f"amendment:{good}0", f"amendment:{good}"]
    assert amendment_refs(None) == (set(), [])


def test_status_comes_from_the_drafts_that_cite_it() -> None:
    a, b = (f"{n}" * 8 + "-0000-0000-0000-000000000000" for n in "ab")
    doc = citing(a)
    assert amendment_status(a, False, [(1, doc), (3, doc), (2, manifest())]) == (
        "used",
        3,
    )
    assert amendment_status(b, False, [(1, doc)]) == ("open", None)
    assert amendment_status(a, True, [(1, doc)]) == ("declined", None)
