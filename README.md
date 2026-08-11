# Chess Lab

[![CI](https://github.com/Roman2050/chess-lab/actions/workflows/ci.yml/badge.svg?branch=develop)](https://github.com/Roman2050/chess-lab/actions/workflows/ci.yml)

Bulk chess game analysis and opponent scouting with Stockfish-backed insights.

Chess Lab imports a player's game history, runs bounded Stockfish analysis,
aggregates recurring weaknesses, and generates a concise training report. Current
API/package version: **0.1.0**. Maintainer: [Roman](https://github.com/Roman2050).
The canonical repository is [Roman2050/chess-lab](https://github.com/Roman2050/chess-lab).

## Demo and API examples

After a local start, the interactive OpenAPI UI is available at
`http://localhost:8000/docs` and ReDoc at `http://localhost:8000/redoc`.
Start at `GET /` for public service links or `GET /api/v1/demo` to discover the
configured read-only demo player without knowing a nickname. The versioned API
exposes deterministic statistics through `GET /api/v1/games/stats/{player_name}`
and a cached, LLM-narrated report through `GET /api/v1/report/{username}`.

## How the pipeline works

```text
Lichess export or PGN upload
    -> PGN parsing and idempotent PostgreSQL ingestion
    -> bounded Celery fan-out
    -> Stockfish MultiPV=2 analysis
    -> deterministic player aggregations and insights
    -> cached LLM narrative report
```

Stockfish results are player-neutral and stored once per game. Reports derive
player-specific win-probability loss, phase, opening, and error signals from that
stored analysis; the LLM phrases those facts but does not calculate them.

## Architecture

| Concern | Implementation |
|---|---|
| HTTP API | FastAPI with async SQLAlchemy 2.0 sessions |
| Persistence | PostgreSQL 16 and Alembic migrations |
| Background work | Celery with Redis as broker |
| Chess analysis | `python-chess` and Stockfish with `MultiPV = 2` |
| Reports | OpenAI-compatible HTTP provider; local Ollama is the default |
| Safety | API-key access, Redis request quotas, bounded uploads and upstream responses |

The Lichess client is deployment-wide single-flight: Redis permits at most one
outbound export at a time and enforces a global cooldown after an upstream `429`.
Read paths avoid loading large PGN or analysis columns unless they use them. See
[ARCHITECTURE.md](ARCHITECTURE.md) for the detailed contracts and invariants.

## Features and engineering highlights

- Bounded Lichess import and manual PGN upload with stable, idempotent game IDs.
- Case-insensitive player lookup with database indexes for the supported filters.
- Claim-based, retryable Stockfish tasks that do not hold a database session while
  the engine is running.
- Weighted ACPL plus live-position win-probability loss, phase accuracy, opening
  performance, and tactical error summaries.
- Cached report generation with an atomic claim and stale-generation lease.
- Fail-closed Redis quotas and readiness reporting for expensive write operations.
- Secret-safe Lichess lifecycle events and an automated outbound-client boundary
  check.

## Local quick start

Prerequisites are Docker, [uv](https://docs.astral.sh/uv/), and Python 3.12.
Stockfish is required to execute analysis tasks; an OpenAI-compatible service is
required only to generate reports.

1. Copy [.env.example](.env.example) to `.env`. Generate a fresh `MVP_API_KEY`, set
   `LICHESS_USER_AGENT` to the deployed application name plus a real monitored
   contact, and configure `STOCKFISH_PATH` when analysis is needed.
   If a separate browser frontend is enabled, set its exact origins in
   `CORS_ALLOWED_ORIGINS`. The operator `MVP_API_KEY` is server-side only and must
   never be embedded in frontend JavaScript.
2. Start PostgreSQL and Redis and apply migrations:

   ```bash
   docker compose up -d db redis
   uv sync --dev
   uv run python scripts/download_eco.py
   uv run alembic upgrade head
   ```

   `data/eco.json` is a generated artifact and is intentionally ignored by Git.
   The generator downloads the immutable `lichess-org/chess-openings` revision
   recorded in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), so local and CI
   builds use the same opening dataset.

3. Start the API:

   ```bash
   uv run uvicorn app.main:app --reload
   ```

4. In two terminals, start dedicated workers for analysis and report jobs:

   ```bash
   uv run celery -A app.tasks.celery_app.celery_app worker -Q analysis --concurrency=1 --loglevel=info
   uv run celery -A app.tasks.celery_app.celery_app worker -Q reports --concurrency=1 --loglevel=info
   ```

   The MVP production defaults are `analysis=1` and `reports=1`. Deployment uses
   separate prefork workers; on Windows local development, append `--pool=solo` to
   each command. Keep analysis capacity within both resource budgets:

   ```text
   analysis concurrency × STOCKFISH_THREADS <= available CPU cores
   analysis concurrency × STOCKFISH_HASH_MB <= Stockfish RAM budget
   ```

   Analysis uses early acknowledgement intentionally. A normal exception records
   `failed`, but a killed worker can leave a stale `running` row until controlled
   operator recovery; automatic leases/reaping are outside the MVP.

## Example API workflow

All mutating or expensive operations require the configured `X-API-Key`. Replace
the shell variables below with local values without committing them.

```bash
curl http://localhost:8000/health
curl http://localhost:8000/ready
curl http://localhost:8000/
curl http://localhost:8000/api/v1/demo

curl -X POST \
  -H "X-API-Key: $CHESS_LAB_API_KEY" \
  "http://localhost:8000/api/v1/games/lichess/$LICHESS_USERNAME?max_games=20"

curl -X POST \
  -H "X-API-Key: $CHESS_LAB_API_KEY" \
  "http://localhost:8000/api/v1/analyze/player/$LICHESS_USERNAME"

curl "http://localhost:8000/api/v1/analyze/player/$LICHESS_USERNAME/status"
curl "http://localhost:8000/api/v1/games/stats/$LICHESS_USERNAME"

curl -X POST \
  -H "X-API-Key: $CHESS_LAB_API_KEY" \
  "http://localhost:8000/api/v1/report/$LICHESS_USERNAME?language=en"

curl "http://localhost:8000/api/v1/report/$LICHESS_USERNAME?language=en"
```

## Tests and quality gates

Unit tests do not need external services. Integration tests use the dedicated
`db_test` PostgreSQL service on port 5433.

```bash
docker compose up -d db_test
uv sync --frozen --dev
uv run --no-sync python scripts/download_eco.py
uv run --no-sync ruff check app tests scripts
uv run --no-sync ruff format --check app tests scripts
uv run --no-sync python scripts/check_lichess_http_boundary.py
uv run --no-sync pytest
```

For a focused local run, use `uv run --no-sync pytest -m unit` or
`uv run --no-sync pytest -m integration`. The GitHub Actions workflow runs Ruff lint,
Ruff format check, the Lichess boundary guard, and the complete automated test suite
as separate visible gates against a disposable PostgreSQL service. It builds the
ignored local ECO dictionary from the official source before linting and tests, then
builds the release Dockerfile and smoke-tests application imports, OCI labels, and the
container `/health` endpoint. Automated tests mock the Lichess API and never spend its
quota; CI does not call an external LLM provider.

## Production image

Build the single Linux x86-64 image with explicit release identity:

```bash
docker build --platform linux/amd64 \
  --build-arg OCI_SOURCE=https://github.com/Roman2050/chess-lab \
  --build-arg OCI_REVISION="$(git rev-parse HEAD)" \
  --build-arg OCI_VERSION=0.1.0 \
  --tag chess-lab:0.1.0 .
```

The image defaults to the Uvicorn API command. Override the command to run either
Celery queue or the one-shot migration job:

```bash
docker run --rm chess-lab:0.1.0 celery -A app.tasks.celery_app.celery_app worker -Q analysis --concurrency=1 --loglevel=info
docker run --rm chess-lab:0.1.0 celery -A app.tasks.celery_app.celery_app worker -Q reports --concurrency=1 --loglevel=info
docker run --rm chess-lab:0.1.0 alembic upgrade head
```

Runtime configuration is supplied through environment variables or an external
environment file; no secrets are included in the image. The generated ECO dictionary
and Stockfish 18 are already present, and the complete project license and third-party
notices are stored in `/usr/share/licenses/chess-lab/`.

### GHCR releases

A published, non-prerelease GitHub Release with an exact `vX.Y.Z` tag triggers the
same complete verification job before publication. The tag without its leading `v`
must equal the version in `pyproject.toml`; a mismatch fails before registry login.
The release job uses GitHub's short-lived `GITHUB_TOKEN`, not a stored registry or VPS
credential, and publishes two Linux x86-64 tags:

```text
ghcr.io/roman2050/chess-lab:vX.Y.Z
ghcr.io/roman2050/chess-lab:<full-40-character-source-commit>
```

No `latest` tag is produced. The image carries OCI source, revision, version,
description, and `GPL-3.0-or-later` labels. For a public repository, the workflow also
attaches signed build provenance to its digest; GitHub Free, Pro, and Team do not provide
artifact attestations for private repositories. Treat release tags as immutable: never
delete and recreate a published version for different source. Production should use
the explicit version tag shown above or, after verification, the registry digest.
Publishing an image does not connect to or deploy the VPS.

After publishing, pull and inspect the release without checking out the repository:

```bash
docker pull ghcr.io/roman2050/chess-lab:v0.1.0
docker image inspect ghcr.io/roman2050/chess-lab:v0.1.0 \
  --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}'
```

## Production Compose and HTTPS boundary

The local [docker-compose.yml](docker-compose.yml) intentionally keeps convenient
host ports and the `db_test` service. The production
[compose.production.yaml](compose.production.yaml) is a separate single-VPS topology:
Caddy is the only service publishing ports (`80/tcp` and `443/tcp`), while Uvicorn,
PostgreSQL, and Redis are reachable only through Docker networks.

Copy [.env.production.example](.env.production.example) to `.env.production`, replace
every example-only value, and restrict the file to the deployment operator. Use an
immutable `CHESS_LAB_IMAGE` tag or digest. The real file is ignored by Git and must
never be pasted into an issue, CI log, or `docker compose config` output shared with
others.

Validate the rendered topology and Caddy syntax before pulling or starting services:

```bash
docker compose --env-file .env.production -f compose.production.yaml --profile migrate config --quiet
docker compose --env-file .env.production -f compose.production.yaml run --rm --no-deps caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
```

The first controlled start keeps migration separate from long-running services:

```bash
docker compose --env-file .env.production -f compose.production.yaml pull
docker compose --env-file .env.production -f compose.production.yaml up -d db redis
docker compose --env-file .env.production -f compose.production.yaml --profile migrate run --rm migrate
docker compose --env-file .env.production -f compose.production.yaml up -d api worker-analysis worker-reports caddy
docker compose --env-file .env.production -f compose.production.yaml ps
```

Caddy terminates TLS, redirects HTTP to HTTPS, permits at most 24 MB per request
(enough for a 20 MiB PGN plus multipart framing), and proxies only to the private API
service. Uvicorn trusts forwarded headers only from Caddy's fixed proxy-network
address. Caddy certificate state and PostgreSQL data are persistent volumes; no
host-installed Python, uv, or Stockfish is used.

The 2 vCPU / 4 GB defaults use one process for each worker,
`STOCKFISH_THREADS=1`, and `STOCKFISH_HASH_MB=128`. Keep these invariants when tuning:

```text
analysis concurrency × STOCKFISH_THREADS <= available CPU cores
analysis concurrency × STOCKFISH_HASH_MB <= analysis-worker memory budget
```

Before updating or stopping workers, inspect active/reserved tasks and queue depth,
then request a warm shutdown. The 30-minute analysis and 15-minute report grace
periods protect ordinary tasks from an immediate cold kill; they are not a stale-task
recovery mechanism.

The production procedures are written for a first deployment on Ubuntu Server 24.04
LTS. Follow them in order; do not improvise destructive recovery steps:

- [deployment, update, and exact rollback](docs/runbooks/deployment.md);
- [DNS, direct TLS, and certificate renewal](docs/runbooks/dns-tls.md);
- [encrypted off-server backup and test restore](docs/runbooks/backup-restore.md);
- [Celery stale-task diagnosis and controlled recovery](docs/runbooks/celery-recovery.md);
- [production incident response and secret rotation](docs/runbooks/incident.md).

## Operations and safety

Lichess imports use a stable application `User-Agent`, an optional server-side
token, a hard total timeout, a bounded response body, a Redis single-flight lock,
and a global `429` cooldown. `/health` is process liveness; `/ready` checks Redis
availability and never calls Lichess.

Production API and worker output is one structured JSON record per stdout line;
development remains human-readable. Caddy emits filtered JSON access logs without
headers or query strings, Uvicorn's duplicate access log is disabled, and every
long-lived container uses Docker's `local` driver with five 10 MB files. Use the
[runtime observability runbook](docs/runbooks/observability.md) to correlate API,
Celery, and Lichess operations, inspect queues and resource usage, and configure the
initial uptime, disk, memory, restart, backlog, and stale-analysis alerts. Do not run
an unconditional `docker system prune`, because previous images are rollback targets.

Use the [Lichess operator runbook](docs/runbooks/lichess.md) for safe credential
changes, `409`/`429`/`503` diagnosis, cooldown inspection, lifecycle log fields, and
the one-request smoke procedure. [.env.example](.env.example) is the local settings
template; [.env.production.example](.env.production.example) is the sanitized
single-VPS contract.

## Known MVP limitations and roadmap

- Lichess import is synchronous and returns `409` when another deployment-wide
  import is active; there is no import-job resource or waiting queue.
- Imports are bounded to 50 games per request and do not maintain an incremental
  synchronization cursor.
- Analysis tasks have no automatic stale-lease reaper if a worker dies while a game
  is marked `running`.
- The MVP uses one operator API key; multi-user authentication and per-user OAuth
  are outside the current scope. Browser clients are intentionally unable to send
  this operator key through CORS.
- Hosted demo data, external monitoring integration, and automated deployment remain
  roadmap work; the first production deploy and rollback are intentionally manual.

## License

Copyright © 2026 Roman Kozhemiachenko.

Chess Lab is licensed under the [GNU General Public License version 3 or
later](LICENSE) (`GPL-3.0-or-later`). Attribution, exact source revisions, and
distribution terms for python-chess, Stockfish, and the ECO opening dataset are
recorded in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Chess Lab is independent and is not affiliated with, endorsed by, or sponsored by
Lichess or the Stockfish project.
