"""PR-2 tests: MethodDraftService, retired v2 cleanup & private leaf admission."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.application.errors.exceptions import BadRequestError, NotFoundError
from app.application.services.formal_methods import FormalMethodService
from app.application.services.knowbot_board import KnowbotBoard
from app.application.services.knowbot_service import KnowbotCreationService
from app.application.services.method_drafts import (
    BindGapResolutionIn,
    CreateMethodDraftIn,
    HandoffSeedIn,
    MethodDraftService,
    PatchMethodDraftIn,
    SpawnGapChildIn,
)
from app.domain.knowbots.drafts import KnowbotRequestIn
from app.domain.knowbots.formal_method import MethodRef, build_slot_preset
from app.domain.knowbots.formal_method_admission import (
    FreezeFormalMethod,
    ValidateFormalMethod,
)
from tests.knowbot_fakes import (
    OWNER,
    all_clear,
    historical_manifest,
    platform_draft,
    request_body,
)
from tests.test_formal_method import composite, frozen
from tests.test_formal_method_service import Repository, Runtime, draft


@pytest.mark.asyncio
async def test_retired_v2_only_requests_are_filtered_from_board_and_detail():
    repo, runtime = Repository(), Runtime()
    creation = KnowbotCreationService(repo, runtime)
    board = KnowbotBoard(repo)

    # Active new request with zero drafts remains visible.
    fresh = await creation.submit(
        OWNER, KnowbotRequestIn.model_validate(request_body())
    )
    # Legacy request that only produced a retired v2 draft disappears (W04).
    legacy = await creation.submit(
        OWNER, KnowbotRequestIn.model_validate(request_body(title="旧固定v2专家"))
    )
    await creation.accept(str(legacy.id), 1, all_clear(), "admin-1")
    await platform_draft(creation, str(legacy.id), historical_manifest())

    lives = await board.lives(OWNER)
    assert [life.request.id for life in lives] == [fresh.id]
    with pytest.raises(NotFoundError):
        await board.life(str(legacy.id), OWNER)


@pytest.mark.asyncio
async def test_method_draft_v2_c_l1_provenance_handoff_and_fractal_gap_roundtrip():
    repo, runtime = Repository(), Runtime()
    drafts = MethodDraftService(repo)
    methods = FormalMethodService(repo, runtime)

    created = await drafts.create(
        OWNER,
        CreateMethodDraftIn(
            archetype="S1",
            title="年报现金流深潜",
            purpose="按准则核查经营现金流与净利润背离",
            applies_when=("存在经审计年报",),
            excludes=("不提供买卖建议",),
        ),
    )
    parent_mid = created["method_id"]
    assert created["revision"] == 1
    assert created["provenance_verified"] is True
    assert "primary_receipt" in created["projection"]["what_to_return"]

    # Accepted AI suggestion without suggestion_ref is rejected by C-L1 gate.
    with pytest.raises(BadRequestError) as exc:
        await drafts.patch(
            OWNER,
            parent_mid,
            PatchMethodDraftIn(
                revision=1,
                elements={"purpose": "更新后的目标说明"},
                provenance_kind="accept",
            ),
        )
    assert exc.value.code == "provenance_ref_missing"

    # Freeze a child tool version with controlled domain meaning (`core.check.receipt`).
    child_mid = uuid4()
    req = await KnowbotCreationService(repo, runtime).submit(
        OWNER, KnowbotRequestIn.model_validate(request_body())
    )
    child_draft_doc = draft().model_dump(mode="json", by_alias=True)
    receipt_slot = build_slot_preset(
        "check_receipt",
        description="现金流核验回执",
        domain_id="core.check.receipt",
    ).model_dump(mode="json", by_alias=True)
    child_draft_doc["definition"]["procedure"]["outputs"]["result"] = receipt_slot
    await methods.workdrafts.save(
        OWNER,
        str(req.id),
        revision=0,
        edits=[("formal_method", child_draft_doc)],
    )
    cmd = ValidateFormalMethod(request_id=req.id, revision=1, run_id=uuid4())
    await methods.validate(OWNER, str(child_mid), cmd)
    runtime.complete(cmd.run_id)
    frozen_child = await methods.freeze(
        OWNER,
        str(child_mid),
        1,
        FreezeFormalMethod(**cmd.model_dump(), visibility="private"),
    )

    # Set an initial orchestration procedure on parent draft, confirm remaining paths.
    placeholder_ref = MethodRef(
        method_id=uuid4(), version=1, digest=frozen_child.digest
    )
    child_in = frozen_child.definition["procedure"]["inputs"]["value"]
    proc = {
        "kind": "orchestration",
        "instructions": "调用现金流勾稽步骤并导出回执",
        "max_depth": 2,
        "max_expansions": 4,
        "max_steps": 4,
        "max_replans": 0,
        "steps": [
            {
                "id": "analyze",
                "candidates": [placeholder_ref.model_dump(mode="json")],
                "select_when": "始终使用现金流勾稽算子",
                "required_when": "每次委托必经",
                "always_required": True,
                "depends_on": [],
                "inputs": {
                    "value": {
                        "slot": child_in,
                        "binding": {"source": "caller", "name": "value"},
                    }
                },
                "outputs": {"result": receipt_slot},
                "on_failure": "fail",
            }
        ],
        "exports": {
            "primary_receipt": {
                "step": "analyze",
                "output": "result",
                "always_present": True,
            }
        },
    }
    patched = await drafts.patch(
        OWNER,
        parent_mid,
        PatchMethodDraftIn(
            revision=1,
            elements={"procedure": proc},
            confirm_paths=tuple(created["draft_contract"]["unconfirmed_paths"]),
        ),
    )
    assert patched["projection"]["ready_to_freeze"] is True
    assert patched["public_contract"]["method_kind"] == "expert"
    assert (
        patched["formal_method"]["definition"]["how_to_validate"]
        == patched["draft_contract"]["validation_rules"]
    )

    # Spawn a fractal child tool draft from parent step `analyze`.
    child_view = await drafts.spawn_gap_child(
        OWNER,
        parent_mid,
        SpawnGapChildIn(
            revision=patched["revision"],
            step_id="analyze",
            node_id="n1",
            title="现金流勾稽原子算子",
            purpose="计算经营现金流净额与净利润差值",
            kind="tool",
        ),
    )
    assert child_view["parent_gap"]["parent_method_id"] == parent_mid
    assert child_view["parent_gap"]["step_id"] == "analyze"
    assert child_view["provenance_verified"] is True

    # Bind the frozen child MethodRef back into parent step `analyze`.
    bound = await drafts.bind_gap_resolution(
        OWNER,
        parent_mid,
        BindGapResolutionIn(
            revision=patched["revision"],
            step_id="analyze",
            child_reference=MethodRef(
                method_id=child_mid,
                version=1,
                digest=frozen_child.digest,
            ),
        ),
    )
    assert bound["draft_contract"]["procedure"]["steps"][0]["candidates"][0][
        "method_id"
    ] == str(child_mid)
    anchor = await drafts.ensure_runtime_anchor(OWNER, parent_mid)
    assert anchor.incubation_mode == "self"
    pending = await drafts.patch(
        OWNER,
        parent_mid,
        PatchMethodDraftIn(
            revision=bound["revision"],
            elements={"unconfirmed_paths": ["element:procedure"]},
        ),
    )
    assert pending["formal_method"] is None
    with pytest.raises(BadRequestError, match="Confirm and complete"):
        await methods.validate(
            OWNER,
            parent_mid,
            ValidateFormalMethod(
                request_id=anchor.id, revision=pending["revision"], run_id=uuid4()
            ),
        )

    # Handoff v2 seed creates a verified draft with C-L1 accept record.
    seeded = await drafts.create_from_handoff(
        OWNER,
        HandoffSeedIn(
            title="来自 KnowBench 的临时规划转孵化",
            goal="把验证过的分析步骤沉淀为正式专家",
            htn_state={"root_id": "root", "nodes": []},
        ),
    )
    assert seeded["provenance_verified"] is True
    assert seeded["sources"]["element:declaration"]["kind"] == "user_confirmed"

    # Inline source proxy registers script source outside DB transaction.
    registered_src = await methods.register_source(
        OWNER,
        str(uuid4()),
        1,
        {"kind": "script", "name": "calc.py", "content": "print('ok')\n"},
    )
    assert registered_src["kind"] == "registered_script"
    assert registered_src["license_basis"] == "owner"


@pytest.mark.asyncio
async def test_public_expert_allows_same_owner_verified_private_script_leaf():
    repo, runtime = Repository(), Runtime()
    svc = FormalMethodService(repo, runtime)
    creation = KnowbotCreationService(repo, runtime)

    # 1. Freeze a same-owner private registered_script leaf WITH execution evidence.
    leaf_req = await creation.submit(
        OWNER, KnowbotRequestIn.model_validate(request_body())
    )
    src_id = str(uuid4())
    leaf_doc = draft().model_dump(mode="json", by_alias=True)
    leaf_doc["definition"]["procedure"]["implementation"].update(
        kind="registered_script", source=src_id, version="1", entry="run.py"
    )
    leaf_doc["source"].update(
        kind="registered_script",
        source=src_id,
        version="1",
        entry="run.py",
        license_basis="owner",
    )
    await svc.workdrafts.save(
        OWNER, str(leaf_req.id), revision=0, edits=[("formal_method", leaf_doc)]
    )
    leaf_mid = uuid4()
    leaf_cmd = ValidateFormalMethod(request_id=leaf_req.id, revision=1, run_id=uuid4())
    await svc.validate(OWNER, str(leaf_mid), leaf_cmd)
    leaf_receipt = runtime.complete(leaf_cmd.run_id, business=True)
    leaf_suite_id = uuid4()
    leaf_receipt["execution"] = {
        "contract": "knowlab/formal-method-execution/v1",
        "suite_id": str(leaf_suite_id),
        "suite_version": 1,
        "suite_digest": "sha256:" + "1" * 64,
        "request_digest": "sha256:" + "2" * 64,
        "evidence_digest": "sha256:" + "3" * 64,
        "validated_callers": ["host"],
    }

    async def fake_execution(owner, method_id, request_id, run_id):
        from app.domain.knowbots.formal_method_admission import (
            FormalMethodValidationReceipt,
        )

        rec = FormalMethodValidationReceipt.model_validate(
            runtime.validation_results[leaf_cmd.run_id]["receipt"]
            if str(run_id) == str(leaf_cmd.run_id)
            else runtime.validation_results[parent_cmd.run_id]["receipt"]
        )
        return {
            "contract": "knowlab/formal-method-execution/v1",
            "run_id": str(run_id),
            "request_id": str(request_id),
            "method_id": str(method_id),
            "request_digest": "sha256:" + "2" * 64,
            "status": "passed",
            "spent": "0",
            "currency": "CNY",
            "suite": {
                "suite_id": str(leaf_suite_id),
                "version": 1,
                "digest": "sha256:" + "1" * 64,
            },
            "admission": {
                "contract": "knowlab/formal-method-validation/v1",
                "run_id": str(run_id),
                "status": "passed",
                "receipt": rec.model_dump(mode="json"),
                "missing_checks": [],
                "details": [],
            },
            "cases": [
                {
                    "case_id": "echo",
                    "repetition": 1,
                    "caller": "host",
                    "status": "passed",
                    "outcome": "succeeded",
                    "rejection_code": None,
                    "task_id": str(uuid4()),
                    "attempt_id": str(uuid4()),
                    "call_id": str(uuid4()),
                    "assertions": [
                        {"index": 0, "passed": True, "detail": ""},
                    ],
                    "detail": "",
                }
            ],
            "actions": [],
        }

    svc.execution = fake_execution  # type: ignore[method-assign]
    frozen_leaf = await svc.freeze(
        OWNER,
        str(leaf_mid),
        1,
        FreezeFormalMethod(**leaf_cmd.model_dump(), visibility="private"),
    )
    assert frozen_leaf.provenance["visibility"] == "private"

    # 2. Freeze a PUBLIC expert referencing that same-owner private leaf (M05).
    parent_req = await creation.submit(
        OWNER, KnowbotRequestIn.model_validate(request_body())
    )
    parent_defn = composite(frozen(frozen_leaf.definition, mid=leaf_mid))
    await svc.workdrafts.save(
        OWNER,
        str(parent_req.id),
        revision=0,
        edits=[
            (
                "formal_method",
                {
                    "definition": parent_defn,
                    "source": None,
                    "allowed_callers": ["host"],
                },
            )
        ],
    )
    parent_mid = uuid4()
    parent_cmd = ValidateFormalMethod(
        request_id=parent_req.id, revision=1, run_id=uuid4()
    )
    await svc.validate(OWNER, str(parent_mid), parent_cmd)
    parent_receipt = runtime.complete(parent_cmd.run_id, business=True)
    parent_receipt["execution"] = dict(leaf_receipt["execution"])
    frozen_parent = await svc.freeze(
        OWNER,
        str(parent_mid),
        1,
        FreezeFormalMethod(**parent_cmd.model_dump(), visibility="public"),
    )
    assert frozen_parent.provenance["visibility"] == "public"
    assert frozen_parent.provenance["method_kind"] == "expert"
    assert datetime.now(UTC) >= frozen_parent.created_at
