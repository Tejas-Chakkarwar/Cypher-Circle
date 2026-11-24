from __future__ import annotations

from collections.abc import Callable
from html import escape
from pathlib import Path
import re
import webbrowser
from typing import Any

from .db import Database
from .errors import NotFoundError, VisualizationError
from .models import EgoNetwork, NetworkEdge, NetworkNode, Session
from .queries import GET_EDGES_AMONG, GET_EGO_NETWORK
from .service_utils import principal_username
from .validation import normalize_username


VisualizationBrowser = Callable[[str], object]

_ANSI_OR_OSC_RE = re.compile(
    r"\x1b\][^\x07]*(?:\x07|\x1b\\)"
    r"|\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])"
    r"|\x9d[^\x07\x9c]*(?:\x07|\x9c)"
    r"|\x9b[0-?]*[ -/]*[@-~]"
)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_EXTERNAL_LINK_RE = re.compile(
    r"\s*<link\b[^>]*\bhref\s*=\s*(['\"])https?://[^'\"]+\1[^>]*>\s*",
    re.IGNORECASE,
)
_EXTERNAL_SCRIPT_RE = re.compile(
    r"\s*<script\b[^>]*\bsrc\s*=\s*(['\"])https?://[^'\"]+\1[^>]*>.*?</script\s*>\s*",
    re.IGNORECASE | re.DOTALL,
)
_EXTERNAL_ATTRIBUTE_RE = re.compile(
    r"<[^>]+\b(?:src|href)\s*=\s*(['\"])https?://[^'\"]+\1",
    re.IGNORECASE,
)
_NETWORK_INIT = "network = new vis.Network(container, data, options);"
_DISABLE_PHYSICS_AFTER_STABILIZATION = """
network.once("stabilizationIterationsDone", function () {
  network.setOptions({physics: {enabled: false}});
});
""".strip()


class VisualizationService:
    """Build and render an authenticated user's directed ego network."""

    NEIGHBOR_CAP = 50
    EDGE_CAP = 500

    COLORS = {
        "center": "#f59e0b",
        "following": "#3b82f6",
        "follower": "#22c55e",
        "mutual": "#a855f7",
    }

    def __init__(
        self,
        db: Database,
        *,
        output_dir: Path | str = Path("output"),
        browser_open: VisualizationBrowser | None = None,
    ) -> None:
        self.db = db
        self.output_dir = Path(output_dir)
        self.browser_open = browser_open or webbrowser.open

    def get_network(self, session: Session) -> EgoNetwork:
        """Load a deterministic, application-level network model from Neo4j."""

        username = principal_username(session)
        rows, _ = self.db.read(
            "GET_EGO_NETWORK",
            GET_EGO_NETWORK,
            username=username,
            cap=self.NEIGHBOR_CAP,
        )
        if not rows:
            raise NotFoundError(f"user @{username} was not found")

        row = rows[0]
        center = normalize_username(str(row.get("center") or username))
        center_name = str(row.get("center_name") or center)
        following = self._people(row.get("following"))
        followers = self._people(row.get("followers"))
        following_names = self._unique_usernames(following)
        follower_names = self._unique_usernames(followers)
        neighbor_names = self._ordered_union(following_names, follower_names)

        edge_rows: list[dict[str, Any]] = []
        if neighbor_names:
            edge_rows, _ = self.db.read(
                "GET_EDGES_AMONG",
                GET_EDGES_AMONG,
                usernames=[center, *neighbor_names],
                edge_cap=self.EDGE_CAP,
            )

        names = {item["username"]: item["name"] for item in [*following, *followers]}
        names[center] = center_name
        following_set = set(following_names)
        follower_set = set(follower_names)
        nodes = [NetworkNode(center, center_name, "center")]
        for neighbor in neighbor_names:
            if neighbor == center:
                continue
            if neighbor in following_set and neighbor in follower_set:
                category = "mutual"
            elif neighbor in following_set:
                category = "following"
            else:
                category = "follower"
            nodes.append(NetworkNode(neighbor, names.get(neighbor, neighbor), category))

        known = {node.username for node in nodes}
        edges: list[NetworkEdge] = []
        seen_edges: set[tuple[str, str]] = set()
        for edge in edge_rows:
            source = self._normalize_edge_username(edge.get("src"))
            target = self._normalize_edge_username(edge.get("dst"))
            if not source or not target or source not in known or target not in known:
                continue
            pair = (source, target)
            if pair not in seen_edges:
                edges.append(NetworkEdge(source, target))
                seen_edges.add(pair)
        return EgoNetwork(nodes=nodes, edges=edges)

    def generate(self, session: Session) -> Path | None:
        """Write the offline visualization and open it in the configured browser."""

        network = self.get_network(session)
        username = principal_username(session)
        path = self._output_path(username)
        if not network.has_connections:
            self._remove_stale_output(path)
            return None
        try:
            network_class = self._load_network_class()
        except (ImportError, ModuleNotFoundError) as exc:
            raise VisualizationError(
                "Network visualization requires pyvis==0.3.2. "
                "Install the pinned requirements with `pip install -r requirements.txt`."
            ) from exc

        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            graph = network_class(
                directed=True,
                cdn_resources="in_line",
                height="750px",
                width="100%",
            )
            # Keep the initial layout deterministic and useful, then stop the
            # force simulation so a settled graph does not continue shaking.
            graph.toggle_physics(True)
            graph.toggle_stabilization(True)
            for node in network.nodes:
                graph.add_node(
                    node.username,
                    label=f"@{self._safe_text(node.username)}\n{self._safe_text(node.name)}",
                    title=self._safe_text(node.name),
                    color=self.COLORS[node.category],
                    size=32 if node.category == "center" else 20,
                )
            for edge in network.edges:
                graph.add_edge(edge.source, edge.target, arrows="to")
            graph.write_html(str(path), open_browser=False)
            html = path.read_text(encoding="utf-8")
            path.write_text(self._make_offline_html(html, username), encoding="utf-8")
        except VisualizationError:
            path.unlink(missing_ok=True)
            raise
        except OSError as exc:
            raise VisualizationError(
                f"Could not write the network visualization to {path}."
            ) from exc
        except Exception as exc:
            raise VisualizationError("Could not render the network visualization.") from exc

        try:
            opened = self.browser_open(path.resolve().as_uri())
            if opened is False:
                raise VisualizationError(
                    f"The network was written to {path}, but the browser could not be opened."
                )
        except (OSError, webbrowser.Error) as exc:
            raise VisualizationError(
                f"The network was written to {path}, but the browser could not be opened."
            ) from exc
        return path

    def _output_path(self, username: str) -> Path:
        return self.output_dir / f"network_{username}.html"

    @staticmethod
    def _remove_stale_output(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # Empty-network handling should remain harmless when output_dir is
            # absent, not a directory, or otherwise unavailable.
            return

    @staticmethod
    def _load_network_class() -> Any:
        from pyvis.network import Network

        return Network

    @staticmethod
    def _people(value: Any) -> list[dict[str, str]]:
        if not isinstance(value, list):
            return []
        people: list[dict[str, str]] = []
        for item in value:
            if not isinstance(item, dict) or not item.get("username"):
                continue
            try:
                username = normalize_username(str(item["username"]))
            except Exception:
                continue
            people.append({"username": username, "name": str(item.get("name") or username)})
        return people

    @staticmethod
    def _unique_usernames(people: list[dict[str, str]]) -> list[str]:
        return VisualizationService._ordered_union(
            [person["username"] for person in people], []
        )

    @staticmethod
    def _ordered_union(first: list[str], second: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for username in [*first, *second]:
            if username not in seen:
                result.append(username)
                seen.add(username)
        return result

    @staticmethod
    def _normalize_edge_username(value: Any) -> str | None:
        if value is None:
            return None
        try:
            return normalize_username(str(value))
        except Exception:
            return None

    @staticmethod
    def _safe_text(value: object) -> str:
        text = _ANSI_OR_OSC_RE.sub("", str(value))
        text = _CONTROL_RE.sub("", text)
        text = re.sub(r"[\r\n\u2028\u2029]", " ", text)
        return escape(text, quote=True)

    @classmethod
    def _make_offline_html(cls, html: str, username: str) -> str:
        html = _EXTERNAL_SCRIPT_RE.sub("\n", html)
        html = _EXTERNAL_LINK_RE.sub("\n", html)
        styles = """
<style>
  body { margin: 0; font-family: sans-serif; }
  #mynetwork { width: 100%; height: 750px; border: 1px solid #cbd5e1; }
  .cyphercircle-legend { padding: 12px; border-bottom: 1px solid #cbd5e1; }
  .cyphercircle-legend span { margin-right: 16px; white-space: nowrap; }
  .cyphercircle-swatch { display: inline-block; width: 12px; height: 12px;
    border-radius: 50%; margin-right: 4px; vertical-align: -1px; }
</style>
""".strip()
        legend = f"""
<div class="cyphercircle-legend" aria-label="Network legend">
  <strong>Cypher Circle network for @{cls._safe_text(username)}</strong><br>
  <span><i class="cyphercircle-swatch" style="background:{cls.COLORS['center']}"></i>Center</span>
  <span><i class="cyphercircle-swatch" style="background:{cls.COLORS['following']}"></i>Following only</span>
  <span><i class="cyphercircle-swatch" style="background:{cls.COLORS['follower']}"></i>Follower only</span>
  <span><i class="cyphercircle-swatch" style="background:{cls.COLORS['mutual']}"></i>Mutual</span>
</div>
""".strip()
        if "</head>" in html:
            html = html.replace("</head>", f"{styles}\n</head>", 1)
        else:
            html = f"{styles}\n{html}"
        if _NETWORK_INIT in html:
            html = html.replace(
                _NETWORK_INIT,
                f"{_NETWORK_INIT}\n{_DISABLE_PHYSICS_AFTER_STABILIZATION}",
                1,
            )
        body = re.search(r"<body\b[^>]*>", html, re.IGNORECASE)
        if body:
            html = html[: body.end()] + f"\n{legend}" + html[body.end() :]
        else:
            html = f"{legend}\n{html}"
        if _EXTERNAL_ATTRIBUTE_RE.search(html):
            raise VisualizationError(
                "Generated visualization contains an external resource and was not saved."
            )
        return html
