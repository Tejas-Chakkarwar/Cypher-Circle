from __future__ import annotations

import pytest

import cyphercircle.config as config
from cyphercircle.db import LoggedQuery, QueryLog, _redact


def _without_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "load_dotenv", lambda *_args, **_kwargs: False)
    for name in ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD"):
        monkeypatch.delenv(name, raising=False)


def test_missing_neo4j_settings_are_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    _without_dotenv(monkeypatch)

    with pytest.raises(ValueError, match="NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD"):
        config.load_settings()


@pytest.mark.parametrize(
    ("uri", "message"),
    [
        ("http://localhost:7687", "Bolt/neo4j URI scheme"),
        ("bolt://example.com:7687", "localhost"),
        ("bolt://", "include a host"),
        ("bolt://localhost:notaport", "invalid NEO4J_URI port"),
        ("bolt://localhost:0", "port must be greater than zero"),
    ],
)
def test_invalid_neo4j_uri_is_rejected(
    monkeypatch: pytest.MonkeyPatch, uri: str, message: str
) -> None:
    _without_dotenv(monkeypatch)
    monkeypatch.setenv("NEO4J_URI", uri)
    monkeypatch.setenv("NEO4J_USER", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "secret")

    with pytest.raises(ValueError, match=message):
        config.load_settings()


def test_query_log_records_and_redacts_nested_passwords() -> None:
    log = QueryLog()
    log.begin_action("TEST")
    log.record(
        LoggedQuery(
            "EXAMPLE",
            "RETURN 1",
            _redact({"rows": [{"password_hash": "secret", "name": "Alice"}]}),
            1,
        )
    )

    entry = log.entries()[0]
    assert entry.params == {"rows": [{"password_hash": "***", "name": "Alice"}]}
    log.begin_action("NEXT")
    assert log.entries() == []


def test_web_settings_have_local_safe_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _without_dotenv(monkeypatch)
    for name in ("WEB_HOST", "WEB_PORT", "WEB_SECRET_KEY", "WEB_DEBUG"):
        monkeypatch.delenv(name, raising=False)

    settings = config.load_web_settings()

    assert settings.host == "127.0.0.1"
    assert settings.port == 5000
    assert settings.secret_key
    assert settings.debug is False


def test_invalid_web_port_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _without_dotenv(monkeypatch)
    monkeypatch.setenv("WEB_PORT", "not-a-port")

    with pytest.raises(ValueError, match="WEB_PORT"):
        config.load_web_settings()
