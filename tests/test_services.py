from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from cyphercircle.auth_service import AuthService
from cyphercircle.errors import (
    AlreadyFollowingError,
    AuthenticationError,
    ConflictError,
    MembershipError,
    NotFollowingError,
    QueryError,
    SelfActionError,
    ValidationError,
)
from cyphercircle.graph_service import GraphService
from cyphercircle.profile_service import ProfileService
from cyphercircle.search_service import SearchService
from cyphercircle.models import Session
from scripts.seed_constants import SEEDED_PASSWORD_HASH


@dataclass
class _Counters:
    relationships_created: int = 0
    relationships_deleted: int = 0


@dataclass
class _Summary:
    counters: _Counters


class FakeDB:
    def __init__(self) -> None:
        self.reads: dict[str, list[dict]] = {}
        self.writes: dict[str, tuple[list[dict], _Summary]] = {}
        self.calls: list[tuple[str, dict]] = []

    def read(self, query_name: str, _query: str, **params):
        self.calls.append((query_name, params))
        return self.reads.get(query_name, []), None

    def write(self, query_name: str, _query: str, **params):
        self.calls.append((query_name, params))
        return self.writes.get(query_name, ([], _Summary(_Counters())))


def test_auth_service_reports_both_registration_conflicts() -> None:
    db = FakeDB()
    db.reads["CHECK_REGISTRATION_CONFLICTS"] = [
        {"username_taken": True, "email_taken": True}
    ]
    with pytest.raises(ConflictError, match="username.*email"):
        AuthService(db).register("Alice", "alice@example.com", "alice.a", "password")


def test_auth_service_login_uses_generic_error_for_unknown_user() -> None:
    db = FakeDB()
    with pytest.raises(AuthenticationError, match="Invalid username or password"):
        AuthService(db).login("missing.user", "wrong-password")


def test_auth_service_rejects_overlong_and_malformed_passwords() -> None:
    db = FakeDB()
    with pytest.raises(ValidationError, match="72 UTF-8 bytes"):
        AuthService(db).register(
            "Alice", "alice@example.com", "alice.a", "x" * 73
        )
    db.reads["GET_AUTH_BY_USERNAME"] = [{"password_hash": "not-a-bcrypt-hash", "name": "Alice"}]
    with pytest.raises(AuthenticationError, match="Invalid username or password"):
        AuthService(db).login("alice.a", "password")


def test_auth_service_login_returns_session_for_valid_password() -> None:
    db = FakeDB()
    db.reads["GET_AUTH_BY_USERNAME"] = [
        {"password_hash": SEEDED_PASSWORD_HASH, "name": "Alice Example"}
    ]
    session = AuthService(db).login("Alice.Example", "cypher123")
    assert session.username == "alice.example"
    assert session.name == "Alice Example"


def test_registration_writes_user_and_circle_memberships_atomically() -> None:
    db = FakeDB()
    db.reads["CIRCLE_EXISTS"] = [{"exists": True}]
    db.reads["CHECK_REGISTRATION_CONFLICTS"] = [
        {"username_taken": False, "email_taken": False}
    ]
    db.writes["CREATE_USER_WITH_CIRCLES"] = (
        [{"username": "alice.a", "name": "Alice"}],
        None,
    )
    session = AuthService(db).register(
        "Alice", "alice@example.com", "alice.a", "password", circle_ids=("1:0",)
    )
    assert session.username == "alice.a"
    write_names = [name for name, _params in db.calls if name.startswith("CREATE") or name == "JOIN_CIRCLE"]
    assert write_names == ["CREATE_USER_WITH_CIRCLES"]
    assert db.calls[-1][1]["circle_ids"] == ["1:0"]


def test_registration_does_not_issue_partial_membership_writes_on_failure() -> None:
    class FailingDB(FakeDB):
        def write(self, query_name: str, _query: str, **params):
            self.calls.append((query_name, params))
            if query_name == "CREATE_USER_WITH_CIRCLES":
                raise QueryError("database query failed")
            return super().write(query_name, _query, **params)

    db = FailingDB()
    db.reads["CIRCLE_EXISTS"] = [{"exists": True}]
    db.reads["CHECK_REGISTRATION_CONFLICTS"] = [
        {"username_taken": False, "email_taken": False}
    ]
    with pytest.raises(QueryError):
        AuthService(db).register(
            "Alice", "alice@example.com", "alice.a", "password", circle_ids=("1:0",)
        )
    assert [name for name, _params in db.calls if name == "JOIN_CIRCLE"] == []


def test_profile_clear_and_circle_membership_messages() -> None:
    db = FakeDB()
    db.reads["USER_EXISTS"] = [{"exists": True}]
    db.reads["CIRCLE_EXISTS"] = [{"exists": True}]
    db.reads["GET_PROFILE"] = [{
        "username": "alice.a",
        "name": "Alice",
        "email": "alice@example.com",
        "bio": None,
        "location": None,
        "circles": [],
        "circle_count": 0,
        "joined_at": datetime(2024, 1, 1, tzinfo=UTC),
        "following_count": 0,
        "followers_count": 0,
    }]
    db.writes["JOIN_CIRCLE"] = ([], _Summary(_Counters(relationships_created=1)))
    db.writes["LEAVE_CIRCLE"] = ([], _Summary(_Counters(relationships_deleted=0)))
    profile = ProfileService(db)
    session = Session("alice.a", "Alice")
    result = profile.update_profile(session, bio="-", location="-")
    assert result.bio is None
    assert "CLEAR_BIO" in [name for name, _params in db.calls]
    with pytest.raises(MembershipError):
        profile.leave_circle(session, "1:0")


def test_public_profile_normalizes_target_username() -> None:
    db = FakeDB()
    db.reads["GET_PROFILE"] = [{
        "username": "bob.b",
        "name": "Bob",
        "email": "bob@example.com",
        "bio": None,
        "location": None,
        "circles": [],
        "circle_count": 0,
        "joined_at": datetime(2024, 1, 1, tzinfo=UTC),
        "following_count": 0,
        "followers_count": 0,
    }]
    profile = ProfileService(db).get_public_profile("  Bob.B ")
    assert profile.username == "bob.b"
    assert db.calls == [("GET_PROFILE", {"username": "bob.b"})]
    with pytest.raises(ValidationError):
        ProfileService(db).get_public_profile("not a username!")


def test_actor_scoped_services_require_a_session() -> None:
    db = FakeDB()
    with pytest.raises(AuthenticationError, match="authenticated session"):
        ProfileService(db).get_profile("alice.a")  # type: ignore[arg-type]
    with pytest.raises(AuthenticationError, match="authenticated session"):
        GraphService(db).follow("alice.a", "bob.b")  # type: ignore[arg-type]
    with pytest.raises(AuthenticationError, match="authenticated session"):
        SearchService(db).search_users("alice.a", "bo")  # type: ignore[arg-type]


def test_graph_service_distinguishes_self_duplicate_and_missing_follow() -> None:
    db = FakeDB()
    db.reads["USER_EXISTS"] = [{"exists": True}]
    db.writes["FOLLOW_USER"] = ([], _Summary(_Counters()))
    graph = GraphService(db)
    session = Session("alice.a", "Alice")
    with pytest.raises(SelfActionError):
        graph.follow(session, "alice.a")
    with pytest.raises(AlreadyFollowingError):
        graph.follow(session, "bob.b")
    db.writes["UNFOLLOW_USER"] = ([], _Summary(_Counters()))
    with pytest.raises(NotFollowingError):
        graph.unfollow(Session("alice.a", "Alice"), "bob.b")


def test_recommendations_run_tiers_with_remaining_limit_and_exclusions() -> None:
    db = FakeDB()
    db.reads["USER_EXISTS"] = [{"exists": True}]
    db.reads["GET_FOLLOWING_USERNAMES"] = [{"usernames": ["already.followed"]}]
    db.reads["RECOMMEND_BY_NETWORK"] = [{
        "username": "network.user", "name": "Network", "location": None,
        "followers": 4, "score": 2, "via": ["mid.one", "mid.two"],
    }]
    db.reads["GET_MY_CIRCLES"] = []
    db.reads["GET_POPULAR_USERS"] = [{
        "username": "popular.user", "name": "Popular", "location": "Town", "followers": 8,
    }]
    results = GraphService(db).recommendations(Session("alice.a", "Alice"), limit=2)
    assert [result.user.username for result in results] == ["network.user", "popular.user"]
    assert [result.reason for result in results] == ["network", "popular"]
    popular_call = next(params for name, params in db.calls if name == "GET_POPULAR_USERS")
    assert popular_call["exclude"] == ["alice.a", "already.followed", "network.user"]


def test_search_service_normalizes_query_and_maps_follow_state() -> None:
    db = FakeDB()
    db.reads["SEARCH_USERS"] = [{
        "username": "alice.other", "name": "Alice Other", "location": None,
        "followers": 3, "i_follow": True,
    }]
    result = SearchService(db).search_users(Session("me.user", "Me"), "  ALI  ")
    assert result[0].username == "alice.other"
    assert result[0].i_follow is True
    name, params = db.calls[-1]
    assert name == "SEARCH_USERS"
    assert params == {"me": "me.user", "q": "ali", "limit": 10}


def test_search_service_popular_users_normalizes_exclusions() -> None:
    db = FakeDB()
    db.reads["GET_POPULAR_USERS"] = [{
        "username": "popular.user",
        "name": "Popular User",
        "location": "Town",
        "followers": 8,
    }]

    result = SearchService(db).popular_users(exclude=["  ALICE.A  "], limit=10)

    assert result[0].username == "popular.user"
    assert result[0].followers == 8
    name, params = db.calls[-1]
    assert name == "GET_POPULAR_USERS"
    assert params == {"exclude": ["alice.a"], "limit": 10}
