from __future__ import annotations

import re
import webbrowser
from pathlib import Path
from typing import Any

import pytest

from cyphercircle.db import LoggedQuery, QueryLog
from cyphercircle.errors import VisualizationError
from cyphercircle.models import Session
from cyphercircle.viz_service import VisualizationService


EGO_ROW = {
    "center": "alice.a",
    "center_name": "Alice",
    "following": [
        {"username": "bob.b", "name": "Bob"},
        {"username": "cara.c", "name": "Cara"},
    ],
    "followers": [
        {"username": "cara.c", "name": "Cara"},
        {"username": "dana.d", "name": "Dana"},
    ],
}
EDGE_ROWS = [
    {"src": "alice.a", "dst": "bob.b"},
    {"src": "bob.b", "dst": "cara.c"},
    {"src": "dana.d", "dst": "alice.a"},
]


class FakeDB:
    def __init__(self, ego: dict[str, Any] | None = None, edges: list[dict[str, str]] | None = None):
        self.ego = ego if ego is not None else EGO_ROW
        self.edges = edges if edges is not None else EDGE_ROWS
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.query_log = QueryLog()

    def read(self, query_name: str, query: str, **params: Any):
        self.calls.append((query_name, params))
        rows = [self.ego] if query_name == "GET_EGO_NETWORK" else self.edges
        self.query_log.record(LoggedQuery(query_name, query, {}, len(rows)))
        return rows, None


def test_get_network_maps_categories_edges_and_caps() -> None:
    db = FakeDB()
    service = VisualizationService(db)

    network = service.get_network(Session("alice.a", "Alice"))

    assert [(node.username, node.category) for node in network.nodes] == [
        ("alice.a", "center"),
        ("bob.b", "following"),
        ("cara.c", "mutual"),
        ("dana.d", "follower"),
    ]
    assert [(edge.source, edge.target) for edge in network.edges] == [
        ("alice.a", "bob.b"),
        ("bob.b", "cara.c"),
        ("dana.d", "alice.a"),
    ]
    assert db.calls == [
        ("GET_EGO_NETWORK", {"username": "alice.a", "cap": 50}),
        (
            "GET_EDGES_AMONG",
            {
                "usernames": ["alice.a", "bob.b", "cara.c", "dana.d"],
                "edge_cap": 500,
            },
        ),
    ]
    assert [entry.name for entry in db.query_log.entries()] == [
        "GET_EGO_NETWORK",
        "GET_EDGES_AMONG",
    ]


def test_get_network_preserves_deterministic_ordering() -> None:
    db = FakeDB()
    service = VisualizationService(db)
    first = service.get_network(Session("alice.a", "Alice"))
    second = service.get_network(Session("alice.a", "Alice"))
    assert first == second


def test_empty_network_skips_html_and_browser(tmp_path: Path) -> None:
    opened: list[str] = []
    db = FakeDB(
        ego={"center": "alice.a", "center_name": "Alice", "following": [], "followers": []}
    )
    service = VisualizationService(db, output_dir=tmp_path, browser_open=opened.append)

    assert service.generate(Session("alice.a", "Alice")) is None
    assert opened == []
    assert list(tmp_path.iterdir()) == []
    assert [entry.name for entry in db.query_log.entries()] == ["GET_EGO_NETWORK"]


def test_empty_network_removes_only_current_stale_output(tmp_path: Path) -> None:
    stale_path = tmp_path / "network_alice.a.html"
    unrelated_path = tmp_path / "network_other.user.html"
    stale_path.write_text("stale", encoding="utf-8")
    unrelated_path.write_text("keep", encoding="utf-8")
    db = FakeDB(
        ego={"center": "alice.a", "center_name": "Alice", "following": [], "followers": []}
    )
    service = VisualizationService(db, output_dir=tmp_path)

    assert service.generate(Session("alice.a", "Alice")) is None
    assert not stale_path.exists()
    assert unrelated_path.read_text(encoding="utf-8") == "keep"


def test_empty_network_ignores_invalid_output_directory(tmp_path: Path) -> None:
    output_file = tmp_path / "not-a-directory"
    output_file.write_text("occupied", encoding="utf-8")
    db = FakeDB(
        ego={"center": "alice.a", "center_name": "Alice", "following": [], "followers": []}
    )

    assert VisualizationService(db, output_dir=output_file).generate(
        Session("alice.a", "Alice")
    ) is None
    assert output_file.read_text(encoding="utf-8") == "occupied"


def test_generate_creates_offline_direct_html_with_legend_and_browser_open(tmp_path: Path) -> None:
    opened: list[str] = []
    service = VisualizationService(
        FakeDB(), output_dir=tmp_path, browser_open=opened.append
    )

    path = service.generate(Session("alice.a", "Alice"))

    assert path == tmp_path / "network_alice.a.html"
    assert path is not None and path.exists()
    html = path.read_text(encoding="utf-8")
    assert not re.search(
        r"<[^>]+\b(?:src|href)\s*=\s*['\"]https?://[^'\"]+['\"]",
        html,
        re.IGNORECASE,
    )
    assert "Cypher Circle network for @alice.a" in html
    assert "#f59e0b" in html and "#3b82f6" in html
    assert "#22c55e" in html and "#a855f7" in html
    assert '"arrows": "to"' in html
    assert re.search(
        r'"physics":\s*\{.*?"enabled": true.*?"stabilization":\s*\{.*?"enabled": true',
        html,
        re.DOTALL,
    )
    assert 'network.once("stabilizationIterationsDone"' in html
    assert 'network.setOptions({physics: {enabled: false}});' in html
    assert "bob.b" in html and "cara.c" in html and "dana.d" in html
    assert "password_hash" not in html
    assert "cypher123" not in html
    assert opened == [path.resolve().as_uri()]


def test_generate_reports_pyvis_import_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeDB()
    service = VisualizationService(db, output_dir=tmp_path)

    def fail_import() -> Any:
        raise ModuleNotFoundError("No module named 'pyvis'")

    monkeypatch.setattr(service, "_load_network_class", fail_import)
    with pytest.raises(VisualizationError, match="pyvis==0.3.2"):
        service.generate(Session("alice.a", "Alice"))


def test_generate_reports_output_directory_failure(tmp_path: Path) -> None:
    output_file = tmp_path / "not-a-directory"
    output_file.write_text("occupied", encoding="utf-8")
    service = VisualizationService(FakeDB(), output_dir=output_file)

    with pytest.raises(VisualizationError, match="Could not write"):
        service.generate(Session("alice.a", "Alice"))


def test_generate_reports_browser_open_failure(tmp_path: Path) -> None:
    service = VisualizationService(
        FakeDB(), output_dir=tmp_path, browser_open=lambda _url: False
    )

    with pytest.raises(VisualizationError, match="browser could not be opened"):
        service.generate(Session("alice.a", "Alice"))
    assert (tmp_path / "network_alice.a.html").exists()


def test_generate_reports_webbrowser_error_and_preserves_html(tmp_path: Path) -> None:
    def fail_browser(_url: str) -> bool:
        raise webbrowser.Error("browser backend failed")

    service = VisualizationService(FakeDB(), output_dir=tmp_path, browser_open=fail_browser)

    with pytest.raises(VisualizationError, match="browser could not be opened") as error:
        service.generate(Session("alice.a", "Alice"))

    assert "browser backend failed" not in str(error.value)
    assert (tmp_path / "network_alice.a.html").exists()


def test_generated_display_fields_are_html_and_terminal_safe(tmp_path: Path) -> None:
    ego = {
        **EGO_ROW,
        "following": [
            {"username": "bob.b", "name": "<script>alert(1)</script>\x1b[31m"}
        ],
    }
    path = VisualizationService(
        FakeDB(ego=ego), output_dir=tmp_path, browser_open=lambda _url: True
    ).generate(Session("alice.a", "Alice"))
    assert path is not None
    html = path.read_text(encoding="utf-8")
    assert "\\u0026lt;script\\u0026gt;alert(1)\\u0026lt;/script\\u0026gt;" in html
    assert "<script>alert(1)</script>" not in html
    assert "\x1b" not in html
