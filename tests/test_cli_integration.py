from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.integration
def test_cli_starts_and_completes_login_profile_logout_flow() -> None:
    preprocess_log = json.loads(
        (ROOT / "data/processed/preprocess_log.json").read_text(encoding="utf-8")
    )
    username = preprocess_log["demo_users"]["busy_user"]
    result = subprocess.run(
        [sys.executable, "-m", "cyphercircle"],
        input=f"1\n{username}\ncypher123\n1\n0\n0\n",
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert f"Welcome back" in result.stdout
    assert "UC-3" in result.stdout
    assert "Logged out" in result.stdout
    assert "Traceback" not in result.stdout
    assert "password_hash" not in result.stdout


@pytest.mark.integration
def test_cli_visualization_flow_generates_uc12_output() -> None:
    preprocess_log = json.loads(
        (ROOT / "data/processed/preprocess_log.json").read_text(encoding="utf-8")
    )
    username = preprocess_log["demo_users"]["busy_user"]
    output_path = ROOT / "output" / f"network_{username}.html"
    environment = os.environ.copy()
    environment["BROWSER"] = "/usr/bin/true %s"
    try:
        result = subprocess.run(
            [sys.executable, "-m", "cyphercircle"],
            input=f"1\n{username}\ncypher123\nv\n0\n0\n",
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )
        assert result.returncode == 0, (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "UC-12 · Network Visualization" in result.stdout
        assert str(output_path.relative_to(ROOT)) in result.stdout
        assert "Traceback" not in result.stdout
        assert output_path.exists()
    finally:
        output_path.unlink(missing_ok=True)
