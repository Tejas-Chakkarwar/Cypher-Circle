from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from cyphercircle.auth_service import AuthService
from cyphercircle.config import load_settings
from cyphercircle.db import Database, QueryLog
from cyphercircle.errors import (
    AlreadyFollowingError,
    AuthenticationError,
    ConflictError,
    DataIntegrityError,
    MembershipError,
    NotFollowingError,
    NotFoundError,
    SelfActionError,
)
from cyphercircle.graph_service import GraphService
from cyphercircle.models import Session
from cyphercircle.profile_service import ProfileService
from cyphercircle.queries import (
    CREATE_TEST_CIRCLE,
    CREATE_USER,
    DELETE_TEST_CIRCLE,
    DELETE_USER_BY_USERNAME,
)
from cyphercircle.search_service import SearchService


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class ServiceContext:
    db: Database
    auth: AuthService
    profile: ProfileService
    graph: GraphService
    search: SearchService
    users: list[str]
    busy_user: str
    circle_id: str


@pytest.fixture(scope="module")
def services() -> ServiceContext:
    log_path = ROOT / "data/processed/preprocess_log.json"
    if not log_path.exists():
        pytest.fail("run `make data` before strict service integration tests")
    preprocess_log = json.loads(log_path.read_text(encoding="utf-8"))
    db = Database(load_settings())
    auth = AuthService(db)
    profile = ProfileService(db)
    graph = GraphService(db)
    search = SearchService(db)
    users: list[str] = []
    circle_id = f"999999:{int(uuid4().hex[:12], 16)}"

    def register(label: str, *, circles: tuple[str, ...] = ()) -> str:
        username = f"zztest_{uuid4().hex[:16]}_"
        auth.register(
            f"{label} Test User",
            f"{username}@example.com",
            username,
            "test-pass-123",
            circle_ids=circles,
        )
        users.append(username)
        return username

    try:
        db.write(
            "CREATE_TEST_CIRCLE",
            CREATE_TEST_CIRCLE,
            circle_id=circle_id,
            name="@integration's isolated list",
        )
        for label in ("Alpha", "Beta", "Gamma"):
            register(label)
        circle_user = register("Circle", circles=(circle_id,))
        register("No Circle")
        register("Integrity")
        yield ServiceContext(
            db=db,
            auth=auth,
            profile=profile,
            graph=graph,
            search=search,
            users=users,
            busy_user=preprocess_log["demo_users"]["busy_user"],
            circle_id=circle_id,
        )
    finally:
        cleanup_errors: list[Exception] = []
        for username in users:
            try:
                db.write(
                    "DELETE_USER_BY_USERNAME",
                    DELETE_USER_BY_USERNAME,
                    username=username,
                    log=False,
                )
            except Exception as exc:  # pragma: no cover - exercised on cleanup failure
                cleanup_errors.append(exc)
        try:
            db.write(
                "DELETE_TEST_CIRCLE",
                DELETE_TEST_CIRCLE,
                circle_id=circle_id,
                log=False,
            )
        except Exception as exc:  # pragma: no cover - exercised on cleanup failure
            cleanup_errors.append(exc)
        try:
            db.close()
        finally:
            if cleanup_errors:
                raise RuntimeError(f"failed to clean up {len(cleanup_errors)} integration users")


@pytest.mark.integration
def test_service_layer_end_to_end(services: ServiceContext) -> None:
    alpha, beta, gamma, circle_user, no_circle_user, integrity_user = services.users
    auth = services.auth
    profile = services.profile
    graph = services.graph
    search = services.search
    alpha_session = Session(alpha, "Alpha Test User")
    beta_session = Session(beta, "Beta Test User")
    gamma_session = Session(gamma, "Gamma Test User")
    circle_session = Session(circle_user, "Circle Test User")
    no_circle_session = Session(no_circle_user, "No Circle Test User")

    assert auth.login(alpha, "test-pass-123").username == alpha
    with pytest.raises(AuthenticationError):
        auth.login(alpha, "wrong-password")
    with pytest.raises(AuthenticationError):
        auth.login("zztest_unknown_", "wrong-password")

    with pytest.raises(ConflictError, match="username is already taken"):
        auth.register("Duplicate", f"{uuid4().hex}@example.com", alpha, "test-pass-123")
    with pytest.raises(ConflictError, match="email is already taken"):
        auth.register("Duplicate", f"{alpha}@example.com", f"{uuid4().hex[:12]}", "test-pass-123")
    with pytest.raises(ConflictError, match="username.*email"):
        auth.register("Duplicate", f"{beta}@example.com", alpha, "test-pass-123")

    empty_profile = profile.get_profile(alpha_session)
    assert empty_profile.circles == []
    assert empty_profile.circle_count == 0
    updated = profile.update_profile(alpha_session, name="Alpha Updated", bio="bio", location="Town")
    assert updated.name == "Alpha Updated"
    cleared = profile.update_profile(alpha_session, bio="-", location="-")
    assert cleared.bio is None
    assert cleared.location is None

    circle_profile = profile.get_profile(circle_session)
    assert circle_profile.circle_count == 1
    assert "@integration's isolated list" in circle_profile.circles
    assert profile.join_circle(circle_session, services.circle_id) is False
    assert profile.leave_circle(circle_session, services.circle_id) is True
    with pytest.raises(MembershipError):
        profile.leave_circle(circle_session, services.circle_id)
    assert profile.join_circle(circle_session, services.circle_id) is True
    circle_profile = profile.get_profile(circle_session)
    assert circle_profile.circle_count == 1
    assert "@integration's isolated list" in circle_profile.circles
    assert profile.leave_circle(circle_session, services.circle_id) is True
    with pytest.raises(MembershipError):
        profile.leave_circle(circle_session, services.circle_id)

    assert graph.follow(alpha_session, beta) is True
    with pytest.raises(AlreadyFollowingError):
        graph.follow(alpha_session, beta)
    with pytest.raises(SelfActionError):
        graph.follow(alpha_session, alpha)
    with pytest.raises(NotFoundError):
        graph.follow(alpha_session, "zztest_missing_")
    with pytest.raises(NotFollowingError):
        graph.unfollow(beta_session, alpha)
    assert graph.unfollow(alpha_session, beta) is True

    graph.follow(alpha_session, beta)
    graph.follow(gamma_session, beta)
    counts = graph.connection_counts(alpha_session)
    assert counts.following == 1
    assert counts.followers == 0
    following_first = graph.following(alpha_session)
    following_second = graph.following(alpha_session)
    followers_first = graph.followers(beta_session)
    followers_second = graph.followers(beta_session)
    assert [row.username for row in following_first] == [beta]
    assert following_first == following_second
    assert {row.username for row in followers_first} == {alpha, gamma}
    assert followers_first == followers_second
    assert graph.following(beta_session) == []
    assert graph.following(alpha_session) == graph.following(alpha_session)
    mutual = graph.mutual_connections(alpha_session, gamma)
    assert mutual.total == len(mutual.users) == 1
    assert mutual.users[0].username == beta

    graph.follow(alpha_session, services.busy_user)
    recommendations = graph.recommendations(alpha_session, limit=10)
    recommended_names = {item.user.username for item in recommendations}
    assert alpha not in recommended_names
    assert services.busy_user not in recommended_names

    query_log = QueryLog()
    services.db.query_log = query_log
    query_log.begin_action("UC-9-CIRCLE-FALLBACK")
    assert profile.join_circle(circle_session, services.circle_id) is True
    assert profile.join_circle(beta_session, services.circle_id) is True
    circle_recommendations = graph.recommendations(circle_session, limit=10)
    circle_query_names = [entry.name for entry in query_log.entries()]
    assert "RECOMMEND_BY_CIRCLE" in circle_query_names
    assert "GET_POPULAR_USERS" in circle_query_names
    assert any(item.reason == "circle" for item in circle_recommendations)
    assert any(item.reason == "popular" for item in circle_recommendations)
    profile.leave_circle(circle_session, services.circle_id)
    profile.leave_circle(beta_session, services.circle_id)

    query_log.begin_action("UC-9-POPULAR-FALLBACK")
    assert graph.connection_counts(no_circle_session).following == 0
    assert profile.get_profile(no_circle_session).circles == []
    no_circle_recommendations = graph.recommendations(no_circle_session, limit=10)
    no_circle_query_names = [entry.name for entry in query_log.entries()]
    assert no_circle_recommendations
    assert "RECOMMEND_BY_CIRCLE" not in no_circle_query_names
    assert "GET_POPULAR_USERS" in no_circle_query_names

    search_results = search.search_users(alpha_session, f"  {beta[:4].upper()}  ")
    assert any(row.username == beta for row in search_results)
    assert all(row.username != alpha for row in search_results)
    popular = search.popular_users(limit=20)
    assert [row.followers for row in popular] == sorted(
        (row.followers for row in popular), reverse=True
    )

    with pytest.raises(DataIntegrityError):
        services.db.write(
            "CREATE_USER",
            CREATE_USER,
            username=integrity_user,
            email=f"duplicate-{uuid4().hex}@example.com",
            name="Duplicate Constraint",
            password_hash="not-a-real-password-hash",
            bio=None,
            location=None,
            joined_at=datetime(2024, 1, 1, tzinfo=UTC).isoformat().replace("+00:00", "Z"),
        )


@pytest.mark.integration
def test_public_profile_reads_other_users(services: ServiceContext) -> None:
    profile = services.profile.get_public_profile(services.busy_user.upper())
    assert profile.username == services.busy_user
    assert profile.followers_count >= 0
    with pytest.raises(NotFoundError):
        services.profile.get_public_profile("zz_missing_user_404")
