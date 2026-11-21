from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    uri: str
    user: str
    password: str


@dataclass(frozen=True)
class WebSettings:
    host: str
    port: int
    secret_key: str
    debug: bool = False


def load_web_settings() -> WebSettings:
    """Load web-server settings without exposing database credentials."""

    load_dotenv(ROOT / ".env")
    host = (os.getenv("WEB_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    raw_port = (os.getenv("WEB_PORT") or "5000").strip()
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError("WEB_PORT must be an integer between 1 and 65535") from exc
    if not 1 <= port <= 65535:
        raise ValueError("WEB_PORT must be an integer between 1 and 65535")
    debug = (os.getenv("WEB_DEBUG") or "false").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    # A random process-local fallback is safer than a committed development
    # secret. Set WEB_SECRET_KEY for sessions that should survive restarts.
    secret_key = os.getenv("WEB_SECRET_KEY") or secrets.token_urlsafe(32)
    return WebSettings(host=host, port=port, secret_key=secret_key, debug=debug)


def load_settings() -> Settings:
    load_dotenv(ROOT / ".env")
    values = {
        "NEO4J_URI": os.getenv("NEO4J_URI"),
        "NEO4J_USER": os.getenv("NEO4J_USER"),
        "NEO4J_PASSWORD": os.getenv("NEO4J_PASSWORD"),
    }
    missing = [key for key, value in values.items() if not value]
    if missing:
        raise ValueError(
            "required Neo4j settings are missing: "
            + ", ".join(missing)
            + "; copy .env.example to .env and set them"
        )
    try:
        parsed_uri = urlparse(values["NEO4J_URI"])
    except ValueError as exc:
        raise ValueError(f"invalid NEO4J_URI: {exc}") from exc
    if parsed_uri.scheme not in {"bolt", "bolt+s", "neo4j", "neo4j+s"}:
        raise ValueError("NEO4J_URI must use a Neo4j Bolt/neo4j URI scheme")
    if parsed_uri.hostname is None:
        raise ValueError("NEO4J_URI must include a host")
    try:
        port = parsed_uri.port
    except ValueError as exc:
        raise ValueError(f"invalid NEO4J_URI port: {exc}") from exc
    if port == 0:
        raise ValueError("NEO4J_URI port must be greater than zero")
    if parsed_uri.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("NEO4J_URI must point to localhost for this local project")
    return Settings(
        uri=values["NEO4J_URI"],
        user=values["NEO4J_USER"],
        password=values["NEO4J_PASSWORD"],
    )

