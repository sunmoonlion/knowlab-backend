"""The transfer remains a reference after storage, reload and idempotent replay."""

import pytest

from app.application.errors.exceptions import ConcurrencyConflictError
from app.domain.knowbots.method_demand import RuntimeIncubationV2
from tests.knowbot_fakes import OWNER
from tests.test_knowbot_repository_db import db as db
from tests.test_knowbot_repository_db import service
from tests.test_runtime_method_demand import request_doc


async def test_method_demand_survives_storage_and_replay(db):
    command = RuntimeIncubationV2.model_validate(request_doc())
    async with db() as session:
        first, created = await service(session).submit_from_runtime(
            OWNER, command, runtime="knowbench"
        )
        assert created
    async with db() as session:
        again, created = await service(session).submit_from_runtime(
            OWNER, command, runtime="knowbench"
        )
        assert not created and again.id == first.id
        assert again.origin["method_demand"] == command.method_demand.model_dump(
            mode="json"
        )
        assert again.reference_answer == command.reference()
        assert again.idea == {"kind": "question", "question": command.question}
        assert again.consent_version == 0
    changed = request_doc()
    changed["method_demand"]["gaps"][0]["reason"] = "不同的缺口"
    async with db() as session:
        with pytest.raises(ConcurrencyConflictError):
            await service(session).submit_from_runtime(
                OWNER, RuntimeIncubationV2.model_validate(changed), runtime="knowbench"
            )
