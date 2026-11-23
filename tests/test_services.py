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
    NotFoundError,
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


def test_recommendations_use_only_the_two_hop_query() -> None:
    db = FakeDB()
    db.reads["USER_EXISTS"] = [{"exists": True}]
    db.reads["RECOMMEND_BY_NETWORK"] = [{
        "username": "network.user", "name": "Network", "location": None,
        "followers": 4, "score": 2, "via": ["mid.one", "mid.two"],
    }]
    results = GraphService(db).recommendations(Session("alice.a", "Alice"), limit=2)
    assert [result.user.username for result in results] == ["network.user"]
    assert results[0].score == 2
    assert results[0].via == ["mid.one", "mid.two"]
    assert [name for name, _params in db.calls] == ["USER_EXISTS", "RECOMMEND_BY_NETWORK"]


def test_recommendations_are_empty_without_two_hop_paths() -> None:
    db = FakeDB()
    db.reads["USER_EXISTS"] = [{"exists": True}]
    assert GraphService(db).recommendations(Session("alice.a", "Alice")) == []
    assert "GET_POPULAR_USERS" not in [name for name, _params in db.calls]


def test_mutual_connections_can_compare_any_two_users() -> None:
    db = FakeDB()
    db.reads["USER_EXISTS"] = [{"exists": True}]
    db.reads["COUNT_MUTUAL_FOLLOWING"] = [{"total": 1}]
    db.reads["GET_MUTUAL_FOLLOWING"] = [{"username": "carol.c", "name": "Carol", "location": None}]
    db.reads["GET_RELATIONSHIP_STATUS"] = [{"i_follow": True, "they_follow": False}]
    result = GraphService(db).mutual_connections(
        Session("alice.a", "Alice"), "Dave.D", first=" Bob.B "
    )
    assert (result.first, result.second, result.total) == ("bob.b", "dave.d", 1)
    count_params = next(params for name, params in db.calls if name == "COUNT_MUTUAL_FOLLOWING")
    assert count_params == {"me": "bob.b", "other": "dave.d"}
    with pytest.raises(SelfActionError):
        GraphService(db).mutual_connections(Session("alice.a", "Alice"), "bob.b", first="bob.b")


def test_degrees_of_separation_returns_path_and_degree_count() -> None:
    db = FakeDB()
    db.reads["USER_EXISTS"] = [{"exists": True}]
    db.reads["FIND_SHORTEST_FOLLOW_PATH"] = [{
        "users": [
            {"username": "a.user", "name": "A"},
            {"username": "b.user", "name": "B"},
            {"username": "c.user", "name": "C"},
            {"username": "d.user", "name": "D"},
        ],
        "hops": 3,
    }]
    result = GraphService(db).degrees_of_separation(Session("me.user", "Me"), "A.User", "d.user")
    assert result.found
    assert result.degrees == 3
    assert result.max_hops == 4
    assert [step.username for step in result.path] == ["a.user", "b.user", "c.user", "d.user"]


def test_degrees_of_separation_reports_no_path_and_rejects_bad_input() -> None:
    db = FakeDB()
    db.reads["USER_EXISTS"] = [{"exists": True}]
    graph = GraphService(db)
    session = Session("me.user", "Me")
    result = graph.degrees_of_separation(session, "a.user", "d.user")
    assert not result.found
    assert result.degrees is None
    with pytest.raises(ValidationError):
        graph.degrees_of_separation(session, "a.user", "A.USER")
    db.reads["USER_EXISTS"] = [{"exists": False}]
    with pytest.raises(NotFoundError):
        graph.degrees_of_separation(session, "a.user", "missing.user")


def test_profile_update_changes_email_but_never_username() -> None:
    db = FakeDB()
    db.reads["USER_EXISTS"] = [{"exists": True}]
    db.reads["CHECK_EMAIL_IN_USE"] = [{"taken": False}]
    db.reads["GET_PROFILE"] = [{
        "username": "alice.a", "name": "Alice", "email": "new@example.com",
        "bio": None, "location": None, "circles": [], "circle_count": 0,
        "joined_at": datetime(2024, 1, 1, tzinfo=UTC),
        "following_count": 0, "followers_count": 0,
    }]
    ProfileService(db).update_profile(Session("alice.a", "Alice"), email="  NEW@Example.com ")
    update = next(params for name, params in db.calls if name == "UPDATE_PROFILE")
    assert update["email"] == "new@example.com"
    assert update["username"] == "alice.a"
    assert "new_username" not in update


def test_profile_update_rejects_email_used_by_someone_else() -> None:
    db = FakeDB()
    db.reads["USER_EXISTS"] = [{"exists": True}]
    db.reads["CHECK_EMAIL_IN_USE"] = [{"taken": True}]
    with pytest.raises(ConflictError, match="email is already taken"):
        ProfileService(db).update_profile(Session("alice.a", "Alice"), email="bob@example.com")
    assert "UPDATE_PROFILE" not in [name for name, _params in db.calls]
    with pytest.raises(ValidationError):
        ProfileService(db).update_profile(Session("alice.a", "Alice"), email="not-an-email")


def test_all_users_lists_everyone_with_follow_state() -> None:
    db = FakeDB()
    db.reads["COUNT_USERS"] = [{"total": 2}]
    db.reads["GET_ALL_USERS"] = [
        {"username": "alice.a", "name": "Alice", "location": None, "followers": 1,
         "is_me": True, "i_follow": False},
        {"username": "bob.b", "name": "Bob", "location": "Town", "followers": 3,
         "is_me": False, "i_follow": True},
    ]
    directory = SearchService(db).all_users(Session("alice.a", "Alice"), skip=0, limit=20)
    assert directory.total == 2
    assert [(row.username, row.is_me, row.i_follow) for row in directory.users] == [
        ("alice.a", True, False),
        ("bob.b", False, True),
    ]
    assert db.calls[-1] == ("GET_ALL_USERS", {"me": "alice.a", "skip": 0, "limit": 20})


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
