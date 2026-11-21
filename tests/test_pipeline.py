from __future__ import annotations

import csv
import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

import scripts.load_data as load_data
from scripts.download_dataset import archive_manifest, extract_if_needed
from scripts.preprocess import (
    choose_demo_users,
    clean_edges,
    parse_edges,
    preprocess,
    recommendation_count,
)


def _write_fixture_raw(root: Path) -> Path:
    raw = root / "raw"
    twitter = raw / "twitter"
    twitter.mkdir(parents=True)
    (raw / "twitter.tar.gz").write_bytes(b"fixture archive")
    (twitter / "1.edges").write_text("2 3\n3 4\n", encoding="utf-8")
    (twitter / "1.circles").write_text("friends 1 2 3 4\n", encoding="utf-8")
    return raw


def _write_archive(archive: Path, *, unexpected: bool = False) -> None:
    members = {
        "twitter/1.edges": "2 3\n",
        "twitter/1.circles": "friends 1 2 3\n",
        "twitter/1.feat": "feature\n",
        "twitter/1.egofeat": "ego\n",
        "twitter/1.featnames": "name\n",
    }
    if unexpected:
        members["twitter/1.secret"] = "unexpected\n"
    with tarfile.open(archive, "w:gz") as tar:
        directory = tarfile.TarInfo("twitter/")
        directory.type = tarfile.DIRTYPE
        tar.addfile(directory)
        for name, content in members.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def _write_csv(path: Path, headers: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _write_processed_fixture(root: Path) -> tuple[Path, dict]:
    processed = root / "processed"
    processed.mkdir()
    users = [
        {
            "source_id": 1, "uid": 0, "username": "alice.a_0000", "email": "alice.a_0000@example.com",
            "name": "Alice Able", "name_lc": "alice able", "password_hash": "hash",
            "bio": "", "location": "Town", "joined_at": "2024-01-01T00:00:00Z", "source": "snap",
        },
        {
            "source_id": 2, "uid": 1, "username": "bob.b_0001", "email": "bob.b_0001@example.com",
            "name": "Bob Baker", "name_lc": "bob baker", "password_hash": "hash",
            "bio": "", "location": "City", "joined_at": "2024-01-01T00:00:00Z", "source": "snap",
        },
    ]
    files = {
        "users.csv": (load_data.EXPECTED_HEADERS["users.csv"], users),
        "circles.csv": (
            load_data.EXPECTED_HEADERS["circles.csv"],
            [{"circle_id": "1:0", "name": "@alice.a_0000's list 1", "source_label": "friends", "owner_source_id": 1}],
        ),
        "memberships.csv": (
            load_data.EXPECTED_HEADERS["memberships.csv"],
            [{"circle_id": "1:0", "source_id": 1}],
        ),
        "follows.csv": (
            load_data.EXPECTED_HEADERS["follows.csv"],
            [{"src": 1, "dst": 2, "since": "2024-01-01T00:00:00Z"}],
        ),
    }
    for filename, (headers, rows) in files.items():
        _write_csv(processed / filename, headers, rows)
    archive_sha = "a" * 64
    log = {
        "archive_sha256": archive_sha,
        "users": 2,
        "circles": 1,
        "memberships": 1,
        "edges_final": 1,
        "processed_files": {
            filename: hashlib.sha256((processed / filename).read_bytes()).hexdigest()
            for filename in files
        },
    }
    (processed / "preprocess_log.json").write_text(json.dumps(log), encoding="utf-8")
    return processed, log


def test_parse_edges_rejects_extra_columns(tmp_path: Path) -> None:
    path = tmp_path / "bad.edges"
    path.write_text("1 2 3\n", encoding="utf-8")

    with pytest.raises(ValueError, match="exactly two IDs"):
        parse_edges(path)


def test_preprocess_is_deterministic_on_a_small_fixture(tmp_path: Path) -> None:
    raw = _write_fixture_raw(tmp_path)
    first = tmp_path / "processed-one"
    second = tmp_path / "processed-two"

    first_log = preprocess(raw, first, 4, 5, enforce_assignment_minimums=False)
    second_log = preprocess(raw, second, 4, 5, enforce_assignment_minimums=False)

    names = ("users.csv", "follows.csv", "circles.csv", "memberships.csv", "preprocess_log.json")
    assert first_log == second_log
    assert {
        name: hashlib.sha256((first / name).read_bytes()).hexdigest()
        for name in names
    } == {
        name: hashlib.sha256((second / name).read_bytes()).hexdigest()
        for name in names
    }


def test_recommendation_selection_uses_two_hop_candidates() -> None:
    edges = [(1, 2), (2, 3), (3, 4)]
    outgoing = {1: {2}, 2: {3}, 3: {4}}
    assert recommendation_count(1, outgoing) == 1
    assert clean_edges(edges + [(1, 1), (1, 2)], {1, 2, 3, 4})[0] == edges
    demo = choose_demo_users(
        [1, 2, 3, 4],
        {1: "alice.a_0000", 2: "bob.b_0001", 3: "cara.c_0002", 4: "drew.d_0003"},
        edges,
        {1: {"1:0"}},
    )
    assert demo["busy_user_network_recommendations"] >= 1
    assert demo["busy_user"] in {"alice.a_0000", "bob.b_0001"}


def test_archive_manifest_and_safe_extraction(tmp_path: Path) -> None:
    archive = tmp_path / "twitter.tar.gz"
    _write_archive(archive)
    manifest = archive_manifest(archive)
    assert set(manifest) == {
        "twitter/1.edges", "twitter/1.circles", "twitter/1.feat",
        "twitter/1.egofeat", "twitter/1.featnames",
    }
    raw = tmp_path / "raw"
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert extract_if_needed(archive, raw) == digest
    assert extract_if_needed(archive, raw) == digest

    bad_archive = tmp_path / "bad.tar.gz"
    _write_archive(bad_archive, unexpected=True)
    with pytest.raises(ValueError, match="unexpected archive member"):
        archive_manifest(bad_archive)


def test_processed_manifest_and_csv_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    processed, log = _write_processed_fixture(tmp_path)
    monkeypatch.setattr(load_data, "TWITTER_ARCHIVE_SHA256", log["archive_sha256"])
    datasets = tuple((name, None, None) for name in load_data.EXPECTED_HEADERS)

    load_data.validate_processed_manifest(processed, log)
    loaded = load_data.validate_rows(
        processed, log, datasets, enforce_assignment_minimums=False
    )
    assert len(loaded["users.csv"]) == 2

    with (processed / "follows.csv").open("a", encoding="utf-8") as handle:
        handle.write("1,999,2024-01-01T00:00:00Z\n")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_data.validate_processed_manifest(processed, log)

    repaired_log = json.loads(json.dumps(log))
    repaired_log["edges_final"] = 2
    repaired_log["processed_files"]["follows.csv"] = hashlib.sha256(
        (processed / "follows.csv").read_bytes()
    ).hexdigest()
    load_data.validate_processed_manifest(processed, repaired_log)
    with pytest.raises(ValueError, match="dangling relationship"):
        load_data.validate_rows(
            processed, repaired_log, datasets, enforce_assignment_minimums=False
        )


def test_failed_load_cleans_only_its_load_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    processed, _log = _write_processed_fixture(tmp_path)
    datasets = {
        "circles.csv": [{"circle_id": "1:0", "name": "circle", "source_label": "friends", "owner_source_id": 1}],
        "users.csv": [{"source_id": 1}],
        "memberships.csv": [{"circle_id": "1:0", "source_id": 1}],
        "follows.csv": [{"src": 1, "dst": 2, "since": "2024-01-01T00:00:00Z"}],
    }

    class FakeDatabase:
        instance = None

        def __init__(self, _settings):
            self.calls = []
            FakeDatabase.instance = self

        def read(self, name, _query, **_params):
            assert name == "COUNT_ALL_NODES"
            return ([{"nodes": 0}], None)

        def write(self, name, _query, **params):
            self.calls.append((name, params))
            if name == "LOAD_USERS":
                raise RuntimeError("simulated batch failure")
            return ([{"loaded": len(params.get("rows", []))}], None)

        def close(self):
            pass

    monkeypatch.setattr(load_data, "Database", FakeDatabase)
    monkeypatch.setattr(load_data, "load_settings", lambda: object())
    monkeypatch.setattr(load_data, "SCHEMA", ())
    monkeypatch.setattr(load_data, "validate_processed_manifest", lambda *_args: None)
    monkeypatch.setattr(load_data, "validate_rows", lambda *_args, **_kwargs: datasets)

    with pytest.raises(RuntimeError, match="simulated batch failure"):
        load_data.load(processed)

    names = [name for name, _params in FakeDatabase.instance.calls]
    assert "CLEAR_LOAD_TOKEN" in names
    assert "FINALIZE_LOAD" not in names
    load_tokens = {
        params["load_token"]
        for _name, params in FakeDatabase.instance.calls
        if "load_token" in params
    }
    assert len(load_tokens) == 1


def test_reset_mode_clears_nonempty_database_before_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    processed, log = _write_processed_fixture(tmp_path)
    datasets = {
        "circles.csv": [{"circle_id": "1:0", "name": "circle", "source_label": "friends", "owner_source_id": 1}],
        "users.csv": [{"source_id": 1}],
        "memberships.csv": [{"circle_id": "1:0", "source_id": 1}],
        "follows.csv": [{"src": 1, "dst": 2, "since": "2024-01-01T00:00:00Z"}],
    }

    class FakeDatabase:
        instance = None

        def __init__(self, _settings):
            self.calls = []
            FakeDatabase.instance = self

        def read(self, name, _query, **_params):
            self.calls.append((name, {}))
            if name == "COUNT_ALL_NODES":
                return ([{"nodes": 2}], None)
            return ([{
                "users": log["users"],
                "circles": log["circles"],
                "follows": log["edges_final"],
                "memberships": log["memberships"],
            }], None)

        def write(self, name, _query, **params):
            self.calls.append((name, params))
            if name.startswith("LOAD_"):
                return ([{"loaded": len(params["rows"])}], None)
            return ([], None)

        def close(self):
            pass

    monkeypatch.setattr(load_data, "Database", FakeDatabase)
    monkeypatch.setattr(load_data, "load_settings", lambda: object())
    monkeypatch.setattr(load_data, "SCHEMA", ())
    monkeypatch.setattr(load_data, "validate_processed_manifest", lambda *_args: None)
    monkeypatch.setattr(load_data, "validate_rows", lambda *_args, **_kwargs: datasets)

    load_data.load(processed, reset=True)

    names = [name for name, _params in FakeDatabase.instance.calls]
    assert names[:2] == ["COUNT_ALL_NODES", "RESET_DATABASE"]
    assert "FINALIZE_LOAD" in names


def test_reset_mode_validates_processed_data_before_deleting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    processed, _log = _write_processed_fixture(tmp_path)

    class FakeDatabase:
        instance = None

        def __init__(self, _settings):
            self.calls = []
            FakeDatabase.instance = self

        def read(self, name, _query, **_params):
            self.calls.append(name)
            return ([{"nodes": 2}], None)

        def write(self, name, _query, **_params):
            self.calls.append(name)
            return ([], None)

        def close(self):
            pass

    monkeypatch.setattr(load_data, "Database", FakeDatabase)
    monkeypatch.setattr(load_data, "load_settings", lambda: object())

    def fail_manifest(*_args):
        raise ValueError("invalid processed data")

    monkeypatch.setattr(load_data, "validate_processed_manifest", fail_manifest)

    with pytest.raises(ValueError, match="invalid processed data"):
        load_data.load(processed, reset=True)

    assert FakeDatabase.instance.calls == ["COUNT_ALL_NODES"]
