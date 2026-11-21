from __future__ import annotations

import csv
from pathlib import Path

from scripts.preprocess import USERNAME_RE, build_username, preprocess


def test_username_normalization_handles_accents_and_punctuation() -> None:
    assert build_username("Élodie", "O'Neill-Smith", 12) == "elodie.o_0012"
    assert build_username("!!!", "---", 0) == "user.x_0000"
    assert USERNAME_RE.fullmatch(build_username("A", "B", 1))


def test_preprocessed_usernames_are_unique_and_match_contract(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    twitter = raw / "twitter"
    twitter.mkdir(parents=True)
    (raw / "twitter.tar.gz").write_bytes(b"fixture archive")
    (twitter / "1.edges").write_text("2 3\n3 4\n", encoding="utf-8")
    (twitter / "1.circles").write_text("friends 1 2 3 4\n", encoding="utf-8")
    processed = tmp_path / "processed"
    preprocess(raw, processed, 4, 5, enforce_assignment_minimums=False)

    with (processed / "users.csv").open(newline="", encoding="utf-8") as handle:
        usernames = [row["username"] for row in csv.DictReader(handle)]
    assert len(usernames) == len(set(usernames))
    assert all(USERNAME_RE.fullmatch(username) for username in usernames)
