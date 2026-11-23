from __future__ import annotations

from collections.abc import Iterable

from .db import Database
from .models import SearchResult, Session, UserSummary
from .queries import GET_POPULAR_USERS, SEARCH_USERS
from .service_utils import principal_username
from .validation import normalize_search_query, normalize_username, validate_limit


class SearchService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def search_users(self, session: Session, query: str, limit: int = 10) -> list[SearchResult]:
        me = principal_username(session)
        query = normalize_search_query(query)
        limit = validate_limit(limit)
        rows, _ = self.db.read(
            "SEARCH_USERS", SEARCH_USERS, me=me, q=query, limit=limit
        )
        return [
            SearchResult(
                username=row["username"],
                name=row["name"],
                location=row.get("location"),
                followers=int(row.get("followers", 0)),
                i_follow=bool(row.get("i_follow")),
            )
            for row in rows
        ]

    def popular_users(
        self, *, exclude: Iterable[str] = (), limit: int = 10
    ) -> list[UserSummary]:
        limit = validate_limit(limit)
        excluded = sorted({normalize_username(username) for username in exclude})
        rows, _ = self.db.read(
            "GET_POPULAR_USERS", GET_POPULAR_USERS, exclude=excluded, limit=limit
        )
        return [
            UserSummary(
                username=row["username"],
                name=row["name"],
                location=row.get("location"),
                followers=int(row.get("followers", 0)),
            )
            for row in rows
        ]

    search = search_users
    popular = popular_users
