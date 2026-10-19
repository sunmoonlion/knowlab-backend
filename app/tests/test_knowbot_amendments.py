"""补充方法（SDD 0035）。

用户写、进规则出处、建稿时核引用、核对出处时挡住没处理的补充。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.application.errors.exceptions import (
    BadRequestError,
    ConcurrencyConflictError,
    NotFoundError,
)
from app.application.services.knowbot_service import KnowbotCreationService
from app.domain.knowbots.drafts import (
    MAX_AMENDMENTS,
    KnowbotRequestIn,
    amendment_refs,
    amendment_status,
    amendments_to_handle,
)
from tests.knowbot_fakes import (
    OWNER,
    FakeKnowmart,
    FakeRuntime,
    MemoryKnowlab,
    manifest,
    request_body,
    result,
)


@pytest.fixture
def parts():
    repo = MemoryKnowlab()
    svc = KnowbotCreationService(
        repo,
        FakeRuntime(),
        FakeKnowmart(),
        now=lambda: datetime(2026, 10, 19, tzinfo=UTC),
    )
    return svc, repo


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


async def _request(svc: KnowbotCreationService):
    return await svc.submit(OWNER, KnowbotRequestIn.model_validate(request_body()))


async def _fail(svc: KnowbotCreationService, repo: MemoryKnowlab, draft_id) -> None:
    """把一稿测成没过，好建下一稿。"""
    await svc.review_rules(str(draft_id), "admin-1")
    run = await svc.start_test(str(draft_id), budget_limit="20", timeout_seconds=600)
    svc.runtimes.results[run.run_id] = result(run.run_id, "failed")  # type: ignore[attr-defined]
    await svc.refresh(str(draft_id))
    assert repo.drafts[draft_id].status == "failed"


@pytest.mark.asyncio
async def test_users_amend_their_own_requests_and_nobody_elses(parts) -> None:
    svc, repo = parts
    request = await _request(svc)
    found = await svc.amend(OWNER, str(request.id), "  第 6 步改成对照管理层讨论  ")
    assert found.text == "第 6 步改成对照管理层讨论"
    [(item, status, revision)] = await svc.amendments(request.id, [])
    assert (item.id, status, revision) == (found.id, "open", None)
    with pytest.raises(NotFoundError):
        await svc.amend("someone-else", str(request.id), "改一下")
    with pytest.raises(NotFoundError):
        await svc.amend(OWNER, "00000000-0000-0000-0000-000000000000", "改一下")
    with pytest.raises(NotFoundError):
        await svc.amend(OWNER, "not-an-id", "改一下")
    assert len(repo.amendments) == 1


@pytest.mark.asyncio
async def test_empty_too_long_and_too_many_amendments_are_refused(parts) -> None:
    svc, repo = parts
    request = await _request(svc)
    for text in ("", "   ", "字" * 4001):
        with pytest.raises(BadRequestError) as raised:
            await svc.amend(OWNER, str(request.id), text)
        assert raised.value.code == "amendment_invalid"
    await svc.amend(OWNER, str(request.id), "字" * 4000)
    for i in range(MAX_AMENDMENTS - 1):
        await svc.amend(OWNER, str(request.id), f"第 {i} 条")
    with pytest.raises(ConcurrencyConflictError) as too_many:
        await svc.amend(OWNER, str(request.id), "再一条")
    assert too_many.value.code == "too_many_amendments"
    assert len(repo.amendments) == MAX_AMENDMENTS


@pytest.mark.asyncio
async def test_a_draft_may_cite_only_this_requests_live_amendments(parts) -> None:
    svc, _ = parts
    request = await _request(svc)
    other = await _request(svc)
    mine = await svc.amend(OWNER, str(request.id), "第 6 步改成对照管理层讨论")
    theirs = await svc.amend(OWNER, str(other.id), "别的想法的补充")
    dropped = await svc.amend(OWNER, str(request.id), "不要了")
    await svc.decline_amendment(str(dropped.id), "被第 1 条取代", "admin-1")

    for bad, why in (
        (citing(str(theirs.id)), "is not one of this request's"),
        (citing("00000000-0000-0000-0000-000000000000"), "is not one of"),
        (citing(str(dropped.id)), "was declined"),
    ):
        with pytest.raises(BadRequestError) as raised:
            await svc.add_draft(str(request.id), bad)
        assert raised.value.code == "manifest_invalid"
        assert why in raised.value.msg

    broken = manifest()
    broken["rule_sources"].append(
        {"rule": "x", "source": "user_confirmed", "ref": f"amendment:{mine.id}"}
    )
    with pytest.raises(BadRequestError) as wrong_source:
        await svc.add_draft(str(request.id), broken)
    assert "must be user_text" in wrong_source.value.msg
    misspelled = manifest()
    misspelled["rule_sources"].append(
        {"rule": "x", "source": "user_text", "ref": "amendment:3f-not-an-id"}
    )
    with pytest.raises(BadRequestError):
        await svc.add_draft(str(request.id), misspelled)

    draft = await svc.add_draft(str(request.id), citing(str(mine.id)))
    assert draft.revision == 1


@pytest.mark.asyncio
async def test_review_waits_until_every_earlier_amendment_is_handled(parts) -> None:
    svc, repo = parts
    request = await _request(svc)
    used = await svc.amend(OWNER, str(request.id), "第 6 步改成对照管理层讨论")
    ignored = await svc.amend(OWNER, str(request.id), "成稿加一张对照表")
    draft = await svc.add_draft(str(request.id), citing(str(used.id)))

    with pytest.raises(ConcurrencyConflictError) as raised:
        await svc.review_rules(str(draft.id), "admin-1")
    assert raised.value.code == "amendments_open"
    assert str(ignored.id) in raised.value.msg
    assert str(used.id) not in raised.value.msg
    assert repo.drafts[draft.id].rules_reviewed_by is None

    await svc.decline_amendment(
        str(ignored.id), "成稿格式不改，这一版先不做", "admin-1"
    )
    await svc.review_rules(str(draft.id), "admin-1")
    assert repo.drafts[draft.id].rules_reviewed_by == "admin-1"


@pytest.mark.asyncio
async def test_a_later_draft_that_drops_an_amendment_is_stopped(parts) -> None:
    """上一稿用了、这一稿没引，照样挡：改版不许悄悄丢掉用户的补充。"""
    svc, repo = parts
    request = await _request(svc)
    used = await svc.amend(OWNER, str(request.id), "第 6 步改成对照管理层讨论")
    first = await svc.add_draft(str(request.id), citing(str(used.id)))
    await _fail(svc, repo, first.id)

    second = await svc.add_draft(str(request.id), manifest())
    with pytest.raises(ConcurrencyConflictError) as raised:
        await svc.review_rules(str(second.id), "admin-1")
    assert raised.value.code == "amendments_open"
    [(_, status, revision)] = await svc.amendments(
        request.id, await svc.request_drafts(request.id)
    )
    assert (status, revision) == ("used", 1)


@pytest.mark.asyncio
async def test_amendments_written_after_the_draft_go_to_the_next_one(parts) -> None:
    svc, repo = parts
    request = await _request(svc)
    draft = await svc.add_draft(str(request.id), manifest())
    await svc.amend(OWNER, str(request.id), "测试中想到的：第 7 步多给一种假设")
    assert await svc.amendments_to_handle(repo.drafts[draft.id]) == ([], 1)
    await svc.review_rules(str(draft.id), "admin-1")


@pytest.mark.asyncio
async def test_decline_needs_a_reason_and_not_a_cited_or_declined_amendment(
    parts,
) -> None:
    svc, repo = parts
    request = await _request(svc)
    used = await svc.amend(OWNER, str(request.id), "第 6 步改成对照管理层讨论")
    other = await svc.amend(OWNER, str(request.id), "给出买入建议")
    await svc.add_draft(str(request.id), citing(str(used.id)))

    with pytest.raises(BadRequestError) as no_reason:
        await svc.decline_amendment(str(other.id), "  ", "admin-1")
    assert no_reason.value.code == "reason_required"
    with pytest.raises(BadRequestError):
        await svc.decline_amendment(str(other.id), "理" * 501, "admin-1")
    with pytest.raises(ConcurrencyConflictError) as cited:
        await svc.decline_amendment(str(used.id), "不做了", "admin-1")
    assert cited.value.code == "amendment_used"

    await svc.decline_amendment(str(other.id), "平台不给买卖建议（C-L1）", "admin-1")
    with pytest.raises(ConcurrencyConflictError) as twice:
        await svc.decline_amendment(str(other.id), "再说一次", "admin-1")
    assert twice.value.code == "amendment_declined"
    with pytest.raises(NotFoundError):
        await svc.decline_amendment("00000000-0000-0000-0000-000000000000", "x", "a")

    stored = repo.amendments[other.id]
    assert stored.text == "给出买入建议"  # 原话不动
    assert (stored.declined_reason, stored.declined_by) == (
        "平台不给买卖建议（C-L1）",
        "admin-1",
    )


@pytest.mark.asyncio
async def test_a_withdrawn_amendment_can_be_declined_once_the_latest_draft_drops_it(
    parts,
) -> None:
    """用户写新补充撤回旧的：旧的被早先的稿引过，最新一稿不引它了就能不照做。

    审读 10-19 第 7 条。
    """
    svc, repo = parts
    request = await _request(svc)
    old = await svc.amend(OWNER, str(request.id), "第 6 步改成对照管理层讨论")
    first = await svc.add_draft(str(request.id), citing(str(old.id)))
    await _fail(svc, repo, first.id)
    new = await svc.amend(OWNER, str(request.id), "撤回上一条，第 6 步照原来的做")
    with pytest.raises(ConcurrencyConflictError) as still:
        await svc.decline_amendment(str(old.id), "被下一条取代", "admin-1")
    assert still.value.code == "amendment_used"  # 最新一稿还引着
    second = await svc.add_draft(str(request.id), citing(str(new.id)))
    await svc.decline_amendment(str(old.id), "被下一条取代", "admin-1")
    assert await svc.amendments_to_handle(second) == ([], 0)
    await svc.review_rules(str(second.id), "admin-1")
    views = await svc.amendments(request.id, await svc.request_drafts(request.id))
    assert [(a.text, status) for a, status, _ in views] == [
        ("第 6 步改成对照管理层讨论", "declined"),
        ("撤回上一条，第 6 步照原来的做", "used"),
    ]


@pytest.mark.asyncio
async def test_status_comes_from_the_drafts_that_cite_it(parts) -> None:
    svc, repo = parts
    request = await _request(svc)
    used = await svc.amend(OWNER, str(request.id), "第 6 步改成对照管理层讨论")
    waiting = await svc.amend(OWNER, str(request.id), "第 7 步多给一种假设")
    dropped = await svc.amend(OWNER, str(request.id), "不要了")
    await svc.decline_amendment(str(dropped.id), "重复", "admin-1")
    first = await svc.add_draft(str(request.id), citing(str(used.id), str(waiting.id)))
    await _fail(svc, repo, first.id)
    await svc.add_draft(str(request.id), citing(str(used.id), str(waiting.id)))

    views = {
        a.id: (status, revision)
        for a, status, revision in await svc.amendments(
            request.id, await svc.request_drafts(request.id)
        )
    }
    assert views == {
        used.id: ("used", 2),
        waiting.id: ("used", 2),
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


def test_status_and_blocking_rules() -> None:
    at = datetime(2026, 10, 19, tzinfo=UTC)
    later = datetime(2026, 10, 20, tzinfo=UTC)
    a, b, c, d = (f"{n}" * 8 + "-0000-0000-0000-000000000000" for n in "abcd")
    doc = citing(a)
    assert amendment_status(a, False, [(1, doc), (3, doc), (2, manifest())]) == (
        "used",
        3,
    )
    assert amendment_status(b, False, [(1, doc)]) == ("open", None)
    assert amendment_status(a, True, [(1, doc)]) == ("declined", None)
    blocking, newer = amendments_to_handle(
        doc,
        at,
        [(a, at, False), (b, at, False), (c, at, True), (d, later, False)],
    )
    assert (blocking, newer) == ([b], 1)
