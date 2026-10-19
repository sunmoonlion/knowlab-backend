"""每条用例的成稿与各步交回物（SDD 0037）：刷新时跑完的用例取一次、存下；只看自己的。"""

from __future__ import annotations

import pytest

from app.application.errors.exceptions import NotFoundError
from app.application.services.knowbot_service import (
    NO_OUTPUTS,
    KnowbotCreationService,
)
from app.domain.knowbots.drafts import KnowbotRequestIn
from app.domain.knowbots.test_run import CONTRACT, CaseOutputsV1
from tests.knowbot_fakes import (
    OWNER,
    FakeKnowmart,
    FakeRuntime,
    MemoryKnowlab,
    manifest,
    request_body,
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
    return KnowbotCreationService(repo, runtime, FakeKnowmart()), repo, runtime


async def _testing(svc: KnowbotCreationService):
    request = await svc.submit(OWNER, KnowbotRequestIn.model_validate(request_body()))
    draft = await svc.add_draft(str(request.id), manifest())
    await svc.review_rules(str(draft.id), "admin-1")
    run = await svc.start_test(str(draft.id), budget_limit="20", timeout_seconds=600)
    return request, draft, run.run_id


@pytest.mark.asyncio
async def test_each_finished_case_is_fetched_once_and_kept_with_the_draft(parts):
    svc, _, runtime = parts
    _, draft, run_id = await _testing(svc)
    await svc.refresh(str(draft.id))  # 还没有跑完的用例：不取
    assert runtime.outputs_asked == []
    runtime.case_outputs[(run_id, "c1")] = outputs(run_id)
    runtime.results[run_id] = result(run_id, "passed")
    done = await svc.refresh(str(draft.id))
    assert done.status == "delivered"
    [kept] = await svc.test_outputs(done)
    assert kept.case_id == "c1" and kept.note is None
    assert kept.outputs and kept.outputs["deliverable"]["text"] == "# 成稿"
    assert kept.fetched_at is not None
    await svc.refresh(str(draft.id))  # 取过了不再取
    assert runtime.outputs_asked == [(run_id, "c1")]


@pytest.mark.asyncio
async def test_a_runtime_without_outputs_is_noted_and_not_asked_again(parts):
    svc, _, runtime = parts
    _, draft, run_id = await _testing(svc)
    runtime.results[run_id] = result(run_id, "failed")
    failed = await svc.refresh(str(draft.id))
    [kept] = await svc.test_outputs(failed)
    assert kept.outputs is None and kept.note == NO_OUTPUTS
    await svc.refresh(str(draft.id))
    assert runtime.outputs_asked == [(run_id, "c1")]


@pytest.mark.asyncio
async def test_an_unreachable_runtime_is_retried_on_a_later_refresh(parts):
    """取不到不挡刷新：结论照记；测完以后再刷新，补取。"""
    svc, _, runtime = parts
    _, draft, run_id = await _testing(svc)
    runtime.fail_outputs = True
    runtime.results[run_id] = result(run_id, "failed")
    failed = await svc.refresh(str(draft.id))
    assert failed.status == "failed"
    assert await svc.test_outputs(failed) == []
    runtime.fail_outputs = False
    runtime.case_outputs[(run_id, "c1")] = outputs(run_id)
    again = await svc.refresh(str(draft.id))
    [kept] = await svc.test_outputs(again)
    assert kept.outputs is not None


@pytest.mark.asyncio
async def test_outputs_belong_to_the_latest_run_of_the_draft(parts):
    svc, repo, runtime = parts
    _, draft, run_id = await _testing(svc)
    runtime.case_outputs[(run_id, "c1")] = outputs(run_id)
    runtime.results[run_id] = result(run_id, "failed")
    await svc.refresh(str(draft.id))
    # 同一稿换了一次测试（超时退回后再测）：旧一次的产出不算这一次的
    repo.drafts[draft.id] = repo.drafts[draft.id].__class__(
        **{**repo.drafts[draft.id].__dict__, "run_id": "knowlab-other-run"}
    )
    assert await svc.test_outputs(repo.drafts[draft.id]) == []


@pytest.mark.asyncio
async def test_users_only_reach_their_own_ideas_avatars_and_experts(parts):
    svc, _, runtime = parts
    request, draft, run_id = await _testing(svc)
    runtime.results[run_id] = result(run_id, "passed")
    await svc.refresh(str(draft.id))
    mine, drafts = await svc.my_request(OWNER, str(request.id))
    assert mine.id == request.id and [d.id for d in drafts] == [draft.id]
    found, owner_request = await svc.my_draft(OWNER, str(draft.id))
    assert found.id == draft.id and owner_request.id == request.id
    for call in (
        svc.my_request("someone-else", str(request.id)),
        svc.my_draft("someone-else", str(draft.id)),
        svc.my_draft(OWNER, "not-a-uuid"),
    ):
        with pytest.raises(NotFoundError):
            await call
    [(expert, _)] = await svc.my_knowbots(OWNER)
    assert expert.id == draft.id and expert.delivery
    assert expert.delivery["knowbot_id"] == "kb-1"
    assert expert.delivery["delivered_at"]
    assert await svc.my_knowbots("someone-else") == []
