from __future__ import annotations

import importlib.metadata
import sys

import bcrypt
import neo4j
import pytest
import rich
import dotenv
import reportlab
import flask
from PIL import Image
from faker import Faker
from pyvis.network import Network

try:
    from scripts.seed_constants import SEED, SEEDED_PASSWORD, SEEDED_PASSWORD_HASH
except ModuleNotFoundError:  # Direct execution: python scripts/verify_env.py
    from seed_constants import SEED, SEEDED_PASSWORD, SEEDED_PASSWORD_HASH


EXPECTED = {
    "neo4j": "5.26.0",
    "bcrypt": "5.0.0",
    "Faker": "40.54.0",
    "rich": "15.0.0",
    "python-dotenv": "1.2.4",
    "pyvis": "0.3.2",
    "pytest": "9.2.0",
    "reportlab": "4.4.5",
    "Pillow": "11.3.0",
    "charset-normalizer": "3.5.2",
    "Flask": "3.1.2",
}


def main() -> int:
    if sys.version_info[:2] != (3, 13):
        raise SystemExit("Python 3.13.x is required for this project")
    for package, expected in EXPECTED.items():
        actual = importlib.metadata.version(package)
        if actual != expected:
            raise SystemExit(f"{package}=={expected} required; found {actual}")
    # Import each declared runtime/test dependency as well as checking metadata.
    _ = (neo4j, pytest, rich, dotenv, Network, reportlab, Image, flask)
    if not bcrypt.checkpw(SEEDED_PASSWORD.encode("utf-8"), SEEDED_PASSWORD_HASH.encode("ascii")):
        raise SystemExit("seeded bcrypt hash does not verify")
    Faker.seed(SEED)
    first = Faker("en_US").first_name()
    Faker.seed(SEED)
    second = Faker("en_US").first_name()
    if first != second:
        raise SystemExit("Faker determinism check failed")
    print(f"Python {sys.version.split()[0]} and pinned dependencies verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

