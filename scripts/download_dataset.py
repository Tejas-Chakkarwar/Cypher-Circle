from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
import os
import stat
import tarfile
import tempfile
import urllib.request
from urllib.parse import urlparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.seed_constants import TWITTER_ARCHIVE_SHA256


URL = "https://snap.stanford.edu/data/twitter.tar.gz"
MEMBER_RE = re.compile(r"^twitter/[0-9]+\.(edges|circles|feat|egofeat|featnames)$")
MAX_MEMBER_SIZE = 200 * 1024 * 1024
MAX_TOTAL_SIZE = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_SIZE = 100 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 30
EXPECTED_EXTENSIONS = {"edges", "circles", "feat", "egofeat", "featnames"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def archive_manifest(archive: Path) -> dict[str, int]:
    manifest: dict[str, int] = {}
    seen_members: set[str] = set()
    total = 0
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            name = member.name.removeprefix("./")
            canonical_name = "twitter/" if name == "twitter" else name
            if canonical_name in seen_members:
                raise ValueError(f"duplicate archive member: {name}")
            seen_members.add(canonical_name)
            if name in {"twitter", "twitter/"}:
                if not member.isdir():
                    raise ValueError("archive member twitter/ is not a directory")
                continue
            if not MEMBER_RE.fullmatch(name):
                raise ValueError(f"unexpected archive member: {member.name}")
            if name in manifest:
                raise ValueError(f"duplicate archive member: {name}")
            if not member.isreg():
                raise ValueError(f"archive member is not a regular file: {name}")
            if member.size > MAX_MEMBER_SIZE:
                raise ValueError(f"archive member is too large: {name}")
            total += member.size
            if total > MAX_TOTAL_SIZE:
                raise ValueError("archive is larger than the supported extraction limit")
            manifest[name] = member.size
    stems_by_extension = {
        extension: {
            name.rsplit("/", 1)[1].removesuffix(f".{extension}")
            for name in manifest
            if name.endswith(f".{extension}")
        }
        for extension in EXPECTED_EXTENSIONS
    }
    missing_extensions = sorted(
        extension for extension, stems in stems_by_extension.items() if not stems
    )
    if missing_extensions:
        raise ValueError(
            "archive is missing required extension groups: "
            + ", ".join(missing_extensions)
        )
    edge_stems = stems_by_extension["edges"]
    incomplete_stems = {
        extension: sorted(edge_stems - stems)
        for extension, stems in stems_by_extension.items()
        if stems != edge_stems
    }
    if incomplete_stems:
        raise ValueError("archive has incomplete per-ego extension groups")
    return manifest


def extracted_manifest(extracted: Path) -> dict[str, int]:
    if not os.path.lexists(extracted):
        return {}
    if extracted.is_symlink() or not stat.S_ISDIR(extracted.lstat().st_mode):
        raise ValueError(f"extracted dataset is not a real directory: {extracted}")
    nested = extracted / "twitter"
    if os.path.lexists(nested):
        raise ValueError("nested twitter/twitter/ extraction layout found")
    result: dict[str, int] = {}
    for path in extracted.iterdir():
        if path.name == ".archive_sha256":
            if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
                raise ValueError("archive marker is not a regular file")
            continue
        if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError(f"extracted path is not a regular file: {path}")
        if not MEMBER_RE.fullmatch(f"twitter/{path.name}"):
            raise ValueError(f"unexpected extracted path: {path}")
        result[f"twitter/{path.name}"] = path.lstat().st_size
    return result


def download_if_missing(archive: Path) -> None:
    if os.path.lexists(archive):
        if archive.is_symlink() or not stat.S_ISREG(archive.lstat().st_mode):
            raise ValueError(f"dataset archive is not a regular file: {archive}")
        return
    archive.parent.mkdir(parents=True, exist_ok=True)
    partial = None
    try:
        print(f"Downloading {URL} ...")
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=".twitter-download-", suffix=".part",
            dir=archive.parent, delete=False
        ) as handle:
            partial = Path(handle.name)
            with urllib.request.urlopen(URL, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
                final_url = urlparse(response.geturl())
                if final_url.scheme != "https" or final_url.hostname != "snap.stanford.edu":
                    raise ValueError("dataset download redirected to an untrusted host")
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > MAX_ARCHIVE_SIZE:
                    raise ValueError("dataset archive exceeds the compressed-size limit")
                total = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_ARCHIVE_SIZE:
                        raise ValueError("dataset archive exceeds the compressed-size limit")
                    handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(partial, archive)
    except Exception as exc:
        if partial is not None:
            partial.unlink(missing_ok=True)
        print(
            f"Could not download the dataset: {exc}\n"
            f"Download the original archive from {URL}\n"
            f"and save it as {archive}, then rerun this command.",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc


def extract_if_needed(
    archive: Path,
    raw_dir: Path,
    *,
    expected_sha256: str | None = None,
) -> str:
    digest = sha256(archive)
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError(
            "archive SHA-256 does not match the pinned SNAP archive: "
            f"expected {expected_sha256}, got {digest}"
        )
    expected = archive_manifest(archive)
    extracted = raw_dir / "twitter"
    marker = extracted / ".archive_sha256"
    marker_matches = False
    if os.path.lexists(marker):
        if marker.is_symlink() or not stat.S_ISREG(marker.lstat().st_mode):
            raise ValueError("archive marker is not a regular file")
        try:
            marker_matches = marker.read_text(encoding="ascii") == f"{digest}\n"
        except (UnicodeDecodeError, OSError):
            marker_matches = False
    if marker_matches:
        try:
            if extracted_manifest(extracted) == expected:
                print(f"Dataset already extracted ({digest})")
                return digest
        except ValueError:
            pass

    raw_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="twitter-extract-", dir=raw_dir) as temp:
        temp_root = Path(temp)
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(temp_root, filter="data")
        candidate = temp_root / "twitter"
        if extracted_manifest(candidate) != expected:
            raise ValueError("extracted files do not match the archive manifest")
        (candidate / ".archive_sha256").write_text(f"{digest}\n", encoding="utf-8")
        if os.path.lexists(extracted):
            if extracted.is_symlink() or stat.S_ISREG(extracted.lstat().st_mode):
                extracted.unlink()
            elif stat.S_ISDIR(extracted.lstat().st_mode):
                shutil.rmtree(extracted)
            else:
                raise ValueError(f"cannot replace extraction target: {extracted}")
        candidate.replace(extracted)
    print(f"Extracted dataset ({digest})")
    return digest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    args = parser.parse_args()
    archive = args.raw_dir / "twitter.tar.gz"
    download_if_missing(archive)
    extract_if_needed(
        archive,
        args.raw_dir,
        expected_sha256=TWITTER_ARCHIVE_SHA256,
    )


if __name__ == "__main__":
    main()

