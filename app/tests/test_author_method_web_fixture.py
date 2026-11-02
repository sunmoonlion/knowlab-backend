"""The author UI fixture is produced by the existing provider models."""

import json
from pathlib import Path

import pytest

from app.domain.knowbots.workdraft import WorkdraftBody
from tests.dynamic_method_fixtures import body
from tests.test_author_methods import definition


def test_web_fixture_preserves_author_cells_and_exact_method_definition():
    path = (
        Path(__file__).resolve().parents[3]
        / "knowlab-web-frontend/app/tests/fixtures/author-method.json"
    )
    if not path.exists():
        pytest.skip("the author web frontend is not checked out")
    sample = json.loads(path.read_text())
    assert sample["workdraft"]["body"] == WorkdraftBody.model_validate(
        body()
    ).model_dump(mode="json")
    actual = definition()
    assert sample["method"]["definition"] == actual.document()
    assert sample["method"]["digest"] == actual.digest()
