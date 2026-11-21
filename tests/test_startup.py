from __future__ import annotations

import pytest

from cyphercircle.errors import StartupDataError
from cyphercircle.models import GraphStats
from cyphercircle.startup_service import StartupService


class FakeDB:
    def __init__(self, counts: dict[str, list[dict]]) -> None:
        self.counts = counts

    def read(self, name: str, _query: str, **_params):
        return self.counts.get(name, []), None


def test_startup_rejects_empty_database() -> None:
    with pytest.raises(StartupDataError, match="Database is empty"):
        StartupService(FakeDB({"COUNT_ALL_NODES": [{"nodes": 0}]})).verify_ready()


@pytest.mark.parametrize(
    "counts",
    [
        {"COUNT_ALL_NODES": [{"nodes": 5}], "VERIFY_COUNTS": [{"users": 0, "follows": 2, "circles": 1}]},
        {"COUNT_ALL_NODES": [{"nodes": 5}], "VERIFY_COUNTS": [{"users": 2, "follows": 0, "circles": 1}]},
    ],
)
def test_startup_rejects_partially_loaded_database(counts: dict[str, list[dict]]) -> None:
    with pytest.raises(StartupDataError, match="partially loaded"):
        StartupService(FakeDB(counts)).verify_ready()


def test_startup_returns_and_refreshes_graph_stats() -> None:
    db = FakeDB(
        {
            "COUNT_ALL_NODES": [{"nodes": 10}],
            "VERIFY_COUNTS": [{"users": 3, "follows": 7, "circles": 2}],
        }
    )
    service = StartupService(db)
    assert service.verify_ready() == GraphStats(users=3, follows=7, circles=2)
    assert service.current_stats() == GraphStats(users=3, follows=7, circles=2)
