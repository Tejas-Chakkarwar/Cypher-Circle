from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import bcrypt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cyphercircle.config import load_settings
from cyphercircle.db import Database, QueryLog
from cyphercircle.errors import CypherCircleError
from cyphercircle.queries import (
    COUNT_ALL_NODES,
    CREATE_SLICE_USER,
    DELETE_SLICE_USER,
    FOLLOW_USER,
    GET_AUTH_BY_USERNAME,
    RECOMMEND_BY_NETWORK,
    VERIFY_DATASET_COUNTS,
)
try:
    from scripts.seed_constants import SEEDED_PASSWORD
except ModuleNotFoundError:  # Direct execution: python scripts/slice_check.py
    from seed_constants import SEEDED_PASSWORD


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    log_path = ROOT / "data/processed/preprocess_log.json"
    if not log_path.exists():
        print("Run `make data` before the slice check.", file=sys.stderr)
        return 1
    preprocess_log = json.loads(log_path.read_text(encoding="utf-8"))
    busy_user = preprocess_log["demo_users"]["busy_user"]
    username = f"slice_{uuid4().hex[:12]}"
    email = f"{username}@example.com"
    query_log = QueryLog()
    query_log.begin_action("MILESTONE-0-SLICE")
    db = None
    try:
        db = Database(load_settings(), query_log)
        all_nodes, _ = db.read("COUNT_ALL_NODES", COUNT_ALL_NODES)
        if all_nodes[0]["nodes"] == 0:
            raise RuntimeError("Neo4j is empty; run `make load` first.")
        counts, _ = db.read("VERIFY_DATASET_COUNTS", VERIFY_DATASET_COUNTS)
        expected = {
            "users": preprocess_log["users"],
            "circles": preprocess_log["circles"],
            "follows": preprocess_log["edges_final"],
            "memberships": preprocess_log["memberships"],
        }
        if counts[0] != expected:
            raise RuntimeError(f"loaded counts do not match preprocessing log: {counts[0]}")
        busy_recommendations, _ = db.read(
            "RECOMMEND_BY_NETWORK",
            RECOMMEND_BY_NETWORK,
            username=busy_user,
            limit=1,
        )
        if not busy_recommendations:
            raise RuntimeError("demo busy_user received no network recommendation")

        db.write(
            "CREATE_SLICE_USER",
            CREATE_SLICE_USER,
            username=username,
            email=email,
            name="Slice Check User",
            password_hash=bcrypt.hashpw(
                SEEDED_PASSWORD.encode("utf-8"), bcrypt.gensalt()
            ).decode("ascii"),
            bio="",
            location="",
            joined_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )
        auth, _ = db.read("GET_AUTH_BY_USERNAME", GET_AUTH_BY_USERNAME, username=username)
        if not auth or not bcrypt.checkpw(
            SEEDED_PASSWORD.encode("utf-8"), auth[0]["password_hash"].encode("ascii")
        ):
            raise RuntimeError("slice login failed")
        db.write("FOLLOW_USER", FOLLOW_USER, me=username, target=busy_user)
        recommendations, _ = db.read(
            "RECOMMEND_BY_NETWORK",
            RECOMMEND_BY_NETWORK,
            username=username,
            limit=10,
        )
        if not recommendations:
            raise RuntimeError("slice user received no network recommendation")
        print(f"slice user: @{username}")
        print(f"logged in as: {auth[0]['name']}")
        print(f"followed: @{busy_user}")
        print(f"network recommendations: {len(recommendations)}")
        print(json.dumps(recommendations, indent=2, default=str))
        print(f"query log entries: {len(query_log.entries())}")
        for entry in query_log.entries():
            print(f"- {entry.name} ({entry.rows} rows)")
        return 0
    except (CypherCircleError, RuntimeError, KeyError) as exc:
        print(f"slice check failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if db is not None:
            try:
                db.write("DELETE_SLICE_USER", DELETE_SLICE_USER, username=username, log=False)
            finally:
                db.close()


if __name__ == "__main__":
    raise SystemExit(main())

