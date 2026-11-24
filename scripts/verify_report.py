from __future__ import annotations

import argparse
import re
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

HEADINGS = (
    "Property Graph Schema", "Dataset", "Architecture", "Use Cases",
    "Security and Correctness", "Reproducibility and Setup",
    "Testing and Acceptance", "Team Responsibilities", "Limitations and Future Work",
    "UC-13",
)


def command(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(f"command failed: {' '.join(args)}\n{result.stdout}\n{result.stderr}")
    return result.stdout


def verify(pdf: Path, render_dir: Path | None = None) -> int:
    if not pdf.is_file() or pdf.stat().st_size == 0:
        raise RuntimeError(f"required PDF is missing or empty: {pdf}")
    info = command(["pdfinfo", str(pdf)])
    match = re.search(r"^Pages:\s+(\d+)", info, re.MULTILINE)
    pages = int(match.group(1)) if match else 0
    text = command(["pdftotext", str(pdf), "-"])
    missing = [heading for heading in HEADINGS if heading not in text]
    if pages < 1:
        raise RuntimeError("PDF has no pages")
    if missing:
        raise RuntimeError("PDF text is missing headings: " + ", ".join(missing))
    if "{{" in text or "}}" in text:
        raise RuntimeError("PDF contains unresolved report placeholders")
    temporary = None
    if render_dir is None:
        temporary = tempfile.TemporaryDirectory(prefix="cyphercircle-report-")
        render_dir = Path(temporary.name)
    render_dir.mkdir(parents=True, exist_ok=True)
    prefix = render_dir / "page"
    command(["pdftoppm", "-png", str(pdf), str(prefix)])
    pages_png = sorted(render_dir.glob("page-*.png"))
    if len(pages_png) != pages:
        raise RuntimeError(f"rendered {len(pages_png)} pages but PDF reports {pages}")
    for path in pages_png:
        if path.stat().st_size < 1000:
            raise RuntimeError(f"rendered page is empty: {path}")
        with Image.open(path) as image:
            if image.width < 100 or image.height < 100:
                raise RuntimeError(f"rendered page has invalid dimensions: {path}")
    print(f"Verified {pdf}: {pages} pages, headings present, every page rendered")
    if temporary is not None:
        temporary.cleanup()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--render-dir", type=Path)
    args = parser.parse_args()
    try:
        return verify(args.pdf, args.render_dir)
    except (OSError, RuntimeError) as exc:
        print(f"report verification failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
