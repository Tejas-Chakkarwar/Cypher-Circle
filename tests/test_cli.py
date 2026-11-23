from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from cyphercircle import cli as cli_module
from cyphercircle.cli import CLI
from cyphercircle.db import LoggedQuery, QueryLog
from cyphercircle.errors import (
    AuthenticationError,
    DatabaseUnavailableError,
    VisualizationError,
)
from cyphercircle.models import (
    Connection,
    ConnectionCounts,
    GraphStats,
    Profile,
    Recommendation,
    Session,
    UserSummary,
)


def _console() -> tuple[Console, StringIO]:
    output = StringIO()
    return Console(file=output, color_system=None, force_terminal=False), output


def _feeder(values: list[str]):
    iterator = iter(values)
    return lambda _prompt: next(iterator)


class FakeAuth:
    def __init__(self, *, login_error: Exception | None = None) -> None:
        self.login_error = login_error
        self.login_calls: list[tuple[str, str]] = []
        self.register_calls: list[tuple] = []

    def login(self, username: str, password: str) -> Session:
        self.login_calls.append((username, password))
        if self.login_error:
            raise self.login_error
        return Session("alice.a", "Alice")

    def register(self, *args, **kwargs) -> Session:
        self.register_calls.append((args, kwargs))
        return Session("alice.a", "Alice")


class FakeProfile:
    def __init__(self, query_log: QueryLog | None = None) -> None:
        self.query_log = query_log
        self.profile = Profile(
            "alice.a",
            "Alice",
            "alice@example.com",
            None,
            None,
            [],
            0,
            datetime(2024, 1, 1, tzinfo=UTC),
            0,
            0,
        )
        self.top_circle_calls = 0

    def get_top_circles(self, _limit: int):
        self.top_circle_calls += 1
        return []

    def get_profile(self, _session: Session):
        return self.profile

    def update_profile(self, _session: Session, **_kwargs):
        if self.query_log is not None:
            self.query_log.record(LoggedQuery("UPDATE_PROFILE", "RETURN 1", {}, 1))
        return self.profile

    def get_my_circles(self, _session: Session):
        return []


class FakeGraph:
    def __init__(self, query_log: QueryLog) -> None:
        self.query_log = query_log
        self.follow_calls: list[str] = []
        self.unfollow_calls: list[str] = []
        self.connection_skips: list[int] = []

    def follow(self, _session: Session, target: str) -> bool:
        self.follow_calls.append(target)
        self.query_log.record(LoggedQuery("FOLLOW_USER", "RETURN 1", {}, 1))
        return True

    def unfollow(self, _session: Session, target: str) -> bool:
        self.unfollow_calls.append(target)
        return True

    def connection_counts(self, _session: Session):
        return ConnectionCounts(30, 30)

    def following(self, _session: Session, *, skip: int, limit: int):
        self.connection_skips.append(skip)
        return [_connection(f"following_{index}") for index in range(skip, skip + limit)]

    def followers(self, _session: Session, *, skip: int, limit: int):
        self.connection_skips.append(skip)
        return [_connection(f"follower_{index}") for index in range(skip, skip + limit)]

    def mutual_connections(self, *_args, **_kwargs):
        raise AssertionError("not used by this test double")

    def recommendations(self, _session: Session, *, limit: int):
        self.query_log.record(
            LoggedQuery("RECOMMEND_BY_NETWORK", "RETURN $password_hash", {"password_hash": "***"}, 1)
        )
        return [
            Recommendation(
                UserSummary("bob.b", "Bob", None, 4), 2, ["mid.one"], "network", None
            )
        ][:limit]


class FakeSearch:
    def search_users(self, *_args, **_kwargs):
        return []

    def popular_users(self, **_kwargs):
        return [UserSummary("bob.b", "Bob", None, 4)]


class FakeVisualization:
    def __init__(self, query_log: QueryLog) -> None:
        self.query_log = query_log

    def generate(self, _session: Session) -> Path:
        self.query_log.record(LoggedQuery("GET_EGO_NETWORK", "RETURN 1", {}, 1))
        self.query_log.record(LoggedQuery("GET_EDGES_AMONG", "RETURN 1", {}, 2))
        return Path("output/network_alice.a.html")


class EmptyVisualization:
    def generate(self, _session: Session) -> None:
        return None


class BrowserFailureVisualization:
    def __init__(self, query_log: QueryLog) -> None:
        self.query_log = query_log

    def generate(self, _session: Session) -> Path:
        self.query_log.record(LoggedQuery("GET_EGO_NETWORK", "RETURN 1", {}, 1))
        self.query_log.record(LoggedQuery("GET_EDGES_AMONG", "RETURN 1", {}, 2))
        raise VisualizationError(
            "The network was written to output/network_alice.a.html, "
            "but the browser could not be opened."
        )


def _cli(
    *,
    auth: FakeAuth | None = None,
    input_values: list[str],
    password_values: list[str] | None = None,
    visualization: FakeVisualization | None = None,
):
    console, output = _console()
    query_log = QueryLog()
    graph = FakeGraph(query_log)
    cli = CLI(
        auth or FakeAuth(),
        FakeProfile(query_log),
        graph,
        FakeSearch(),
        query_log,
        console=console,
        input_func=_feeder(input_values),
        password_func=_feeder(password_values or []),
        visualization=visualization,
    )
    return cli, graph, output


def _connection(username: str) -> Connection:
    return Connection(username, username.title(), None, datetime(2024, 1, 1, tzinfo=UTC), False)


def test_startup_failure_is_friendly(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def fail_startup(_console=None):
        raise DatabaseUnavailableError("Neo4j is unavailable")

    monkeypatch.setattr(cli_module, "build_cli", fail_startup)
    assert cli_module.main() == 1
    assert "Try `make up`" in capsys.readouterr().out


def test_registration_stores_session_and_enters_main_menu() -> None:
    auth = FakeAuth()
    cli, _graph, output = _cli(
        auth=auth,
        input_values=["2", "Alice", "alice@example.com", "alice.a", "", "x"],
        password_values=["password", "password"],
    )
    assert cli.run() == 0
    assert cli.session is None
    assert auth.register_calls
    assert "Account created for @alice.a" in output.getvalue()


def test_three_failed_logins_return_to_start_menu_without_traceback() -> None:
    auth = FakeAuth(login_error=AuthenticationError("Invalid username or password"))
    cli, _graph, output = _cli(
        auth=auth,
        input_values=["1", "alice.a", "0"],
        password_values=["wrong-one", "wrong-two", "wrong-three"],
    )
    assert cli.run() == 0
    assert len(auth.login_calls) == 3
    assert "Too many failed attempts" in output.getvalue()
    assert "Traceback" not in output.getvalue()


def test_logout_clears_session() -> None:
    cli, _graph, _output = _cli(
        input_values=["1", "alice.a", "0", "0"], password_values=["password"]
    )
    assert cli.run() == 0
    assert cli.session is None


def test_start_menu_shows_live_graph_stats() -> None:
    cli, _graph, output = _cli(input_values=["0"])
    cli.stats = GraphStats(users=3, follows=7, circles=2)
    assert cli.run() == 0
    assert "3 users · 7 follows · 2 circles" in output.getvalue()


def test_connections_support_next_previous_and_back_pagination() -> None:
    cli, graph, output = _cli(
        input_values=["1", "alice.a", "5", "n", "p", "b", "0", "0"],
        password_values=["password"],
    )
    assert cli.run() == 0
    assert graph.connection_skips == [0, 0, 15, 15, 0, 0]
    assert "Following (30)" in output.getvalue()


def test_invalid_menu_and_recommendation_selection_are_safe() -> None:
    cli, graph, output = _cli(
        input_values=["bad", "1", "alice.a", "7", "99", "0", "0"],
        password_values=["password"],
    )
    assert cli.run() == 0
    assert graph.follow_calls == []
    assert "not a valid menu choice" in output.getvalue()
    assert "selection is not valid" in output.getvalue()


def test_recommendation_selection_starts_follow_action_log() -> None:
    cli, graph, _output = _cli(
        input_values=["1", "alice.a", "7", "1", "0", "0"],
        password_values=["password"],
    )
    assert cli.run() == 0
    assert graph.follow_calls == ["bob.b"]
    assert cli.query_log.action == "UC-5"


@pytest.mark.parametrize(
    ("method", "input_value", "title"),
    [
        ("_follow", "bob.b", "UC-5 · Follow a user"),
        ("_unfollow", "bob.b", "UC-6 · Unfollow a user"),
        ("_connections", "b", "UC-7 · Connections"),
    ],
)
def test_social_graph_actions_have_uc_titles(
    method: str, input_value: str, title: str
) -> None:
    cli, _graph, output = _cli(input_values=[input_value])
    cli.session = Session("alice.a", "Alice")
    getattr(cli, method)()
    assert title in output.getvalue()


def test_edit_profile_shows_one_query_panel_per_subaction() -> None:
    cli, _graph, output = _cli(
        input_values=["1", "alice.a", "c", "2", "1", "New Name", "", "", "0", "0", "0"],
        password_values=["password"],
    )
    assert cli.run() == 0
    assert output.getvalue().count("UPDATE_PROFILE") == 1


def test_terminal_controls_are_removed_from_rendered_profile_fields() -> None:
    cli, _graph, output = _cli(input_values=["0"])
    cli._print_profile(
        Profile(
            "alice.a",
            "Alice\nFAKE MENU\x1b]8;;https://evil.example\x1b\\Click\x1b]8;;\x1b\\",
            "alice@example.com",
            "bio\x1b[31m",
            "Town\x07",
            ["Circle\x1b[2J"],
            1,
            datetime(2024, 1, 1, tzinfo=UTC),
            0,
            0,
        )
    )
    rendered = output.getvalue()
    assert "\x1b" not in rendered
    assert "\x07" not in rendered
    assert "Alice\nFAKE MENU" not in rendered


def test_c1_terminal_controls_are_removed_from_rendered_profile_fields() -> None:
    cli, _graph, output = _cli(input_values=["0"])
    cli._print_profile(
        Profile(
            "alice.a",
            "Alice\x9d8;;https://evil.example\x9cClick\x9d8;;\x9c",
            "alice@example.com",
            "bio\x9b31m",
            "Town",
            ["Circle"],
            1,
            datetime(2024, 1, 1, tzinfo=UTC),
            0,
            0,
        )
    )
    rendered = output.getvalue()
    assert "\x9b" not in rendered
    assert "\x9c" not in rendered
    assert "\x9d" not in rendered


def test_numeric_selection_parser_rejects_unicode_and_huge_values() -> None:
    assert CLI._selection_index("²", 3) is None
    assert CLI._selection_index("9" * 5000, 3) is None
    assert CLI._selection_index("2", 3) == 2


def test_show_cypher_displays_redacted_parameters_and_not_password_field() -> None:
    cli, _graph, output = _cli(
        input_values=["1", "alice.a", "c", "7", "1", "0", "0"],
        password_values=["password"],
    )
    assert cli.run() == 0
    rendered = output.getvalue()
    assert "Show Cypher: ON" in rendered
    assert "RECOMMEND_BY_NETWORK" in rendered
    assert "FOLLOW_USER" in rendered
    assert "***" in rendered
    assert "password_hash" not in rendered
    assert "password" not in rendered


def test_visualization_action_shows_all_queries_and_output_path() -> None:
    console, output = _console()
    query_log = QueryLog()
    visualization = FakeVisualization(query_log)
    cli = CLI(
        FakeAuth(),
        FakeProfile(query_log),
        FakeGraph(query_log),
        FakeSearch(),
        query_log,
        console=console,
        input_func=_feeder(["1", "alice.a", "c", "v", "0", "0"]),
        password_func=_feeder(["password"]),
        visualization=visualization,
    )

    assert cli.run() == 0
    rendered = output.getvalue()
    assert cli.query_log.action == "UC-12"
    assert "UC-12 · Network Visualization" in rendered
    assert "GET_EGO_NETWORK" in rendered
    assert "GET_EDGES_AMONG" in rendered
    assert "output/network_alice.a.html" in rendered


def test_empty_visualization_action_shows_no_network_message() -> None:
    cli, _graph, output = _cli(
        input_values=["1", "alice.a", "v", "0", "0"],
        password_values=["password"],
        visualization=EmptyVisualization(),
    )

    assert cli.run() == 0
    rendered = output.getvalue()
    assert "No network yet. Follow someone first." in rendered
    assert "Traceback" not in rendered


def test_visualization_browser_failure_is_friendly_and_shows_all_queries() -> None:
    console, output = _console()
    query_log = QueryLog()
    visualization = BrowserFailureVisualization(query_log)
    cli = CLI(
        FakeAuth(),
        FakeProfile(query_log),
        FakeGraph(query_log),
        FakeSearch(),
        query_log,
        console=console,
        input_func=_feeder(["1", "alice.a", "c", "v", "0", "0"]),
        password_func=_feeder(["password"]),
        visualization=visualization,
    )

    assert cli.run() == 0
    rendered = output.getvalue()
    assert "browser could" in rendered
    assert "not be opened" in rendered
    assert "GET_EGO_NETWORK" in rendered
    assert "GET_EDGES_AMONG" in rendered
    assert "Traceback" not in rendered
    assert "password_hash" not in rendered
    assert "cypher123" not in rendered
