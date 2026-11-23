from __future__ import annotations

from collections.abc import Iterable

from .db import Database
from .models import DirectoryEntry, SearchResult, Session, UserDirectory, UserSummary
from .queries import COUNT_USERS, GET_ALL_USERS, GET_POPULAR_USERS, SEARCH_USERS
from .service_utils import principal_username
from .validation import normalize_search_query, normalize_username, validate_limit, validate_paging


class SearchService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def all_users(self, session: Session, *, skip: int = 0, limit: int = 20) -> UserDirectory:
        """List every user, including the logged-in user and people they follow (UC-10)."""

        me = principal_username(session)
        skip, limit = validate_paging(skip, limit)
        totals, _ = self.db.read("COUNT_USERS", COUNT_USERS)
        rows, _ = self.db.read("GET_ALL_USERS", GET_ALL_USERS, me=me, skip=skip, limit=limit)
        return UserDirectory(
            total=int(totals[0]["total"]) if totals else 0,
            users=[
                DirectoryEntry(
                    username=row["username"],
                    name=row["name"],
                    location=row.get("location"),
                    followers=int(row.get("followers", 0)),
                    is_me=bool(row.get("is_me")),
                    i_follow=bool(row.get("i_follow")),
                )
                for row in rows
            ],
        )

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

    list_all_users = all_users
    search = search_users
    popular = popular_users
