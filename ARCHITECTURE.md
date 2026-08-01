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

**3.6 Win-Probability (WP) Metric**
Centipawn loss is linear in centipawns, but the centipawns→result relationship is
not: a 60cp slip near equality costs far more than one at +900, and the opening
(book-like, near-equal positions) always shows the lowest raw `cp_loss` for anyone,
so ranking phases by ACPL trivially crowns the opening. To fix both, move quality in
the **report** is expressed as **win-probability loss** — the drop in the mover's
winning chances, via a logistic map `WP(cp) = 100 / (1 + exp(-WP_SCALE * cp))`. This
is a **read-side derived metric**: it is computed from the `eval_before` / `eval_after`
already stored for every move (§5.3), so there is **no DB change, no migration, no
re-analysis**. The `cp_loss` classification thresholds, `MultiPV`, and `analysis_data`
schema are untouched; ACPL remains for the public `/stats` endpoints. See
`Phase_6-WinProbabilityMetric.md`.

Calibration on real-player data showed that already decided positions dilute the
average with near-zero losses. WP aggregations therefore include a move only when
the mover's pre-move winning chance is strictly inside the calibrated live window
`20% < WP_before < 90%`; ineligible moves produce no WP value. The interim report
skill buckets are `strong <= 2.5`, `solid <= 4.0`, `inconsistent <= 6.0`, and
`weak > 6.0`. These narrative buckets may be recalibrated as the sample grows.

**3.7 Read-Side Column Loading (Phase 7)**
`games` carries two heavy columns — `pgn_content` and `analysis_data` (§5.1) — and a read
path must load neither unless it actually uses it. `GET /games` selects only the columns
`GameSummary` serializes; the aggregation fetches keep `analysis_data` (every metric is
derived from it) and drop `pgn_content`. Both are expressed as
`load_only(..., raiseload=True)`: `raiseload` turns an accidental access into an immediate
error rather than a silent per-row SELECT (sync) or a `MissingGreenlet` raised far from the
cause (async). `build_report_context` makes **one** fetch and filters the analyzed subset in
memory, so a report never transfers the same `analysis_data` twice. The pagination COUNT is
deliberately left alone — SQLAlchemy loader options don't propagate into `.subquery()`, and
Postgres projects the unused columns away. No schema, response, or metric changes.

**3.8 PGN Ingestion Contract (Phase 7)**
`unique_id` identifies a game across re-imports and is the conflict target of the bulk
insert, so it must be stable *and* collision-free. Lichess games take the last path
segment of the `Site` URL. Everything else is hashed:
`sha256("White|Black|Date|Result|clean_pgn")`, where `Date` is the **raw PGN tag**
(possibly `"????.??.??"`), not the parsed `date_played` column, and `clean_pgn` is the
output of `StringExporter(headers=False, variations=False, comments=False)` — the same
text stored in `pgn_content`. The movetext is what makes the hash discriminating: the
previous contract hashed the *initial* FEN, which is identical for every standard game,
so two different games between the same players on the same day collided and
`on_conflict_do_nothing` silently dropped the second one.

Parsing is deliberately lenient about dates: an absent, partial or `????.??.??` tag
yields `date_played = NULL` instead of aborting the upload, so one malformed game no
longer costs the whole file. Uploads are bounded before the body is buffered — over
`MAX_UPLOAD_BYTES` (20 MB) is a 413, a missing filename / wrong extension / non-UTF-8
payload is a 400.

**3.9 Case-Insensitive Player Identity (Phase 7)**
Every database lookup by player name compares `lower(stored_player_name)` with
`lower(requested_player_name)`. Functional indexes on `lower(white_player)` and
`lower(black_player)` support game-list, aggregation, and analysis-queue reads; the
existing exact-case indexes remain available until production usage is evaluated.

For reports, the logical cache key and atomic generation-claim target are
`(lower(player_name), language)`, with `language` intentionally case-sensitive. The
first inserted `PlayerReport.player_name` casing is retained as its display value:
later requests with different casing find and update the same row without rewriting
that field.

---

## 4. Project File Structure

```
chess-lab/
├── app/
│   ├── config.py              # Pydantic Settings: DB_*, redis_url, STOCKFISH_*
│   ├── database.py            # async + sync engines, session factories, Base
│   ├── main.py                # FastAPI app instance, router registration
│   ├── models/
│   │   ├── db.py              # SQLAlchemy ORM models (see Section 5)
│   │   └── enums.py           # StandardPerfType (blitz, bullet, rapid, etc.)
│   ├── schemas/
│   │   ├── games.py           # Pydantic: GameSummary, GameDetail, PaginatedGames,
│   │   │                      #           UploadResponse, SortOrder
│   │   ├── stats.py           # PlayerStats, AcplStats, PhaseStats, OpeningStat,
│   │   │                      #   MoveAccuracyStat, WpLossStats [Phase 6], ...
│   │   ├── analysis.py        # BatchAnalysisResponse, AnalysisProgress
│   │   └── report.py          # [PLANNED Phase 5] ReportContext, ReportInsights,
│   │                          #           Report{Request,Status}Response, ReportResponse
│   ├── routers/
│   │   ├── games.py           # Games + per-player stats endpoints (see Section 6)
│   │   ├── analysis.py        # Batch analysis enqueue + progress
│   │   └── report.py          # [PLANNED Phase 5] LLM report POST/GET/status
│   ├── services/
│   │   ├── lichess.py         # fetch_games_from_lichess() → raw PGN text
│   │   ├── db_manager.py      # bulk_save_games() with on_conflict_do_nothing
│   │   ├── game_queries.py    # get_filtered_games(): filters + pagination,
│   │   │                      #   summary columns only (§3.7)
│   │   ├── analysis_queue.py  # Batch-analysis fan-out + progress queries
│   │   ├── analysis/          # Stockfish analysis pipeline
│   │   │   ├── engine.py      # Stockfish wrapper, MultiPV config
│   │   │   ├── classifier.py  # cp_loss → classification + phase + analysis_data
│   │   │   ├── phase.py       # detect_phase(board, ply) → opening/middlegame/endgame
│   │   │   └── tactical.py    # Heuristic tactical tag detector (fork, pin, hanging)
│   │   ├── aggregation/       # Per-player stat aggregations (Phase 4)
│   │   │   ├── helpers.py     # game fetch (async + sync, no pgn_content §3.7)
│   │   │   │                  #   + move/color iterators
│   │   │   ├── acpl.py        # compute_player_acpl() weighted ACPL breakdown
│   │   │   ├── winprob.py     # win_prob/move_wp_loss + compute_player_wp_loss()
│   │   │   ├── accuracy.py    # accuracy by phase / by move number (+ avg_wp_loss, Phase 6)
│   │   │   ├── openings.py    # compute_opening_stats() win-rate + opening ACPL/WP
│   │   │   └── errors.py      # compute_error_patterns() by piece / move number
│   │   ├── eco.py             # [PLANNED] ECO opening dictionary lookup
│   │   ├── llm/               # [PLANNED Phase 5] LLM provider abstraction
│   │   │   ├── base.py        #   LLMProvider Protocol + LLMError
│   │   │   ├── openai_compat.py  # httpx client for OpenAI-compatible APIs (Ollama, …)
│   │   │   └── factory.py     #   get_llm_provider() from settings
│   │   ├── report_context.py  # [PLANNED Phase 5] build ReportContext + derive_insights
│   │   ├── report_prompt.py   # [PLANNED Phase 5] system prompt + context digest
│   │   ├── report_repository.py  # [PLANNED Phase 5] PlayerReport CRUD (async read + claim / sync write)
│   │   └── report.py          # [PLANNED Phase 5] decide_report_action() + ReportAction
│   ├── tasks/
│   │   └── celery_app.py      # Celery app + analyze_game; [PLANNED Phase 5]
│   │                          #           generate_player_report task
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
                               # REDIS_URL, STOCKFISH_*
```

---

## 5. Database Models

### 5.1 `Game` — raw and analyzed games

```python
class Game(Base):
    __tablename__ = "games"

    id            = Integer PK
    unique_id     = String  UNIQUE NOT NULL   # Lichess ID or sha256(...) — see §3.8
    white_player  = String  NOT NULL index
    black_player  = String  NOT NULL index
    result        = String  NOT NULL          # "1-0" | "0-1" | "1/2-1/2"
    winner        = String  index             # "White" | "Black" | "Draw"
    opening_name  = String  index             # from PGN tag OR ECO lookup
    time_control  = String
    date_played   = Date    index nullable    # NULL when the PGN Date tag is absent or partial
    pgn_content   = Text    NOT NULL          # clean PGN (no headers, no comments)
    analysis_data = JSONB   nullable          # see Section 5.3
    is_analyzed   = Boolean NOT NULL default=False index

    # Analysis lifecycle (Phase 7) — see Section 7
    analysis_status     = String   NOT NULL default "pending"  # pending|running|completed|failed
    analysis_started_at = DateTime nullable   # set on every claim
    analysis_error      = Text     nullable   # truncated str(exc) of the last failure
    analysis_attempts   = Integer  NOT NULL default 0          # incremented by the claim

    # Composite indexes for player+winner filtering
    ix_games_white_winner (white_player, winner)
    ix_games_black_winner (black_player, winner)
    # Functional indexes for case-insensitive player lookup
    ix_games_white_player_lower (lower(white_player))
    ix_games_black_player_lower (lower(black_player))
    # Partial index over the claimable slice — the batch fan-out scans only it
    ix_games_pending_analysis (id) WHERE analysis_status IN ('pending', 'failed')
```

**Invariant: `is_analyzed` is True if and only if `analysis_status == 'completed'`.**
Both fields are written in the same transaction (Section 7, phase C). `is_analyzed`
stays because the whole read side depends on it (aggregations, reports, progress);
`analysis_status` exists to make execution *claimable*, which a boolean cannot express.
A game is claimable by a worker from `pending` or `failed` only
(`ANALYSIS_STATUS_CLAIMABLE` in `app/models/db.py`) — `POST /analyze/player/{username}`
selects exactly that set, so games already `running` are never re-enqueued.

There is no lease reaper: a worker killed mid-analysis leaves the row `running`, and
`analysis_started_at` makes such stale jobs visible. Returning them to the queue
automatically is a separate iteration (it needs a safe lease interval).

### 5.2 `PlayerReport` — LLM reports [PLANNED Phase 5]

```python
class PlayerReport(Base):
    __tablename__ = "player_reports"

    id                   = Integer PK
    player_name          = String   NOT NULL index
    language             = String   NOT NULL default "en"   # report language
    report_text          = Text     nullable    # NULL while first generation runs
    analyzed_games_count = Integer  NOT NULL default 0      # snapshot used for report
    last_game_played_at  = DateTime nullable    # informational ("covers up to ...")
    status               = String   NOT NULL default "ready"  # ready|generating|failed
    created_at           = DateTime default=now()
    updated_at           = DateTime default=now() onupdate=now()

    # One logical report per case-insensitive player and case-sensitive language.
    # This expression index is also the generation claim's conflict target.
    UNIQUE INDEX uq_player_reports_player_lang_lower
        (lower(player_name), language)
```

The unique expression index makes report identity case-insensitive without changing
the stored display value. `generating` doubles as the claim: the row is flipped by
`POST /report` *before* the task is enqueued, and only the worker moves it on to
`ready` / `failed`. Because a killed worker leaves nothing behind that could finish
the job, `generating` carries a lease — a row untouched for
`REPORT_GENERATION_LEASE_SECONDS` (default 900, well above `LLM_TIMEOUT`) is
considered abandoned and may be reclaimed. `updated_at` is the lease clock: nothing
else writes the row while it is `generating`.

**Regeneration is driven by `analyzed_games_count`, not by date.** A report is
(re)generated only when the count of the player's analyzed games has grown by at
least `REPORT_REFRESH_THRESHOLD` (default 20) since the snapshot stored on the last
report — see Section 7.1 for the full decision table. `last_game_played_at` is purely
informational (which games the report covers) and does **not** drive the decision,
because analysis is asynchronous: a game can be played long before it gets analyzed,
so a date threshold would be unreliable.

There is no `force` regeneration: to force a fresh report, delete the row — the
"report absent" branch then regenerates it (if enough analyzed games exist).

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

**Note (Phase 6):** `eval_before` / `eval_after` are stored on **every** move (both
White-relative centipawns), so the win-probability loss metric (§3.6) is derived
read-side from them — no extra fields and no schema change are required.

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
| `POST` | `/games/upload` | Upload a `.pgn` file, parse, save; 413 over 20 MB, 400 on missing filename / non-`.pgn` / non-UTF-8 (§3.8) |
| `GET` | `/games` | Paginated list with filters (player_name, winner, sort); summary columns only, no PGN / analysis payload (§3.7) |
| `GET` | `/games/{game_id}` | Full game detail including pgn_content |
| `POST` | `/games/{game_id}/analyze` | Enqueue Stockfish analysis for one game |
| `GET` | `/games/stats/{player_name}` | Aggregated player stats: ACPL, accuracy by phase, error patterns |
| `GET` | `/games/stats/{player_name}/openings` | Per-opening statistics (win-rate + ACPL + live-position `wp_loss_in_opening`) |
| `GET` | `/games/stats/{player_name}/moves` | Accuracy by move number (`avg_cp_loss` + live-position `avg_wp_loss`) |
| `POST` | `/analyze/player/{username}` | Enqueue batch analysis for all unanalyzed games of a player |
| `GET` | `/analyze/player/{username}/status` | Read analysis progress for a player |
| `GET` | `/health` | Health check |

Every endpoint parameter that identifies a player is case-insensitive on the
database read side (§3.9); response shapes and the requested display spelling are
unchanged.

**Planned endpoints (Phase 5):**

| Method | Path | Description |
|---|---|---|
| `POST` | `/report/{username}` | Decide & enqueue report generation (202) or report why not (200): up-to-date / not enough analyzed games |
| `GET` | `/report/{username}` | Return the cached report text (404 if none yet); flags `is_stale` |
| `GET` | `/report/{username}/status` | Generation state: none / generating / ready / failed |

All three accept `?language=` (default `REPORT_LANGUAGE`, currently `en`). The report
is a one-shot generated text (not a chat): `POST` triggers a background Celery task,
`GET` reads the cached result. See Section 7.1.
 
---

## 7. Background Processing Flow (Planned)

Stockfish runs for minutes, so the task never holds a DB session while it works:
`analyze_game` is three phases, two of them short transactions.

```
FastAPI route → Celery task enqueue (Redis)
                     ↓
              Celery worker (sync)
                     ↓
  ── phase A: claim (short transaction) ───────────────────────────
    UPDATE games SET analysis_status='running', analysis_started_at=now(),
                     analysis_error=NULL, analysis_attempts=attempts+1
     WHERE id=:id AND analysis_status IN ('pending','failed')
    RETURNING pgn_content
                     ↓
    no row back → another worker owns it (or it is already completed) → return
                     ↓
  ── phase B: analyse (NO session open) ───────────────────────────
         Stockfish.analyse() with MultiPV=2
                     ↓
         classifier.py → cp_loss → classification
                     ↓
         tactical.py  → tactical_tags (if error)
                     ↓
         Build analysis_data JSON
                     ↓
    exception → own short transaction: analysis_status='failed',
                analysis_error=str(exc)[:1000] → re-raise
                     ↓
  ── phase C: save (short transaction) ────────────────────────────
    UPDATE games SET analysis_data=..., is_analyzed=True,
                     analysis_status='completed', analysis_error=NULL
```

The claim, not the read-then-check in the router, is what makes the task idempotent:
`UPDATE ... WHERE status IN (...) RETURNING` is a single statement, so two workers
racing for the same game cannot both win — under READ COMMITTED the loser blocks on
the row lock and re-evaluates the predicate after the winner commits, matching zero
rows. `POST /games/{game_id}/analyze` still rejects an already-analyzed game with 400,
but only as a fast, non-authoritative check.

### 7.1 Report Generation Flow (Planned, Phase 5)

The LLM is a **narrator, not an analyst**: every number and conclusion is computed
deterministically in code (Phase 4 aggregations + derived `insights`); the model only
phrases the supplied facts in prose. It never queries the DB and never invents data.

```
POST /report/{username}?language=en
        ↓
  count_analyzed_games + get_report + lease check   (async reads)
        ↓
  decide_report_action(current, report, threshold, generation_is_stale)   (pure)
        ├── INSUFFICIENT_GAMES → 200 message (need {threshold}, have {current})
        ├── UP_TO_DATE         → 200 message (delta < threshold; GET returns cache)
        ├── ALREADY_GENERATING → 202 (a task is already running)
        └── GENERATE
              ↓
  ── claim (committed before the task exists) ──────────────────────
    INSERT INTO player_reports (..., status='generating')
    ON CONFLICT (lower(player_name), language) DO UPDATE
       SET status='generating', updated_at=now()
     WHERE status <> 'generating' OR updated_at < now() - :lease
 RETURNING id
              ↓
    no row back → another request owns it → 202 ALREADY_GENERATING
              ↓
    enqueue Celery task → 202
    (enqueue raises → claim released → 503, the row is never left stuck)
                                       ↓
                          generate_player_report (sync worker)
                                       ↓
  ── phase A: context (short transaction) ─────────────────────────
       build_report_context(): ONE game fetch (no pgn_content, §3.7), analyzed
              subset filtered in memory → compute_player_wp_loss / accuracy /
              errors / opening_stats  +  derive_insights()   → ReportContext
                                       ↓
  ── phase B: generate (NO session open) ──────────────────────────
              build_messages() → (system, user) digest
                                       ↓
              get_llm_provider().generate()  (httpx, OpenAI-compatible)
                                       ↓
  ── phase C: save (short transaction) ────────────────────────────
       PlayerReport: report_text, analyzed_games_count snapshot,
              last_game_played_at, status=ready
                                       ↓
    any exception → fresh session: status=failed (previous text kept)
```

Decision table (`threshold = REPORT_REFRESH_THRESHOLD`, default 20):

| State | Condition | Action |
|---|---|---|
| No report (absent / deleted / text NULL) | `current < threshold` | `INSUFFICIENT_GAMES` |
| No report | `current >= threshold` | `GENERATE` (first report) |
| Report exists | `current - snapshot >= threshold` | `GENERATE` (refresh) |
| Report exists | `current - snapshot < threshold` | `UP_TO_DATE` (serve cache) |
| Report row | `status == "generating"`, lease alive | `ALREADY_GENERATING` |
| Report row | `status == "generating"`, lease expired | treated as not in flight |

Progress is read from `PlayerReport.status` in the DB (no Celery result backend),
mirroring how batch-analysis progress is read from `is_analyzed`. The decision above
is advisory — the claim is what decides, exactly as in Section 7: the read can be
stale by the time we act on it, so a request that loses the `ON CONFLICT` race
downgrades itself to `ALREADY_GENERATING` instead of enqueueing a second task.

The claim writes `analyzed_games_count` only when it inserts a new row. On an
existing report the snapshot still describes the `report_text` that is about to be
replaced; bumping it up front would make a *failed* generation look up to date and
the report would never be regenerated.

The conflict target follows the case-insensitive report identity from §3.9. A
cross-case request therefore cannot enqueue a second live generation, and the
conflict update deliberately omits `player_name` so the first row's display casing
is preserved.

Model switching: a single `httpx` OpenAI-compatible provider covers Ollama (local,
`http://localhost:11434/v1`) and remote services — switch via `LLM_BASE_URL` /
`LLM_MODEL` / `LLM_API_KEY` in `.env`, no code change. No new dependencies (`httpx`
is already used).

---

## 8. Implementation Roadmap

**Phase 1 — Analysis Pipeline (Celery + Stockfish)**
Configure Celery workers to pick up claimable games (`analysis_status` in
`pending|failed`; before Phase 7: `is_analyzed=False`), run
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
Assemble a `ReportContext` from the Phase 4 aggregations plus deterministic
`insights`, render it into a language-neutral fact digest, and have an LLM phrase it
as a human-readable report (characterization + recommendations). The model is a
narrator only — all numbers come from code. A pluggable `httpx` OpenAI-compatible
provider (`app/services/llm/`) makes switching between local Ollama and remote APIs a
config change. Generation runs as a background Celery task `generate_player_report`;
the result is cached in `PlayerReport` and exposed via `POST` / `GET` /
`GET .../status`. Reports are regenerated only when the player's analyzed-game count
has grown by `REPORT_REFRESH_THRESHOLD` since the last snapshot (Section 7.1).
See `Phase_5-LLMIntegration.md` for the per-chat implementation plan.

**Phase 6 — Win-Probability Report Metric**
The report uses **win-probability loss** instead of raw ACPL (Section 3.6): a
logistic map of the stored per-move evals into "winning chances lost per move",
restricted to calibrated live positions and applied to overall / color / phase /
opening signals and the derived `insights`.
This removes the linear-centipawn bias (notably the "opening always looks strongest"
artifact) and yields a metric both humans and the LLM read intuitively. The public
`/stats` endpoints stay on ACPL; `/stats/.../moves` gains `avg_wp_loss` alongside
`avg_cp_loss`. It is a **read-side derived metric** over existing `eval_before` /
`eval_after` — no DB change, no migration, no re-analysis.
See `Phase_6-WinProbabilityMetric.md` for the per-chat implementation plan.

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

# Stockfish — only STOCKFISH_PATH is required; the rest fall back to safe
# defaults defined in app/config.py.
STOCKFISH_PATH=/usr/local/bin/stockfish
STOCKFISH_DEPTH=20         # search depth per position (1..40)
STOCKFISH_MULTIPV=2        # PV lines; needed for is_only_move detection (1..10)
STOCKFISH_THREADS=1        # UCI option, set once at engine startup (1..128)
STOCKFISH_HASH_MB=128      # UCI option, transposition table size (1..16384 MB)
```

`STOCKFISH_DEPTH` and `STOCKFISH_MULTIPV` are passed per-call to
`engine.analyse(...)`. `STOCKFISH_THREADS` / `STOCKFISH_HASH_MB` are applied
once via `engine.configure({...})` after process startup, and are silently
skipped if the underlying UCI binary doesn't expose them (e.g. some forks).

```
# LLM / report (Phase 5) — all optional, safe defaults in app/config.py.
# Any OpenAI-compatible endpoint works; switch models by changing these.
LLM_BASE_URL=http://localhost:11434/v1   # Ollama local default
LLM_MODEL=llama3.1
LLM_API_KEY=                             # required only for remote services
LLM_TEMPERATURE=0.4                      # 0.0..2.0
LLM_TIMEOUT=120                          # seconds per generation (1..600)

REPORT_LANGUAGE=en                       # default report language
REPORT_REFRESH_THRESHOLD=20              # new analyzed games needed to regenerate
REPORT_GENERATION_LEASE_SECONDS=900      # when a stuck `generating` may be reclaimed
```
