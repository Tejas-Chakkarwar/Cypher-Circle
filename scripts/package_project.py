from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "docs" / "report"
ARCHIVE = ROOT / "projects.zip"
PDF = REPORT_DIR / "report.pdf"
TOP_LEVEL = (
    "README.md", "docs/PROJECT_DESIGN.pdf", "requirements.txt", "pyproject.toml",
    "Makefile", "docker-compose.yml", ".env.example", ".gitattributes", "build_report.sh",
)
FORBIDDEN = {".env", ".venv", "data/raw", "data/processed", "output", "__pycache__", ".pytest_cache"}


def python_files(directory: str) -> list[Path]:
    root = ROOT / directory
    if root.is_symlink():
        raise RuntimeError(f"symlink source directory is not allowed: {root.relative_to(ROOT)}")
    return sorted(path for path in root.rglob("*.py") if path.is_file() and "__pycache__" not in path.parts)


def resource_files(directory: str) -> list[Path]:
    root = ROOT / directory
    if root.is_symlink():
        raise RuntimeError(f"symlink source directory is not allowed: {root.relative_to(ROOT)}")
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )


def sources() -> list[tuple[Path, str]]:
    entries: list[tuple[Path, str]] = [(ROOT / name, f"source/{name}") for name in TOP_LEVEL]
    entries += [(path, f"source/{path.relative_to(ROOT)}") for path in python_files("cyphercircle")]
    entries += [(path, f"source/{path.relative_to(ROOT)}") for path in resource_files("cyphercircle/web_templates")]
    entries += [(path, f"source/{path.relative_to(ROOT)}") for path in resource_files("cyphercircle/web_static")]
    entries += [(path, f"source/{path.relative_to(ROOT)}") for path in python_files("scripts")]
    entries += [(path, f"source/{path.relative_to(ROOT)}") for path in python_files("tests")]
    entries += [(path, f"source/docs/report/diagrams/{path.name}") for path in sorted((REPORT_DIR / "diagrams").glob("*.png"))]
    entries += [
        (path, f"source/docs/report/screenshots/{path.name}")
        for path in sorted((REPORT_DIR / "screenshots").glob("*"))
        if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg"}
    ]
    entries += [
        (REPORT_DIR / "report.md", "source/docs/report/report.md"),
        (REPORT_DIR / "report.template.md", "source/docs/report/report.template.md"),
    ]
    missing = [str(path.relative_to(ROOT)) for path, _ in entries if not path.is_file()]
    if missing:
        raise RuntimeError("required package files are missing: " + ", ".join(missing))
    return sorted(entries, key=lambda pair: pair[1])


def validate_names(names: list[str]) -> None:
    for name in names:
        normalized = name.removeprefix("source/")
        if normalized == "report.pdf":
            continue
        if any(normalized == part or normalized.startswith(part + "/") for part in FORBIDDEN):
            raise RuntimeError(f"forbidden path selected for package: {name}")
        if name.endswith((".log", ".pem", ".key", ".secret")):
            raise RuntimeError(f"credential or generated log selected for package: {name}")


def validate_source_paths(entries: list[tuple[Path, str]]) -> None:
    """Reject symlinks and paths that resolve outside the repository."""
    root = ROOT.resolve()
    for path, name in entries:
        if path.is_symlink():
            raise RuntimeError(f"symlink source is not allowed: {name}")
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"package source must remain inside the repository: {name}") from exc

        relative = path.relative_to(ROOT)
        current = ROOT
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                raise RuntimeError(f"symlink source is not allowed: {name}")


def verify_pdf() -> None:
    if not PDF.is_file():
        raise RuntimeError("docs/report/report.pdf is missing; run make report first")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/verify_report.py"), "--pdf", str(PDF)],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise RuntimeError("report PDF verification failed:\n" + result.stdout + result.stderr)


def build_archive() -> int:
    verify_pdf()
    entries = [(PDF, "report.pdf"), *sources()]
    validate_names([name for _, name in entries])
    validate_source_paths(entries)
    ARCHIVE.unlink(missing_ok=True)
    with zipfile.ZipFile(ARCHIVE, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path, name in entries:
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    with zipfile.ZipFile(ARCHIVE) as archive:
        names = archive.namelist()
        validate_names(names)
        required = {
            "report.pdf", "source/README.md", "source/docs/PROJECT_DESIGN.pdf",
            "source/cyphercircle/cli.py", "source/scripts/preprocess.py",
            "source/tests/test_cli.py",
        }
        missing = sorted(required - set(names))
        if missing:
            raise RuntimeError("archive is missing required entries: " + ", ".join(missing))
    print(f"Created {ARCHIVE}")
    print(f"Package manifest ({len(names)} files):")
    print("\n".join(f"  {name}" for name in names))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(build_archive())
    except (OSError, RuntimeError) as exc:
        print(f"package failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
