"""PR-3 KnowLab suite: formal-handoff/v2 & SelectionService Five-Element guidance."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.application.services.incubation_selection import SelectionService
from app.application.services.knowbot_service import KnowbotCreationService
from app.application.services.method_drafts import (
    CreateMethodDraftIn,
    MethodDraftService,
)
from app.domain.knowbots.formal_method_admission import FreezeFormalMethod
from app.domain.knowbots.method_demand import FormalHandoff
from tests.knowbot_fakes import OWNER, own_computer
from tests.test_formal_method_execution import complete_trial, prepared


@pytest.mark.asyncio
async def test_formal_handoff_v2_persists_htn_tree_and_draft_contract():
    repo, runtime, _, _, _ = await prepared()
    svc = KnowbotCreationService(repo, runtime)
    v2_body = FormalHandoff(
        contract="knowlab/formal-handoff/v2",
        owner_subject=OWNER,
        project_id="00000000-0000-4000-8000-000000000001",
        task_id="00000000-0000-4000-8000-000000000002",
        key="knowbench:formal-handoff:v2-1",
        question="按多维口径核对原表并输出标准化行集",
        gap="缺少原表列提取子工具",
        review_digest="sha256:" + "a" * 64,
        htn_tree={"catalog_digest": "b" * 64, "nodes": [{"id": "n1", "goal": "核对"}]},
        draft_contract={"archetype_id": "S1", "title": "原表核对"},
    )
    created, is_new = await svc.submit_from_runtime(OWNER, v2_body, runtime="knowbench")
    assert is_new is True
    assert created.origin["handoff_contract"] == "knowlab/formal-handoff/v2"
    assert created.origin["htn_tree"]["catalog_digest"] == "b" * 64
    wd = await repo.get_workdraft(created.id)
    assert wd is not None
    assert wd.draft_contract["title"] == "原表核对"
    assert str(wd.method_id) == created.origin["studio_method_id"]
    assert wd.htn_state == {"tree": v2_body.htn_tree}

    # v1 rejects v2-only fields cleanly.
    with pytest.raises(ValueError, match="formal-handoff/v2"):
        FormalHandoff(
            contract="knowlab/formal-handoff/v1",
            owner_subject=OWNER,
            project_id="00000000-0000-4000-8000-000000000001",
            task_id="00000000-0000-4000-8000-000000000002",
            key="knowbench:formal-handoff:v1-bad",
            question="核对",
            gap="缺工具",
            review_digest="sha256:" + "c" * 64,
            htn_tree={"nodes": []},
        )


@pytest.mark.asyncio
async def test_selection_catalog_annotates_reachability_from_method_draft_v2(
    monkeypatch,
):
    repo, runtime, methods, mid, command = await prepared()
    await methods.execute(OWNER, str(mid), command)
    complete_trial(runtime, command, business=True)
    await methods.freeze(
        OWNER,
        str(mid),
        1,
        FreezeFormalMethod(
            request_id=command.request_id,
            revision=command.revision,
            run_id=command.run_id,
            visibility="private",
        ),
    )
    monkeypatch.setattr(
        runtime,
        "incubation_tool_versions",
        AsyncMock(return_value={"items": [], "next": None}),
        raising=False,
    )
    monkeypatch.setattr(
        runtime,
        "formal_method_sources",
        AsyncMock(return_value={"items": []}),
        raising=False,
    )
    monkeypatch.setattr(
        runtime,
        "planning_capabilities",
        AsyncMock(
            return_value={
                "patterns": dict.fromkeys(
                    (
                        "sequence",
                        "join",
                        "independent_review",
                        "object_batch",
                        "conditional",
                    ),
                    True,
                )
            }
        ),
        raising=False,
    )
    own_computer(repo)
    draft_svc = MethodDraftService(repo)
    created_draft = await draft_svc.create(
        OWNER,
        CreateMethodDraftIn(
            title="交付核对专家",
            purpose="核对并产出结果",
            archetype="S1",
            method_kind="expert",
        ),
    )
    anchored = await draft_svc.ensure_runtime_anchor(OWNER, created_draft["method_id"])
    rid = str(anchored.id)
    # Attach expected_domain_id on the V2 draft promise and verify catalog guidance.
    wd = await repo.get_method_workdraft(
        method_id=__import__("uuid").UUID(created_draft["method_id"])
    )
    assert wd is not None
    v2 = dict(wd.draft_contract)
    v2["expected_deliverables"][0]["expected_domain_id"] = "table.normalized_rows"
    await repo.update_workdraft(
        wd.id,
        body=wd.body,
        sources=wd.sources,
        revision=wd.revision + 1,
        draft_contract=v2,
    )

    ctx = await SelectionService(repo, runtime).catalog(OWNER, rid)
    first_hint = ctx["target_deliverables"][0]
    assert first_hint["expected_domain_id"] == "table.normalized_rows"
    assert "m1" in ctx["methods"]
    assert ctx["methods"]["m1"]["reachability"] in (
        "direct_deliverable",
        "upstream_producer",
        "available",
    )
