# Chess Lab

Chess Lab is a backend for importing a player's chess history, running bounded
Stockfish analysis, aggregating recurring weaknesses, and generating a concise
training report.

## Demo and API examples

After a local start, the interactive OpenAPI UI is available at
`http://localhost:8000/docs` and ReDoc at `http://localhost:8000/redoc`.
The current API exposes deterministic player statistics through
`GET /games/stats/{player_name}` and a cached, LLM-narrated report through
`GET /report/{username}`. A hosted demo and release screenshots are intentionally
left for production packaging.

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
2. Start PostgreSQL and Redis and apply migrations:

   ```bash
   docker compose up -d db redis
   uv sync --dev
   uv run alembic upgrade head
   ```

3. Start the API:

   ```bash
   uv run uvicorn app.main:app --reload
   ```

4. In another terminal, start the worker for analysis and report jobs:

   ```bash
   uv run celery -A app.tasks.celery_app.celery_app worker --loglevel=info
   ```

## Example API workflow

All mutating or expensive operations require the configured `X-API-Key`. Replace
the shell variables below with local values without committing them.

```bash
curl http://localhost:8000/health
curl http://localhost:8000/ready

curl -X POST \
  -H "X-API-Key: $CHESS_LAB_API_KEY" \
  "http://localhost:8000/games/lichess/$LICHESS_USERNAME?max_games=20"

curl -X POST \
  -H "X-API-Key: $CHESS_LAB_API_KEY" \
  "http://localhost:8000/analyze/player/$LICHESS_USERNAME"

curl "http://localhost:8000/analyze/player/$LICHESS_USERNAME/status"
curl "http://localhost:8000/games/stats/$LICHESS_USERNAME"

curl -X POST \
  -H "X-API-Key: $CHESS_LAB_API_KEY" \
  "http://localhost:8000/report/$LICHESS_USERNAME?language=en"

curl "http://localhost:8000/report/$LICHESS_USERNAME?language=en"
```

## Tests and quality gates

Unit tests do not need external services. Integration tests use the dedicated
`db_test` PostgreSQL service on port 5433.

```bash
uv run pytest -m unit
docker compose up -d db_test
uv run pytest -m integration
uv run pytest
uv run python scripts/check_lichess_http_boundary.py
```

The GitHub Actions workflow runs the locked dependency set, the Lichess boundary
guard as a separate visible step, and the complete automated test suite against a
disposable PostgreSQL service. Automated tests mock Lichess and never spend its
quota.

## Operations and safety

Lichess imports use a stable application `User-Agent`, an optional server-side
token, a hard total timeout, a bounded response body, a Redis single-flight lock,
and a global `429` cooldown. `/health` is process liveness; `/ready` checks Redis
availability and never calls Lichess.

Use the [Lichess operator runbook](docs/runbooks/lichess.md) for safe credential
changes, `409`/`429`/`503` diagnosis, cooldown inspection, lifecycle log fields, and
the one-request smoke procedure. [.env.example](.env.example) is the canonical list
of settings and safe placeholders.

## Known MVP limitations and roadmap

- Lichess import is synchronous and returns `409` when another deployment-wide
  import is active; there is no import-job resource or waiting queue.
- Imports are bounded to 50 games per request and do not maintain an incremental
  synchronization cursor.
- Analysis tasks have no automatic stale-lease reaper if a worker dies while a game
  is marked `running`.
- The MVP uses one operator API key; multi-user authentication and per-user OAuth
  are outside the current scope.
- Production packaging, hosted demo metadata, monitoring integration, and deployment
  smoke automation remain roadmap work.

## License

No license file has been published for this repository. Copyright is therefore
reserved by default.
