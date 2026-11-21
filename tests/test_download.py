from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

import scripts.download_dataset as download_dataset


def _write_archive(
    archive: Path,
    *,
    extra_members: tuple[tuple[str, str], ...] = (),
    nested: bool = False,
) -> None:
    members = {
        "twitter/1.edges": "2 3\n",
        "twitter/1.circles": "friends 1 2 3\n",
        "twitter/1.feat": "feature\n",
        "twitter/1.egofeat": "ego\n",
        "twitter/1.featnames": "name\n",
    }
    members.update(dict(extra_members))
    with tarfile.open(archive, "w:gz") as tar:
        directory = tarfile.TarInfo("twitter/")
        directory.type = tarfile.DIRTYPE
        tar.addfile(directory)
        for name, content in members.items():
            if nested:
                name = f"twitter/{name}"
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def test_archive_manifest_rejects_nested_and_unexpected_members(tmp_path: Path) -> None:
    nested = tmp_path / "nested.tar.gz"
    _write_archive(nested, nested=True)
    with pytest.raises(ValueError, match="unexpected archive member"):
        download_dataset.archive_manifest(nested)

    unexpected = tmp_path / "unexpected.tar.gz"
    _write_archive(unexpected, extra_members=(("twitter/1.secret", "nope\n"),))
    with pytest.raises(ValueError, match="unexpected archive member"):
        download_dataset.archive_manifest(unexpected)


def test_archive_manifest_rejects_non_regular_members(tmp_path: Path) -> None:
    archive = tmp_path / "symlink.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        directory = tarfile.TarInfo("twitter/")
        directory.type = tarfile.DIRTYPE
        tar.addfile(directory)
        for name in (
            "twitter/1.edges", "twitter/1.circles", "twitter/1.feat",
            "twitter/1.egofeat", "twitter/1.featnames",
        ):
            data = b"ok\n"
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("twitter/2.edges")
        link.type = tarfile.SYMTYPE
        link.linkname = "1.edges"
        tar.addfile(link)

    with pytest.raises(ValueError, match="not a regular file"):
        download_dataset.archive_manifest(archive)


def test_extracted_manifest_rejects_nested_layout(tmp_path: Path) -> None:
    extracted = tmp_path / "twitter"
    (extracted / "twitter").mkdir(parents=True)
    with pytest.raises(ValueError, match="nested twitter/twitter"):
        download_dataset.extracted_manifest(extracted)


def test_extraction_skips_only_matching_complete_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "twitter.tar.gz"
    _write_archive(archive)
    raw = tmp_path / "raw"
    original_extractall = tarfile.TarFile.extractall
    calls = 0

    def counting_extractall(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        return original_extractall(self, *args, **kwargs)

    monkeypatch.setattr(tarfile.TarFile, "extractall", counting_extractall)
    digest = download_dataset.extract_if_needed(archive, raw)
    assert calls == 1

    assert download_dataset.extract_if_needed(archive, raw) == digest
    assert calls == 1

    marker = raw / "twitter" / ".archive_sha256"
    marker.write_text("wrong\n", encoding="ascii")
    assert download_dataset.extract_if_needed(archive, raw) == digest
    assert calls == 2

    (raw / "twitter" / "1.feat").unlink()
    assert download_dataset.extract_if_needed(archive, raw) == digest
    assert calls == 3
    assert (raw / "twitter" / "1.feat").read_text(encoding="utf-8") == "feature\n"
