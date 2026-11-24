from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import build_report, package_project, verify_report


def _log_payload() -> dict[str, object]:
    return {
        "archive_sha256": "a" * 64,
        "users": 4,
        "edges_final": 5,
        "circles": 1,
        "memberships": 3,
        "egos_total": 3,
        "egos_selected": 2,
        "edges_from_ego_files": 4,
        "ego_edges_added": 3,
        "self_loops_dropped": 1,
        "duplicates_dropped": 1,
        "dangling_dropped": 0,
        "circles_raw": 2,
        "circles_dropped_small": 1,
        "users_without_circle": 1,
        "demo_search_term": "zoe",
        "demo_search_matches": 1,
        "demo_users": {
            "busy_user": "busy.user",
            "busy_user_rule": "0.20",
            "busy_user_network_recommendations": 1,
            "mutual_partner": "mutual.user",
            "no_circle_user": "no.circle",
        },
        "faker_version": "40.54.0",
        "seed": 157,
        "processed_files": {
            name: "b" * 64
            for name in ("users.csv", "follows.csv", "circles.csv", "memberships.csv")
        },
    }


def test_report_data_extraction_reads_log_values(tmp_path: Path) -> None:
    path = tmp_path / "preprocess_log.json"
    path.write_text(json.dumps(_log_payload()), encoding="utf-8")

    loaded = build_report.load_log(path)

    assert loaded["users"] == 4
    assert loaded["edges_final"] == 5
    assert loaded["demo_users"]["busy_user"] == "busy.user"  # type: ignore[index]


def test_missing_preprocessing_log_fails_clearly(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="required preprocessing log is missing"):
        build_report.load_log(tmp_path / "missing.json")


def test_invalid_preprocessing_log_fails_with_missing_fields(tmp_path: Path) -> None:
    path = tmp_path / "preprocess_log.json"
    path.write_text("{}", encoding="utf-8")

    with pytest.raises(RuntimeError, match="preprocessing log is missing fields"):
        build_report.load_log(path)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("users", "4", "users.*non-negative integer"),
        ("archive_sha256", "not-a-hash", "archive_sha256"),
        ("processed_files", {"users.csv": "bad"}, "processed_files"),
    ],
)
def test_invalid_preprocessing_log_rejects_bad_types_and_hashes(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    payload = _log_payload()
    payload[field] = value
    path = tmp_path / "preprocess_log.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeError, match=message):
        build_report.load_log(path)


def test_invalid_preprocessing_log_requires_all_demo_user_fields(tmp_path: Path) -> None:
    payload = _log_payload()
    del payload["demo_users"]["mutual_partner"]  # type: ignore[index]
    path = tmp_path / "preprocess_log.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeError, match="demo_users is missing fields: mutual_partner"):
        build_report.load_log(path)


def test_image_font_has_cross_platform_candidates_and_readable_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert any("Arial" in str(path) for path in build_report.FONT_CANDIDATES[False])
    assert any("DejaVuSans" in str(path) for path in build_report.FONT_CANDIDATES[False])

    monkeypatch.setitem(build_report.FONT_CANDIDATES, False, ())
    build_report.image_font.cache_clear()
    try:
        font = build_report.image_font(24)
        assert font.getbbox("Readable text")[2] > 0
    finally:
        build_report.image_font.cache_clear()


def test_missing_report_asset_fails_clearly() -> None:
    with pytest.raises(RuntimeError, match="required report image is missing"):
        build_report.report_flowables(
            "![missing](diagrams/does-not-exist.png)", build_report.report_styles()
        )


def test_report_rejects_absolute_and_traversal_image_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    report_dir = tmp_path / "report"
    report_dir.mkdir()
    monkeypatch.setattr(build_report, "REPORT", report_dir)

    with pytest.raises(RuntimeError, match="must be relative"):
        build_report.report_flowables("![escape](/tmp/secret.png)", build_report.report_styles())
    with pytest.raises(RuntimeError, match="escapes docs/report"):
        build_report.report_flowables("![escape](../secret.png)", build_report.report_styles())


def test_report_rejects_symlink_image(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    report_dir = tmp_path / "report"
    report_dir.mkdir()
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"not an image")
    link = report_dir / "secret.png"
    link.symlink_to(outside)
    monkeypatch.setattr(build_report, "REPORT", report_dir)

    with pytest.raises(RuntimeError, match="symlink report images"):
        build_report.report_flowables("![escape](secret.png)", build_report.report_styles())


def test_report_rejects_symlink_report_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_report = tmp_path / "real-report"
    real_report.mkdir()
    report_link = tmp_path / "report"
    report_link.symlink_to(real_report, target_is_directory=True)
    monkeypatch.setattr(build_report, "REPORT", report_link)

    with pytest.raises(RuntimeError, match="symlink report directories"):
        build_report.report_flowables("![escape](missing.png)", build_report.report_styles())


def test_malformed_report_code_block_fails_clearly() -> None:
    with pytest.raises(RuntimeError, match="unterminated report code block"):
        build_report.report_flowables("```\nSELECT 1", build_report.report_styles())


def test_report_render_replaces_values_without_placeholders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    template = tmp_path / "report.template.md"
    template.write_text("Users: {{PROCESSED_COUNTS}}\n{{UNIT_RESULT}}", encoding="utf-8")
    monkeypatch.setattr(build_report, "TEMPLATE", template)

    rendered = build_report.render(_log_payload(), "3 passed", None, "Neo4j unavailable")

    assert "4" in rendered
    assert "3 passed" in rendered
    assert "{{" not in rendered


def test_pdf_verification_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="missing or empty"):
        verify_report.verify(tmp_path / "missing.pdf")


def test_package_requires_report_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(package_project, "PDF", tmp_path / "missing.pdf")

    with pytest.raises(RuntimeError, match="docs/report/report.pdf is missing"):
        package_project.verify_pdf()


def test_package_rejects_forbidden_paths() -> None:
    with pytest.raises(RuntimeError, match="forbidden path"):
        package_project.validate_names(["source_code/.env"])
    with pytest.raises(RuntimeError, match="forbidden path"):
        package_project.validate_names(["source_code/output/network_alice.html"])
    with pytest.raises(RuntimeError, match="credential or generated log"):
        package_project.validate_names(["source_code/cyphercircle.log"])


def _fake_package_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "package"
    root.mkdir()
    for name in package_project.TOP_LEVEL:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("placeholder", encoding="utf-8")
    for relative in (
        "cyphercircle/cli.py", "scripts/preprocess.py", "tests/test_cli.py", "tests/test_report.py",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("placeholder", encoding="utf-8")
    (root / "cyphercircle/web_templates").mkdir(parents=True)
    (root / "cyphercircle/web_templates/base.html").write_text("template", encoding="utf-8")
    (root / "cyphercircle/web_static").mkdir(parents=True)
    (root / "cyphercircle/web_static/style.css").write_text("style", encoding="utf-8")
    report_dir = root / "docs" / "report"
    (report_dir / "diagrams").mkdir(parents=True)
    (report_dir / "report.md").write_text("report", encoding="utf-8")
    (report_dir / "report.template.md").write_text("template", encoding="utf-8")
    for name in ("schema.png", "architecture.png", "pipeline.png", "use_cases.png"):
        (report_dir / "diagrams" / name).write_bytes(b"png")
    monkeypatch.setattr(package_project, "ROOT", root)
    monkeypatch.setattr(package_project, "REPORT_DIR", report_dir)
    monkeypatch.setattr(package_project, "PDF", report_dir / "report.pdf")


def test_package_sources_are_allowlisted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_package_tree(tmp_path, monkeypatch)
    entries = package_project.sources()
    names = [name for _, name in entries]
    package_project.validate_names(names)

    assert "source_code/docs/report/report.md" in names
    assert "source_code/docs/report/report.template.md" in names
    assert "source_code/docs/report/diagrams/schema.png" in names
    assert "source_code/cyphercircle/web_templates/base.html" in names
    assert "source_code/cyphercircle/web_static/style.css" in names
    assert "source_code/docs/PROJECT_DESIGN.pdf" in names


def test_archive_creation_and_contents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_package_tree(tmp_path, monkeypatch)
    archive_path = tmp_path / "project.zip"
    package_project.PDF.write_bytes(b"placeholder pdf")
    monkeypatch.setattr(package_project, "ARCHIVE", archive_path)
    monkeypatch.setattr(package_project, "verify_pdf", lambda: None)

    assert package_project.build_archive() == 0

    import zipfile

    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
    assert "report.pdf" in names
    assert "source_code/cyphercircle/cli.py" in names
    assert "source_code/tests/test_report.py" in names
    assert not any(
        name.startswith(("source_code/data/", "source_code/.venv/", "source_code/output/"))
        for name in names
    )

    first = archive_path.read_bytes()
    assert package_project.build_archive() == 0
    assert archive_path.read_bytes() == first


def test_package_rejects_symlink_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = tmp_path / "outside.txt"
    target.write_text("secret", encoding="utf-8")
    link = root / "linked.txt"
    link.symlink_to(target)
    monkeypatch.setattr(package_project, "ROOT", root)

    with pytest.raises(RuntimeError, match="symlink source"):
        package_project.validate_source_paths([(link, "source_code/linked.txt")])


def test_package_rejects_paths_outside_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    monkeypatch.setattr(package_project, "ROOT", root)

    with pytest.raises(RuntimeError, match="inside the repository"):
        package_project.validate_source_paths([(outside, "source_code/outside.txt")])
