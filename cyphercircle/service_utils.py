from __future__ import annotations

from datetime import datetime
from typing import Any

from .errors import AuthenticationError, NotFoundError
from .models import Session
from .queries import USER_EXISTS
from .validation import normalize_username


def counter(summary: Any, name: str) -> int:
    counters = getattr(summary, "counters", None)
    if counters is None and isinstance(summary, dict):
        counters = summary.get("counters")
    if counters is None:
        return 0
    value = getattr(counters, name, None)
    if value is None and isinstance(counters, dict):
        value = counters.get(name)
    return int(value or 0)


def native_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if hasattr(value, "to_native"):
        return value.to_native()
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise TypeError(f"unsupported Neo4j datetime value: {value!r}")


def principal_username(session: Session) -> str:
    """Return the actor from an authenticated service-layer session."""

    if not isinstance(session, Session):
        raise AuthenticationError("an authenticated session is required")
    return normalize_username(session.username)


def require_user(db: Any, username: str) -> None:
    rows, _ = db.read("USER_EXISTS", USER_EXISTS, username=username)
    if not rows or not rows[0].get("exists"):
        raise NotFoundError(f"user @{username} was not found")
