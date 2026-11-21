from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from neo4j import GraphDatabase
from neo4j.exceptions import AuthError, ConstraintError, Neo4jError, ServiceUnavailable

from .config import Settings
from .errors import DataIntegrityError, DatabaseUnavailableError, QueryError


@dataclass(frozen=True)
class LoggedQuery:
    name: str
    query: str
    params: dict[str, Any]
    rows: int


class QueryLog:
    def __init__(self) -> None:
        self._entries: list[LoggedQuery] = []
        self.action: str | None = None

    def begin_action(self, label: str) -> None:
        self.action = label
        self._entries.clear()

    def record(self, entry: LoggedQuery) -> None:
        self._entries.append(entry)

    def entries(self) -> list[LoggedQuery]:
        return list(self._entries)


def _redact(value: Any, key: str | None = None) -> Any:
    if key and "password" in key.lower():
        return "***"
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        if len(value) > 20:
            return f"[... {len(value)} items]"
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return [_redact(item) for item in value]
    return value


class Database:
    def __init__(self, settings: Settings, query_log: QueryLog | None = None) -> None:
        self.query_log = query_log or QueryLog()
        driver = None
        verified = False
        try:
            driver = GraphDatabase.driver(
                settings.uri,
                auth=(settings.user, settings.password),
            )
            driver.verify_connectivity()
            verified = True
        except AuthError as exc:
            raise DatabaseUnavailableError("Neo4j authentication failed") from exc
        except (ServiceUnavailable, OSError) as exc:
            raise DatabaseUnavailableError("Neo4j is unavailable") from exc
        finally:
            if driver is not None and not verified:
                try:
                    driver.close()
                except Exception:
                    # Preserve the actionable connection error if cleanup fails.
                    pass
        self.driver = driver

    def close(self) -> None:
        self.driver.close()

    def _run(self, query_name: str, query: str, *, log: bool, **params: Any):
        try:
            with self.driver.session() as session:
                result = session.run(query, **params)
                records = [record.data() for record in result]
                summary = result.consume()
        except ConstraintError as exc:
            message = str(exc)
            constraint_name = next(
                (
                    name
                    for name in (
                        "user_username_unique",
                        "user_email_unique",
                        "user_source_id_unique",
                        "circle_id_unique",
                    )
                    if name in message
                ),
                None,
            )
            raise DataIntegrityError("database constraint violation", constraint_name) from exc
        except (Neo4jError, OSError) as exc:
            raise QueryError("database query failed") from exc
        if log:
            self.query_log.record(
                LoggedQuery(query_name, query, _redact(params), len(records))
            )
        return records, summary

    def read(self, query_name: str, query: str, **params: Any):
        return self._run(query_name, query, log=True, **params)

    def write(self, query_name: str, query: str, *, log: bool = True, **params: Any):
        return self._run(query_name, query, log=log, **params)

