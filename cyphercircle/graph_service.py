from __future__ import annotations

from .db import Database
from .errors import (
    AlreadyFollowingError,
    NotFollowingError,
    NotFoundError,
    SelfActionError,
    ValidationError,
)
from .models import (
    Connection,
    ConnectionCounts,
    MutualConnection,
    MutualConnections,
    PathStep,
    Recommendation,
    RelationshipStatus,
    SeparationResult,
    Session,
    UserSummary,
)
from .queries import (
    COUNT_CONNECTIONS,
    COUNT_MUTUAL_FOLLOWING,
    FIND_SHORTEST_FOLLOW_PATH,
    FOLLOW_USER,
    GET_FOLLOWERS,
    GET_FOLLOWING,
    GET_MUTUAL_FOLLOWING,
    GET_RELATIONSHIP_STATUS,
    RECOMMEND_BY_NETWORK,
    UNFOLLOW_USER,
    USER_EXISTS,
)
from .service_utils import counter, native_datetime, principal_username
from .validation import normalize_username, validate_limit, validate_paging


# UC-13 searches directed follow paths of at most this many hops. The same
# bound is written into FIND_SHORTEST_FOLLOW_PATH because Cypher does not
# accept parameters for variable-length relationship bounds.
MAX_SEPARATION_HOPS = 4


class GraphService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def follow(self, session: Session, target: str) -> bool:
        me = principal_username(session)
        target = normalize_username(target)
        if me == target:
            raise SelfActionError("you cannot follow yourself")
        self._require_user(me)
        self._require_user(target)
        _, summary = self.db.write("FOLLOW_USER", FOLLOW_USER, me=me, target=target)
        if counter(summary, "relationships_created") == 0:
            raise AlreadyFollowingError(f"you already follow @{target}")
        return True

    def unfollow(self, session: Session, target: str) -> bool:
        me = principal_username(session)
        target = normalize_username(target)
        if me == target:
            raise SelfActionError("you cannot unfollow yourself")
        self._require_user(me)
        self._require_user(target)
        _, summary = self.db.write("UNFOLLOW_USER", UNFOLLOW_USER, me=me, target=target)
        if counter(summary, "relationships_deleted") == 0:
            raise NotFollowingError(f"you were not following @{target}")
        return True

    def connection_counts(self, session: Session) -> ConnectionCounts:
        username = principal_username(session)
        self._require_user(username)
        rows, _ = self.db.read("COUNT_CONNECTIONS", COUNT_CONNECTIONS, username=username)
        row = rows[0] if rows else {"following": 0, "followers": 0}
        return ConnectionCounts(int(row["following"]), int(row["followers"]))

    def following(self, session: Session, *, skip: int = 0, limit: int = 15) -> list[Connection]:
        username = principal_username(session)
        skip, limit = validate_paging(skip, limit)
        self._require_user(username)
        rows, _ = self.db.read(
            "GET_FOLLOWING", GET_FOLLOWING, username=username, skip=skip, limit=limit
        )
        return [self._connection(row, "follows_back") for row in rows]

    def followers(self, session: Session, *, skip: int = 0, limit: int = 15) -> list[Connection]:
        username = principal_username(session)
        skip, limit = validate_paging(skip, limit)
        self._require_user(username)
        rows, _ = self.db.read(
            "GET_FOLLOWERS", GET_FOLLOWERS, username=username, skip=skip, limit=limit
        )
        return [self._connection(row, "i_follow_back") for row in rows]

    def mutual_connections(
        self,
        session: Session,
        other: str,
        *,
        first: str | None = None,
        skip: int = 0,
        limit: int = 15,
    ) -> MutualConnections:
        """Return users followed by both ``first`` and ``other`` (UC-8).

        ``first`` defaults to the logged-in user, so callers can compare
        themselves with someone else or compare any two users.
        """

        me = principal_username(session)
        first_user = normalize_username(first) if first else me
        other = normalize_username(other)
        if first_user == other:
            raise SelfActionError("choose two different users to compare")
        skip, limit = validate_paging(skip, limit)
        self._require_user(me)
        self._require_user(first_user)
        self._require_user(other)
        totals, _ = self.db.read(
            "COUNT_MUTUAL_FOLLOWING", COUNT_MUTUAL_FOLLOWING, me=first_user, other=other
        )
        rows, _ = self.db.read(
            "GET_MUTUAL_FOLLOWING",
            GET_MUTUAL_FOLLOWING,
            me=first_user,
            other=other,
            skip=skip,
            limit=limit,
        )
        status_rows, _ = self.db.read(
            "GET_RELATIONSHIP_STATUS", GET_RELATIONSHIP_STATUS, me=first_user, other=other
        )
        total = int(totals[0]["total"]) if totals else 0
        status = status_rows[0] if status_rows else {"i_follow": False, "they_follow": False}
        return MutualConnections(
            total=total,
            users=[
                MutualConnection(row["username"], row["name"], row.get("location"))
                for row in rows
            ],
            status=RelationshipStatus(bool(status["i_follow"]), bool(status["they_follow"])),
            first=first_user,
            second=other,
        )

    def recommendations(self, session: Session, limit: int = 10) -> list[Recommendation]:
        """Recommend users reached by an exactly two-hop follow path (UC-9).

        The query excludes the logged-in user and users they already follow,
        and returns each candidate once even when several paths reach them.
        """

        username = principal_username(session)
        limit = validate_limit(limit)
        self._require_user(username)
        rows, _ = self.db.read(
            "RECOMMEND_BY_NETWORK",
            RECOMMEND_BY_NETWORK,
            username=username,
            limit=limit,
        )
        return [self._recommendation(row) for row in rows]

    def degrees_of_separation(
        self, session: Session, source: str, target: str
    ) -> SeparationResult:
        """Find the shortest directed follow path from ``source`` to ``target`` (UC-13).

        Returns a result whose ``path`` is empty when no path of at most
        ``MAX_SEPARATION_HOPS`` hops exists.
        """

        principal_username(session)
        source = normalize_username(source)
        target = normalize_username(target)
        if source == target:
            raise ValidationError("choose two different users")
        self._require_user(source)
        self._require_user(target)
        rows, _ = self.db.read(
            "FIND_SHORTEST_FOLLOW_PATH",
            FIND_SHORTEST_FOLLOW_PATH,
            source=source,
            target=target,
        )
        path = [PathStep(step["username"], step["name"]) for step in rows[0]["users"]] if rows else []
        return SeparationResult(
            source=source, target=target, max_hops=MAX_SEPARATION_HOPS, path=path
        )

    def _require_user(self, username: str) -> None:
        rows, _ = self.db.read("USER_EXISTS", USER_EXISTS, username=username)
        if not rows or not rows[0].get("exists"):
            raise NotFoundError(f"user @{username} was not found")

    @staticmethod
    def _connection(row: dict, flag: str) -> Connection:
        return Connection(
            username=row["username"],
            name=row["name"],
            location=row.get("location"),
            since=native_datetime(row["since"]),
            follows_back=bool(row.get(flag)),
        )

    @staticmethod
    def _recommendation(row: dict) -> Recommendation:
        return Recommendation(
            user=UserSummary(
                username=row["username"],
                name=row["name"],
                location=row.get("location"),
                followers=int(row.get("followers", 0)),
            ),
            score=int(row.get("score", 0)),
            via=list((row.get("via") or [])[:3]),
        )

    follow_user = follow
    unfollow_user = unfollow
    get_following = following
    get_followers = followers
    recommend = recommendations
    find_degrees_of_separation = degrees_of_separation
