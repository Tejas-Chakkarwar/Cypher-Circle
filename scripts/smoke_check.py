from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cyphercircle.config import load_settings
from cyphercircle.db import Database, QueryLog
from cyphercircle.errors import CypherCircleError, DatabaseUnavailableError
from cyphercircle.queries import SMOKE_RETURN_ONE


def main() -> int:
    try:
        settings = load_settings()
    except ValueError as exc:
        print(f"Neo4j configuration error: {exc}", file=sys.stderr)
        return 2

    query_log = QueryLog()
    query_log.begin_action("MILESTONE-1-SMOKE")
    db = None
    try:
        db = Database(settings, query_log)
        rows, _summary = db.read("SMOKE_RETURN_ONE", SMOKE_RETURN_ONE)
    except DatabaseUnavailableError as exc:
        print(
            f"Neo4j is unavailable: {exc}. Is Neo4j running? Try `make up`.",
            file=sys.stderr,
        )
        return 1
    except CypherCircleError as exc:
        print(f"Neo4j smoke query failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if db is not None:
            db.close()

    entries = query_log.entries()
    if rows != [{"value": 1}]:
        print(f"Neo4j smoke query returned unexpected rows: {rows}", file=sys.stderr)
        return 1
    if len(entries) != 1 or entries[0].name != "SMOKE_RETURN_ONE":
        print("Neo4j smoke query was not recorded in QueryLog", file=sys.stderr)
        return 1
    print("smoke query returned 1; QueryLog recorded SMOKE_RETURN_ONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
