"""knowlab 这边的孵化一轮（领域）：六问拼成种子、核交回的出处、照建议改工作稿（set、
add、remove、move）、改动到的格子出自哪一行、创意原句的引用与复核。"""

from __future__ import annotations

import pytest

from app.domain.knowbots.incubation import Handback, SourceEntry
from app.domain.knowbots.incubation_rounds import (
    apply_suggestion,
    review,
    seed_of,
    source_of,
)
from app.domain.knowbots.workdraft import (
    WorkdraftBody,
    empty_body,
    idea_quote,
    idea_ref,
    source_problems,
)

BODY = WorkdraftBody.model_validate(
    {
        "name": "财务",
        "steps": [
            {
                "id": "a",
                "title": "摸底",
                "rules": [{"id": "r1", "text": "一"}, {"id": "r2", "text": "二"}],
                "checks": [
                    {"template": "required", "params": {"keys": ["x"]}},
                    {"template": "max_chars", "params": {"max": 10}},
                ],
            },
            {"id": "b", "title": "趋势"},
        ],
    }
).model_dump(mode="json")


def test_seeds() -> None:
    assert seed_of({"kind": "question", "question": "收入会怎样？"}) == (
        "question",
        "收入会怎样？",
    )
    assert seed_of({"kind": "skill", "skill": "# s"}) == ("skill", "# s")
    kind, text = seed_of(
        {"kind": "six_questions", "answers": {"solves": "甲", "correctness": "乙"}}
    )
    assert kind == "six_questions"
    assert text.startswith("问：解决什么\n答：甲\n\n问：不解决什么\n答：\n\n")
    assert text.endswith("问：怎样算对\n答：乙")


def test_review_marks_what_does_not_check_out() -> None:
    handback = Handback.model_validate(
        {
            "sources": [
                {"path": "steps/a/title", "kind": "idea", "quote": "甲乙"},
                {"path": "steps/a/rules/r1", "kind": "answer", "ref": "q-1"},
                {"path": "steps/b", "kind": "answer", "ref": "q-2"},
            ],
            "suggestions": [
                {
                    "id": "s1",
                    "path": "steps/a",
                    "op": "set",
                    "value": {},
                    "reason": "r",
                },
                {
                    "id": "s2",
                    "path": "steps/b/title",
                    "op": "set",
                    "value": "x",
                    "reason": "r",
                },
                {"id": "s3", "path": "name", "op": "set", "value": "x", "reason": "r"},
            ],
        }
    )
    found = review(handback, seed_text="……甲乙……", answer_ids={"q-1"})
    assert found["s1"] == {
        "verified": True,
        "kinds": ["answer", "idea"],
        "problems": [],
    }
    assert found["s2"]["problems"] == [
        {"path": "steps/b", "problem": "answer_not_found"}
    ]
    assert found["s3"] == {"verified": True, "kinds": ["proposal"], "problems": []}


def test_source_of_picks_the_nearest_row() -> None:
    rows = [
        SourceEntry(path="steps/a", kind="proposal"),
        SourceEntry(path="steps/a/title", kind="idea", quote="甲"),
    ]
    assert source_of(rows, "steps/a/title") == rows[1]
    assert source_of(rows, "steps/a/rules/r1") == rows[0]
    assert source_of(rows, "name") is None


def test_apply_each_op() -> None:
    added = apply_suggestion(BODY, "steps/a/rules", "add", "三")
    assert [r["id"] for r in added["steps"][0]["rules"]] == ["r1", "r2", "r3"]
    added = apply_suggestion(BODY, "steps/c", "add", {"title": "新"})
    assert [s["id"] for s in added["steps"]] == ["a", "b", "c"]
    with pytest.raises(ValueError):
        apply_suggestion(BODY, "steps/a", "add", {"title": "重"})
    inserted = apply_suggestion(
        BODY, "steps/a/checks/1", "add", {"template": "non_empty", "params": {}}
    )
    assert [c["template"] for c in inserted["steps"][0]["checks"]] == [
        "required",
        "non_empty",
        "max_chars",
    ]
    removed = apply_suggestion(BODY, "steps/a/rules/r1", "remove", None)
    assert [r["id"] for r in removed["steps"][0]["rules"]] == ["r2"]
    assert (
        apply_suggestion(BODY, "steps/a/title", "remove", None)["steps"][0]["title"]
        == ""
    )
    assert apply_suggestion(BODY, "name", "remove", None)["name"] == ""
    moved = apply_suggestion(BODY, "steps/b", "move", 0)
    assert [s["id"] for s in moved["steps"]] == ["b", "a"]
    moved = apply_suggestion(BODY, "steps/a/rules/r2", "move", 0)
    assert [r["id"] for r in moved["steps"][0]["rules"]] == ["r2", "r1"]
    with pytest.raises(ValueError):
        apply_suggestion(BODY, "steps/a/title", "move", 0)
    with pytest.raises(ValueError):
        apply_suggestion(BODY, "steps/z/title", "set", "x")
    # 原来那份不动
    assert [s["id"] for s in BODY["steps"]] == ["a", "b"]


def test_idea_refs_round_trip_and_are_checked_at_publish() -> None:
    seed = "问：解决什么\n答：按我的口径分析"
    ref = idea_ref(3, seed, "按我的口径")
    assert ref is not None and ref.startswith("idea:3:")
    assert idea_quote(ref, {3: seed}) == "按我的口径"
    assert idea_ref(3, seed, "不在里面") is None
    assert idea_quote(ref, {3: seed.replace("口径", "办法")}) is None
    assert idea_quote(ref, {2: seed}) is None
    body = WorkdraftBody.model_validate(empty_body() | {"name": "随便写的名字"})
    good = {"name": {"kind": "user_text", "ref": ref}}
    kwargs = {"workdraft_id": None, "owner_subject": "u", "entries": {}}
    assert source_problems(body, good, ideas={3: seed}, **kwargs) == []
    wrong_kind = {"name": {"kind": "user_confirmed", "ref": ref}}
    (problem,) = source_problems(body, wrong_kind, ideas={3: seed}, **kwargs)
    assert problem.message == "an idea sentence is user_text"
    (problem,) = source_problems(body, good, ideas={}, **kwargs)
    assert problem.message == "the quote is not in that version of the idea"
