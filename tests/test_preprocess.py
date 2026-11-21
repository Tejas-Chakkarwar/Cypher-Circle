from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import bcrypt

from scripts.seed_constants import SEEDED_PASSWORD, SEEDED_PASSWORD_HASH
from scripts.preprocess import clean_edges, preprocess


def _write_raw_fixture(root: Path) -> Path:
    raw = root / "raw"
    twitter = raw / "twitter"
    twitter.mkdir(parents=True)
    (raw / "twitter.tar.gz").write_bytes(b"fixture archive")
    (twitter / "1.edges").write_text(
        "2 3\n3 4\n3 4\n1 1\n", encoding="utf-8"
    )
    (twitter / "1.circles").write_text(
        "friends 1 2 3 4\ntiny 1 2\n", encoding="utf-8"
    )
    return raw


def test_clean_edges_reports_and_removes_invalid_relationships() -> None:
    cleaned, self_loops, duplicates, dangling = clean_edges(
        [(1, 1), (1, 2), (1, 2), (2, 3), (9, 1)], {1, 2, 3}
    )
    assert cleaned == [(1, 2), (2, 3)]
    assert (self_loops, duplicates, dangling) == (1, 1, 1)


def test_preprocess_adds_ego_edges_and_filters_small_circles(tmp_path: Path) -> None:
    raw = _write_raw_fixture(tmp_path)
    processed = tmp_path / "processed"
    log = preprocess(
        raw,
        processed,
        target_users=4,
        min_edges=5,
        enforce_assignment_minimums=False,
    )

    assert log["edges_from_ego_files"] == 4
    assert log["ego_edges_added"] == 3
    assert log["self_loops_dropped"] == 1
    assert log["duplicates_dropped"] == 1
    assert log["dangling_dropped"] == 0
    assert log["edges_final"] == 5
    assert log["circles_raw"] == 2
    assert log["circles_dropped_small"] == 1
    assert log["circles"] == 1

    with (processed / "follows.csv").open(newline="", encoding="utf-8") as handle:
        follows = list(csv.DictReader(handle))
    assert {(int(row["src"]), int(row["dst"])) for row in follows} == {
        (1, 2), (1, 3), (1, 4), (2, 3), (3, 4)
    }

    with (processed / "preprocess_log.json").open(encoding="utf-8") as handle:
        persisted = json.load(handle)
    assert persisted == log


def test_preprocess_fixture_is_byte_deterministic(tmp_path: Path) -> None:
    raw = _write_raw_fixture(tmp_path)
    first = tmp_path / "first"
    second = tmp_path / "second"
    preprocess(raw, first, 4, 5, enforce_assignment_minimums=False)
    preprocess(raw, second, 4, 5, enforce_assignment_minimums=False)
    names = ("users.csv", "follows.csv", "circles.csv", "memberships.csv", "preprocess_log.json")
    assert {
        name: hashlib.sha256((first / name).read_bytes()).hexdigest() for name in names
    } == {
        name: hashlib.sha256((second / name).read_bytes()).hexdigest() for name in names
    }


def test_seeded_password_hash_is_valid() -> None:
    assert bcrypt.checkpw(
        SEEDED_PASSWORD.encode("utf-8"), SEEDED_PASSWORD_HASH.encode("ascii")
    )
