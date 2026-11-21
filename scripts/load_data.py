from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cyphercircle.config import load_settings
from cyphercircle.db import Database
from cyphercircle.queries import (
    COUNT_ALL_NODES,
    AWAIT_INDEXES,
    CLEAR_LOAD_TOKEN,
    CREATE_CIRCLE_ID_CONSTRAINT,
    CREATE_USER_EMAIL_CONSTRAINT,
    CREATE_USER_NAME_TEXT_INDEX,
    CREATE_USER_SOURCE_ID_CONSTRAINT,
    CREATE_USER_USERNAME_CONSTRAINT,
    CREATE_USER_USERNAME_TEXT_INDEX,
    LOAD_CIRCLES,
    LOAD_FOLLOWS,
    LOAD_MEMBERSHIPS,
    LOAD_USERS,
    FINALIZE_LOAD,
    RESET_DATABASE,
    VERIFY_DATASET_COUNTS,
)
from scripts.seed_constants import TWITTER_ARCHIVE_SHA256


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = (
    ("CREATE_USER_USERNAME_CONSTRAINT", CREATE_USER_USERNAME_CONSTRAINT),
    ("CREATE_USER_EMAIL_CONSTRAINT", CREATE_USER_EMAIL_CONSTRAINT),
    ("CREATE_USER_SOURCE_ID_CONSTRAINT", CREATE_USER_SOURCE_ID_CONSTRAINT),
    ("CREATE_CIRCLE_ID_CONSTRAINT", CREATE_CIRCLE_ID_CONSTRAINT),
    ("CREATE_USER_NAME_TEXT_INDEX", CREATE_USER_NAME_TEXT_INDEX),
    ("CREATE_USER_USERNAME_TEXT_INDEX", CREATE_USER_USERNAME_TEXT_INDEX),
)


EXPECTED_HEADERS = {
    "users.csv": [
        "source_id", "uid", "username", "email", "name", "name_lc",
        "password_hash", "bio", "location", "joined_at", "source",
    ],
    "circles.csv": ["circle_id", "name", "source_label", "owner_source_id"],
    "memberships.csv": ["circle_id", "source_id"],
    "follows.csv": ["src", "dst", "since"],
}


def read_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        expected = EXPECTED_HEADERS.get(path.name)
        if reader.fieldnames != expected:
            raise ValueError(
                f"{path.name} has headers {reader.fieldnames}; expected {expected}"
            )
        rows = list(reader)
        for row in rows:
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"{path.name} contains a row with missing or extra fields")
    for row in rows:
        for key in ("source_id", "uid", "src", "dst", "owner_source_id"):
            if key in row and row[key] != "":
                row[key] = int(row[key])
    return rows


def validate_processed_manifest(processed_dir: Path, log: dict) -> None:
    if log.get("archive_sha256") != TWITTER_ARCHIVE_SHA256:
        raise ValueError("preprocess log is not bound to the pinned SNAP archive")
    expected = log.get("processed_files")
    if set(expected or {}) != set(EXPECTED_HEADERS):
        raise ValueError("preprocess log has an incomplete processed-file manifest")
    for filename, expected_digest in expected.items():
        actual_digest = hashlib.sha256((processed_dir / filename).read_bytes()).hexdigest()
        if actual_digest != expected_digest:
            raise ValueError(f"processed file hash mismatch: {filename}")


def validate_rows(
    processed_dir: Path,
    log: dict,
    datasets: tuple,
    *,
    enforce_assignment_minimums: bool = True,
) -> dict[str, list[dict]]:
    if enforce_assignment_minimums and (log["users"] < 1000 or log["edges_final"] < 5000):
        raise ValueError("preprocess log is below the mandatory dataset minimums")
    loaded = {filename: read_rows(processed_dir / filename) for filename, _, _ in datasets}
    expected_lengths = {
        "users.csv": log["users"],
        "circles.csv": log["circles"],
        "memberships.csv": log["memberships"],
        "follows.csv": log["edges_final"],
    }
    for filename, expected in expected_lengths.items():
        if len(loaded[filename]) != expected:
            raise ValueError(
                f"{filename} has {len(loaded[filename])} rows; expected {expected}"
            )
    users = {row["source_id"] for row in loaded["users.csv"]}
    circles = {row["circle_id"] for row in loaded["circles.csv"]}
    if len(users) != len(loaded["users.csv"]):
        raise ValueError("users.csv contains duplicate source_id values")
    if len(circles) != len(loaded["circles.csv"]):
        raise ValueError("circles.csv contains duplicate circle_id values")
    required_user_fields = (
        "source_id", "uid", "username", "email", "name", "name_lc",
        "password_hash", "joined_at", "source",
    )
    blank_user_fields = [
        (row.get("source_id"), field)
        for row in loaded["users.csv"]
        for field in required_user_fields
        if row.get(field) is None or str(row.get(field)).strip() == ""
    ]
    if blank_user_fields:
        raise ValueError(
            f"users.csv contains blank required fields: {blank_user_fields[:3]}"
        )
    required_circle_fields = (
        "circle_id", "name", "source_label", "owner_source_id",
    )
    blank_circle_fields = [
        (row.get("circle_id"), field)
        for row in loaded["circles.csv"]
        for field in required_circle_fields
        if row.get(field) is None or str(row.get(field)).strip() == ""
    ]
    if blank_circle_fields:
        raise ValueError(
            f"circles.csv contains blank required fields: {blank_circle_fields[:3]}"
        )
    required_membership_fields = ("circle_id", "source_id")
    required_follow_fields = ("src", "dst", "since")
    blank_membership_fields = [
        (row.get("circle_id"), field)
        for row in loaded["memberships.csv"]
        for field in required_membership_fields
        if row.get(field) is None or str(row.get(field)).strip() == ""
    ]
    blank_follow_fields = [
        (row.get("src"), field)
        for row in loaded["follows.csv"]
        for field in required_follow_fields
        if row.get(field) is None or str(row.get(field)).strip() == ""
    ]
    if blank_membership_fields or blank_follow_fields:
        raise ValueError(
            "relationship CSVs contain blank required fields: "
            f"memberships={blank_membership_fields[:3]}, follows={blank_follow_fields[:3]}"
        )
    invalid_owners = [
        row for row in loaded["circles.csv"]
        if row["owner_source_id"] not in users
    ]
    if invalid_owners:
        raise ValueError(
            f"circles.csv contains {len(invalid_owners)} invalid owner_source_id values"
        )
    mismatched_owners = []
    for row in loaded["circles.csv"]:
        if not re.fullmatch(r"[0-9]+:[0-9]+", row["circle_id"]):
            raise ValueError(f"invalid circle_id format: {row['circle_id']}") from None
        encoded_owner = int(row["circle_id"].split(":", 1)[0])
        if encoded_owner != row["owner_source_id"]:
            mismatched_owners.append(row["circle_id"])
    if mismatched_owners:
        raise ValueError(
            "circles.csv owner_source_id does not match circle_id for: "
            + ", ".join(mismatched_owners[:3])
        )
    missing_memberships = [
        row for row in loaded["memberships.csv"]
        if row["source_id"] not in users or row["circle_id"] not in circles
    ]
    missing_follows = [
        row for row in loaded["follows.csv"]
        if row["src"] not in users or row["dst"] not in users
    ]
    if missing_memberships or missing_follows:
        raise ValueError(
            "processed data contains dangling relationship rows: "
            f"{len(missing_memberships)} memberships, {len(missing_follows)} follows"
        )
    return loaded


def load(processed_dir: Path, batch_size: int = 1000, *, reset: bool = False) -> dict:
    log = json.loads((processed_dir / "preprocess_log.json").read_text(encoding="utf-8"))
    db = Database(load_settings())
    load_token = uuid4().hex
    datasets = (
        ("circles.csv", LOAD_CIRCLES, "LOAD_CIRCLES"),
        ("users.csv", LOAD_USERS, "LOAD_USERS"),
        ("memberships.csv", LOAD_MEMBERSHIPS, "LOAD_MEMBERSHIPS"),
        ("follows.csv", LOAD_FOLLOWS, "LOAD_FOLLOWS"),
    )
    load_started = False
    try:
        rows, _ = db.read("COUNT_ALL_NODES", COUNT_ALL_NODES)
        if rows[0]["nodes"] and not reset:
            raise SystemExit("Database isn't empty. Use make reset for a clean reload.")
        validate_processed_manifest(processed_dir, log)
        loaded = validate_rows(processed_dir, log, datasets)
        if reset:
            db.write("RESET_DATABASE", RESET_DATABASE, log=False)
        load_started = True
        for name, query in SCHEMA:
            db.write(name, query, log=False)
        db.write("AWAIT_INDEXES", AWAIT_INDEXES, log=False)
        for filename, query, name in datasets:
            data = loaded[filename]
            for start in range(0, len(data), batch_size):
                batch = data[start : start + batch_size]
                result, _ = db.write(
                    name,
                    query,
                    rows=batch,
                    load_token=load_token,
                    log=False,
                )
                if not result or result[0]["loaded"] != len(batch):
                    raise RuntimeError(
                        f"{name} loaded {result[0]['loaded'] if result else 0} "
                        f"of {len(batch)} rows"
                    )
        verified, _ = db.read("VERIFY_DATASET_COUNTS", VERIFY_DATASET_COUNTS)
        actual = verified[0]
        expected = {
            "users": log["users"],
            "circles": log["circles"],
            "follows": log["edges_final"],
            "memberships": log["memberships"],
        }
        if actual != expected:
            raise RuntimeError(f"count mismatch: expected {expected}, got {actual}")
        db.write("FINALIZE_LOAD", FINALIZE_LOAD, load_token=load_token, log=False)
        print(json.dumps(actual, indent=2))
        return actual
    except BaseException:
        if load_started:
            try:
                db.write(
                    "CLEAR_LOAD_TOKEN",
                    CLEAR_LOAD_TOKEN,
                    load_token=load_token,
                    log=False,
                )
            except Exception as cleanup_error:
                print(f"cleanup after failed load also failed: {cleanup_error}", file=sys.stderr)
        raise
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--processed-dir", type=Path, default=ROOT / "data/processed")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="wipe the configured database before loading the processed dataset",
    )
    args = parser.parse_args()
    load(args.processed_dir, reset=args.reset)


if __name__ == "__main__":
    main()

