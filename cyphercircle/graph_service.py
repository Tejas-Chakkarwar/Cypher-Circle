from __future__ import annotations

from .db import Database
from .errors import AlreadyFollowingError, NotFollowingError, NotFoundError, SelfActionError
from .models import (
    Connection,
    ConnectionCounts,
    MutualConnection,
    MutualConnections,
    Recommendation,
    RelationshipStatus,
    Session,
    UserSummary,
)
from .queries import (
    COUNT_CONNECTIONS,
    COUNT_MUTUAL_FOLLOWING,
    FOLLOW_USER,
    GET_FOLLOWERS,
    GET_FOLLOWING,
    GET_FOLLOWING_USERNAMES,
    GET_MY_CIRCLES,
    GET_MUTUAL_FOLLOWING,
    GET_POPULAR_USERS,
    GET_RELATIONSHIP_STATUS,
    RECOMMEND_BY_CIRCLE,
    RECOMMEND_BY_NETWORK,
    UNFOLLOW_USER,
    USER_EXISTS,
)
from .service_utils import counter, native_datetime, principal_username
from .validation import normalize_username, validate_limit, validate_paging


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
        skip: int = 0,
        limit: int = 15,
    ) -> MutualConnections:
        me = principal_username(session)
        other = normalize_username(other)
        if me == other:
            raise SelfActionError("you cannot compare yourself with yourself")
        skip, limit = validate_paging(skip, limit)
        self._require_user(me)
        self._require_user(other)
        totals, _ = self.db.read(
            "COUNT_MUTUAL_FOLLOWING", COUNT_MUTUAL_FOLLOWING, me=me, other=other
        )
        rows, _ = self.db.read(
            "GET_MUTUAL_FOLLOWING",
            GET_MUTUAL_FOLLOWING,
            me=me,
            other=other,
            skip=skip,
            limit=limit,
        )
        status_rows, _ = self.db.read(
            "GET_RELATIONSHIP_STATUS", GET_RELATIONSHIP_STATUS, me=me, other=other
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
        )

    def recommendations(self, session: Session, limit: int = 10) -> list[Recommendation]:
        username = principal_username(session)
        limit = validate_limit(limit)
        self._require_user(username)
        following_rows, _ = self.db.read(
            "GET_FOLLOWING_USERNAMES", GET_FOLLOWING_USERNAMES, username=username
        )
        excluded = {username}
        if following_rows:
            excluded.update(following_rows[0].get("usernames") or [])
        results: list[Recommendation] = []

        network_rows, _ = self.db.read(
            "RECOMMEND_BY_NETWORK",
            RECOMMEND_BY_NETWORK,
            username=username,
            limit=limit,
        )
        for row in network_rows:
            if row["username"] not in excluded and len(results) < limit:
                results.append(self._recommendation(row, "network", int(row.get("score", 0)), row.get("via") or [], None))
                excluded.add(row["username"])
        remaining = limit - len(results)
        if not remaining:
            return results

        circles, _ = self.db.read("GET_MY_CIRCLES", GET_MY_CIRCLES, username=username)
        if circles:
            circle_rows, _ = self.db.read(
                "RECOMMEND_BY_CIRCLE",
                RECOMMEND_BY_CIRCLE,
                username=username,
                exclude=sorted(excluded),
                limit=remaining,
            )
            for row in circle_rows:
                if row["username"] not in excluded and len(results) < limit:
                    results.append(self._recommendation(row, "circle", 0, [], row.get("circle")))
                    excluded.add(row["username"])
        remaining = limit - len(results)
        if remaining:
            popular_rows, _ = self.db.read(
                "GET_POPULAR_USERS",
                GET_POPULAR_USERS,
                exclude=sorted(excluded),
                limit=remaining,
            )
            for row in popular_rows:
                if row["username"] not in excluded and len(results) < limit:
                    results.append(self._recommendation(row, "popular", 0, [], None))
                    excluded.add(row["username"])
        return results

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
    def _recommendation(
        row: dict,
        reason: str,
        score: int,
        via: list[str],
        circle: str | None,
    ) -> Recommendation:
        return Recommendation(
            user=UserSummary(
                username=row["username"],
                name=row["name"],
                location=row.get("location"),
                followers=int(row.get("followers", 0)),
            ),
            score=score,
            via=list(via[:3]),
            reason=reason,
            circle=circle,
        )

    follow_user = follow
    unfollow_user = unfollow
    get_following = following
    get_followers = followers
    recommend = recommendations
