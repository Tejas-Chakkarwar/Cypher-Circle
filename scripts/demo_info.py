from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LOG_PATH = ROOT / "data/processed/preprocess_log.json"


def read_log() -> dict:
    if not LOG_PATH.is_file():
        raise RuntimeError(f"preprocessing log is missing: {LOG_PATH}. Run make data first.")
    try:
        data = json.loads(LOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not read preprocessing log: {LOG_PATH}") from exc
    if not isinstance(data.get("demo_users"), dict):
        raise RuntimeError("preprocessing log has no valid demo_users section")
    return data


def check_neo4j(usernames: list[str]) -> None:
    try:
        from cyphercircle.config import load_settings
        from cyphercircle.db import Database
        from cyphercircle.queries import USER_EXISTS
        db = Database(load_settings())
        try:
            states = []
            for username in usernames:
                rows, _ = db.read("USER_EXISTS", USER_EXISTS, username=username)
                states.append(f"@{username}={'yes' if rows and rows[0].get('exists') else 'no'}")
        finally:
            db.close()
        print("Neo4j: available; " + ", ".join(states))
    except Exception:
        print("Neo4j: unavailable; run make up and make load to verify demo users.")


def main() -> int:
    try:
        data = read_log()
    except RuntimeError as exc:
        print(f"demo-info failed: {exc}", file=sys.stderr)
        return 1
    demo = data["demo_users"]
    print(f"busy_user: {demo.get('busy_user')}")
    print(f"mutual_partner: {demo.get('mutual_partner')}")
    print(f"no_circle_user: {demo.get('no_circle_user')}")
    print(f"demo_search_term: {data.get('demo_search_term')}")
    print(f"demo_search_matches: {data.get('demo_search_matches')}")
    print("seeded_demo_password: cypher123 (demo-only)")
    print(f"users: {data.get('users')}")
    print(f"follows: {data.get('edges_final')}")
    print(f"circles: {data.get('circles')}")
    print(f"memberships: {data.get('memberships')}")
    usernames = [
        demo[key]
        for key in ("busy_user", "mutual_partner", "no_circle_user")
        if isinstance(demo.get(key), str)
    ]
    if usernames:
        check_neo4j(usernames)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
