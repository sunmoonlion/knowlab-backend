"""提交内容的形状、清单先核的几项、状态迁移、测试契约的硬约束。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.knowbots.drafts import (
    KnowbotRequestIn,
    can_move,
    draft_manifest_problems,
)
from app.domain.knowbots.test_run import CONTRACT, Expectation, RunResultV1
from tests.knowbot_fakes import manifest, request_body


def test_a_complete_request_is_accepted() -> None:
    body = KnowbotRequestIn.model_validate(request_body())
    assert body.idea.kind == "six_questions"
    skill = request_body(idea={"kind": "skill", "skill": "# 我的方法\n先算毛利率"})
    assert KnowbotRequestIn.model_validate(skill).idea.skill


@pytest.mark.parametrize(
    "overrides",
    [
        {"consent_test_data": False},  # 不同意数据用于测试不受理
        {"idea": {"kind": "skill"}},
        {"idea": {"kind": "six_questions", "skill": "x"}},
        {"cases": []},
        {"runtime": "Investment"},
        {"title": ""},
        {"extra": 1},
    ],
)
def test_incomplete_or_unconsented_requests_are_rejected(overrides: dict) -> None:
    with pytest.raises(ValidationError):
        KnowbotRequestIn.model_validate(request_body(**overrides))


def test_case_ids_must_be_unique() -> None:
    case = request_body()["cases"][0]
    with pytest.raises(ValidationError):
        KnowbotRequestIn.model_validate(request_body(cases=[case, case]))


def test_manifest_checks() -> None:
    assert draft_manifest_problems(manifest(), runtime="investment", version="1") == []
    assert draft_manifest_problems(
        manifest(runtime="other"), runtime="investment", version="1"
    ) == ["runtime must be investment"]
    assert draft_manifest_problems(manifest(), runtime="investment", version="2")
    assert draft_manifest_problems(
        manifest(rule_sources=[]), runtime="investment", version="1"
    ) == ["every rule needs a source (rule_sources is empty)"]
    # 出处只能是用户原文或用户确认过的（C-L1），不能是我们或模型补的
    invented = [{"rule": "x", "source": "model", "ref": "r"}]
    assert draft_manifest_problems(
        manifest(rule_sources=invented), runtime="investment", version="1"
    )
    assert draft_manifest_problems([], runtime="investment", version="1")


def test_only_listed_moves_are_allowed() -> None:
    assert can_move("pending_test", "testing")
    assert can_move("testing", "pending_test")
    assert can_move("passed", "delivered")
    assert not can_move("pending_test", "passed")
    assert not can_move("failed", "testing")  # 未通过就出新草稿，不在原地重测
    assert not can_move("delivered", "pending_test")


def test_a_passed_run_needs_every_case_passed() -> None:
    with pytest.raises(ValidationError):
        RunResultV1.model_validate(
            {"contract": CONTRACT, "run_id": "run-00001", "status": "passed"}
        )
    with pytest.raises(ValidationError):
        RunResultV1.model_validate(
            {
                "contract": CONTRACT,
                "run_id": "run-00001",
                "status": "passed",
                "cases": [{"case_id": "c1", "status": "needs_human"}],
            }
        )


def test_approx_expectations_need_a_number_and_a_tolerance() -> None:
    base = {"artifact": "a", "path": "p"}
    Expectation.model_validate(base | {"kind": "approx", "value": 1, "tolerance": 0.1})
    with pytest.raises(ValidationError):
        Expectation.model_validate(base | {"kind": "approx", "value": 1})
    with pytest.raises(ValidationError):
        Expectation.model_validate(
            base | {"kind": "approx", "value": "x", "tolerance": 0.1}
        )
    with pytest.raises(ValidationError):
        Expectation.model_validate(
            base | {"kind": "equals", "value": 1, "tolerance": 0.1}
        )
