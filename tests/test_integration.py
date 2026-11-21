from __future__ import annotations

import subprocess
import sys

import pytest


@pytest.mark.integration
def test_vertical_slice_against_local_neo4j() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/slice_check.py"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "Integration check could not complete against local Neo4j. "
        "Run `make up` and retry.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


@pytest.mark.integration
def test_smoke_query_is_logged() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/smoke_check.py"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "Smoke check failed. Run `make up` and retry.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "smoke query returned 1" in result.stdout
    assert "QueryLog recorded SMOKE_RETURN_ONE" in result.stdout
