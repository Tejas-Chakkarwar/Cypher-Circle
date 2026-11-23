# Cypher Circle

[![CI](https://github.com/Tejas-Chakkarwar/Cypher-Circle/actions/workflows/ci.yml/badge.svg)](https://github.com/Tejas-Chakkarwar/Cypher-Circle/actions/workflows/ci.yml)
[![Docker integration](https://github.com/Tejas-Chakkarwar/Cypher-Circle/actions/workflows/docker-integration.yml/badge.svg)](https://github.com/Tejas-Chakkarwar/Cypher-Circle/actions/workflows/docker-integration.yml)

See [docs/PROJECT_DESIGN.pdf](docs/PROJECT_DESIGN.pdf) for the project design document covering
the architecture, data pipeline, graph model, use cases, security behavior,
testing, and setup.

Cypher Circle is a complete Python 3.13 application backed by Neo4j, with a
first-class Rich console, a polished Flask browser interface, a deterministic
SNAP ego-Twitter data pipeline, offline network visualization, and reproducible
report/package tooling. Both interfaces implement all 13 required use cases
below, plus an optional offline network visualization.

## Use cases

| UC | Use case | Console menu | Web page |
|---|---|---|---|
| UC-1 | User registration | Start menu `2` | `/register` |
| UC-2 | User login | Start menu `1` | `/login` |
| UC-3 | View profile | `1` | `/profile` |
| UC-4 | Edit profile (name, email, bio; username fixed) | `2` → `1` | `/profile/edit` |
| UC-5 | Follow another user | `3`, or pick from `7`, `8`, `9`, `10` | Follow buttons |
| UC-6 | Unfollow a user | `4`, or pick from `8`, `9` | Following buttons |
| UC-7 | View following and followers | `5` | `/connections` |
| UC-8 | Mutual connections of two users | `6` | `/mutual` |
| UC-9 | Friend recommendations (exactly two hops) | `7` | `/recommendations` |
| UC-10 | View all users | `8` | `/users` |
| UC-11 | Search users by name or username | `9` | `/search` |
| UC-12 | Explore popular users | `10` | `/popular` |
| UC-13 | Degrees of separation (shortest directed path, ≤4 hops) | `11` | `/separation` |

Every Cypher query is a named constant in `cyphercircle/queries.py`. In the
console, press `C` to toggle Show Cypher and see the queries each action ran.

## Local setup

Use Python 3.13 in the project-local environment only:

```sh
python3.13 -m venv .venv
source .venv/bin/activate
python --version
pip install -r requirements.txt
cp .env.example .env
```

Start Neo4j 5.26.0 Community and run the vertical slice. `make up` creates a
local, gitignored `.env` from `.env.example` if needed; the Compose ports are
bound to localhost only:

```sh
make up
make verify-env
make smoke-check
make test
make data
make load
make test-integration
make slice-check
make run
```

`make load` intentionally refuses a non-empty database. To deliberately clear
the configured database and reload the processed dataset, use `make reset`.
This passes `--reset` to the loader; run it only against a database that is
safe to erase.

All Makefile targets use `.venv/bin/python` by default. Override `PYTHON` only
when intentionally running a compatible toolchain, for example
`make PYTHON=python3.13 test` or `make PYTHON=python3.13 report`. The report
wrapper and packaging target use the selected interpreter consistently.

`make data` keeps the original archive at `data/raw/twitter.tar.gz`, safely
extracts it under `data/raw/twitter/`, and writes deterministic CSV output to
`data/processed/`. Raw and processed data, credentials, and the local virtual
environment are gitignored.

`make smoke-check` loads the local Neo4j settings, executes `RETURN 1` through
`Database.read()`, and confirms that the query was recorded in `QueryLog`.
Missing or invalid `NEO4J_*` settings fail with an actionable configuration
message.

Report and packaging commands use only local inputs:

```sh
make report
make zip
make demo-info
```

`make report` reads deterministic values from
`data/processed/preprocess_log.json`, captures live `VERIFY_COUNTS` when Neo4j
is available, builds local diagrams, writes `docs/report/report.pdf`, extracts its
text, and renders every page for verification. It requires the project
environment plus ReportLab, Pillow, and Poppler (`pdfinfo`, `pdftotext`, and
`pdftoppm`). If Neo4j or screenshots are unavailable, the report records that
fact instead of inventing evidence. Screenshots belong in
`docs/report/screenshots/` and remain a team-supplied submission asset.

`make zip` verifies the PDF and creates the reproducible `project.zip` archive
required for submission: `report.pdf` at the top level and a `source_code/`
directory with everything needed to run the application. It excludes raw and
processed data, `.env`, `.venv`, `output/`, caches, logs, and credentials.

The accepted SNAP archive SHA-256 is
`5ed7b52a8013a1a1d43301a5414dfcb844e50cb4e2fb8dc801a7bef399552bb0`.

The console application is started with `python -m cyphercircle` or `make run`.
It provides all 13 use cases listed above, pagination, an optional Show Cypher
view, and the optional `V` network visualization action. Visualization writes
`output/network_<username>.html`, opens it in the browser, and embeds all
resources so the generated directed graph works offline. The page includes a
legend for center, following-only, follower-only, and mutual nodes, plus
neighbor-to-neighbor edges.

To verify the generated page after running the visualization action, confirm
that the file exists and has no external resource attributes:

```sh
rg -n -i 'href="https?://|src="https?://' output/network_*.html
```

The command should produce no matches.

## Browser UI

The browser interface is an additional entry point; it does not replace the
CLI. Start Neo4j and load the graph first, then run:

```sh
make web
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000). The host and port come
from `WEB_HOST` and `WEB_PORT`; `WEB_SECRET_KEY` controls signed web sessions
and should be a private random value for a persistent local setup. `WEB_DEBUG`
defaults to `false` and is not enabled by `make web`.

The web UI supports all 13 use cases listed above, plus public profiles,
circle membership, and the offline network visualization. Mutations use POST plus CSRF tokens, and the browser
session stores only the signed username and display name. Passwords and
password hashes are never stored in the web session or rendered in templates.

Actor-scoped service calls receive the authenticated `Session` returned by
registration or login; callers do not provide an arbitrary username as the
acting user. Target usernames remain explicit for operations such as follow,
mutual connections, and search.

