"""每条用例的成稿与各步交回物（SDD 0037）。

刷新时跑完的用例取一次、存下，跟着那次测试；只看自己的。
"""

from __future__ import annotations

import pytest

from app.application.errors.exceptions import NotFoundError
from app.application.services.knowbot_experts import KnowbotExpertService
from app.application.services.knowbot_service import (
    NO_OUTPUTS,
    KnowbotCreationService,
)
from app.domain.knowbots.drafts import CONSENT_VERSION
from app.domain.knowbots.test_run import CONTRACT, CaseOutputsV1
from tests.knowbot_fakes import (
    OWNER,
    FakeKnowmart,
    FakeRuntime,
    MemoryKnowlab,
    accepted_request,
    list_expert,
    manifest,
    result,
)


def outputs(run_id: str, case_id: str = "c1") -> CaseOutputsV1:
    return CaseOutputsV1.model_validate(
        {
            "contract": CONTRACT,
            "run_id": run_id,
            "case_id": case_id,
            "deliverable": {
                "path": "底稿/成稿.md",
                "text": "# 成稿",
                "truncated": False,
            },
            "steps": [
                {
                    "step_id": "s1",
                    "title": "第一步",
                    "outcome": "accepted",
                    "artifact": {"answer": "x"},
                    "checks": [{"label": "格式对", "passed": True}],
                }
            ],
        }
    )


@pytest.fixture
def parts():
    repo, runtime = MemoryKnowlab(), FakeRuntime()
    return KnowbotCreationService(repo, runtime), repo, runtime


async def shown(svc: KnowbotCreationService, repo: MemoryKnowlab, test):  # type: ignore[no-untyped-def]
    found = repo.tests[test.id]
    return await svc.test_outputs(found, repo.requests[found.request_id])


async def _testing(svc: KnowbotCreationService):
    request = await accepted_request(svc)
    draft = await svc.add_draft(str(request.id), manifest())
    await svc.review_rules(str(draft.id), "admin-1")
    test = await svc.start_test(str(draft.id), budget_limit="20", timeout_seconds=600)
    return request, draft, test


@pytest.mark.asyncio
async def test_each_finished_case_is_fetched_once_and_kept_with_the_draft(parts):
    svc, repo, runtime = parts
    _, draft, test = await _testing(svc)
    run_id = test.run_id
    await svc.refresh(str(test.id))  # 还没有跑完的用例：不取
    assert runtime.outputs_asked == []
    runtime.case_outputs[(run_id, "c1")] = outputs(run_id)
    runtime.results[run_id] = result(run_id, "passed")
    done = await svc.refresh(str(test.id))
    assert done.status == "passed"
    [kept] = await shown(svc, repo, done)
    assert kept.case_id == "c1" and kept.note is None
    assert kept.outputs and kept.outputs["deliverable"]["text"] == "# 成稿"
    assert kept.fetched_at is not None
    await svc.refresh(str(test.id))  # 取过了不再取
    assert runtime.outputs_asked == [(run_id, "c1")]


@pytest.mark.asyncio
async def test_a_runtime_without_outputs_is_noted_and_not_asked_again(parts):
    svc, repo, runtime = parts
    _, draft, test = await _testing(svc)
    run_id = test.run_id
    runtime.results[run_id] = result(run_id, "failed")
    failed = await svc.refresh(str(test.id))
    [kept] = await shown(svc, repo, failed)
    assert kept.outputs is None and kept.note == NO_OUTPUTS
    await svc.refresh(str(test.id))
    assert runtime.outputs_asked == [(run_id, "c1")]


@pytest.mark.asyncio
async def test_an_unreachable_runtime_is_retried_on_a_later_refresh(parts):
    """取不到不挡刷新：结论照记；测完以后再刷新，补取。"""
    svc, repo, runtime = parts
    _, draft, test = await _testing(svc)
    run_id = test.run_id
    runtime.fail_outputs = True
    runtime.results[run_id] = result(run_id, "failed")
    failed = await svc.refresh(str(test.id))
    assert failed.status == "failed"
    assert await shown(svc, repo, failed) == []
    runtime.fail_outputs = False
    runtime.case_outputs[(run_id, "c1")] = outputs(run_id)
    again = await svc.refresh(str(test.id))
    [kept] = await shown(svc, repo, again)
    assert kept.outputs is not None


@pytest.mark.asyncio
async def test_outputs_belong_to_their_own_test(parts):
    svc, repo, runtime = parts
    _, draft, test = await _testing(svc)
    run_id = test.run_id
    runtime.case_outputs[(run_id, "c1")] = outputs(run_id)
    runtime.results[run_id] = result(run_id, "error")
    await svc.refresh(str(test.id))
    assert len(await shown(svc, repo, test)) == 1
    # 平台出错后重测同一位准专家：新编号，产出各是各的
    again = await svc.start_test(str(draft.id), budget_limit="20", timeout_seconds=600)
    assert again.number == 2
    assert await shown(svc, repo, again) == []
    assert len(await shown(svc, repo, test)) == 1


@pytest.mark.asyncio
async def test_users_only_reach_their_own_ideas_drafts_and_tests(parts):
    svc, _, runtime = parts
    request, draft, test = await _testing(svc)
    run_id = test.run_id
    runtime.results[run_id] = result(run_id, "passed")
    await svc.refresh(str(test.id))
    assert (await svc.my_request(OWNER, str(request.id))).id == request.id
    found, owner_request = await svc.my_draft(OWNER, str(draft.id))
    assert found.id == draft.id and owner_request.id == request.id
    assert (await svc.my_test(OWNER, str(test.id)))[0].id == test.id
    for call in (
        svc.my_request("someone-else", str(request.id)),
        svc.my_draft("someone-else", str(draft.id)),
        svc.my_draft(OWNER, "not-a-uuid"),
        svc.my_test("someone-else", str(test.id)),
        svc.my_test(OWNER, "not-a-uuid"),
    ):
        with pytest.raises(NotFoundError):
            await call


@pytest.mark.asyncio
async def test_old_consent_means_no_outputs_until_the_user_agrees(parts):
    """老创意是在「测试产出不给任何人看」下同意的：不取、不显示。

    用户同意新说明后才有（审读 10-19）。
    """
    svc, repo, runtime = parts
    request, draft, test = await _testing(svc)
    run_id = test.run_id
    assert (
        repo.requests[request.id].consent_version == CONSENT_VERSION
    )  # 新提交就是新说明
    await repo.set_consent_version(request.id, 1)
    runtime.case_outputs[(run_id, "c1")] = outputs(run_id)
    runtime.results[run_id] = result(run_id, "failed")
    failed = await svc.refresh(str(test.id))
    assert runtime.outputs_asked == []
    assert await shown(svc, repo, failed) is None
    with pytest.raises(NotFoundError):
        await svc.consent("someone-else", str(request.id))
    await svc.consent(OWNER, str(request.id))
    await svc.consent(OWNER, str(request.id))  # 再点一次也行
    again = await svc.refresh(str(test.id))
    [kept] = await shown(svc, repo, again)
    assert kept.outputs is not None


@pytest.mark.asyncio
async def test_one_refresh_takes_at_most_ten_and_stops_when_the_runtime_is_down(parts):
    from app.application.services.knowbot_service import OUTPUTS_PER_REFRESH

    svc, repo, runtime = parts
    _, draft, test = await _testing(svc)
    run_id = test.run_id
    many = [
        {"case_id": f"c{n}", "status": "failed", "steps": [], "expectations": []}
        for n in range(OUTPUTS_PER_REFRESH + 3)
    ]
    runtime.results[run_id] = result(run_id, "failed", cases=many)
    runtime.fail_outputs = True
    await svc.refresh(str(test.id))
    assert runtime.outputs_asked == [(run_id, "c0")]  # 不可达：这一轮就停
    runtime.fail_outputs = False
    await svc.refresh(str(test.id))
    assert len(await shown(svc, repo, test)) == OUTPUTS_PER_REFRESH
    await svc.refresh(str(test.id))
    assert len(await shown(svc, repo, test)) == OUTPUTS_PER_REFRESH + 3


@pytest.mark.asyncio
async def test_a_reply_outside_the_contract_is_noted_and_not_fetched_again(parts):
    svc, repo, runtime = parts
    _, draft, test = await _testing(svc)
    run_id = test.run_id
    runtime.bad_outputs = True
    runtime.results[run_id] = result(run_id, "failed")
    failed = await svc.refresh(str(test.id))
    [kept] = await shown(svc, repo, failed)
    assert kept.outputs is None and "test_run_contract_invalid" in (kept.note or "")
    await svc.refresh(str(test.id))
    assert runtime.outputs_asked == [(run_id, "c1")]


@pytest.mark.asyncio
async def test_outputs_are_cleared_90_days_after_listing_or_a_newer_draft(parts):
    from datetime import timedelta

    from app.domain.knowbots.drafts import OUTPUTS_EXPIRED

    svc, repo, runtime = parts
    request, draft, test = await _testing(svc)
    run_id = test.run_id
    runtime.case_outputs[(run_id, "c1")] = outputs(run_id)
    runtime.results[run_id] = result(run_id, "passed")
    await svc.refresh(str(test.id))
    [expert] = repo.experts.values()
    listed = await list_expert(KnowbotExpertService(repo, FakeKnowmart()), expert.id)
    assert await svc.expire_test_outputs() == 0  # 刚上架：留着
    at = listed.listed_at
    assert at is not None
    svc.now = lambda: at + timedelta(days=91)
    assert await svc.expire_test_outputs() == 1
    [kept] = await shown(svc, repo, test)
    assert kept.outputs is None and kept.note == OUTPUTS_EXPIRED
