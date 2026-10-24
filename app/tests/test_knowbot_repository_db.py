"""创意到专家在真 PostgreSQL 上（一次性 schema）。

真迁移、真仓储、约束、并发；0012 的回填按 SDD 0037 第十节核。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.application.errors.exceptions import ConcurrencyConflictError
from app.application.services.knowbot_data import KnowbotDataService
from app.application.services.knowbot_experts import KnowbotExpertService, stage_of
from app.application.services.knowbot_service import KnowbotCreationService
from app.domain.knowbots.lifecycle import ExpertDecisionIn, RevisionIn
from app.infrastructure.repositories.knowbots import SqlKnowlabRepository
from app.infrastructure.storage.postgres import make_session_factory
from tests.knowbot_fakes import (
    OWNER,
    FakeKnowmart,
    FakeRuntime,
    MemoryDataStore,
    accepted_request,
    list_expert,
    manifest,
    request_body,
    result,
    waiting,
)

ROOT = Path(__file__).resolve().parents[1]


def _versions(until: str | None = None) -> list[Path]:
    found = sorted((ROOT / "alembic/versions").glob("20*.py"))
    if until is None:
        return found
    return [p for p in found if p.stem <= until]


def _run(connection, paths: list[Path], direction: str = "upgrade") -> None:
    with Operations.context(MigrationContext.configure(connection)):
        for path in paths:
            spec = importlib.util.spec_from_file_location(path.stem, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            getattr(module, direction)()


def migrate(connection):
    _run(connection, _versions())


async def _schema(until: str | None):
    url = os.environ.get("DELIVERY_TEST_DATABASE_URL")
    if not url:
        pytest.skip("set DELIVERY_TEST_DATABASE_URL to a disposable *_tests database")
    parsed = make_url(url)
    assert parsed.database and parsed.database.endswith("_tests")
    schema = "knowlab_test_" + uuid.uuid4().hex
    engine = create_async_engine(
        parsed.set(drivername="postgresql+asyncpg"),
        connect_args={"server_settings": {"search_path": schema + ",public"}},
    )
    async with engine.begin() as c:
        await c.execute(text(f'CREATE SCHEMA "{schema}"'))
        await c.execute(
            text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA public')
        )
        await c.run_sync(lambda conn: _run(conn, _versions(until)))
    return engine, schema


async def _drop(engine, schema: str) -> None:
    async with engine.begin() as c:
        await c.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    await engine.dispose()


@pytest_asyncio.fixture
async def db():
    engine, schema = await _schema(None)
    try:
        yield make_session_factory(engine)
    finally:
        await _drop(engine, schema)


RUNTIME, KNOWMART = FakeRuntime(), FakeKnowmart()


def service(session) -> KnowbotCreationService:
    return KnowbotCreationService(SqlKnowlabRepository(session), RUNTIME)


def experts(session) -> KnowbotExpertService:
    return KnowbotExpertService(SqlKnowlabRepository(session), KNOWMART)


async def _reviewed(db, owner: str = OWNER):
    async with db() as session:
        request = await accepted_request(service(session), owner)
    async with db() as session:
        draft = await service(session).add_draft(str(request.id), manifest())
    async with db() as session:
        await service(session).review_rules(str(draft.id), "admin-1")
    return request, draft


async def _start(db, draft):
    async with db() as session:
        return await service(session).start_test(
            str(draft.id), budget_limit="20", timeout_seconds=600, starter="admin-1"
        )


@pytest.mark.asyncio
async def test_the_whole_life_round_trips(db) -> None:
    request, draft = await _reviewed(db)
    test = await _start(db, draft)
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        found = await repo.find_test_by_run(test.run_id)
        assert found is not None and (found.number, found.status) == (1, "queued")
        assert await repo.find_test_by_run("knowlab-run-none") is None
    RUNTIME.results[test.run_id] = result(test.run_id, "passed")
    async with db() as session:
        passed = await service(session).refresh(str(test.id))
    assert passed.status == "passed" and passed.finished_at is not None
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        [expert] = await repo.list_experts([request.id])
        assert (expert.version, expert.test_id, stage_of(expert)) == (
            1,
            test.id,
            "to_return",
        )
        assert await repo.find_listed_expert("kb-1", "1") is None  # 还没上架
    async with db() as session:
        listed = await list_expert(experts(session), expert.id)
    assert stage_of(listed) == "listed"
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        found_expert = await repo.find_listed_expert("kb-1", "1")
        assert found_expert is not None and found_expert.id == expert.id
        assert await repo.find_listed_expert("kb-1", "2") is None
        assert await repo.find_listed_expert("kb-1", "x") is None
        stored = await repo.get_request(request.id)
        assert stored and stored.latest_version == 1
        [v1] = await repo.list_versions([request.id])
        assert v1.created_by == OWNER and v1.idea["kind"] == "six_questions"
        [acceptance] = await repo.list_acceptances([request.id])
        assert acceptance.accepted and set(acceptance.checks) == {
            "C-L5",
            "C-L2",
            "C-L1",
        }
        assert (await repo.get_draft(draft.id)).status == "passed"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_revising_keeps_old_versions_and_points_the_name_at_the_new_one(
    db,
) -> None:
    async with db() as session:
        request = await accepted_request(service(session))
    body = request_body()
    body["idea"]["answers"]["steps"] = "第二版的步骤"
    revision = RevisionIn.model_validate(
        {k: body[k] for k in ("idea", "cases", "consent_test_data")}
    )
    async with db() as session:
        v2 = await service(session).revise(OWNER, str(request.id), revision)
    assert v2.version == 2
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        stored = await repo.get_request(request.id)
        assert stored and stored.latest_version == 2
        assert stored.idea["answers"]["steps"] == "第二版的步骤"
        v1 = await repo.get_version(request.id, 1)
        assert v1 and v1.idea["answers"]["steps"] != "第二版的步骤"
    # 版本只插不改：同一个版本号再插一次，库挡住
    with pytest.raises(DBAPIError):
        async with db() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO knowbot_request_version (request_id, version, idea,"
                    " cases, consent_version, created_by)"
                    " VALUES (:r, 2, '{}'::jsonb, '[]'::jsonb, 2, 'x')"
                ),
                {"r": request.id},
            )


@pytest.mark.asyncio
async def test_the_database_refuses_bad_states_and_duplicates(db) -> None:
    request, draft = await _reviewed(db)
    test = await _start(db, draft)
    for statement in (
        f"UPDATE knowbot_draft SET status = 'approved' WHERE id = '{draft.id}'",
        "INSERT INTO knowbot_draft (id, request_id, request_version, revision,"
        f" version, status, manifest, run_attempts) VALUES ('{uuid.uuid4()}',"
        f" '{request.id}', 1, 1, '1', 'pending_test', '{{}}'::jsonb, 0)",
        "INSERT INTO knowbot_draft (id, request_id, request_version, revision,"
        f" version, status, manifest, run_attempts) VALUES ('{uuid.uuid4()}',"
        f" '{request.id}', 9, 2, '1', 'pending_test', '{{}}'::jsonb, 0)",
        f"DELETE FROM knowbot_request WHERE id = '{request.id}'",
        f"UPDATE knowbot_test SET status = 'returned' WHERE id = '{test.id}'",
        # 同一个名称同时只有一个在跑
        "INSERT INTO knowbot_test (id, request_id, number, draft_id, status, run_id,"
        f" started_at) VALUES ('{uuid.uuid4()}', '{request.id}', 2, '{draft.id}',"
        " 'running', 'knowlab-test-other', now())",
        # 不受理要写理由
        f"UPDATE knowbot_acceptance SET accepted = false WHERE request_id ="
        f" '{request.id}'",
    ):
        async with db() as session:
            with pytest.raises(DBAPIError):
                async with session.begin():
                    await session.execute(text(statement))
    RUNTIME.results[test.run_id] = result(test.run_id, "passed")
    async with db() as session:
        await service(session).refresh(str(test.id))
    for statement in (
        # 版本号不重
        "INSERT INTO knowbot_expert (id, request_id, version, draft_id, created_at)"
        f" VALUES ('{uuid.uuid4()}', '{request.id}', 1, '{draft.id}', now())",
        # 审核不过要写理由；通过要定自用或公开
        "UPDATE knowbot_expert SET review_approved = false",
        "UPDATE knowbot_expert SET review_approved = true",
        "UPDATE knowbot_expert SET stopped_at = now()",
    ):
        async with db() as session:
            with pytest.raises(DBAPIError):
                async with session.begin():
                    await session.execute(text(statement))


@pytest.mark.asyncio
async def test_concurrent_starts_leave_one_test_running(db) -> None:
    async with db() as session:
        request = await accepted_request(service(session))
    drafts = []
    for _ in range(2):
        async with db() as session:
            drafts.append(await service(session).add_draft(str(request.id), manifest()))
    assert sorted(d.revision for d in drafts) == [1, 2]
    for draft in drafts:
        async with db() as session:
            await service(session).review_rules(str(draft.id), "admin-1")

    async def start(draft) -> object:  # type: ignore[no-untyped-def]
        async with db() as session:
            return await service(session).start_test(
                str(draft.id), budget_limit="20", timeout_seconds=600
            )

    results = await asyncio.gather(*(start(d) for d in drafts), return_exceptions=True)
    conflicts = [r for r in results if isinstance(r, ConcurrencyConflictError)]
    assert len(conflicts) == 1, results
    async with db() as session:
        count = (
            await session.execute(text("SELECT count(*) FROM knowbot_test"))
        ).scalar()
    assert count == 1


@pytest.mark.asyncio
async def test_bound_data_stays_while_the_draft_or_its_expert_lives(db) -> None:
    store = MemoryDataStore()
    async with db() as session:
        data = KnowbotDataService(SqlKnowlabRepository(session), store)
        mine = await data.upload(OWNER, "年报.pdf", b"%PDF-1.7 a")
        await data.upload("someone-else", "别人的.pdf", b"%PDF-1.7 b")
        found, used = await data.mine(OWNER)
        assert [d.id for d in found] == [mine.id] and used == len(b"%PDF-1.7 a")
    async with db() as session:
        request = await accepted_request(service(session))
    binding = [{"dataset_id": str(mine.id), "version": "1", "sha256": mine.sha256}]
    async with db() as session:
        draft = await service(session).add_draft(
            str(request.id), manifest(data_bindings=binding)
        )
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        assert await repo.dataset_in_use(OWNER, mine.id)  # 待测的准专家绑着
    async with db() as session:
        await service(session).review_rules(str(draft.id), "admin-1")
    test = await _start(db, draft)
    RUNTIME.results[test.run_id] = result(test.run_id, "failed")
    async with db() as session:
        await service(session).refresh(str(test.id))
    async with db() as session:
        # 没通过：没有专家，也不在测，可以删
        assert not await SqlKnowlabRepository(session).dataset_in_use(OWNER, mine.id)
    async with db() as session:
        again = await service(session).add_draft(
            str(request.id), manifest(data_bindings=binding)
        )
    async with db() as session:
        await service(session).review_rules(str(again.id), "admin-1")
    second = await _start(db, again)
    RUNTIME.results[second.run_id] = result(second.run_id, "passed")
    async with db() as session:
        await service(session).refresh(str(second.id))
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        assert await repo.dataset_in_use(OWNER, mine.id)  # 专家还没走完
        [expert] = await repo.list_experts([request.id])
    async with db() as session:
        exp = experts(session)
        await exp.return_to_user(str(expert.id), "admin-1")
        await exp.decide(OWNER, str(expert.id), ExpertDecisionIn(accept=False))
    async with db() as session:
        # 用户不要：这一版走完了
        assert not await SqlKnowlabRepository(session).dataset_in_use(OWNER, mine.id)


@pytest.mark.asyncio
async def test_questions_are_stored_once_and_routes_respect_their_kind(db) -> None:
    from app.application.services.knowbot_questions import (
        AnswerIn,
        KnowbotQuestionService,
    )

    request, draft = await _reviewed(db)
    test = await _start(db, draft)
    RUNTIME.results[test.run_id] = waiting(test.run_id)
    for _ in range(2):
        async with db() as session:
            await service(session).refresh(str(test.id))
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        found = await repo.list_questions(draft_id=draft.id, open_only=True)
        assert sorted(q.question_id for q in found) == [
            "q-approve",
            "q-judge",
            "q-scope",
        ]
        scope = next(q for q in found if q.key == "period")
        approve = next(q for q in found if q.kind == "tool_approval")
        # 先归谁（审读第 2 条）：审批归我们，别的建出来就归用户
        assert approve.route is None and scope.route == "user"
        asks = KnowbotQuestionService(repo, RUNTIME)
        await asks.answer_as_user(OWNER, str(scope.id), AnswerIn(option_id="3y"))
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        stored = await repo.get_request(request.id)
        assert stored and stored.case_answers == {"c1": {"period": "3y"}}
        judge = next(
            q
            for q in await repo.list_questions(draft_id=draft.id, open_only=True)
            if q.question_id == "q-judge"
        )
        await KnowbotQuestionService(repo, RUNTIME).take(str(judge.id), "跑法问题")
    async with db() as session:
        taken = await SqlKnowlabRepository(session).get_question(judge.id)
        assert taken and (taken.route, taken.route_reason) == ("operator", "跑法问题")
    # 库里的约束兜底：审批不能转给谁；改派给我们必须写理由
    for sql, q in [
        ("UPDATE knowbot_question SET route = 'user' WHERE id = :q", approve.id),
        (
            "UPDATE knowbot_question SET route_reason = NULL WHERE id = :q",
            judge.id,
        ),
        ("UPDATE knowbot_question SET route = NULL WHERE id = :q", judge.id),
    ]:
        async with db() as session:
            with pytest.raises(DBAPIError):
                async with session.begin():
                    await session.execute(text(sql), {"q": str(q)})


def _step(name: str, direction: str):
    """只跑一条迁移的 upgrade 或 downgrade。"""

    def run(connection) -> None:
        _run(connection, [ROOT / "alembic/versions" / f"{name}.py"], direction)

    return run


@pytest.mark.asyncio
async def test_the_route_migration_moves_rows_that_were_already_there(db) -> None:
    """0007 上线时库里已有的问题：没答的转给用户，运营直接答过的记成运营改派的。"""
    _, draft = await _reviewed(db)
    test = await _start(db, draft)
    RUNTIME.results[test.run_id] = waiting(test.run_id)
    async with db() as session:
        await service(session).refresh(str(test.id))
    name = "20261018_0007_question_route"
    async with db() as session, session.begin():
        connection = await session.connection()
        await connection.run_sync(_step(name, "downgrade"))
        # 0006 时的样子：路由都空着，有一条是运营直接答的
        await session.execute(text("UPDATE knowbot_question SET route = NULL"))
        await session.execute(
            text(
                'UPDATE knowbot_question SET answer = \'{"option_id": "3y"}\','
                " answered_by = 'operator' WHERE question_id = 'q-scope'"
            )
        )
        await connection.run_sync(_step(name, "upgrade"))
    async with db() as session:
        rows = await session.execute(
            text("SELECT question_id, route, route_reason FROM knowbot_question")
        )
        assert {r[0]: (r[1], r[2]) for r in rows} == {
            "q-approve": (None, None),
            "q-scope": ("operator", "迁移前运营直接答的"),
            "q-judge": ("user", None),
        }


@pytest.mark.asyncio
async def test_old_amendments_on_the_real_database(db) -> None:
    """补充方法（SDD 0035，已取消）：旧的照旧可查；真约束；迁移能退能进。"""
    async with db() as session:
        request = await accepted_request(service(session))
    async with db() as session, session.begin():
        repo = SqlKnowlabRepository(session)
        used = await repo.create_amendment(
            request_id=request.id, owner_subject=OWNER, text="第 6 步改成对照"
        )
        await repo.create_amendment(
            request_id=request.id, owner_subject=OWNER, text="第 7 步多一种"
        )
        await repo.decline_amendment(
            used.id, reason="下一版再说", by="admin-1", at=datetime.now(UTC)
        )
    async with db() as session:
        svc = service(session)
        views = await svc.amendments(request.id, [])
        # 同一个事务里写的两条，提交时间一样：不按先后比
        assert {a.text: status for a, status, _ in views} == {
            "第 6 步改成对照": "declined",
            "第 7 步多一种": "open",
        }
    for bad in (
        "UPDATE knowbot_amendment SET text = ''",
        "UPDATE knowbot_amendment SET declined_reason = NULL"
        " WHERE declined_at IS NOT NULL",
        "UPDATE knowbot_amendment SET declined_at = now() WHERE declined_at IS NULL",
    ):
        with pytest.raises(DBAPIError):
            async with db() as session, session.begin():
                await session.execute(text(bad))

    name = "20261019_0008_knowbot_amendments"
    async with db() as session, session.begin():
        connection = await session.connection()
        await connection.run_sync(_step(name, "downgrade"))
        await connection.run_sync(_step(name, "upgrade"))
    async with db() as session:
        count = await session.execute(text("SELECT count(*) FROM knowbot_amendment"))
        assert count.scalar_one() == 0


@pytest.mark.asyncio
async def test_test_outputs_on_the_real_database(db) -> None:
    """每条用例的产出（SDD 0037）：刷新时取一次存下、只取一次；约束与迁移。"""
    from app.domain.knowbots.test_run import CONTRACT, CaseOutputsV1

    request, draft = await _reviewed(db)
    test = await _start(db, draft)
    RUNTIME.case_outputs[(test.run_id, "c1")] = CaseOutputsV1.model_validate(
        {
            "contract": CONTRACT,
            "run_id": test.run_id,
            "case_id": "c1",
            "deliverable": {"path": "底稿/成稿.md", "text": "# 成稿\n\n营业收入上升"},
        }
    )
    RUNTIME.results[test.run_id] = result(test.run_id, "failed")
    async with db() as session:
        failed = await service(session).refresh(str(test.id))
    async with db() as session:
        await service(session).refresh(str(test.id))  # 取过了不再取
    assert RUNTIME.outputs_asked.count((test.run_id, "c1")) == 1
    async with db() as session:
        stored = await SqlKnowlabRepository(session).get_request(request.id)
        assert stored is not None
        [kept] = await service(session).test_outputs(failed, stored)
    assert kept.outputs and kept.outputs["deliverable"]["text"].endswith("营业收入上升")
    assert kept.fetched_at is not None and kept.note is None

    with pytest.raises(DBAPIError):  # 产出与原因至少有一样
        async with db() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO knowbot_test_output (draft_id, run_id, case_id)"
                    " VALUES (:d, 'r', 'c9')"
                ),
                {"d": draft.id},
            )

    name = "20261019_0009_knowbot_test_outputs"
    async with db() as session, session.begin():
        connection = await session.connection()
        await connection.run_sync(_step(name, "downgrade"))
        await connection.run_sync(_step(name, "upgrade"))


@pytest.mark.asyncio
async def test_consent_version_and_output_expiry_on_the_real_database(db) -> None:
    """同意版本与产出 90 天到期的 SQL：上架满期，或被下一位准专家取代满期。"""
    from app.application.ports.knowbots import TestOutputRecord
    from app.domain.knowbots.drafts import CONSENT_VERSION, OUTPUTS_EXPIRED

    async with db() as session:
        request = await accepted_request(service(session))
    assert request.consent_version == CONSENT_VERSION
    async with db() as session, session.begin():
        await SqlKnowlabRepository(session).set_consent_version(request.id, 1)
    async with db() as session:
        assert (
            await SqlKnowlabRepository(session).get_request(request.id)
        ).consent_version == 1  # type: ignore[union-attr]
    async with db() as session:
        first = await service(session).add_draft(str(request.id), manifest())
    async with db() as session, session.begin():
        repo = SqlKnowlabRepository(session)
        await repo.put_test_output(
            TestOutputRecord(
                draft_id=first.id, run_id="r1", case_id="c1", outputs={"steps": []}
            )
        )
        assert await repo.test_output_cases(first.id, "r1") == {"c1"}

    async def expire(at: datetime) -> int:
        async with db() as session, session.begin():
            return await SqlKnowlabRepository(session).expire_test_outputs(at)

    assert await expire(datetime.now(UTC) + timedelta(days=1)) == 0  # 没有更新的
    async with db() as session:
        await service(session).add_draft(str(request.id), manifest())
    assert await expire(datetime.now(UTC) - timedelta(days=1)) == 0  # 还没满期
    assert await expire(datetime.now(UTC) + timedelta(days=1)) == 1
    async with db() as session:
        [kept] = await SqlKnowlabRepository(session).list_test_outputs(first.id, "r1")
    assert kept.outputs is None and kept.note == OUTPUTS_EXPIRED

    # 上架满期的也清（按专家的上架时间）
    async with db() as session:
        other = await accepted_request(service(session))
    async with db() as session:
        listed_draft = await service(session).add_draft(str(other.id), manifest())
    async with db() as session, session.begin():
        repo = SqlKnowlabRepository(session)
        await repo.put_test_output(
            TestOutputRecord(
                draft_id=listed_draft.id, run_id="r2", case_id="c1", outputs={}
            )
        )
        await session.execute(
            text(
                "INSERT INTO knowbot_expert (id, request_id, version, draft_id,"
                " created_at, listed_at) VALUES (:i, :r, 1, :d, now(), now())"
            ),
            {"i": uuid.uuid4(), "r": other.id, "d": listed_draft.id},
        )
    assert await expire(datetime.now(UTC) - timedelta(days=1)) == 0
    assert await expire(datetime.now(UTC) + timedelta(days=1)) == 1

    name = "20261019_0010_knowbot_consent_version"
    async with db() as session, session.begin():
        connection = await session.connection()
        await connection.run_sync(_step(name, "downgrade"))
        await connection.run_sync(_step(name, "upgrade"))


@pytest.mark.asyncio
async def test_the_knowledge_base_moves_into_knowbase(db) -> None:
    """SDD 0042 第七节：资料编号、测试同意、测试的运行授权都记得住；旧数据搬一次。"""
    from tests.test_knowbot_knowledge_base import FakeKnowledge, token

    store, kb = MemoryDataStore(), FakeKnowledge()
    async with db() as session:
        legacy = KnowbotDataService(SqlKnowlabRepository(session), store)
        old = await legacy.upload(OWNER, "老年报.pdf", b"%PDF-1.7 old")
    async with db() as session:
        data = KnowbotDataService(SqlKnowlabRepository(session), store, knowledge=kb)
        new = await data.upload(OWNER, "新年报.pdf", b"%PDF-1.7 new", token)
        assert (new.item_id, new.object_key) == ("item-1", None)
        repo = SqlKnowlabRepository(session)
        assert [d.id for d in await repo.datasets_to_import()] == [old.id]
        assert await data.import_old() == 1
        assert await repo.datasets_to_import() == []
        await data.ensure_consent(OWNER, token)
        consent = await repo.get_test_consent(OWNER)
        assert consent is not None and consent["consent_id"] == "consent-2"
        assert sorted(i["item_id"] for i in kb.consents["consent-2"]) == [
            "imported-" + str(old.id),
            "item-1",
        ]
        # 换下来的旧同意记在待撤，撤成了划掉（Astra 评审 F03）
        assert await repo.consent_withdrawals(OWNER) == []
        async with repo.transaction():
            await repo.add_consent_withdrawal(OWNER, "old-1")
            await repo.add_consent_withdrawal(OWNER, "old-1")
            await repo.add_consent_withdrawal("someone-else", "old-2")
        assert await repo.consent_withdrawals(OWNER) == ["old-1"]
        async with repo.transaction():
            await repo.drop_consent_withdrawal("old-1")
        assert await repo.consent_withdrawals(OWNER) == []
    _, draft = await _reviewed(db)
    test = await _start(db, draft)
    async with db() as session:
        repo = SqlKnowlabRepository(session)
        async with repo.transaction():
            await repo.update_test(test.id, grant_id="grant-9")
        found = await repo.get_test(test.id)
        assert found is not None and found.grant_id == "grant-9"
    # 一份资料要么有 knowbase 的编号，要么有旧对象
    async with db() as session:
        with pytest.raises(DBAPIError, match="ck_knowbot_dataset_somewhere"):
            async with session.begin():
                await session.execute(
                    text(
                        "update knowbot_dataset"
                        " set object_key = null, version_id = null where id = :i"
                    ),
                    {"i": new.id},
                )
                await session.execute(
                    text(
                        "update knowbot_dataset"
                        " set knowbase_item_id = null where id = :i"
                    ),
                    {"i": new.id},
                )


# ---------- 0012 的回填（SDD 0037 第十节） ----------
DELIVERY = {
    "accepted_by": OWNER,
    "accepted_at": "2026-10-20T08:00:00+00:00",
    "knowbot_id": "084acc72-0000-4000-8000-000000000001",
    "version": "1",
    "digest": "sha256:" + "a" * 64,
    "delivered_at": "2026-10-20T08:01:00+00:00",
}


async def _old_rows(session) -> dict[str, uuid.UUID]:
    """0011 时的样子：一个已交付的、一个测试中的、一个没通过又退回再测中断的。"""
    ids = {k: uuid.uuid4() for k in ("r1", "r2", "d1", "d2", "d3")}
    for rid, title in ((ids["r1"], "财务分析与预测"), (ids["r2"], "另一个")):
        await session.execute(
            text(
                "INSERT INTO knowbot_request (id, owner_subject, title, runtime, idea,"
                " cases, consent_version, created_at, updated_at) VALUES (:id, :o, :t,"
                " 'knowbench', '{\"kind\": \"skill\"}'::jsonb, '[]'::jsonb, 2,"
                " '2026-10-16T00:00:00Z', '2026-10-16T00:00:00Z')"
            ),
            {"id": rid, "o": OWNER, "t": title},
        )
    passed = {"status": "passed", "finished_at": "2026-10-19T09:00:00Z"}
    rows = [
        # 名称 1：1.1 没通过；1.2 已交付（第 1 版）
        (ids["d1"], ids["r1"], 1, "failed", {"status": "failed"}, None, None),
        (ids["d2"], ids["r1"], 2, "delivered", passed, DELIVERY, None),
        # 名称 2：1.1 超时退回待测（旧规矩一次一稿，之后没有别的稿）
        (ids["d3"], ids["r2"], 1, "pending_test", None, None, "timed out; returned"),
    ]
    for did, rid, rev, status, report, delivery, error in rows:
        await session.execute(
            text(
                "INSERT INTO knowbot_draft (id, request_id, revision, version, status,"
                " manifest, run_attempts, run_id, run_started_at, run_timeout_seconds,"
                " report, delivery, last_error, created_at, updated_at) VALUES (:id,"
                " :r, :rev, :ver, :s, '{}'::jsonb, 1, :run, :at, 600,"
                " CAST(:report AS jsonb), CAST(:delivery AS jsonb), :err, :at, :at)"
            ),
            {
                "id": did,
                "r": rid,
                "rev": rev,
                "ver": "1",
                "s": status,
                "run": f"knowlab-{did}-1",
                "at": datetime(2026, 10, 17 + rev, tzinfo=UTC),
                "report": None if report is None else json.dumps(report),
                "delivery": None if delivery is None else json.dumps(delivery),
                "err": error,
            },
        )
    return ids


@pytest.mark.asyncio
async def test_0012_backfills_versions_acceptances_tests_and_experts() -> None:
    engine, schema = await _schema("20261023_0011_knowbase_items")
    try:
        factory = make_session_factory(engine)
        async with factory() as session, session.begin():
            ids = await _old_rows(session)
        path = ROOT / "alembic/versions/20261024_0012_knowbot_lifecycle.py"
        async with engine.begin() as c:
            await c.run_sync(lambda conn: _run(conn, [path]))
        async with factory() as session:
            repo = SqlKnowlabRepository(session)
            for rid in (ids["r1"], ids["r2"]):
                [v1] = await repo.list_versions([rid])
                assert (v1.version, v1.idea, v1.consent_version) == (
                    1,
                    {"kind": "skill"},
                    2,
                )
                [acceptance] = await repo.list_acceptances([rid])
                assert acceptance.accepted and acceptance.trial
                assert acceptance.decided_by == "system:trial"
            tests = await repo.list_tests(request_ids=[ids["r1"]])
            assert [(t.number, t.status, t.draft_id) for t in tests] == [
                (1, "failed", ids["d1"]),
                (2, "passed", ids["d2"]),
            ]
            [timed_out] = await repo.list_tests(request_ids=[ids["r2"]])
            assert timed_out.status == "platform_error"
            assert timed_out.error == "timed out; returned"
            assert timed_out.budget_limit is None and timed_out.started_by is None
            [expert] = await repo.list_experts([ids["r1"]])
            assert (expert.version, expert.draft_id, expert.test_id) == (
                1,
                ids["d2"],
                tests[1].id,
            )
            assert expert.trial and stage_of(expert) == "listed"
            assert expert.knowmart_knowbot_id == DELIVERY["knowbot_id"]
            assert expert.digest == DELIVERY["digest"]
            assert expert.user_accepted is True and expert.user_decided_by == OWNER
            assert expert.reviewed_at is None  # 上线后补一次运营审核记录
            assert await repo.list_experts([ids["r2"]]) == []
            d2 = await repo.get_draft(ids["d2"])
            assert d2 is not None and d2.status == "passed" and d2.request_version == 1
            found = await repo.find_listed_expert(DELIVERY["knowbot_id"], "1")
            assert found is not None and found.id == expert.id
        # 退回去再进来：退回时已上架的准专家改回「已交付」，再进来回填照旧
        async with engine.begin() as c:
            await c.run_sync(lambda conn: _run(conn, [path], "downgrade"))
        async with factory() as session:
            status = await session.execute(
                text(
                    "SELECT status, delivery ->> 'knowbot_id' FROM knowbot_draft"
                    " WHERE id = :d"
                ),
                {"d": ids["d2"]},
            )
            assert tuple(status.one()) == ("delivered", DELIVERY["knowbot_id"])
        async with engine.begin() as c:
            await c.run_sync(lambda conn: _run(conn, [path]))
        async with factory() as session:
            repo = SqlKnowlabRepository(session)
            [expert] = await repo.list_experts([ids["r1"]])
            assert expert.trial and stage_of(expert) == "listed"
    finally:
        await _drop(engine, schema)
