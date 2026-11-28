"""Cross-app execution of frozen checks using explicitly synthetic evidence."""

import json
import os
import subprocess
from pathlib import Path

PARENT = Path(__file__).resolve().parents[3]
KNOWBENCH_APP_DIR = PARENT.parent / "knowbench-app/knowbench-backend/app"
FIXTURES = Path(__file__).parent


def test_financial_positive_chain_and_single_fault_regressions():
    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)
    env["PYTHONPATH"] = str(KNOWBENCH_APP_DIR)
    result = subprocess.run(
        [
            "uv",
            "run",
            "--locked",
            "python",
            str(FIXTURES / "financial_runtime_probe.py"),
            str(FIXTURES / "fixtures/financial-expert-manifest.json"),
        ],
        cwd=KNOWBENCH_APP_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr
    checks = json.loads(result.stdout)
    assert len(checks) == 138
    assert all(checks.values())


def test_author_and_runtime_share_delivery_assertion_syntax():
    author = FIXTURES.parent / "app/domain/knowbots/delivery_assertion_contract.py"
    runtime = KNOWBENCH_APP_DIR / "app/domain/workbench/delivery_assertion_contract.py"
    assert author.read_bytes() == runtime.read_bytes()
