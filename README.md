# Cypher Circle

See [docs/PROJECT_DESIGN.pdf](docs/PROJECT_DESIGN.pdf) for the project design document covering
the architecture, data pipeline, graph model, use cases, security behavior,
testing, and setup.

Cypher Circle is a complete Python 3.13 application backed by Neo4j, with a
first-class Rich console, a polished Flask browser interface, a deterministic
SNAP ego-Twitter data pipeline, offline network visualization, and reproducible
report/package tooling. The loaded graph is exercised
through registration, login, profile editing, circles, connections, search,
recommendations, and directed network visualization.

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

`make zip` verifies the PDF and creates the reproducible `projects.zip` archive.
The archive includes the report and a `source/` tree, but excludes raw and
processed data, `.env`, `.venv`, `output/`, caches, logs, and credentials.

The accepted SNAP archive SHA-256 is
`5ed7b52a8013a1a1d43301a5414dfcb844e50cb4e2fb8dc801a7bef399552bb0`.

The console application is started with `python -m cyphercircle` or `make run`.
It provides registration, login, profiles, circles, connections,
recommendations, search, popular users, pagination, an optional Show Cypher
view, and the `V` network visualization action. Visualization writes
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

The web UI supports registration and login, profile editing, public profiles,
following and unfollowing, connections and mutual connections, search,
recommendations, popular users, circle membership, and the existing offline
network visualization. Mutations use POST plus CSRF tokens, and the browser
session stores only the signed username and display name. Passwords and
password hashes are never stored in the web session or rendered in templates.

Actor-scoped service calls receive the authenticated `Session` returned by
registration or login; callers do not provide an arbitrary username as the
acting user. Target usernames remain explicit for operations such as follow,
mutual connections, and search.

