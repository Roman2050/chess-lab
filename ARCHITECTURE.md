# Chess Lab — Architecture & Project Specification

## 1. Project Goal

A backend tool for **bulk analysis of chess game history** (e.g., from Lichess) to prepare
for specific opponents at tournaments.

The system loads games, runs deep technical analysis via the Stockfish engine in the
background, aggregates metrics (accuracy, errors, patterns), and passes them to an LLM
to generate a human-readable report on a player's weaknesses and strengths.

---

## 2. Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, FastAPI |
| Database | PostgreSQL 16 (Docker) |
| ORM | SQLAlchemy 2.0 — async (`asyncpg`) + sync (`psycopg2`) |
| Migrations | Alembic |
| Background tasks | Celery + Redis |
| Chess logic | `python-chess` + Stockfish engine |
| Config | Pydantic Settings (`.env`) |
| Package manager | uv |
| Tests | pytest, pytest-asyncio, respx, factory-boy |

---

## 3. Key Architectural Decisions

**3.1 Analysis Neutrality**
A game is analyzed by the engine only once. Base metrics in the DB are not tied to a
specific player (no "my moves" vs "opponent's moves" split at the engine level).
Player-specific filtering happens only at the report generation stage.

**3.2 MultiPV**
Stockfish is configured to search multiple lines (`MultiPV = 2`). This is required to
detect "only move" situations — when the gap between the first and second line is large.

**3.3 Local ECO Opening Dictionary**
PGN files may not contain the `Opening` tag. The system must recognize openings from
the first moves by matching FEN or move sequences against a local JSON/ECO dictionary.
This enrichment happens at PGN parse time.

**3.4 Memory-Efficient FEN Storage**
FEN strings are stored in the DB **only for moves with errors** (inaccuracy / mistake /
blunder), not for every move. This allows the frontend to instantly render the critical
position for training without storing megabytes of redundant data.

**3.5 Async/Sync Session Split**
- `get_async_db()` — FastAPI route dependency (asyncpg)
- `get_sync_db_session()` — context manager for Celery workers (psycopg2)

---

## 4. Project File Structure

```
chess-lab/
├── app/
│   ├── config.py              # Pydantic Settings: DB_*, redis_url, stockfish_path
│   ├── database.py            # async + sync engines, session factories, Base
│   ├── main.py                # FastAPI app instance, router registration
│   ├── models/
│   │   ├── db.py              # SQLAlchemy ORM models (see Section 5)
│   │   └── enums.py           # StandardPerfType (blitz, bullet, rapid, etc.)
│   ├── schemas/
│   │   └── games.py           # Pydantic: GameSummary, GameDetail, PaginatedGames,
│   │                          #           UploadResponse, SortOrder
│   ├── routers/
│   │   └── games.py           # HTTP endpoints (see Section 6)
│   ├── services/
│   │   ├── lichess.py         # fetch_games_from_lichess() → raw PGN text
│   │   ├── db_manager.py      # bulk_save_games() with on_conflict_do_nothing
│   │   ├── game_queries.py    # get_filtered_games(): filters + pagination
│   │   ├── analysis/          # [PLANNED] Stockfish analysis pipeline
│   │   │   ├── engine.py      # Stockfish wrapper, MultiPV config
│   │   │   ├── classifier.py  # cp_loss → classification (inaccuracy/mistake/blunder)
│   │   │   └── tactical.py    # Heuristic tactical tag detector (fork, pin, hanging)
│   │   ├── eco.py             # [PLANNED] ECO opening dictionary lookup
│   │   └── report.py          # [PLANNED] LLM prompt builder + report generator
│   ├── tasks/
│   │   └── celery_app.py      # [PLANNED] Celery app + analysis task definition
│   └── utils/
│       └── parser.py          # parse_pgn_text() → list[dict]
├── tests/
│   ├── conftest.py            # fixtures: app, async_client, sample_pgn_text
│   ├── api/                   # API-level tests (ASGI transport, DB overrides)
│   ├── integration/           # Tests requiring live Postgres
│   └── unit/                  # Isolated tests (no DB, no network)
├── scripts/
│   └── check_db.py            # Quick DB connectivity check
├── docker-compose.yml         # PostgreSQL 16 + (planned) Redis
├── pyproject.toml             # Dependencies, pytest config, build system
├── ARCHITECTURE.md            # This file — always read at start of new chat
└── .env                       # DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME,
                               # REDIS_URL, STOCKFISH_PATH
```

---

## 5. Database Models

### 5.1 `Game` — raw and analyzed games

```python
class Game(Base):
    __tablename__ = "games"

    id            = Integer PK
    unique_id     = String  UNIQUE NOT NULL   # Lichess ID or SHA-256 of moves+players+date
    white_player  = String  NOT NULL index
    black_player  = String  NOT NULL index
    result        = String  NOT NULL          # "1-0" | "0-1" | "1/2-1/2"
    winner        = String  index             # "White" | "Black" | "Draw"
    opening_name  = String  index             # from PGN tag OR ECO lookup
    time_control  = String
    date_played   = Date    index
    pgn_content   = Text    NOT NULL          # clean PGN (no headers, no comments)
    analysis_data = JSONB   nullable          # see Section 5.3
    is_analyzed   = Boolean default=False index

    # Composite indexes for player+winner filtering
    ix_games_white_winner (white_player, winner)
    ix_games_black_winner (black_player, winner)
```

### 5.2 `PlayerReport` — LLM reports [PLANNED]

```python
class PlayerReport(Base):
    __tablename__ = "player_reports"

    id                  = Integer PK
    player_name         = String  NOT NULL index
    report_text         = Text    NOT NULL        # generated by LLM
    last_game_played_at = DateTime                # newest game included in report
    created_at          = DateTime default=now()
```

`last_game_played_at` is used to analyze **only new games** on the next report request,
instead of reprocessing the entire history.

### 5.3 `analysis_data` JSONB Schema

```json
{
  "summary": {
    "white_acpl": 22,
    "black_acpl": 54,
    "advantage_lost": {
      "white": false,
      "black": true
    }
  },
  "moves": [
    {
      "ply": 15,
      "move_num": 8,
      "color": "White",
      "san": "Nf3",
      "piece": "N",
      "eval_before": 45,
      "eval_after": -250,
      "cp_loss": 295,
      "classification": "blunder",
      "is_only_move": false,
      "best_move_engine": "Bxc4",
      "tactical_tags": ["missed_fork", "ignored_threat"],
      "fen_before": "<FEN string>",
      "fen_after": "<FEN string>"
    }
  ]
}
```

**Important:** `fen_before`, `fen_after`, `best_move_engine`, and `tactical_tags` are
populated **only** when `classification` is `"inaccuracy"`, `"mistake"`, or `"blunder"`.
Good and excellent moves store only: `ply`, `move_num`, `color`, `san`, `piece`,
`eval_before`, `eval_after`, `cp_loss`, `classification`.

**Classification thresholds (cp_loss):**

| cp_loss | classification |
|---|---|
| 0–10 | best |
| 11–25 | excellent |
| 26–50 | good |
| 51–100 | inaccuracy |
| 101–300 | mistake |
| 300+ | blunder |

---

## 6. API Endpoints (Current)

| Method | Path | Description |
|---|---|---|
| `POST` | `/games/lichess/{username}` | Fetch games from Lichess API, parse, save |
| `POST` | `/games/upload` | Upload a `.pgn` file, parse, save |
| `GET` | `/games` | Paginated list with filters (player_name, winner, sort) |
| `GET` | `/games/{id}` | Full game detail including pgn_content |
| `GET` | `/health` | Health check |

**Planned endpoints:**

| Method | Path | Description |
|---|---|---|
| `POST` | `/games/{id}/analyze` | Enqueue Stockfish analysis for one game |
| `POST` | `/analyze/player/{username}` | Enqueue batch analysis for all unanalyzed games |
| `GET` | `/report/{username}` | Get or generate LLM report for a player |

---

## 7. Background Processing Flow (Planned)

```
FastAPI route → Celery task enqueue (Redis)
                     ↓
              Celery worker (sync)
                     ↓
         get_sync_db_session() → fetch Game
                     ↓
         Stockfish.analyse() with MultiPV=2
                     ↓
         classifier.py → cp_loss → classification
                     ↓
         tactical.py  → tactical_tags (if error)
                     ↓
         Build analysis_data JSON
                     ↓
         Update Game: analysis_data=..., is_analyzed=True
```

---

## 8. Implementation Roadmap

**Phase 1 — Analysis Pipeline (Celery + Stockfish)**
Configure Celery workers to pick up unanalyzed games (`is_analyzed=False`), run
Stockfish with `MultiPV=2`, calculate `cp_loss`, assign `classification`, write
`analysis_data` to DB, set `is_analyzed=True`.

**Phase 2 — Opening Enrichment (ECO)**
Integrate ECO opening dictionary lookup at PGN parse time when the `Opening` PGN tag
is missing. Match via FEN prefix or move sequence.

**Phase 3 — Tactical Detector (Heuristics)**
Build `python-chess`-based algorithms to explain why the engine evaluation dropped:
forks, pins, hanging pieces, missed threats. Populate `tactical_tags`.

**Phase 4 — Data Aggregation**
Write functions/SQL queries to collect per-player stats:
- Weighted average ACPL (by move count)
- Accuracy by game phase (opening / middlegame / endgame)
- Favorite openings and win rates
- Most frequent error pieces and move numbers

**Phase 5 — LLM Integration**
Build final prompt from aggregated stats, call the language model API, save and expose
the text report via `PlayerReport`. Use `last_game_played_at` to only process new games
on incremental report updates.

---

## 9. Coding Conventions

- **Type hints:** modern style — `str | None`, not `Optional[str]`
- **SQLAlchemy:** 2.0 style — `select(Model).where(...)`, not `session.query()`
- **Async default:** use `get_async_db()` in all FastAPI routes
- **Sync only for:** Celery tasks via `get_sync_db_session()`
- **Schema changes:** always via Alembic migration, never alter tables manually
- **New dependencies:** discuss before adding to `pyproject.toml`
- **Tests:**
  - `@pytest.mark.unit` — isolated, no DB, no network
  - `@pytest.mark.integration` — requires live Postgres (Docker)

---

## 10. Environment Variables (`.env`)

```
DB_HOST=localhost
DB_PORT=5432
DB_USER=chess
DB_PASSWORD=chess
DB_NAME=chess_lab
REDIS_URL=redis://localhost:6379/0
STOCKFISH_PATH=/usr/local/bin/stockfish
```
