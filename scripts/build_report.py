from __future__ import annotations

import json
import re
import subprocess
import sys
import textwrap
from functools import lru_cache
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import BaseDocTemplate, Frame, HRFlowable, Image as PdfImage, ListFlowable, ListItem, PageBreak, PageTemplate, Paragraph, Preformatted, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
REPORT = ROOT / "docs" / "report"
DIAGRAMS = REPORT / "diagrams"
TEMPLATE = REPORT / "report.template.md"
MARKDOWN = REPORT / "report.md"
PDF = REPORT / "report.pdf"
LOG = ROOT / "data/processed/preprocess_log.json"

REQUIRED_LOG_FIELDS = {
    "archive_sha256", "users", "edges_final", "circles", "memberships",
    "egos_total", "egos_selected", "edges_from_ego_files", "ego_edges_added",
    "self_loops_dropped", "duplicates_dropped", "dangling_dropped",
    "circles_raw", "circles_dropped_small", "users_without_circle",
    "demo_search_term", "demo_search_matches", "demo_users", "faker_version",
    "seed", "processed_files",
}
COUNT_FIELDS = {
    "users", "edges_final", "circles", "memberships", "egos_total", "egos_selected",
    "edges_from_ego_files", "ego_edges_added", "self_loops_dropped", "duplicates_dropped",
    "dangling_dropped", "circles_raw", "circles_dropped_small", "users_without_circle",
    "demo_search_matches",
}
DEMO_USER_FIELDS = {
    "busy_user", "busy_user_rule", "busy_user_network_recommendations",
    "mutual_partner", "no_circle_user",
}
PROCESSED_FILE_NAMES = {"users.csv", "follows.csv", "circles.csv", "memberships.csv"}
DEMO_USER_RULES = {"0.20", "0.10", "top_followers"}
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

FONT_CANDIDATES = {
    False: (
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
        Path("/Library/Fonts/Arial.ttf"),
        Path.home() / "Library/Fonts/Arial.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSans-Regular.ttf"),
    ),
    True: (
        Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
        Path("/Library/Fonts/Arial Bold.ttf"),
        Path.home() / "Library/Fonts/Arial Bold.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSans-Bold.ttf"),
    ),
}
# Use-case numbers follow the professor's assignment (UC-1 to UC-13).
UC_QUERIES = {
    "UC-1": ["GET_TOP_CIRCLES", "CIRCLE_EXISTS", "CHECK_REGISTRATION_CONFLICTS", "CREATE_USER_WITH_CIRCLES"],
    "UC-2": ["GET_AUTH_BY_USERNAME"],
    "UC-3": ["GET_PROFILE"],
    "UC-4": ["USER_EXISTS", "CHECK_EMAIL_IN_USE", "UPDATE_PROFILE", "CLEAR_BIO", "CLEAR_LOCATION", "GET_PROFILE"],
    "UC-5": ["USER_EXISTS", "FOLLOW_USER"], "UC-6": ["USER_EXISTS", "UNFOLLOW_USER"],
    "UC-7": ["USER_EXISTS", "COUNT_CONNECTIONS", "GET_FOLLOWING", "GET_FOLLOWERS"],
    "UC-8": ["USER_EXISTS", "COUNT_MUTUAL_FOLLOWING", "GET_MUTUAL_FOLLOWING", "GET_RELATIONSHIP_STATUS"],
    "UC-9": ["USER_EXISTS", "RECOMMEND_BY_NETWORK"],
    "UC-10": ["COUNT_USERS", "GET_ALL_USERS"],
    "UC-11": ["SEARCH_USERS"],
    "UC-12": ["GET_POPULAR_USERS"],
    "UC-13": ["USER_EXISTS", "FIND_SHORTEST_FOLLOW_PATH"],
}
UC_NAMES = {
    "UC-1": "User registration", "UC-2": "User login", "UC-3": "View profile",
    "UC-4": "Edit profile", "UC-5": "Follow another user", "UC-6": "Unfollow a user",
    "UC-7": "View following and followers", "UC-8": "Mutual connections",
    "UC-9": "Friend recommendations", "UC-10": "View all users",
    "UC-11": "Search users", "UC-12": "Explore popular users",
    "UC-13": "Find degrees of separation",
}


def load_log(path: Path = LOG) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(f"required preprocessing log is missing: {path}. Run make data first.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not read preprocessing log: {path}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("preprocessing log must contain a JSON object")
    missing = sorted(REQUIRED_LOG_FIELDS - set(data))
    if missing:
        raise RuntimeError("preprocessing log is missing fields: " + ", ".join(missing))
    for field in COUNT_FIELDS:
        value = data[field]
        if type(value) is not int or value < 0:
            raise RuntimeError(f"preprocessing log field {field!r} must be a non-negative integer")
    if not isinstance(data["archive_sha256"], str) or not SHA256_PATTERN.fullmatch(data["archive_sha256"]):
        raise RuntimeError("preprocessing log field 'archive_sha256' must be a 64-character lowercase SHA-256")
    if not isinstance(data["faker_version"], str) or not data["faker_version"].strip():
        raise RuntimeError("preprocessing log field 'faker_version' must be a non-empty string")
    if type(data["seed"]) is not int:
        raise RuntimeError("preprocessing log field 'seed' must be an integer")
    if not isinstance(data["demo_search_term"], str) or not data["demo_search_term"].strip():
        raise RuntimeError("preprocessing log field 'demo_search_term' must be a non-empty string")
    if not isinstance(data["demo_users"], dict):
        raise RuntimeError("preprocessing log has an invalid demo_users section")
    missing_demo = sorted(DEMO_USER_FIELDS - set(data["demo_users"]))
    if missing_demo:
        raise RuntimeError("demo_users is missing fields: " + ", ".join(missing_demo))
    demo = data["demo_users"]
    if not isinstance(demo["busy_user"], str) or not demo["busy_user"].strip():
        raise RuntimeError("demo_users.busy_user must be a non-empty string")
    if not isinstance(demo["busy_user_rule"], str) or demo["busy_user_rule"] not in DEMO_USER_RULES:
        raise RuntimeError("demo_users.busy_user_rule has an invalid value")
    if type(demo["busy_user_network_recommendations"]) is not int or demo["busy_user_network_recommendations"] < 0:
        raise RuntimeError("demo_users.busy_user_network_recommendations must be a non-negative integer")
    for field in ("mutual_partner", "no_circle_user"):
        if demo[field] is not None and (not isinstance(demo[field], str) or not demo[field].strip()):
            raise RuntimeError(f"demo_users.{field} must be a username or null")
    if not isinstance(data["processed_files"], dict) or set(data["processed_files"]) != PROCESSED_FILE_NAMES:
        raise RuntimeError("preprocessing log processed_files must contain the four processed CSV files")
    if any(not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value) for value in data["processed_files"].values()):
        raise RuntimeError("preprocessing log processed_files must contain 64-character lowercase SHA-256 values")
    return data


def num(value: Any) -> str:
    return f"{int(value):,}"


def unit_result() -> str:
    result = subprocess.run([sys.executable, "-m", "pytest", "-m", "not integration", "-q"], cwd=ROOT, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError("unit tests must pass before report build:\n" + result.stdout + result.stderr)
    summary = next((line.strip() for line in reversed(result.stdout.splitlines()) if "passed" in line), "unit tests passed")
    # Omit pytest's elapsed time so report.md and the package remain stable
    # across otherwise identical builds.
    return re.sub(r"\s+in\s+\d+(?:\.\d+)?s$", "", summary)


def live_counts() -> tuple[dict[str, int] | None, str]:
    try:
        from cyphercircle.config import load_settings
        from cyphercircle.db import Database
        from cyphercircle.queries import VERIFY_COUNTS
        db = Database(load_settings())
        try:
            rows, _ = db.read("VERIFY_COUNTS", VERIFY_COUNTS)
        finally:
            db.close()
        if not rows:
            return None, "Live Neo4j VERIFY_COUNTS returned no row."
        return {key: int(rows[0][key]) for key in ("users", "follows", "circles", "memberships")}, "Live Neo4j VERIFY_COUNTS was captured during this build."
    except Exception:
        return None, "Live Neo4j VERIFY_COUNTS was unavailable; run make up and make load, then rebuild the report."


def tick() -> str:
    return chr(96)


def md_table(rows: list[tuple[str, Any]]) -> str:
    mark = tick()
    return "| Field | Value |\n|---|---|\n" + "\n".join(f"| {mark}{key}{mark} | {value} |" for key, value in rows)


def query_block(name: str, query: str) -> str:
    mark = tick()
    fence = mark * 3
    return f"### {mark}{name}{mark}\n\n{fence}cypher\n{query}\n{fence}"


def query_text(names_by_uc: dict[str, list[str]]) -> str:
    from cyphercircle import queries
    blocks = []
    for uc, names in names_by_uc.items():
        blocks.append(f"### {uc} - {UC_NAMES[uc]}")
        blocks.extend(query_block(name, getattr(queries, name)) for name in names)
    return "\n\n".join(blocks)


def render(data: dict[str, Any], tests: str, counts: dict[str, int] | None, live_note: str) -> str:
    demo = data["demo_users"]
    mark = tick()
    from cyphercircle import queries
    schema_names = [
        "CREATE_USER_USERNAME_CONSTRAINT", "CREATE_USER_EMAIL_CONSTRAINT",
        "CREATE_USER_SOURCE_ID_CONSTRAINT", "CREATE_CIRCLE_ID_CONSTRAINT",
        "CREATE_USER_NAME_TEXT_INDEX", "CREATE_USER_USERNAME_TEXT_INDEX",
        "LOAD_CIRCLES", "LOAD_USERS", "LOAD_MEMBERSHIPS", "LOAD_FOLLOWS", "VERIFY_COUNTS",
    ]
    schema = "\n\n".join(query_block(name, getattr(queries, name)) for name in schema_names)
    replacements = {
        "{{PROCESSED_COUNTS}}": md_table([("users", num(data["users"])), ("follows", num(data["edges_final"])), ("circles", num(data["circles"])), ("memberships", num(data["memberships"]))]),
        "{{LIVE_COUNTS}}": md_table([(key, num(value)) for key, value in counts.items()]) if counts else live_note,
        "{{DEMO_VALUES}}": md_table([
            ("busy_user", f"{mark}{demo.get('busy_user')}{mark}"), ("busy_user_rule", f"{mark}{demo.get('busy_user_rule')}{mark}"),
            ("busy_user_network_recommendations", num(demo.get("busy_user_network_recommendations", 0))),
            ("mutual_partner", f"{mark}{demo.get('mutual_partner')}{mark}"), ("no_circle_user", f"{mark}{demo.get('no_circle_user')}{mark}"),
            ("demo_search_term", f"{mark}{data['demo_search_term']}{mark}"), ("demo_search_matches", num(data["demo_search_matches"])),
        ]),
        "{{PROCESSED_HASHES}}": md_table([(name, f"{mark}{value}{mark}") for name, value in sorted(data["processed_files"].items())]),
        "{{UNIT_RESULT}}": tests, "{{SCHEMA_QUERY_DETAILS}}": schema, "{{UC_QUERY_DETAILS}}": query_text(UC_QUERIES),
        "{{ARCHIVE_SHA256}}": f"{mark}{data['archive_sha256']}{mark}", "{{EGOS_TOTAL}}": num(data["egos_total"]),
        "{{EGOS_SELECTED}}": num(data["egos_selected"]), "{{EDGES_FROM_FILES}}": num(data["edges_from_ego_files"]),
        "{{EGO_EDGES_ADDED}}": num(data["ego_edges_added"]), "{{SELF_LOOPS_DROPPED}}": num(data["self_loops_dropped"]),
        "{{DUPLICATES_DROPPED}}": num(data["duplicates_dropped"]), "{{DANGLING_DROPPED}}": num(data["dangling_dropped"]),
        "{{CIRCLES_RAW}}": num(data["circles_raw"]), "{{CIRCLES_DROPPED}}": num(data["circles_dropped_small"]),
        "{{USERS_WITHOUT_CIRCLE}}": num(data["users_without_circle"]), "{{FAKER_VERSION}}": f"{mark}{data['faker_version']}{mark}",
        "{{SEED}}": f"{mark}{data['seed']}{mark}",
    }
    rendered = TEMPLATE.read_text(encoding="utf-8")
    for old, new in replacements.items():
        rendered = rendered.replace(old, new)
    unresolved = sorted(set(re.findall(r"\{\{[^}]+\}\}", rendered)))
    if unresolved:
        raise RuntimeError("unresolved report placeholders: " + ", ".join(unresolved))
    return rendered


@lru_cache(maxsize=None)
def image_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in FONT_CANDIDATES[bold]:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    # Pillow normally provides a FreeType-backed Aileron fallback. The sized
    # API also keeps the fallback readable when no system font is available.
    return ImageFont.load_default(size=size)


def diagram_box(draw: ImageDraw.ImageDraw, coords: tuple[int, int, int, int], title: str, body: str, fill: str) -> None:
    draw.rounded_rectangle(coords, radius=18, fill=fill, outline="#183b56", width=3)
    x1, y1, x2, _ = coords
    draw.text((x1 + 18, y1 + 14), title, font=image_font(27, True), fill="#102a43")
    width = max(16, (x2 - x1 - 36) // 14)
    draw.multiline_text((x1 + 18, y1 + 62), "\n".join(textwrap.wrap(body, width=width)), font=image_font(20), fill="#243b53", spacing=4)


def diagram_arrow(draw: ImageDraw.ImageDraw, start: tuple[int, int], end: tuple[int, int], label: str = "") -> None:
    draw.line([start, end], fill="#486581", width=5)
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = max((dx * dx + dy * dy) ** 0.5, 1)
    ux, uy = dx / length, dy / length
    left = (int(end[0] - 18 * ux + 10 * uy), int(end[1] - 18 * uy - 10 * ux))
    right = (int(end[0] - 18 * ux - 10 * uy), int(end[1] - 18 * uy + 10 * ux))
    draw.polygon([end, left, right], fill="#486581")
    if label:
        draw.text(((start[0] + end[0]) // 2 + 8, (start[1] + end[1]) // 2 - 27), label, font=image_font(18, True), fill="#486581")


def save_diagram(name: str, size: tuple[int, int], painter: Any) -> None:
    image = Image.new("RGB", size, "#f7fafc")
    draw = ImageDraw.Draw(image)
    draw.text((35, 25), name.replace("_", " ").title(), font=image_font(35, True), fill="#102a43")
    painter(draw)
    image.save(DIAGRAMS / f"{name}.png", format="PNG", optimize=False)


def build_diagrams() -> None:
    DIAGRAMS.mkdir(parents=True, exist_ok=True)

    def schema(draw: ImageDraw.ImageDraw) -> None:
        diagram_box(draw, (90, 210, 560, 465), "User", "username, email, name,\npassword_hash, source", "#dbeafe")
        diagram_box(draw, (940, 210, 1410, 465), "User", "username, email, name,\npassword_hash, source", "#dbeafe")
        diagram_box(draw, (650, 545, 1120, 770), "Circle", "circle_id, name,\nsource_label, owner_source_id", "#dcfce7")
        diagram_arrow(draw, (560, 340), (940, 340), "FOLLOWS {since}")
        diagram_arrow(draw, (350, 465), (720, 545), "IN_CIRCLE")
        diagram_arrow(draw, (1190, 465), (1010, 545), "IN_CIRCLE")

    def architecture(draw: ImageDraw.ImageDraw) -> None:
        diagram_box(draw, (60, 260, 390, 510), "CLI", "Rich menus, prompts,\nShow Cypher", "#fef3c7")
        services = [(500, "AuthService"), (750, "ProfileService"), (1000, "GraphService"), (1250, "SearchService"), (1500, "VisualizationService")]
        for x, title in services:
            diagram_box(draw, (x, 155, x + 220, 510), title, "validation and\nbusiness rules", "#e0e7ff")
            diagram_arrow(draw, (390, 385), (x, 340))
        diagram_box(draw, (700, 635, 1080, 835), "Database", "Neo4j driver boundary\nparameterized reads/writes", "#fee2e2")
        diagram_box(draw, (1170, 635, 1550, 835), "QueryLog", "action labels, ordered\nredacted query entries", "#fce7f3")
        diagram_arrow(draw, (890, 510), (890, 635))
        diagram_arrow(draw, (1400, 510), (1360, 635))

    def pipeline(draw: ImageDraw.ImageDraw) -> None:
        entries = [(40, "SNAP archive", "twitter.tar.gz"), (360, "Download", "checksum + extraction"), (680, "Preprocess", "select, clean, enrich"), (1000, "CSV outputs", "users, follows, circles"), (1320, "Loader", "batched UNWIND"), (1640, "Neo4j", "VERIFY_COUNTS")]
        for index, (x, title, body) in enumerate(entries):
            diagram_box(draw, (x, 240, x + 250, 500), title, body, "#e0f2fe" if index % 2 == 0 else "#ecfdf5")
            if index + 1 < len(entries):
                diagram_arrow(draw, (x + 250, 370), (entries[index + 1][0], 370))

    def use_cases(draw: ImageDraw.ImageDraw) -> None:
        diagram_box(draw, (70, 250, 350, 530), "User", "registration, login,\nsearch, exploration", "#fef3c7")
        diagram_box(draw, (520, 145, 900, 665), "Use cases", "UC-1 to UC-13\nCLI and web UI\nprofiles, follows\nsearch, recommendations\ndegrees of separation", "#e0e7ff")
        diagram_box(draw, (1080, 145, 1430, 665), "Extra", "VisualizationService\nego network\noffline HTML", "#dcfce7")
        diagram_box(draw, (1550, 250, 1840, 530), "Evidence", "QueryLog\nreport screenshots\nacceptance tests", "#fce7f3")
        diagram_arrow(draw, (350, 390), (520, 390))
        diagram_arrow(draw, (900, 390), (1080, 390))
        diagram_arrow(draw, (1430, 390), (1550, 390))

    save_diagram("schema", (1500, 850), schema)
    save_diagram("architecture", (1800, 900), architecture)
    save_diagram("pipeline", (1900, 680), pipeline)
    save_diagram("use_cases", (1900, 850), use_cases)


def inline(value: str) -> str:
    value = escape(value, {'"': "&quot;"})
    mark = re.escape(chr(96))
    value = re.sub(mark + r"([^" + mark + r"]+)" + mark, r'<font name="Courier">\1</font>', value)
    value = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", value)
    value = re.sub(r"\*([^*]+)\*", r"<i>\1</i>", value)
    return value


def resolve_report_image(raw_path: str) -> Path:
    """Resolve a report image without allowing paths outside ``docs/report/``."""
    if REPORT.is_symlink():
        raise RuntimeError("symlink report directories are not allowed")
    relative = Path(raw_path)
    if relative.is_absolute():
        raise RuntimeError(f"report image path must be relative to docs/report/: {raw_path}")

    report_root = REPORT.resolve()
    candidate = REPORT / relative
    current = REPORT
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise RuntimeError(f"symlink report images are not allowed: {raw_path}")

    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(report_root)
    except ValueError as exc:
        raise RuntimeError(f"report image path escapes docs/report/: {raw_path}") from exc
    except OSError as exc:
        raise RuntimeError(f"required report image is missing: {candidate}") from exc

    if not resolved.is_file():
        raise RuntimeError(f"required report image is missing: {resolved}")
    return resolved


def report_styles() -> Any:
    result = getSampleStyleSheet()
    result.add(ParagraphStyle("ReportTitle", parent=result["Title"], fontName="Helvetica-Bold", fontSize=28, leading=34, textColor=colors.HexColor("#102a43"), alignment=TA_CENTER, spaceAfter=16))
    result.add(ParagraphStyle("H2Report", parent=result["Heading2"], fontName="Helvetica-Bold", fontSize=18, leading=23, textColor=colors.HexColor("#102a43"), spaceBefore=15, spaceAfter=7, keepWithNext=True))
    result.add(ParagraphStyle("H3Report", parent=result["Heading3"], fontName="Helvetica-Bold", fontSize=12.5, leading=16, textColor=colors.HexColor("#243b53"), spaceBefore=9, spaceAfter=4, keepWithNext=True))
    result.add(ParagraphStyle("BodyReport", parent=result["BodyText"], fontName="Helvetica", fontSize=9.2, leading=13, textColor=colors.HexColor("#243b53"), spaceAfter=5))
    result.add(ParagraphStyle("CodeReport", parent=result["Code"], fontName="Courier", fontSize=6.5, leading=8, leftIndent=7, rightIndent=7, backColor=colors.HexColor("#f0f4f8"), borderColor=colors.HexColor("#d9e2ec"), borderWidth=0.5, borderPadding=5, spaceBefore=3, spaceAfter=7))
    result.add(ParagraphStyle("Caption", parent=result["BodyText"], fontName="Helvetica-Oblique", fontSize=8, leading=10, alignment=TA_CENTER, textColor=colors.HexColor("#486581"), spaceAfter=8))
    return result


def parse_table(lines: list[str], style: Any) -> Table:
    rows = []
    for line in lines:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if all(set(cell) <= {"-", ":", " "} for cell in cells):
            continue
        rows.append([Paragraph(inline(cell), style["BodyReport"]) for cell in cells])
    result = Table(rows, repeatRows=1, hAlign="LEFT")
    result.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#d9e2ec")),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#bcccdc")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return result


def report_flowables(markdown: str, style: Any) -> list[Any]:
    lines = markdown.splitlines()
    result: list[Any] = []
    paragraph: list[str] = []
    fence = chr(96) * 3
    index = 0

    def flush() -> None:
        if paragraph:
            text = " ".join(part.strip() for part in paragraph)
            if text:
                result.append(Paragraph(inline(text), style["BodyReport"]))
            paragraph.clear()

    while index < len(lines):
        line = lines[index]
        if line.strip() == "<!-- PAGEBREAK -->":
            flush()
            result.append(PageBreak())
            index += 1
        elif not line.strip():
            flush()
            index += 1
        elif line.startswith(fence):
            flush()
            code = []
            index += 1
            while index < len(lines) and not lines[index].startswith(fence):
                code.append(lines[index])
                index += 1
            if index >= len(lines):
                raise RuntimeError("unterminated report code block")
            result.append(Preformatted("\n".join(code), style["CodeReport"]))
            index += 1
        elif line.startswith("![") and line.endswith(")"):
            flush()
            match = re.fullmatch(r"!\[([^]]*)\]\(([^)]+)\)", line.strip())
            if not match:
                raise RuntimeError(f"invalid report image syntax: {line}")
            alt, raw_path = match.groups()
            image_path = resolve_report_image(raw_path)
            with Image.open(image_path) as image:
                width, height = image.size
            scale = min((6.6 * inch) / width, (4.2 * inch) / height, 1)
            result.append(PdfImage(str(image_path), width=width * scale, height=height * scale))
            result.append(Paragraph(inline(alt), style["Caption"]))
            index += 1
        elif line.startswith("# "):
            flush()
            result.extend([Spacer(1, 0.4 * inch), Paragraph(inline(line[2:]), style["ReportTitle"])])
            index += 1
        elif line.startswith("## ") or line.startswith("### "):
            flush()
            level = "H2Report" if line.startswith("## ") else "H3Report"
            offset = 3 if level == "H2Report" else 4
            result.append(Paragraph(inline(line[offset:]), style[level]))
            index += 1
        elif line.strip() == "---":
            flush()
            result.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#bcccdc"), spaceBefore=5, spaceAfter=7))
            index += 1
        elif line.startswith("|"):
            flush()
            table_lines = []
            while index < len(lines) and lines[index].startswith("|"):
                table_lines.append(lines[index])
                index += 1
            result.extend([parse_table(table_lines, style), Spacer(1, 5)])
        elif line.startswith("- ") or line.startswith("* "):
            flush()
            items = []
            while index < len(lines) and (lines[index].startswith("- ") or lines[index].startswith("* ")):
                items.append(ListItem(Paragraph(inline(lines[index][2:]), style["BodyReport"])))
                index += 1
            result.extend([ListFlowable(items, bulletType="bullet", leftIndent=17), Spacer(1, 3)])
        else:
            paragraph.append(line)
            index += 1
    flush()
    return result


def page_decor(canvas: Any, document: Any) -> None:
    canvas.saveState()
    width, height = letter
    canvas.setStrokeColor(colors.HexColor("#d9e2ec"))
    canvas.line(0.65 * inch, height - 0.48 * inch, width - 0.65 * inch, height - 0.48 * inch)
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(colors.HexColor("#627d98"))
    canvas.drawString(0.65 * inch, 0.32 * inch, "Cypher Circle - SJSU CS157C")
    canvas.drawRightString(width - 0.65 * inch, 0.32 * inch, f"Page {document.page}")
    canvas.restoreState()


def build_pdf() -> None:
    document = BaseDocTemplate(
        str(PDF), pagesize=letter, rightMargin=0.65 * inch, leftMargin=0.65 * inch,
        topMargin=0.65 * inch, bottomMargin=0.55 * inch, title="Cypher Circle",
        author="Cypher Circle team",
    )
    frame = Frame(document.leftMargin, document.bottomMargin, document.width, document.height, id="normal")
    document.addPageTemplates([PageTemplate(id="report", frames=[frame], onPage=page_decor)])

    def deterministic_canvas(filename: str, **kwargs: Any) -> Canvas:
        # ReportLab otherwise writes the current time into CreationDate and
        # ModDate, making an unchanged report produce a different archive.
        kwargs["invariant"] = 1
        return Canvas(filename, **kwargs)

    document.build(
        report_flowables(MARKDOWN.read_text(encoding="utf-8"), report_styles()),
        canvasmaker=deterministic_canvas,
    )


def main() -> int:
    data = load_log()
    tests = unit_result()
    counts, live_note = live_counts()
    build_diagrams()
    MARKDOWN.write_text(render(data, tests, counts, live_note), encoding="utf-8")
    build_pdf()
    print(f"Built {PDF}")
    print(f"Processed counts: users={data['users']} follows={data['edges_final']} circles={data['circles']} memberships={data['memberships']}")
    print(live_note)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"report build failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
