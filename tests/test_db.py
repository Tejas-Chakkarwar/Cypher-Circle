from __future__ import annotations

import pytest

import cyphercircle.db as db_module
from cyphercircle.config import Settings
from cyphercircle.errors import DatabaseUnavailableError


class FailingDriver:
    def __init__(self) -> None:
        self.closed = False

    def verify_connectivity(self) -> None:
        raise OSError("connection refused")

    def close(self) -> None:
        self.closed = True


def test_database_closes_driver_when_connectivity_verification_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = FailingDriver()
    monkeypatch.setattr(db_module.GraphDatabase, "driver", lambda *_args, **_kwargs: driver)

    with pytest.raises(DatabaseUnavailableError, match="Neo4j is unavailable"):
        db_module.Database(Settings("bolt://localhost:7687", "neo4j", "secret"))

    assert driver.closed is True
