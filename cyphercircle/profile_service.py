from __future__ import annotations

from .db import Database
from .errors import MembershipError, NotFoundError
from .models import CircleSummary, Profile, Session
from .queries import (
    CIRCLE_EXISTS,
    CLEAR_BIO,
    CLEAR_LOCATION,
    GET_MY_CIRCLES,
    GET_PROFILE,
    GET_TOP_CIRCLES,
    JOIN_CIRCLE,
    LEAVE_CIRCLE,
    UPDATE_PROFILE,
    USER_EXISTS,
)
from .service_utils import counter, native_datetime, principal_username
from .validation import (
    normalize_circle_id,
    normalize_username,
    validate_bio,
    validate_location,
    validate_name,
    validate_limit,
)


class ProfileService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get_profile(self, session: Session) -> Profile:
        username = principal_username(session)
        return self._get_profile(username)

    def get_public_profile(self, username: str) -> Profile:
        """Return public profile data for a validated target username."""

        return self._get_profile(normalize_username(username))

    def _get_profile(self, username: str) -> Profile:
        rows, _ = self.db.read("GET_PROFILE", GET_PROFILE, username=username)
        if not rows:
            raise NotFoundError(f"user @{username} was not found")
        row = rows[0]
        return Profile(
            username=row["username"],
            name=row["name"],
            email=row["email"],
            bio=row.get("bio"),
            location=row.get("location"),
            circles=[circle for circle in (row.get("circles") or []) if circle is not None],
            circle_count=int(row.get("circle_count", 0)),
            joined_at=native_datetime(row["joined_at"]),
            following_count=int(row.get("following_count", 0)),
            followers_count=int(row.get("followers_count", 0)),
        )

    def update_profile(
        self,
        session: Session,
        *,
        name: str | None = None,
        bio: str | None = None,
        location: str | None = None,
    ) -> Profile:
        username = principal_username(session)
        if name is not None:
            name = validate_name(name)
        clear_bio = bio == "-"
        clear_location = location == "-"
        if not clear_bio:
            bio = validate_bio(bio)
        if not clear_location:
            location = validate_location(location)
        self._require_user(username)
        if name is not None or (bio is not None and not clear_bio) or (location is not None and not clear_location):
            self.db.write(
                "UPDATE_PROFILE",
                UPDATE_PROFILE,
                username=username,
                name=name,
                bio=None if clear_bio else bio,
                location=None if clear_location else location,
            )
        if clear_bio:
            self.db.write("CLEAR_BIO", CLEAR_BIO, username=username)
        if clear_location:
            self.db.write("CLEAR_LOCATION", CLEAR_LOCATION, username=username)
        return self.get_profile(session)

    def get_top_circles(self, limit: int = 10) -> list[CircleSummary]:
        limit = validate_limit(limit)
        rows, _ = self.db.read("GET_TOP_CIRCLES", GET_TOP_CIRCLES, limit=limit)
        return [
            CircleSummary(row["circle_id"], row["name"], int(row.get("members", 0)))
            for row in rows
        ]

    def get_my_circles(self, session: Session) -> list[CircleSummary]:
        username = principal_username(session)
        self._require_user(username)
        rows, _ = self.db.read("GET_MY_CIRCLES", GET_MY_CIRCLES, username=username)
        return [CircleSummary(row["circle_id"], row["name"]) for row in rows]

    def join_circle(self, session: Session, circle_id: str) -> bool:
        username = principal_username(session)
        circle_id = normalize_circle_id(circle_id)
        self._require_user(username)
        self._require_circle(circle_id)
        _, summary = self.db.write(
            "JOIN_CIRCLE", JOIN_CIRCLE, username=username, circle_id=circle_id
        )
        return counter(summary, "relationships_created") == 1

    def leave_circle(self, session: Session, circle_id: str) -> bool:
        username = principal_username(session)
        circle_id = normalize_circle_id(circle_id)
        self._require_user(username)
        self._require_circle(circle_id)
        _, summary = self.db.write(
            "LEAVE_CIRCLE", LEAVE_CIRCLE, username=username, circle_id=circle_id
        )
        if counter(summary, "relationships_deleted") == 0:
            raise MembershipError(f"@{username} is not a member of circle {circle_id}")
        return True

    def _require_user(self, username: str) -> None:
        rows, _ = self.db.read("USER_EXISTS", USER_EXISTS, username=username)
        if not rows or not rows[0].get("exists"):
            raise NotFoundError(f"user @{username} was not found")

    def _require_circle(self, circle_id: str) -> None:
        rows, _ = self.db.read("CIRCLE_EXISTS", CIRCLE_EXISTS, circle_id=circle_id)
        if not rows or not rows[0].get("exists"):
            raise NotFoundError(f"circle {circle_id} was not found")

    edit_profile = update_profile
