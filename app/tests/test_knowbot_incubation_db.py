"""迁移 20261028_0018（AI 孵化的一轮与缺口）与孵化在真 PostgreSQL 上：已有的问题照旧挂
在准专家上；孵化表与约束（一份工作稿同时只有一轮没结束、成了才有交回）；缺口挂在那一轮与
名称上、同一轮同一个缺口只记一次；发起、取回、接受建议、出一版走真仓储；退回删掉缺口与
孵化表。一次性 schema。"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.application.errors.exceptions import ConcurrencyConflictError
from app.application.services.knowbot_incubation import KnowbotIncubationService
from app.application.services.knowbot_questions import AnswerIn, KnowbotQuestionService
from app.application.services.knowbot_workdraft import KnowbotWorkdraftService
from app.domain.knowbots.incubation import IncubationResultV1
from app.infrastructure.repositories.knowbots import SqlKnowlabRepository
from app.infrastructure.storage.postgres import make_session_factory
from tests.knowbot_fakes import (
    COMPUTER,
    COMPUTER_ROOT,
    FakeIncubator,
    FakeRuntime,
    request_body,
)
from tests.test_knowbot_repository_db import ROOT, _drop, _run, _schema, _versions

TARGET = ROOT / "alembic/versions/20261028_0018_incubations.py"
OWNER = "user-123"
REQUEST, DRAFT = uuid.UUID(int=1), uuid.UUID(int=11)
IDEA = request_body()["idea"]
STEP = {
    "title": "数据摸底",
    "does": "看清数据有哪些表、哪些报告期。",
    "rules": [{"id": "r1", "text": "合并、母公司都有时问用户。"}],
    "outputs": [{"name": "periods", "type": "list"}],
}


async def _rows(c) -> None:  # type: ignore[no-untyped-def]
    await c.execute(
        text(
            "INSERT INTO knowbot_request (id, owner_subject, title, runtime, idea,"
            " cases) VALUES (:r, :o, '财务分析与预测', 'knowbench', CAST(:i AS jsonb),"
            " '[]')"
        ),
        {"r": REQUEST, "o": OWNER, "i": json.dumps(IDEA)},
    )
    await c.execute(
        text(
            "INSERT INTO knowbot_request_version (request_id, version, idea, cases,"
            " consent_version, created_by) VALUES (:r, 1, CAST(:i AS jsonb), '[]', 2,"
            " :o)"
        ),
        {"r": REQUEST, "o": OWNER, "i": json.dumps(IDEA)},
    )
    await c.execute(
        text(
            "INSERT INTO knowbot_draft (id, request_id, revision, version, status,"
            " manifest, request_version) VALUES (:d, :r, 1, '1', 'pending_test',"
            " '{}', 1)"
        ),
        {"d": DRAFT, "r": REQUEST},
    )
    await c.execute(
        text(
            "INSERT INTO knowbot_question (id, draft_id, run_id, question_id, case_id,"
            " kind, text, options, allow_free_text, asked_at, route) VALUES"
            " (gen_random_uuid(), :d, 'run-1', 'q1', 'c1', 'question', '口径？', '[]',"
            " true, '2026-10-28T00:00:00Z', 'user')"
        ),
        {"d": DRAFT},
    )


async def _all(c, sql: str) -> list:  # type: ignore[no-untyped-def]
    return [tuple(r) for r in (await c.execute(text(sql))).all()]


@pytest.mark.asyncio
async def test_incubations_migrate_round_trip_and_run_for_real() -> None:
    engine, schema = await _schema("20261028_0017_workdrafts")
    try:
        async with engine.begin() as c:
            await _rows(c)
        async with engine.begin() as c:
            await c.run_sync(lambda conn: _run(conn, [TARGET]))
        async with engine.begin() as c:
            # 已有的问题照旧挂在准专家上
            assert await _all(
                c, "SELECT kind, incubation_id, request_id FROM knowbot_question"
            ) == [("question", None, None)]
        # 缺口不挂准专家；测试中的问题不挂孵化
        for bad in (
            "INSERT INTO knowbot_question (id, draft_id, run_id, question_id,"
            " case_id, kind, text, options, allow_free_text, asked_at, route) VALUES"
            " (gen_random_uuid(), :d, 'r', 'g', '', 'gap', 'x', '[]', true, 'a',"
            " 'user')",
            "INSERT INTO knowbot_question (id, run_id, question_id, case_id, kind,"
            " text, options, allow_free_text, asked_at, route) VALUES"
            " (gen_random_uuid(), 'r', 'q', 'c', 'question', 'x', '[]', true, 'a',"
            " 'user')",
        ):
            with pytest.raises(DBAPIError):
                async with engine.begin() as c:
                    await c.execute(text(bad), {"d": DRAFT})

        # 之后的迁移也跑上，仓储读的是最新的表
        later = [p for p in _versions() if p.stem > TARGET.stem]
        async with engine.begin() as c:
            await c.run_sync(lambda conn: _run(conn, later))
        factory = make_session_factory(engine)
        incubator, runtime = FakeIncubator(), FakeRuntime()

        def services(session):  # type: ignore[no-untyped-def]
            repo = SqlKnowlabRepository(session)
            return (
                KnowbotIncubationService(repo, incubator, runtime),
                KnowbotWorkdraftService(repo),
                KnowbotQuestionService(repo, runtime),
            )

        async with factory() as session:
            inc, _, _ = services(session)
            record = await inc.start(
                OWNER,
                str(REQUEST),
                action="draft",
                environment_id=COMPUTER,
                workspace_root=COMPUTER_ROOT,
            )
        assert (record.round, record.status) == (1, "queued")
        async with factory() as session:
            inc, _, _ = services(session)
            with pytest.raises(ConcurrencyConflictError) as caught:
                await inc.start(OWNER, str(REQUEST), action="draft")
            assert caught.value.code == "incubation_running"
        incubator.results[record.run_id] = IncubationResultV1.model_validate(
            {
                "contract": "knowbot-incubation/v1",
                "incubation_id": record.run_id,
                "status": "succeeded",
                "cost": {"spent": "1.2"},
                "handback": {
                    "sources": [
                        {
                            "path": "scope/does",
                            "kind": "idea",
                            "quote": IDEA["answers"]["solves"],
                        },
                        {"path": "steps/overview", "kind": "proposal"},
                    ],
                    "gaps": [
                        {"id": "g1", "question": "口径？", "proposal": "合并"},
                        {"id": "g2", "question": "几年？", "proposal": "三年"},
                    ],
                    "suggestions": [
                        {
                            "id": "s1",
                            "path": "scope/does",
                            "op": "set",
                            "value": "分析三张表",
                            "reason": "创意里写了",
                        },
                        {
                            "id": "s2",
                            "path": "steps/overview",
                            "op": "add",
                            "value": STEP,
                            "reason": "先摸底",
                        },
                    ],
                },
            }
        )
        async with factory() as session:
            inc, _, _ = services(session)
            done = await inc.refresh(OWNER, str(REQUEST), str(record.id))
            assert (done.status, done.spent) == ("succeeded", "1.2")
            assert (done.review or {})["s1"]["verified"] is True
            gaps = await inc.gaps(done)
            assert [(g.question_id, g.proposal) for g in gaps] == [
                ("g1", "合并"),
                ("g2", "三年"),
            ]
            # 同一轮的同一个缺口只记一次
            await SqlKnowlabRepository(session).add_gaps(
                done,
                [{"question_id": "g1", "text": "口径？", "asked_at": "a"}],
            )
            await session.commit()
            assert len(await inc.gaps(done)) == 2
        async with factory() as session:
            inc, works, questions = services(session)
            assert await questions.operator_queue() == []
            assert len(await questions.user_queue(OWNER)) == 2
            await questions.answer_as_user(
                OWNER, str(gaps[0].id), AnswerIn(option_id="proposal")
            )
            stored, _ = await inc.decide(
                OWNER, str(REQUEST), str(done.id), "s1", action="accept", revision=0
            )
            assert stored is not None
            stored, after = await inc.decide(
                OWNER, str(REQUEST), str(done.id), "s2", action="accept", revision=1
            )
            assert after.decisions["s2"]["state"] == "accepted"
            draft = await works.publish(OWNER, str(REQUEST))
            assert draft.mode == "self"
        async with engine.begin() as c:
            kinds = await _all(
                c,
                "SELECT kind, count(*) FROM knowbot_workdraft_record"
                " WHERE suggestion_ref IS NOT NULL GROUP BY kind",
            )
            assert kinds == [("accept", 5)]
            # 成了才有交回；同一份工作稿同时只有一轮没结束
            with pytest.raises(DBAPIError):
                async with c.begin_nested():
                    await c.execute(
                        text("UPDATE knowbot_incubation SET handback = NULL")
                    )
            another = (
                "INSERT INTO knowbot_incubation (id, request_id, workdraft_id,"
                " owner_subject, round, action, status, budget_limit,"
                " timeout_seconds, run_id, environment_id, workspace_root,"
                " request_version, seed, workdraft_revision) SELECT"
                " gen_random_uuid(), request_id, workdraft_id, owner_subject, :n,"
                " 'draft', 'queued', '1', 60, :run, 'e', '/', 1, '{}', 0"
                " FROM knowbot_incubation WHERE round = 1"
            )
            async with c.begin_nested():
                await c.execute(text(another), {"n": 9, "run": "x"})
            with pytest.raises(DBAPIError):
                async with c.begin_nested():
                    await c.execute(text(another), {"n": 10, "run": "y"})
            await c.execute(text("DELETE FROM knowbot_incubation WHERE round = 9"))
        # 退回：缺口与孵化表没了，问题表退回只挂准专家
        async with engine.begin() as c:
            await c.run_sync(lambda conn: _run(conn, [TARGET], "downgrade"))
        async with engine.begin() as c:
            found = await c.execute(text("SELECT to_regclass('knowbot_incubation')"))
            assert found.scalar() is None
            assert await _all(c, "SELECT kind FROM knowbot_question") == [("question",)]
            columns = await _all(
                c,
                "SELECT column_name, is_nullable FROM information_schema.columns"
                " WHERE table_schema = current_schema()"
                " AND table_name = 'knowbot_question'"
                " AND column_name IN ('draft_id', 'incubation_id', 'proposal')",
            )
            assert columns == [("draft_id", "NO")]
    finally:
        await _drop(engine, schema)
