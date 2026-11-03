"""Owner opts in to exact content; release personnel decide; delivery gates remain."""

import uuid
from dataclasses import asdict
from datetime import UTC, datetime

from app.application.errors.exceptions import ConcurrencyConflictError, NotFoundError
from app.application.ports.knowbots import (
    ExpertRecord,
    KnowlabRepository,
    ManualReleaseRecord,
)
from app.domain.knowbots.lifecycle import ACTIVE_TESTS, review_state
from app.domain.knowbots.manual_release import (
    ManualReleaseDecision,
    ManualReleaseIn,
    manifest_digest,
)


def conflict(message: str) -> ConcurrencyConflictError:
    return ConcurrencyConflictError(message, code="manual_release_conflict")


class KnowbotManualReleaseService:
    def __init__(self, repo: KnowlabRepository):
        self.repo = repo

    async def request(
        self, owner: str, draft_id: str, body: ManualReleaseIn
    ) -> ManualReleaseRecord:
        draft = await self.repo.get_draft(uuid.UUID(draft_id))
        request = (
            None if draft is None else await self.repo.get_request(draft.request_id)
        )
        if draft is None or request is None or request.owner_subject != owner:
            raise NotFoundError("No such draft", code="draft_not_found")
        async with self.repo.transaction():
            await self.repo.lock_request(request.id)
            for prior in await self.repo.list_manual_releases(owner_subject=owner):
                if prior.draft_id == draft.id:
                    payload = body.model_dump(mode="json")
                    if (
                        prior.manifest_sha256 == body.manifest_sha256
                        and prior.baseline_test_id == body.baseline_test_id
                        and prior.reason == body.reason
                        and prior.evidence == payload
                    ):
                        return prior
                    raise conflict("This draft already has a different release request")
            await self._eligible(draft.id, body.manifest_sha256, body.baseline_test_id)
            record = ManualReleaseRecord(
                id=uuid.uuid4(),
                request_id=request.id,
                draft_id=draft.id,
                owner_subject=owner,
                manifest_sha256=body.manifest_sha256,
                baseline_test_id=body.baseline_test_id,
                reason=body.reason,
                evidence=body.model_dump(mode="json"),
                requested_at=datetime.now(UTC),
            )
            return await self.repo.create_manual_release(record)

    async def _eligible(
        self, draft_id: uuid.UUID, digest: str, baseline_id: uuid.UUID
    ) -> None:
        draft = await self.repo.get_draft(draft_id)
        if (
            draft is None
            or draft.status != "pending_test"
            or manifest_digest(draft.manifest) != digest
            or (
                draft.user_review is not None
                and review_state(draft.user_review) != "ok"
            )
        ):
            raise conflict("A pending untested draft with this digest is required")
        baseline = await self.repo.get_test(baseline_id)
        if (
            baseline is None
            or baseline.request_id != draft.request_id
            or baseline.status != "passed"
            or baseline.draft_id == draft_id
        ):
            raise conflict("A passed earlier version of the same expert is required")
        old = await self.repo.get_draft(baseline.draft_id)
        if old is None or old.revision >= draft.revision:
            raise conflict("Baseline must precede the target draft")
        tests = await self.repo.list_tests(request_ids=[draft.request_id])
        if any(t.status in ACTIVE_TESTS or t.draft_id == draft_id for t in tests):
            raise conflict("Do not override active tests or an existing verdict")
        experts = await self.repo.list_experts([draft.request_id])
        if any(e.version == int(draft.version) for e in experts):
            raise conflict("Expert version already exists; create a new draft")
        acceptance = await self.repo.get_acceptance(
            draft.request_id, draft.request_version
        )
        if acceptance is None or not acceptance.accepted or not draft.rules_reviewed_by:
            raise conflict("Idea acceptance and rule review still apply")

    async def decide(
        self, release_id: str, actor: str, body: ManualReleaseDecision
    ) -> ManualReleaseRecord:
        # Authentication/knowlab:release is enforced by the admin API dependency.
        found = await self.repo.get_manual_release(uuid.UUID(release_id))
        if found is None:
            raise NotFoundError("No release request", code="manual_release_not_found")
        async with self.repo.transaction():
            await self.repo.lock_request(found.request_id)
            found = await self.repo.get_manual_release(found.id, for_update=True)
            assert found is not None
            wanted = "approved" if body.approved else "rejected"
            if found.status != "pending":
                if (
                    found.status == wanted
                    and found.decided_by == actor
                    and found.decision_reason == body.reason
                ):
                    return found
                raise conflict("Manual release already decided")
            expert_id = None
            now = datetime.now(UTC)
            if body.approved:
                await self._eligible(
                    found.draft_id, found.manifest_sha256, found.baseline_test_id
                )
                draft = await self.repo.get_draft(found.draft_id)
                assert draft is not None
                expert_id = uuid.uuid4()
                await self.repo.create_expert(
                    ExpertRecord(
                        id=expert_id,
                        request_id=found.request_id,
                        version=int(draft.version),
                        draft_id=found.draft_id,
                        created_at=now,
                        test_id=None,
                        manual_release={
                            "release_id": str(found.id),
                            "manifest_sha256": found.manifest_sha256,
                            "baseline_test_id": str(found.baseline_test_id),
                            "retested": False,
                            "approved_by": actor,
                            "approved_at": now.isoformat(),
                            "reason": body.reason,
                            "evidence": found.evidence,
                        },
                    )
                )
            await self.repo.decide_manual_release(
                found.id,
                status=wanted,
                actor=actor,
                at=now,
                reason=body.reason,
                expert_id=expert_id,
            )
        saved = await self.repo.get_manual_release(found.id)
        assert saved is not None
        return saved


def release_view(record: ManualReleaseRecord) -> dict:
    import json

    return json.loads(json.dumps(asdict(record), default=str))
