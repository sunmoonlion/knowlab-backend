"""Formal admission follows persisted author input and a trusted runtime receipt."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.application.errors.exceptions import (
    BadGatewayError,
    BadRequestError,
    ConcurrencyConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from app.application.ports.knowbots import MethodVersionRecord
from app.application.services.formal_methods import FormalMethodService
from app.application.services.knowbot_service import KnowbotCreationService
from app.application.services.knowbot_workdraft import KnowbotWorkdraftService
from app.domain.knowbots.drafts import KnowbotRequestIn
from app.domain.knowbots.formal_method_admission import (
    CONTRACT,
    GOVERNANCE_CHECKS,
    FormalMethodDraft,
    FreezeFormalMethod,
    ValidateFormalMethod,
)
from tests.knowbot_fakes import OWNER, FakeRuntime, MemoryKnowlab, request_body
from tests.test_formal_method import composite, frozen, tool


class Repository(MemoryKnowlab):
    def __init__(self):
        super().__init__()
        self.method_owners = {}
        self.method_versions = {}

    async def lock_method(self, mid):
        pass

    async def method_owner(self, mid):
        return self.method_owners.get(mid)

    async def method_head(self, mid):
        return max(
            (version for method, version in self.method_versions if method == mid),
            default=0,
        )

    async def method_version(self, mid, version):
        return self.method_versions.get((mid, version))

    async def create_method(self, mid, owner):
        self.method_owners[mid] = owner

    async def insert_method_version(self, **values):
        record = MethodVersionRecord(
            **values,
            owner_subject=self.method_owners[values["method_id"]],
            created_at=datetime.now(UTC),
        )
        self.method_versions[(record.method_id, record.version)] = record

    async def list_methods(self, owner, *, limit, offset, formal_only=False):
        rows = []
        for mid, have_owner in self.method_owners.items():
            if have_owner != owner:
                continue
            record = self.method_versions[(mid, await self.method_head(mid))]
            is_formal = record.definition.get("contract") == "knowlab/formal-method/v1"
            if is_formal != formal_only:
                continue
            rows.append(
                {
                    "id": mid,
                    "version": record.version,
                    "digest": record.digest,
                    "method_kind": record.provenance["method_kind"],
                    "title": record.definition["capability_declaration"]["title"],
                    "summary": record.definition["capability_declaration"]["purpose"],
                    "created_at": record.created_at,
                }
            )
        return rows[offset : offset + limit]


class Runtime(FakeRuntime):
    def __init__(self):
        super().__init__()
        self.validation_requests = {}
        self.validation_results = {}
        self.registrations = {}
        self.registration_calls = []
        self.fail_register = False
        self.on_register = None

    async def submit_formal_method_validation(self, runtime, request):
        self.validation_requests[request.run_id] = request
        return self.validation_results.setdefault(
            request.run_id,
            {
                "contract": CONTRACT,
                "run_id": str(request.run_id),
                "status": "pending",
                "receipt": None,
                "missing_checks": ["outcome"],
                "details": ["Execution required"],
            },
        )

    async def formal_method_validation(self, runtime, owner, run_id):
        return self.validation_results[UUID(run_id)]

    async def register_formal_method(self, runtime, frozen):
        if self.on_register is not None:
            await self.on_register(frozen)
        self.registration_calls.append(frozen)
        key = (frozen.reference.method_id, frozen.reference.version)
        assert self.registrations.get(key, frozen) == frozen
        assert frozen.draft == self.validation_requests[frozen.validation.run_id].draft
        self.registrations[key] = frozen
        if self.fail_register:
            raise ServiceUnavailableError(
                "Registration reply lost", code="registration_unavailable"
            )
        return {
            "reference": frozen.reference.model_dump(mode="json"),
            "owner_subject": frozen.owner_subject,
            "status": "registered",
        }

    async def formal_method_registration(self, runtime, owner, method_id, version):
        frozen = self.registrations.get((UUID(method_id), version))
        if frozen is None or frozen.owner_subject != owner:
            return None
        return {
            "reference": frozen.reference.model_dump(mode="json"),
            "owner_subject": owner,
            "status": "registered",
        }

    def complete(self, run_id, *, business=True):
        request = self.validation_requests[run_id]
        receipt = {
            key: str(getattr(request, key)) if key != "revision" else request.revision
            for key in (
                "run_id",
                "owner_subject",
                "request_id",
                "workdraft_id",
                "revision",
                "method_id",
            )
        }
        receipt.update(
            contract=CONTRACT,
            definition_digest=request.draft.definition.digest(),
            source_digest=request.draft.source.fingerprint()
            if request.draft.source
            else None,
            dependencies=[r.model_dump(mode="json") for r in request.dependencies],
            checks=sorted(GOVERNANCE_CHECKS | ({"business"} if business else set())),
            uncovered=[]
            if business
            else [
                {
                    "scenario": "Real account",
                    "reason": "Not exercised",
                    "scope": "Own use only",
                }
            ],
            completed_at=datetime.now(UTC).isoformat(),
        )
        self.validation_results[run_id] = {
            "contract": CONTRACT,
            "run_id": str(run_id),
            "status": "passed",
            "receipt": receipt,
            "missing_checks": [],
            "details": [],
        }
        return receipt


def draft():
    definition = tool()
    implementation = definition["procedure"]["implementation"]
    return FormalMethodDraft.model_validate(
        {
            "definition": definition,
            "source": {
                **{k: v for k, v in implementation.items() if k != "requirement_ids"},
                "asset_id": None,
                "asset_revision": None,
                "license_basis": "Platform test implementation",
            },
            "allowed_callers": ["host", "model"],
        }
    )


async def setup(repo=None, runtime=None):
    repo, runtime = repo or Repository(), runtime or Runtime()
    request = await KnowbotCreationService(repo, runtime).submit(
        OWNER, KnowbotRequestIn.model_validate(request_body())
    )
    await KnowbotWorkdraftService(repo).save(
        OWNER,
        str(request.id),
        revision=0,
        edits=[("formal_method", draft().model_dump(mode="json", by_alias=True))],
    )
    command = ValidateFormalMethod(request_id=request.id, revision=1, run_id=uuid4())
    return repo, runtime, FormalMethodService(repo, runtime), uuid4(), command


def freeze_command(command, visibility="private"):
    return FreezeFormalMethod(**command.model_dump(), visibility=visibility)


@pytest.mark.asyncio
async def test_persisted_draft_validation_and_idempotent_freeze():
    repo, runtime, service, mid, command = await setup()
    assert (await service.validate(OWNER, str(mid), command))["status"] == "pending"
    with pytest.raises(ConcurrencyConflictError, match="not complete"):
        await service.freeze(OWNER, str(mid), 1, freeze_command(command))
    assert not repo.method_versions
    runtime.complete(command.run_id)
    record = await service.freeze(OWNER, str(mid), 1, freeze_command(command))
    assert record.provenance["method_kind"] == "tool"
    assert record.provenance["dispatch"] == "direct"
    assert record.provenance["validation"]["run_id"] == str(command.run_id)
    assert record.definition["procedure"]["inputs"]["value"]["schema"] == {
        "type": "number"
    }
    assert await service.freeze(OWNER, str(mid), 1, freeze_command(command)) == record
    assert await service.get(OWNER, str(mid), 1) == record


@pytest.mark.asyncio
async def test_registration_reply_loss_replays_the_frozen_snapshot_after_draft_edit():
    repo, runtime, service, mid, command = await setup()
    await service.validate(OWNER, str(mid), command)
    runtime.complete(command.run_id)
    runtime.fail_register = True
    with pytest.raises(ServiceUnavailableError):
        await service.freeze(OWNER, str(mid), 1, freeze_command(command))
    record = await service.get(OWNER, str(mid), 1)
    assert len(repo.method_versions) == len(runtime.registrations) == 1
    changed = draft().model_dump(mode="json", by_alias=True)
    changed["allowed_callers"] = ["host"]
    await service.workdrafts.save(
        OWNER, str(command.request_id), revision=1, edits=[("formal_method", changed)]
    )
    runtime.fail_register = False
    assert await service.freeze(OWNER, str(mid), 1, freeze_command(command)) == record
    assert runtime.registration_calls[0] == runtime.registration_calls[1]
    assert runtime.registration_calls[1].draft.allowed_callers == ("host", "model")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("owner_subject", "another-user"),
        ("method_id", str(uuid4())),
        ("definition_digest", "sha256:" + "b" * 64),
        ("source_digest", "sha256:" + "c" * 64),
        ("revision", 2),
        ("workdraft_id", str(uuid4())),
    ],
)
async def test_receipt_cannot_be_reused_for_another_owner_source_or_revision(
    field, value
):
    repo, runtime, service, mid, command = await setup()
    await service.validate(OWNER, str(mid), command)
    runtime.complete(command.run_id)[field] = value
    with pytest.raises(ConcurrencyConflictError) as error:
        await service.freeze(OWNER, str(mid), 1, freeze_command(command))
    assert error.value.code == "formal_validation_stale"
    assert not repo.method_versions


@pytest.mark.asyncio
async def test_source_change_requires_new_validation_and_preserves_old_receipt():
    repo, runtime, service, mid, command = await setup()
    await service.validate(OWNER, str(mid), command)
    runtime.complete(command.run_id)
    changed = draft().model_dump(mode="json", by_alias=True)
    changed["source"]["license_basis"] = "Another statement must be checked"
    await service.workdrafts.save(
        OWNER, str(command.request_id), revision=1, edits=[("formal_method", changed)]
    )
    with pytest.raises(ConcurrencyConflictError):
        await service.freeze(OWNER, str(mid), 1, freeze_command(command))
    with pytest.raises(ConcurrencyConflictError) as error:
        await service.freeze(
            OWNER,
            str(mid),
            1,
            freeze_command(command.model_copy(update={"revision": 2})),
        )
    assert error.value.code == "formal_validation_stale"
    assert not repo.method_versions


@pytest.mark.asyncio
async def test_private_business_limit_does_not_waive_governance_or_allow_public():
    repo, runtime, service, mid, command = await setup()
    await service.validate(OWNER, str(mid), command)
    receipt = runtime.complete(command.run_id, business=False)
    with pytest.raises(BadRequestError) as error:
        await service.freeze(OWNER, str(mid), 1, freeze_command(command, "public"))
    assert error.value.code == "formal_validation_uncovered"
    receipt["checks"].remove("isolation")
    with pytest.raises(BadGatewayError):
        await service.freeze(OWNER, str(mid), 1, freeze_command(command))
    receipt["checks"].append("isolation")
    saved = await service.freeze(OWNER, str(mid), 1, freeze_command(command))
    assert saved.provenance["visibility"] == "private"
    assert saved.provenance["validation"]["uncovered"]


@pytest.mark.asyncio
async def test_foreign_workdraft_and_unconfirmed_author_definition_are_rejected():
    repo, runtime, service, mid, command = await setup()
    with pytest.raises(NotFoundError):
        await service.validate("another-user", str(mid), command)
    current = repo.workdrafts[command.request_id]
    repo.workdrafts[command.request_id] = replace(current, sources={})
    with pytest.raises(BadRequestError) as error:
        await service.validate(OWNER, str(mid), command)
    assert error.value.code == "method_source_invalid"
    assert not runtime.validation_requests


@pytest.mark.asyncio
async def test_new_formal_definition_cannot_be_silently_compiled_as_old_expert_pack():
    repo, runtime, service, mid, command = await setup()
    with pytest.raises(BadRequestError) as error:
        await service.workdrafts.preview(OWNER, str(command.request_id))
    assert error.value.code == "formal_method_requires_validation"
    with pytest.raises(BadRequestError):
        await service.workdrafts.save(
            OWNER,
            str(command.request_id),
            revision=1,
            edits=[("steps", [{"id": "old", "title": "Old step"}])],
        )


@pytest.mark.asyncio
async def test_all_candidates_must_resolve_to_exact_admitted_owned_versions():
    repo, runtime, service, mid, command = await setup()
    await service.validate(OWNER, str(mid), command)
    runtime.complete(command.run_id)
    child = await service.freeze(OWNER, str(mid), 1, freeze_command(command))
    definition = composite(frozen(child.definition, mid=mid))
    proposal = {"definition": definition, "source": None, "allowed_callers": ["host"]}
    request = await KnowbotCreationService(repo, runtime).submit(
        OWNER, KnowbotRequestIn.model_validate(request_body())
    )
    await service.workdrafts.save(
        OWNER, str(request.id), revision=0, edits=[("formal_method", proposal)]
    )
    validate = ValidateFormalMethod(request_id=request.id, revision=1, run_id=uuid4())
    parent = uuid4()
    await service.validate(OWNER, str(parent), validate)
    assert runtime.validation_requests[validate.run_id].dependencies[0].method_id == mid
    runtime.complete(validate.run_id)
    saved = await service.freeze(OWNER, str(parent), 1, freeze_command(validate))
    assert saved.provenance["method_kind"] == "expert"
    # A tampered/legacy stored record does not become admitted because its ID resolves.
    repo.method_versions[(mid, 1)] = replace(child, provenance={})
    with pytest.raises(ConcurrencyConflictError) as error:
        await service.validate(OWNER, str(parent), validate)
    assert error.value.code == "formal_method_unadmitted"


@pytest.mark.asyncio
async def test_unregistered_freeze_is_not_selectable_or_referenced():
    repo, runtime, service, mid, command = await setup()
    await service.validate(OWNER, str(mid), command)
    runtime.complete(command.run_id)
    child = await service.freeze(OWNER, str(mid), 1, freeze_command(command))
    assert len(await service.listing(OWNER, limit=30, offset=0)) == 1
    runtime.registrations.clear()
    assert await service.listing(OWNER, limit=30, offset=0) == []
    definition = composite(frozen(child.definition, mid=mid))
    request = await KnowbotCreationService(repo, runtime).submit(
        OWNER, KnowbotRequestIn.model_validate(request_body())
    )
    await service.workdrafts.save(
        OWNER,
        str(request.id),
        revision=0,
        edits=[
            (
                "formal_method",
                {"definition": definition, "source": None, "allowed_callers": ["host"]},
            )
        ],
    )
    parent_command = ValidateFormalMethod(
        request_id=request.id, revision=1, run_id=uuid4()
    )
    parent = uuid4()
    with pytest.raises(ConcurrencyConflictError) as error:
        await service.validate(OWNER, str(parent), parent_command)
    assert error.value.code == "formal_method_unregistered"
    assert parent_command.run_id not in runtime.validation_requests
    # The precise frozen snapshot remains retrievable to recover its delivery.
    assert await service.get(OWNER, str(mid), 1) == child
    await service.freeze(OWNER, str(mid), 1, freeze_command(command))
    assert len(await service.listing(OWNER, limit=30, offset=0)) == 1
    assert (await service.validate(OWNER, str(parent), parent_command))[
        "status"
    ] == "pending"
