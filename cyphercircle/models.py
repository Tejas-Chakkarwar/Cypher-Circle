from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class UserSummary:
    username: str
    name: str
    location: str | None
    followers: int


@dataclass(frozen=True)
class Profile:
    username: str
    name: str
    email: str
    bio: str | None
    location: str | None
    circles: list[str]
    circle_count: int
    joined_at: datetime
    following_count: int
    followers_count: int


@dataclass(frozen=True)
class Recommendation:
    """A user reached by an exactly two-hop follow path (UC-9).

    ``score`` is the number of distinct intermediate users; ``via`` lists up
    to three of them.
    """

    user: UserSummary
    score: int
    via: list[str]


@dataclass(frozen=True)
class Session:
    username: str
    name: str


@dataclass(frozen=True)
class GraphStats:
    users: int
    follows: int
    circles: int


@dataclass(frozen=True)
class CircleSummary:
    circle_id: str
    name: str
    members: int | None = None


@dataclass(frozen=True)
class Connection:
    username: str
    name: str
    location: str | None
    since: datetime
    follows_back: bool


@dataclass(frozen=True)
class ConnectionCounts:
    following: int
    followers: int


@dataclass(frozen=True)
class MutualConnection:
    username: str
    name: str
    location: str | None


@dataclass(frozen=True)
class RelationshipStatus:
    i_follow: bool
    they_follow: bool


@dataclass(frozen=True)
class MutualConnections:
    """Users followed by both ``first`` and ``second`` (UC-8).

    ``status.i_follow`` means ``first`` follows ``second``;
    ``status.they_follow`` means ``second`` follows ``first``.
    """

    total: int
    users: list[MutualConnection]
    status: RelationshipStatus
    first: str = ""
    second: str = ""


@dataclass(frozen=True)
class SearchResult:
    username: str
    name: str
    location: str | None
    followers: int
    i_follow: bool


@dataclass(frozen=True)
class NetworkNode:
    username: str
    name: str
    category: str


@dataclass(frozen=True)
class NetworkEdge:
    source: str
    target: str


@dataclass(frozen=True)
class EgoNetwork:
    nodes: list[NetworkNode]
    edges: list[NetworkEdge]

    @property
    def has_connections(self) -> bool:
        return len(self.nodes) > 1


@dataclass(frozen=True)
class DirectoryEntry:
    """One row of the all-users list (UC-10)."""

    username: str
    name: str
    location: str | None
    followers: int
    is_me: bool
    i_follow: bool


@dataclass(frozen=True)
class UserDirectory:
    total: int
    users: list[DirectoryEntry]


@dataclass(frozen=True)
class PathStep:
    username: str
    name: str


@dataclass(frozen=True)
class SeparationResult:
    """Shortest directed follow path from ``source`` to ``target`` (UC-13)."""

    source: str
    target: str
    max_hops: int
    path: list[PathStep]

    @property
    def found(self) -> bool:
        return bool(self.path)

    @property
    def degrees(self) -> int | None:
        return len(self.path) - 1 if self.path else None
