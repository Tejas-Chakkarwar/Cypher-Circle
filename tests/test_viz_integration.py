from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from cyphercircle.config import load_settings
from cyphercircle.db import Database
from cyphercircle.models import Session
from cyphercircle.viz_service import VisualizationService


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.integration
def test_visualization_generates_an_offline_network_for_demo_user(tmp_path: Path) -> None:
    log_path = ROOT / "data/processed/preprocess_log.json"
    if not log_path.exists():
        pytest.fail("run `make data` before strict visualization integration tests")
    preprocess_log = json.loads(log_path.read_text(encoding="utf-8"))
    username = preprocess_log["demo_users"]["busy_user"]
    db = Database(load_settings())
    try:
        service = VisualizationService(
            db,
            output_dir=tmp_path,
            browser_open=lambda _url: True,
        )
        path = service.generate(Session(username, "Demo"))
        assert path is not None and path.exists()
        html = path.read_text(encoding="utf-8")
        assert not re.search(
            r"<[^>]+\b(?:src|href)\s*=\s*['\"]https?://[^'\"]+['\"]",
            html,
            re.IGNORECASE,
        )
        assert '"arrows": "to"' in html
        assert "Cypher Circle network for @" in html
        assert [entry.name for entry in db.query_log.entries()] == [
            "GET_EGO_NETWORK",
            "GET_EDGES_AMONG",
        ]
    finally:
        db.close()
