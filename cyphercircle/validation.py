from __future__ import annotations

import re

from .errors import ValidationError


USERNAME_RE = re.compile(r"^[a-z0-9._]{3,30}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CIRCLE_ID_RE = re.compile(r"^[0-9]+:[0-9]+$")


def normalize_username(value: str) -> str:
    if not isinstance(value, str):
        raise ValidationError("username is required")
    username = value.strip().lower()
    if not USERNAME_RE.fullmatch(username):
        raise ValidationError("username must match ^[a-z0-9._]{3,30}$")
    return username


def normalize_email(value: str) -> str:
    if not isinstance(value, str):
        raise ValidationError("email is required")
    email = value.strip().lower()
    if not EMAIL_RE.fullmatch(email):
        raise ValidationError("email must be a valid address")
    return email


def validate_password(value: str) -> str:
    if not isinstance(value, str) or len(value) < 8:
        raise ValidationError("password must be at least 8 characters")
    try:
        password_bytes = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValidationError("password contains invalid characters") from exc
    if len(password_bytes) > 72:
        raise ValidationError("password must be at most 72 UTF-8 bytes")
    return value


def validate_name(value: str) -> str:
    if not isinstance(value, str):
        raise ValidationError("name is required")
    name = value.strip()
    if not name:
        raise ValidationError("name cannot be empty")
    if any(character in name for character in "\r\n\u2028\u2029"):
        raise ValidationError("name must be a single line")
    return name


def validate_bio(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError("bio must be text")
    if len(value) > 160:
        raise ValidationError("bio must be at most 160 characters")
    if any(character in value for character in "\r\n\u2028\u2029"):
        raise ValidationError("bio must be a single line")
    return value


def validate_location(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError("location must be text")
    if any(character in value for character in "\r\n\u2028\u2029"):
        raise ValidationError("location must be a single line")
    return value


def normalize_circle_id(value: str) -> str:
    if not isinstance(value, str):
        raise ValidationError("circle_id is required")
    circle_id = value.strip()
    if not CIRCLE_ID_RE.fullmatch(circle_id):
        raise ValidationError("circle_id must have the form '<ego>:<line>'")
    return circle_id


def normalize_search_query(value: str) -> str:
    if not isinstance(value, str):
        raise ValidationError("search query is required")
    query = value.strip().lower()
    if len(query) < 2:
        raise ValidationError("search query must be at least 2 characters")
    return query


def validate_limit(value: int, *, maximum: int = 100) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValidationError(f"limit must be between 1 and {maximum}")
    return value


def validate_paging(skip: int, limit: int) -> tuple[int, int]:
    if isinstance(skip, bool) or not isinstance(skip, int) or skip < 0:
        raise ValidationError("skip must be a non-negative integer")
    return skip, validate_limit(limit)
