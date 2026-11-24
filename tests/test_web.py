from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from cyphercircle.config import WebSettings
from cyphercircle.errors import AuthenticationError, QueryError, ValidationError
from cyphercircle.models import (
    CircleSummary,
    Connection,
    ConnectionCounts,
    DirectoryEntry,
    GraphStats,
    MutualConnection,
    MutualConnections,
    PathStep,
    Profile,
    Recommendation,
    RelationshipStatus,
    SearchResult,
    SeparationResult,
    Session,
    UserDirectory,
    UserSummary,
)
from cyphercircle.web import WebServices, create_app


NOW = datetime(2024, 1, 1, tzinfo=UTC)


def _profile(username: str = "alice.a", name: str = "Alice") -> Profile:
    return Profile(
        username,
        name,
        f"{username}@example.com",
        "A short bio",
        "Toronto",
        ["Designers"],
        1,
        NOW,
        4,
        7,
    )


class FakeAuth:
    def __init__(self) -> None:
        self.login_calls: list[tuple[str, str]] = []
        self.register_calls: list[tuple] = []
        self.login_error: Exception | None = None
        self.register_error: Exception | None = None

    def login(self, username: str, password: str) -> Session:
        self.login_calls.append((username, password))
        if self.login_error:
            raise self.login_error
        return Session("alice.a", "Alice")

    def register(self, *args, **kwargs) -> Session:
        self.register_calls.append((args, kwargs))
        if self.register_error:
            raise self.register_error
        return Session("alice.a", "Alice")


class FakeProfile:
    def __init__(self) -> None:
        self.profile = _profile()
        self.public_profile = _profile("bob.b", "Bob")
        self.circles = [CircleSummary("1:0", "Designers", 12), CircleSummary("2:0", "Builders", 8)]
        self.update_calls: list[dict] = []
        self.join_calls: list[str] = []
        self.leave_calls: list[str] = []

    def get_top_circles(self, limit: int = 10):
        return self.circles[:limit]

    def get_profile(self, _session: Session) -> Profile:
        return self.profile

    def get_public_profile(self, username: str) -> Profile:
        if username.lower() == "bob.b":
            return self.public_profile
        return _profile(username, "Someone")

    def update_profile(self, _session: Session, **kwargs) -> Profile:
        self.update_calls.append(kwargs)
        self.profile = _profile(name=kwargs.get("name") or "Alice")
        return self.profile

    def get_my_circles(self, _session: Session):
        return self.circles[:1]

    def join_circle(self, _session: Session, circle_id: str) -> bool:
        self.join_calls.append(circle_id)
        return True

    def leave_circle(self, _session: Session, circle_id: str) -> bool:
        self.leave_calls.append(circle_id)
        return True


class FakeGraph:
    def __init__(self) -> None:
        self.follow_calls: list[str] = []
        self.unfollow_calls: list[str] = []
        self.recommendation = Recommendation(
            UserSummary("bob.b", "Bob", "Toronto", 6),
            2,
            ["mid.one"],
        )
        self.mutual_calls: list[tuple[str | None, str]] = []
        self.separation_calls: list[tuple[str, str]] = []

    def recommendations(self, _session: Session, *, limit: int = 10):
        return [self.recommendation][:limit]

    def mutual_connections(self, session: Session, other: str, *, first=None, **_kwargs):
        self.mutual_calls.append((first, other))
        return MutualConnections(
            1, [], RelationshipStatus(True, False), first or session.username, other.lower()
        )

    def degrees_of_separation(self, _session: Session, source: str, target: str):
        self.separation_calls.append((source, target))
        if target == "nobody.n":
            return SeparationResult(source, target, 4, [])
        return SeparationResult(
            source,
            target,
            4,
            [PathStep(source, "Alice"), PathStep("mid.one", "Mid"), PathStep(target, "Bob")],
        )

    def connection_counts(self, _session: Session):
        return ConnectionCounts(4, 7)

    def following(self, _session: Session, *, skip: int, limit: int):
        return [Connection("bob.b", "Bob", "Toronto", NOW, True)][skip : skip + limit]

    def followers(self, _session: Session, *, skip: int, limit: int):
        return [Connection("bob.b", "Bob", "Toronto", NOW, True)][skip : skip + limit]

    def follow(self, _session: Session, target: str) -> bool:
        self.follow_calls.append(target)
        return True

    def unfollow(self, _session: Session, target: str) -> bool:
        self.unfollow_calls.append(target)
        return True


class FakeSearch:
    def __init__(self) -> None:
        self.directory_skips: list[int] = []

    def all_users(self, _session: Session, *, skip: int = 0, limit: int = 20):
        self.directory_skips.append(skip)
        users = [
            DirectoryEntry("alice.a", "Alice", "Toronto", 7, True, False),
            DirectoryEntry("bob.b", "Bob", "Toronto", 6, False, True),
            DirectoryEntry("carol.c", "Carol", None, 2, False, False),
        ]
        return UserDirectory(60, users)

    def search_users(self, _session: Session, _query: str, limit: int = 10):
        return [SearchResult("bob.b", "Bob", "Toronto", 6, False)][:limit]

    def popular_users(self, *, exclude=(), limit: int = 10):
        return [UserSummary("bob.b", "Bob", "Toronto", 6)][:limit]


class FakeStartup:
    def current_stats(self):
        return GraphStats(100, 240, 12)


class FakeVisualization:
    def __init__(self, tmp_path: Path) -> None:
        self.output_dir = tmp_path
        self.path = tmp_path / "network_alice.a.html"

    def generate(self, _session: Session):
        self.path.write_text("<!doctype html><title>Offline graph</title>", encoding="utf-8")
        return self.path


def _app(tmp_path: Path):
    auth = FakeAuth()
    profile = FakeProfile()
    graph = FakeGraph()
    services = WebServices(
        auth=auth,
        profile=profile,
        graph=graph,
        search=FakeSearch(),
        startup=FakeStartup(),
        visualization=FakeVisualization(tmp_path),
    )
    app = create_app(
        services,
        web_settings=WebSettings("127.0.0.1", 5000, "test-secret"),
        config={"TESTING": True},
    )
    return app, auth, profile, graph


def _csrf(client) -> str:
    with client.session_transaction() as state:
        return state["_csrf_token"]


def _login(client) -> None:
    client.get("/login")
    token = _csrf(client)
    response = client.post(
        "/login",
        data={"csrf_token": token, "username": "alice.a", "password": "password123"},
    )
    assert response.status_code == 302


def test_landing_page_shows_stats_and_primary_actions(tmp_path: Path) -> None:
    app, *_ = _app(tmp_path)
    response = app.test_client().get("/")
    assert response.status_code == 200
    assert b"Cypher Circle" in response.data
    assert b"100" in response.data and b"240" in response.data
    assert b"Create your account" in response.data


def test_registration_success_stores_only_minimum_identity(tmp_path: Path) -> None:
    app, auth, *_ = _app(tmp_path)
    client = app.test_client()
    client.get("/register")
    response = client.post(
        "/register",
        data={
            "csrf_token": _csrf(client),
            "name": "New Person",
            "email": "new@example.com",
            "username": "new.person",
            "password": "password123",
            "password_confirm": "password123",
            "bio": "Hello",
            "circle_ids": "1:0",
        },
    )
    assert response.status_code == 302
    assert auth.register_calls
    with client.session_transaction() as state:
        assert state["identity"] == {"username": "alice.a", "name": "Alice"}
        assert "password" not in str(dict(state))
        assert state["_csrf_token"]


def test_registration_error_never_repopulates_password(tmp_path: Path) -> None:
    app, auth, *_ = _app(tmp_path)
    auth.register_error = ValidationError("username is invalid")
    client = app.test_client()
    client.get("/register")
    response = client.post(
        "/register",
        data={
            "csrf_token": _csrf(client),
            "name": "New Person",
            "email": "new@example.com",
            "username": "bad",
            "password": "password123",
            "password_confirm": "password123",
        },
    )
    assert response.status_code == 400
    assert b"username is invalid" in response.data
    assert b"password123" not in response.data


def test_registration_requires_matching_password_confirmation(tmp_path: Path) -> None:
    app, auth, *_ = _app(tmp_path)
    client = app.test_client()
    client.get("/register")
    response = client.post(
        "/register",
        data={
            "csrf_token": _csrf(client),
            "name": "New Person",
            "email": "new@example.com",
            "username": "new.person",
            "password": "password123",
            "password_confirm": "different123",
        },
    )
    assert response.status_code == 400
    assert b"Passwords do not match" in response.data
    assert auth.register_calls == []
    assert b"password123" not in response.data


def test_registration_passes_all_circle_selections_to_service(tmp_path: Path) -> None:
    app, auth, *_ = _app(tmp_path)
    client = app.test_client()
    client.get("/register")
    response = client.post(
        "/register",
        data={
            "csrf_token": _csrf(client),
            "name": "New Person",
            "email": "new@example.com",
            "username": "new.person",
            "password": "password123",
            "password_confirm": "password123",
            "circle_ids": ["1:0", "2:0", "3:0", "4:0"],
        },
    )
    assert response.status_code == 302
    assert auth.register_calls[0][1]["circle_ids"] == ["1:0", "2:0", "3:0", "4:0"]


def test_login_failure_is_generic_and_password_is_not_rendered(tmp_path: Path) -> None:
    app, auth, *_ = _app(tmp_path)
    auth.login_error = AuthenticationError("Invalid username or password")
    client = app.test_client()
    client.get("/login")
    response = client.post(
        "/login",
        data={"csrf_token": _csrf(client), "username": "alice.a", "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert b"Invalid username or password" in response.data
    assert b"wrong-password" not in response.data


def test_unauthorized_routes_redirect_and_csrf_is_required(tmp_path: Path) -> None:
    app, *_ = _app(tmp_path)
    client = app.test_client()
    response = client.get("/dashboard")
    assert response.status_code == 302 and "/login" in response.location
    response = client.post("/login", data={"username": "alice.a", "password": "password123"})
    assert response.status_code == 400
    assert b"Invalid or missing request token" in response.data


def test_authenticated_actions_render_and_use_services(tmp_path: Path) -> None:
    app, _auth, profile, graph = _app(tmp_path)
    client = app.test_client()
    _login(client)
    assert b"People worth knowing" in client.get("/dashboard").data
    assert b"Bob" in client.get("/users/bob.b").data
    assert b"Connections" in client.get("/connections").data
    assert b"Mutual connections" in client.get("/mutual?username=bob.b").data
    assert b"Bob" in client.get("/recommendations").data
    assert b"Bob" in client.get("/search?q=bo").data
    assert b"Popular users" in client.get("/popular").data
    assert b'aria-current="page"' in client.get("/popular").data
    assert b"Your circles" in client.get("/circles").data
    assert b"Network visualization" in client.get("/visualization").data

    token = _csrf(client)
    client.post("/users/bob.b/follow", data={"csrf_token": token, "next": "/dashboard"})
    client.post("/users/bob.b/unfollow", data={"csrf_token": token, "next": "/dashboard"})
    client.post("/circles/join", data={"csrf_token": token, "circle_id": "2:0"})
    client.post("/circles/leave", data={"csrf_token": token, "circle_id": "1:0"})
    client.post("/profile/edit", data={"csrf_token": token, "name": "Alice Updated", "bio": "-", "location": "-"})
    assert graph.follow_calls == ["bob.b"]
    assert graph.unfollow_calls == ["bob.b"]
    assert profile.join_calls == ["2:0"]
    assert profile.leave_calls == ["1:0"]
    assert profile.update_calls[0]["bio"] == "-"


def test_recommendations_page_renders_card_structure_and_responsive_styles(tmp_path: Path) -> None:
    app, *_ = _app(tmp_path)
    client = app.test_client()
    _login(client)
    response = client.get("/recommendations")
    assert response.status_code == 200
    assert b"recommendation-grid" in response.data
    assert b"recommendation-card" in response.data
    assert b"card-top" in response.data
    assert b"score" in response.data
    assert b"reason-network" in response.data
    assert b"Follow" in response.data

    css = Path("cyphercircle/web_static/style.css").read_text(encoding="utf-8")
    assert ".recommendation-grid" in css
    assert "grid-template-columns: repeat(3" in css
    assert "grid-template-columns: repeat(2" in css
    assert "grid-template-columns: 1fr" in css


def test_mutual_connections_page_has_pagination_for_full_result_set(tmp_path: Path) -> None:
    app, _auth, _profile, graph = _app(tmp_path)
    graph.mutual_connections = lambda _session, _other, **_kwargs: MutualConnections(
        31,
        [MutualConnection("shared.user", "Shared User", "Toronto")],
        RelationshipStatus(True, False),
    )  # type: ignore[method-assign]
    client = app.test_client()
    _login(client)

    response = client.get("/mutual?username=bob.b")

    assert response.status_code == 200
    assert b"31 total" in response.data
    assert b"Next" in response.data
    assert b"/mutual?username=bob.b&amp;page=2" in response.data


def test_circles_page_shows_empty_directory_state(tmp_path: Path) -> None:
    app, _auth, profile, _graph = _app(tmp_path)
    profile.circles = []
    client = app.test_client()
    _login(client)

    response = client.get("/circles")

    assert response.status_code == 200
    assert b"No circles are available yet." in response.data


def test_circles_page_keeps_memberships_outside_popular_list(tmp_path: Path) -> None:
    app, _auth, profile, _graph = _app(tmp_path)
    profile.circles = [CircleSummary("1:0", "Popular", 12)]
    profile.get_my_circles = lambda _session: [CircleSummary("99:0", "My Private Circle")]  # type: ignore[method-assign]
    client = app.test_client()
    _login(client)

    response = client.get("/circles")

    assert response.status_code == 200
    assert b"My Private Circle" in response.data
    assert b"Your membership" in response.data


def test_logout_clears_session_and_visualization_is_safely_embedded(tmp_path: Path) -> None:
    app, *_ = _app(tmp_path)
    client = app.test_client()
    _login(client)
    assert client.get("/visualization").status_code == 200
    response = client.get("/visualization/document")
    assert response.status_code == 200
    assert b"Offline graph" in response.data
    response = client.post("/logout", data={"csrf_token": _csrf(client)})
    assert response.status_code == 302
    assert client.get("/dashboard").status_code == 302


def test_database_errors_are_sanitized(tmp_path: Path) -> None:
    app, _auth, profile, _graph = _app(tmp_path)
    profile.get_profile = lambda _session: (_ for _ in ()).throw(QueryError("neo4j password=secret"))  # type: ignore[method-assign]
    client = app.test_client()
    _login(client)
    response = client.get("/dashboard")
    assert response.status_code == 400
    assert b"database operation could not be completed" in response.data
    assert b"neo4j password=secret" not in response.data
    assert b"Traceback" not in response.data


def test_user_content_is_escaped_and_external_redirects_are_rejected(tmp_path: Path) -> None:
    app, _auth, profile, graph = _app(tmp_path)
    profile.profile = _profile(name="<script>alert(1)</script>")
    client = app.test_client()
    _login(client)
    response = client.get("/profile", follow_redirects=True)
    assert b"&lt;script&gt;alert(1)&lt;/script&gt;" in response.data
    assert b"<script>alert(1)</script>" not in response.data

    token = _csrf(client)
    response = client.post(
        "/users/bob.b/follow",
        data={"csrf_token": token, "next": "https://evil.example/steal"},
    )
    assert response.status_code == 302
    assert response.location.endswith("/users/bob.b")
    assert graph.follow_calls == ["bob.b"]


def test_make_run_remains_the_cli_entry_point() -> None:
    makefile = Path("Makefile").read_text(encoding="utf-8")
    assert "run:" in makefile
    assert "$(PYTHON) -m cyphercircle\n" in makefile
    assert "web:" in makefile
    assert "$(PYTHON) -m cyphercircle.web" in makefile


def test_new_use_case_pages_require_login(tmp_path: Path) -> None:
    app, *_ = _app(tmp_path)
    client = app.test_client()
    for path in ("/users", "/separation"):
        response = client.get(path)
        assert response.status_code == 302 and "/login" in response.location


def test_all_users_page_lists_everyone_with_follow_controls(tmp_path: Path) -> None:
    app, *_ = _app(tmp_path)
    search = app.extensions["cyphercircle.services"].search
    client = app.test_client()
    _login(client)
    response = client.get("/users?page=2")
    html = response.data.decode()
    assert response.status_code == 200
    assert "UC-10 · View all users" in html
    assert "@alice.a" in html and "@bob.b" in html and "@carol.c" in html
    assert '<span class="pill">You</span>' in html
    assert "/users/bob.b/unfollow" in html
    assert "/users/carol.c/follow" in html
    assert "Page 2 of 3" in html
    assert search.directory_skips == [25]


def test_separation_page_shows_path_degrees_and_no_path_message(tmp_path: Path) -> None:
    app, _auth, _profile, graph = _app(tmp_path)
    client = app.test_client()
    _login(client)
    empty = client.get("/separation").data.decode()
    assert "UC-13 · Degrees of separation" in empty
    assert graph.separation_calls == []

    found = client.get("/separation?source=alice.a&target=bob.b").data.decode()
    assert "Degrees of separation from @alice.a to @bob.b: 2" in found
    assert found.index("@alice.a") < found.index("@mid.one") < found.rindex("@bob.b")

    missing = client.get("/separation?source=alice.a&target=nobody.n").data.decode()
    assert "No connection found within 4 hops" in missing
    assert graph.separation_calls == [("alice.a", "bob.b"), ("alice.a", "nobody.n")]


def test_separation_page_shows_validation_errors(tmp_path: Path) -> None:
    app, _auth, _profile, graph = _app(tmp_path)

    def reject(_session, _source, _target):
        raise ValidationError("choose two different users")

    graph.degrees_of_separation = reject
    client = app.test_client()
    _login(client)
    html = client.get("/separation?source=alice.a&target=alice.a").data.decode()
    assert "choose two different users" in html


def test_mutual_page_can_compare_two_other_users(tmp_path: Path) -> None:
    app, _auth, _profile, graph = _app(tmp_path)
    client = app.test_client()
    _login(client)
    html = client.get("/mutual?first=carol.c&username=bob.b").data.decode()
    assert "@carol.c and @bob.b both follow 1 person" in html
    assert "@carol.c follows @bob.b" in html
    assert graph.mutual_calls[-1] == ("carol.c", "bob.b")
    client.get("/mutual?username=bob.b")
    assert graph.mutual_calls[-1] == (None, "bob.b")


def test_edit_profile_submits_email_and_keeps_username_read_only(tmp_path: Path) -> None:
    app, _auth, profile, _graph = _app(tmp_path)
    client = app.test_client()
    _login(client)
    form = client.get("/profile/edit").data.decode()
    assert 'name="email"' in form
    assert 'value="@alice.a" disabled' in form
    assert 'name="username"' not in form
    response = client.post(
        "/profile/edit",
        data={"csrf_token": _csrf(client), "name": "Alice", "email": "new@example.com", "bio": ""},
    )
    assert response.status_code == 302
    assert profile.update_calls[-1]["email"] == "new@example.com"


def test_recommendations_page_only_describes_two_hop_paths(tmp_path: Path) -> None:
    app, *_ = _app(tmp_path)
    client = app.test_client()
    _login(client)
    html = client.get("/recommendations").data.decode()
    assert "UC-9" in html
    assert "Followed by @mid.one" in html
    assert "Popular on Cypher Circle" not in html


def test_navigation_links_to_every_use_case_page(tmp_path: Path) -> None:
    app, *_ = _app(tmp_path)
    client = app.test_client()
    _login(client)
    html = client.get("/dashboard").data.decode()
    for path in ("/users", "/search", "/popular", "/separation"):
        assert f'href="{path}"' in html
