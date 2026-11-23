from __future__ import annotations

from collections.abc import Iterable

import bcrypt

from scripts.seed_constants import SEEDED_PASSWORD_HASH

from .db import Database
from .errors import (
    AuthenticationError,
    ConflictError,
    DataIntegrityError,
    NotFoundError,
    ValidationError,
)
from .models import Session
from .queries import (
    CHECK_REGISTRATION_CONFLICTS,
    CIRCLE_EXISTS,
    CREATE_USER_WITH_CIRCLES,
    GET_AUTH_BY_USERNAME,
)
from .validation import (
    normalize_circle_id,
    normalize_email,
    normalize_username,
    validate_bio,
    validate_name,
    validate_password,
)


class AuthService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def register(
        self,
        name: str,
        email: str,
        username: str,
        password: str,
        bio: str | None = None,
        circle_ids: Iterable[str] = (),
    ) -> Session:
        name = validate_name(name)
        email = normalize_email(email)
        username = normalize_username(username)
        password = validate_password(password)
        bio = validate_bio(bio)
        requested_circles = [normalize_circle_id(circle_id) for circle_id in circle_ids]
        if len(requested_circles) > 3:
            raise ValidationError("a new user may join at most 3 circles")
        if len(set(requested_circles)) != len(requested_circles):
            raise ValidationError("circle_ids must be unique")
        for circle_id in requested_circles:
            rows, _ = self.db.read("CIRCLE_EXISTS", CIRCLE_EXISTS, circle_id=circle_id)
            if not rows or not rows[0].get("exists"):
                raise NotFoundError(f"circle {circle_id} was not found")

        conflicts, _ = self.db.read(
            "CHECK_REGISTRATION_CONFLICTS",
            CHECK_REGISTRATION_CONFLICTS,
            username=username,
            email=email,
        )
        if not conflicts:
            raise ConflictError("could not verify registration availability")
        conflict = conflicts[0]
        problems = []
        if conflict.get("username_taken"):
            problems.append("username is already taken")
        if conflict.get("email_taken"):
            problems.append("email is already taken")
        if problems:
            raise ConflictError("; ".join(problems))

        password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
        try:
            rows, _ = self.db.write(
                "CREATE_USER_WITH_CIRCLES",
                CREATE_USER_WITH_CIRCLES,
                username=username,
                email=email,
                name=name,
                password_hash=password_hash,
                bio=bio,
                location=None,
                circle_ids=requested_circles,
            )
        except DataIntegrityError as exc:
            raise self._map_integrity_error(exc) from exc
        if not rows:
            raise ConflictError("registration did not create a user")
        return Session(username=rows[0].get("username", username), name=rows[0].get("name", name))

    def login(self, username: str, password: str) -> Session:
        try:
            username = normalize_username(username)
        except Exception as exc:
            raise AuthenticationError("Invalid username or password") from exc
        if not isinstance(password, str):
            password = ""
        rows, _ = self.db.read("GET_AUTH_BY_USERNAME", GET_AUTH_BY_USERNAME, username=username)
        stored_hash = rows[0].get("password_hash") if rows else None
        candidate = stored_hash or SEEDED_PASSWORD_HASH
        try:
            valid = (
                len(password.encode("utf-8")) <= 72
                and bcrypt.checkpw(password.encode("utf-8"), candidate.encode("ascii"))
            )
        except (AttributeError, UnicodeEncodeError, ValueError, TypeError):
            valid = False
        if not stored_hash or not valid:
            raise AuthenticationError("Invalid username or password")
        return Session(username=username, name=rows[0].get("name", username))

    @staticmethod
    def _map_integrity_error(error: DataIntegrityError) -> ConflictError:
        message = str(error).lower()
        if error.constraint_name == "user_username_unique" or "username" in message:
            if error.constraint_name == "user_email_unique" or "email" in message:
                return ConflictError("username and email are already taken")
            return ConflictError("username is already taken")
        if error.constraint_name == "user_email_unique" or "email" in message:
            return ConflictError("email is already taken")
        return ConflictError("username or email is already taken")

    register_user = register
    login_user = login
