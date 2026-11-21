from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path

from faker import Faker

try:
    from scripts.seed_constants import SEED, SEEDED_PASSWORD_HASH, TWITTER_ARCHIVE_SHA256
except ModuleNotFoundError:  # Direct execution: python scripts/preprocess.py
    from seed_constants import SEED, SEEDED_PASSWORD_HASH, TWITTER_ARCHIVE_SHA256


USERNAME_RE = re.compile(r"^[a-z0-9._]{3,30}$")
START = datetime(2022, 1, 1, tzinfo=UTC)
END = datetime(2025, 12, 31, 23, 59, 59, tzinfo=UTC)


def parse_edges(path: Path) -> list[tuple[int, int]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        tokens = line.split()
        if len(tokens) != 2:
            raise ValueError(f"edge row must contain exactly two IDs: {line!r}")
        left, right = tokens
        rows.append((int(left), int(right)))
    return rows


def clean_edges(raw_edges: list[tuple[int, int]], users: set[int]):
    self_loops = sum(src == dst for src, dst in raw_edges)
    without_self = [(src, dst) for src, dst in raw_edges if src != dst]
    dangling = sum(src not in users or dst not in users for src, dst in without_self)
    without_dangling = [
        (src, dst)
        for src, dst in without_self
        if src in users and dst in users
    ]
    seen: set[tuple[int, int]] = set()
    cleaned = []
    for edge in without_dangling:
        if edge not in seen:
            seen.add(edge)
            cleaned.append(edge)
    duplicates = len(without_dangling) - len(cleaned)
    return sorted(cleaned), self_loops, duplicates, dangling


def timestamp(rng: random.Random, lower: datetime, upper: datetime) -> str:
    seconds = int((upper - lower).total_seconds())
    return (lower + timedelta(seconds=rng.randint(0, seconds))).isoformat().replace(
        "+00:00", "Z"
    )


def clean_part(value: str) -> str:
    return re.sub(r"[^a-z]", "", value.lower())


def build_username(first: str, last: str, uid: int) -> str:
    first_clean = clean_part(unicodedata.normalize("NFKD", first))[:12] or "user"
    last_clean = clean_part(unicodedata.normalize("NFKD", last))
    initial = last_clean[:1] or "x"
    username = f"{first_clean}.{initial}_{uid:04d}"
    if not USERNAME_RE.fullmatch(username):
        raise ValueError(f"generated invalid username: {username}")
    return username


def recommendation_count(me: int, outgoing: dict[int, set[int]]) -> int:
    followed = outgoing.get(me, set())
    candidates: set[int] = set()
    for middle in followed:
        candidates.update(outgoing.get(middle, set()))
    candidates.discard(me)
    candidates.difference_update(followed)
    return len(candidates)


def choose_demo_users(
    source_ids: list[int],
    usernames: dict[int, str],
    edges: list[tuple[int, int]],
    memberships: dict[int, set[str]],
):
    outgoing: dict[int, set[int]] = defaultdict(set)
    incoming: Counter[int] = Counter()
    for src, dst in edges:
        outgoing[src].add(dst)
        incoming[dst] += 1

    def candidates(min_asymmetry: float):
        rows = []
        for user in source_ids:
            following = len(outgoing[user])
            followers = incoming[user]
            asymmetry = abs(following - followers) / max(following, followers) if max(following, followers) else 0
            if following >= 10 and followers >= 10 and asymmetry >= min_asymmetry and recommendation_count(user, outgoing):
                rows.append((user, asymmetry))
        return rows

    rule = "0.20"
    busy_candidates = candidates(0.20)
    if not busy_candidates:
        rule = "0.10"
        busy_candidates = candidates(0.10)
    if busy_candidates:
        busy = min(
            busy_candidates,
            key=lambda row: (-incoming[row[0]], -row[1], usernames[row[0]]),
        )[0]
    else:
        rule = "top_followers"
        fallback = [user for user in source_ids if recommendation_count(user, outgoing)]
        if not fallback:
            raise ValueError("no user has a valid network recommendation")
        busy = min(fallback, key=lambda user: (-incoming[user], usernames[user]))

    top_users = sorted(source_ids, key=lambda user: (-incoming[user], usernames[user]))[:20]
    top_users = [user for user in top_users if user != busy]
    if top_users:
        mutual_partner = sorted(
            top_users,
            key=lambda user: (-len(outgoing[busy] & outgoing[user]), usernames[user]),
        )[0]
    else:
        mutual_partner = None

    no_circle = next(
        (
            user
            for user in sorted(source_ids)
            if not memberships.get(user) and len(outgoing[user]) >= 5
        ),
        None,
    )
    return {
        "busy_user": usernames[busy],
        "busy_user_rule": rule,
        "busy_user_network_recommendations": recommendation_count(busy, outgoing),
        "mutual_partner": usernames[mutual_partner] if mutual_partner is not None else None,
        "no_circle_user": usernames[no_circle] if no_circle is not None else None,
    }


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def preprocess(
    raw_dir: Path,
    processed_dir: Path,
    target_users: int,
    min_edges: int,
    *,
    enforce_assignment_minimums: bool = True,
    expected_archive_sha256: str | None = None,
) -> dict:
    if enforce_assignment_minimums and (target_users < 1000 or min_edges < 5000):
        raise ValueError(
            "production preprocessing thresholds cannot be below 1000 users and 5000 edges"
        )
    twitter_dir = raw_dir / "twitter"
    archive = raw_dir / "twitter.tar.gz"
    if expected_archive_sha256 is not None:
        archive_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if archive_digest != expected_archive_sha256:
            raise ValueError(
                f"archive SHA-256 mismatch: expected {expected_archive_sha256}, got {archive_digest}"
            )
    edge_files = sorted(
        twitter_dir.glob("*.edges"), key=lambda path: int(path.stem)
    )
    if not edge_files:
        raise ValueError(f"no .edges files found in {twitter_dir}")

    selected_egos: list[int] = []
    selected_users: set[int] = set()
    raw_edges: list[tuple[int, int]] = []
    ego_edges_added = 0
    final_edges: list[tuple[int, int]] = []
    cleaning_counts = (0, 0, 0)
    for edge_file in edge_files:
        ego = int(edge_file.stem)
        file_edges = parse_edges(edge_file)
        nodes = {node for edge in file_edges for node in edge}
        selected_egos.append(ego)
        selected_users.update(nodes)
        selected_users.add(ego)
        raw_edges.extend(file_edges)
        ego_edges = [(ego, node) for node in sorted(nodes) if node != ego]
        raw_edges.extend(ego_edges)
        ego_edges_added += len(ego_edges)
        final_edges, *cleaning_counts = clean_edges(raw_edges, selected_users)
        if len(selected_users) >= target_users and len(final_edges) >= min_edges:
            break
    if len(selected_users) < target_users or len(final_edges) < min_edges:
        raise ValueError("all ego networks were used before both thresholds were met")
    if enforce_assignment_minimums and (
        len(selected_users) < 1000 or len(final_edges) < 5000
    ):
        raise ValueError("processed dataset is below the assignment minimums")

    source_ids = sorted(selected_users)
    uid_by_source = {source_id: uid for uid, source_id in enumerate(source_ids)}
    fake = Faker("en_US")
    Faker.seed(SEED)
    fake.seed_instance(SEED)
    rng = random.Random(SEED)
    users = []
    username_by_source: dict[int, str] = {}
    joined_by_source: dict[int, str] = {}
    for uid, source_id in enumerate(source_ids):
        first = fake.first_name()
        last = fake.last_name()
        username = build_username(first, last, uid)
        username_by_source[source_id] = username
        joined = timestamp(rng, START, END)
        joined_by_source[source_id] = joined
        users.append(
            {
                "source_id": source_id,
                "uid": uid,
                "username": username,
                "email": f"{username}@example.com",
                "name": f"{first} {last}",
                "name_lc": f"{first} {last}".lower(),
                "password_hash": SEEDED_PASSWORD_HASH,
                "bio": fake.sentence(nb_words=10)[:160],
                "location": fake.city(),
                "joined_at": joined,
                "source": "snap",
            }
        )

    follows = []
    for src, dst in final_edges:
        lower = max(
            datetime.fromisoformat(joined_by_source[src].replace("Z", "+00:00")),
            datetime.fromisoformat(joined_by_source[dst].replace("Z", "+00:00")),
        )
        follows.append(
            {
                "src": src,
                "dst": dst,
                "since": timestamp(rng, lower, END),
            }
        )

    circles = []
    memberships = []
    circles_raw = 0
    circles_dropped_small = 0
    membership_sets: dict[int, set[str]] = defaultdict(set)
    for ego in selected_egos:
        path = twitter_dir / f"{ego}.circles"
        if not path.exists():
            continue
        for line_index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
            if not line.strip():
                continue
            circles_raw += 1
            tokens = line.split()
            source_label = tokens[0]
            members = sorted({int(token) for token in tokens[1:] if int(token) in selected_users})
            if len(members) < 3:
                circles_dropped_small += 1
                continue
            circle_id = f"{ego}:{line_index}"
            circles.append(
                {
                    "circle_id": circle_id,
                    "name": f"@{username_by_source[ego]}'s list {line_index + 1}",
                    "source_label": source_label,
                    "owner_source_id": ego,
                }
            )
            for source_id in members:
                memberships.append({"circle_id": circle_id, "source_id": source_id})
                membership_sets[source_id].add(circle_id)

    circles.sort(key=lambda row: row["circle_id"])
    memberships.sort(key=lambda row: (row["circle_id"], row["source_id"]))
    follows.sort(key=lambda row: (row["src"], row["dst"]))
    if len({row["username"] for row in users}) != len(users):
        raise ValueError("generated usernames are not unique")
    if len({row["name"] for row in circles}) != len(circles):
        raise ValueError("generated circle names are not unique")

    prefixes = sorted(
        {
            row["name"].split(" ", 1)[0].lower()[:3]
            for row in users
            if len(row["name"].split(" ", 1)[0]) >= 3
        }
    )
    prefix_counts = {
        prefix: sum(
            prefix in row["name_lc"] or prefix in row["username"] for row in users
        )
        for prefix in prefixes
    }
    demo_search_term = max(prefixes, key=lambda prefix: (-prefix_counts[prefix], prefix))
    demo_search_matches = prefix_counts[demo_search_term]
    demo_users = choose_demo_users(
        source_ids,
        username_by_source,
        final_edges,
        membership_sets,
    )

    processed_dir.mkdir(parents=True, exist_ok=True)
    write_csv(processed_dir / "users.csv", list(users[0]), users)
    write_csv(processed_dir / "follows.csv", ["src", "dst", "since"], follows)
    write_csv(processed_dir / "circles.csv", ["circle_id", "name", "source_label", "owner_source_id"], circles)
    write_csv(processed_dir / "memberships.csv", ["circle_id", "source_id"], memberships)
    archive_sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()
    processed_files = {
        filename: hashlib.sha256((processed_dir / filename).read_bytes()).hexdigest()
        for filename in ("users.csv", "follows.csv", "circles.csv", "memberships.csv")
    }
    log = {
        "archive_sha256": archive_sha256,
        "egos_total": len(edge_files),
        "egos_selected": len(selected_egos),
        "users": len(users),
        "edges_from_ego_files": len(raw_edges) - ego_edges_added,
        "ego_edges_added": ego_edges_added,
        "self_loops_dropped": cleaning_counts[0],
        "duplicates_dropped": cleaning_counts[1],
        "dangling_dropped": cleaning_counts[2],
        "edges_final": len(follows),
        "circles_raw": circles_raw,
        "circles_dropped_small": circles_dropped_small,
        "circles": len(circles),
        "memberships": len(memberships),
        "users_without_circle": sum(not membership_sets.get(user) for user in source_ids),
        "demo_search_term": demo_search_term,
        "demo_search_matches": demo_search_matches,
        "demo_users": demo_users,
        "faker_version": version("Faker"),
        "seed": SEED,
        "processed_files": processed_files,
    }
    (processed_dir / "preprocess_log.json").write_text(
        json.dumps(log, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return log


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--processed-dir", type=Path, default=Path("data/processed"))
    parser.add_argument("--target-users", type=int, default=3000)
    parser.add_argument("--min-edges", type=int, default=5000)
    args = parser.parse_args()
    log = preprocess(
        args.raw_dir,
        args.processed_dir,
        args.target_users,
        args.min_edges,
        expected_archive_sha256=TWITTER_ARCHIVE_SHA256,
    )
    print(json.dumps({key: log[key] for key in ("users", "edges_final", "circles", "memberships", "demo_users")}, indent=2))


if __name__ == "__main__":
    main()

