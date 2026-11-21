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
    user: UserSummary
    score: int
    via: list[str]
    reason: str
    circle: str | None


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
    total: int
    users: list[MutualConnection]
    status: RelationshipStatus


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
