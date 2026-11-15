"""Browser schemas and sample verdicts come from the provider, not copied guesses."""

import hashlib
import json

from jsonschema import Draft202012Validator
from pydantic import ValidationError

from scripts.export_formal_method import WEB_MODELS, generate_web


def test_web_bundle_is_data_only_and_every_hash_matches(tmp_path):
    generated = generate_web(tmp_path)
    assert len(generated) == len(WEB_MODELS) + 2
    assert all(
        path.parent == tmp_path / "contracts/formal-method-provider"
        for path in generated
    )
    assert all(path.suffix == ".json" for path in generated)
    texts = {path.name: value for path, value in generated.items()}
    lock = json.loads(texts["lock.json"])
    assert lock["files_sha256"] == {
        name: hashlib.sha256(value.encode()).hexdigest()
        for name, value in texts.items()
        if name != "lock.json"
    }
    for name in WEB_MODELS:
        Draft202012Validator.check_schema(json.loads(texts[name + ".schema.json"]))
    samples = json.loads(texts["samples.json"])["samples"]
    assert len(samples) == 25
    assert len({sample["name"] for sample in samples}) == len(samples)
    for sample in samples:
        try:
            WEB_MODELS[sample["schema"]].model_validate(sample["value"])
            valid = True
        except ValidationError:
            valid = False
        assert valid == sample["valid"], sample["name"]
        if valid:
            Draft202012Validator(
                json.loads(texts[sample["schema"] + ".schema.json"])
            ).validate(sample["value"])
