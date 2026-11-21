from __future__ import annotations

from .db import Database
from .errors import StartupDataError
from .models import GraphStats
from .queries import COUNT_ALL_NODES, VERIFY_COUNTS


class StartupService:
    """Validate the loaded graph and provide live banner statistics."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def verify_ready(self) -> GraphStats:
        rows, _ = self.db.read("COUNT_ALL_NODES", COUNT_ALL_NODES)
        nodes = int(rows[0]["nodes"]) if rows else 0
        if nodes == 0:
            raise StartupDataError("Database is empty. Run `make data load` first.")
        return self._read_stats(partial_check=True)

    def current_stats(self) -> GraphStats:
        return self._read_stats(partial_check=False)

    def _read_stats(self, *, partial_check: bool) -> GraphStats:
        rows, _ = self.db.read("VERIFY_COUNTS", VERIFY_COUNTS)
        if not rows:
            raise StartupDataError("Database looks partially loaded. Run `make reset` to reload cleanly.")
        row = rows[0]
        stats = GraphStats(
            users=int(row.get("users", 0)),
            follows=int(row.get("follows", 0)),
            circles=int(row.get("circles", 0)),
        )
        if partial_check and (stats.users == 0 or stats.follows == 0):
            raise StartupDataError(
                "Database looks partially loaded. Run `make reset` to reload cleanly."
            )
        return stats
