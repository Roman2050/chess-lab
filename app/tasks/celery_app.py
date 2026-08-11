import logging
from time import perf_counter

from celery import Celery, Task
from celery.signals import setup_logging, worker_process_shutdown
from chess.engine import SimpleEngine
from sqlalchemy import Update, func, update

from app.config import REPORT_LLM_MAX_RETRIES, settings
from app.database import get_sync_db_session
from app.logging_config import configure_logging, log_context
from app.models.db import ANALYSIS_STATUS_CLAIMABLE, Game
from app.services.analysis.classifier import build_analysis_data
from app.services.analysis.engine import StockfishEngine
from app.services.llm.base import LLMError
from app.services.llm.factory import get_llm_provider
from app.services.report_context import build_report_context
from app.services.report_prompt import build_messages
from app.services.report_repository import (
    mark_failed_sync,
    save_report_result_sync,
)

logger = logging.getLogger(__name__)

celery_app = Celery(
    "chess_lab",
    broker=settings.redis_url,
)
celery_app.conf.update(
    task_routes={
        "app.tasks.celery_app.analyze_game": {"queue": "analysis"},
        "app.tasks.celery_app.generate_player_report": {"queue": "reports"},
    },
    worker_prefetch_multiplier=1,
    task_acks_late=False,
    task_reject_on_worker_lost=False,
    worker_hijack_root_logger=False,
)

# `analysis_error` is an unbounded text column, but a whole traceback inside a
# status field is noise — the full exception goes to the log instead.
_ANALYSIS_ERROR_MAX_LEN = 1000
# Celery prefork children do not share module state. Each analysis child lazily
# owns one UCI process, while a reports-only child never reaches this holder.
_worker_engine: SimpleEngine | None = None


@setup_logging.connect
def _setup_worker_logging(**_: object) -> None:
    """Keep Celery worker and task output on the centralized stdout handler."""
    configure_logging(force=True)


def _stockfish_wrapper() -> StockfishEngine:
    """Build the lightweight wrapper for the configured analysis policy."""
    return StockfishEngine(
        settings.STOCKFISH_PATH or "",
        depth=settings.STOCKFISH_DEPTH,
        multipv=settings.STOCKFISH_MULTIPV,
        threads=settings.STOCKFISH_THREADS,
        hash_mb=settings.STOCKFISH_HASH_MB,
    )


def _close_worker_engine() -> None:
    """Best-effort close of the UCI process owned by this worker child."""
    global _worker_engine

    engine = _worker_engine
    _worker_engine = None
    if engine is None:
        return

    try:
        engine.quit()
    except Exception:
        logger.error(
            "stockfish.worker.close_failed",
            extra={"status": "failed", "failure_kind": "engine_shutdown"},
            exc_info=True,
        )


def get_worker_engine(
    wrapper: StockfishEngine | None = None,
) -> SimpleEngine:
    """Return this prefork child's live, lazily created Stockfish process."""
    global _worker_engine

    if _worker_engine is not None:
        try:
            _worker_engine.ping()
        except Exception:
            logger.warning(
                "stockfish.worker.restarting",
                extra={"status": "restarting", "failure_kind": "engine_unavailable"},
                exc_info=True,
            )
            _close_worker_engine()

    if _worker_engine is None:
        owner = wrapper or _stockfish_wrapper()
        _worker_engine = owner.open_engine()

    return _worker_engine


@worker_process_shutdown.connect
def _shutdown_worker_engine(**_: object) -> None:
    """Release this prefork child's Stockfish process during warm shutdown."""
    _close_worker_engine()


def _claim_stmt(game_id: int) -> Update:
    """The claim UPDATE: take ownership of a game and hand back its PGN.

    A single statement, so two workers racing for the same row cannot both win:
    under READ COMMITTED the loser blocks on the row lock and re-evaluates the
    status predicate after the winner commits, matching zero rows.

    Exposed separately from :func:`_claim_game_for_analysis` so the concurrency
    test can drive it on two connections with manual commit control.
    """
    return (
        update(Game)
        .where(
            Game.id == game_id,
            Game.analysis_status.in_(ANALYSIS_STATUS_CLAIMABLE),
        )
        .values(
            analysis_status="running",
            analysis_started_at=func.now(),
            analysis_error=None,
            analysis_attempts=Game.analysis_attempts + 1,
        )
        .returning(Game.pgn_content)
        .execution_options(synchronize_session=False)
    )


def _claim_game_for_analysis(game_id: int) -> str | None:
    """Claim a game in its own short transaction; `None` if it wasn't claimable."""
    with get_sync_db_session() as session:
        return session.execute(_claim_stmt(game_id)).scalar_one_or_none()


def _mark_analysis_failed(game_id: int, exc: Exception) -> None:
    """Move a claimed game to `failed` in a fresh session.

    The session that ran Stockfish is already gone, and a failure here must not
    replace the original exception — the caller re-raises that one.
    """
    try:
        with get_sync_db_session() as session:
            session.execute(
                update(Game)
                .where(Game.id == game_id)
                .values(
                    analysis_status="failed",
                    analysis_error=str(exc)[:_ANALYSIS_ERROR_MAX_LEN],
                )
                .execution_options(synchronize_session=False)
            )
    except Exception:
        logger.error(
            "analysis.task.failure_persist_failed",
            extra={
                "game_id": game_id,
                "status": "failed",
                "failure_kind": "database",
            },
            exc_info=True,
        )


def _save_analysis_result(game_id: int, analysis_data: dict) -> None:
    """Persist the analysis and close the lifecycle in one short transaction.

    `is_analyzed` and `analysis_status` are written together — the invariant
    is_analyzed=True <=> status='completed' must never be observable as broken.
    """
    with get_sync_db_session() as session:
        session.execute(
            update(Game)
            .where(Game.id == game_id)
            .values(
                analysis_data=analysis_data,
                is_analyzed=True,
                analysis_status="completed",
                analysis_error=None,
            )
            .execution_options(synchronize_session=False)
        )


@celery_app.task(bind=True)
def analyze_game(self: Task, game_id: int) -> None:
    """Run Stockfish on a single Game row and persist the result.

    Three phases (per ARCHITECTURE.md §7), because Stockfish runs for minutes
    and must not hold a DB connection while it does:

    A. claim — atomic `pending|failed -> running`, returns the PGN (short txn)
    B. analyse — engine + classifier, no session open
    C. save — analysis_data + is_analyzed + `completed` (short txn)

    Phase A doubles as the idempotency guard: whoever loses the race gets no
    PGN back and returns without touching the engine.
    """
    started_at = perf_counter()
    with log_context(task_id=self.request.id, game_id=game_id):
        logger.info(
            "analysis.task.started",
            extra={"game_id": game_id, "status": "started"},
        )

        # Checked before the claim: a game marked `running` by a worker that then
        # bails out has nothing to move it back — there is no lease reaper.
        if not settings.STOCKFISH_PATH:
            logger.error(
                "analysis.task.failed",
                extra={
                    "game_id": game_id,
                    "status": "failed",
                    "failure_kind": "configuration",
                    "duration_ms": round((perf_counter() - started_at) * 1000, 3),
                },
            )
            return None

        pgn_content = _claim_game_for_analysis(game_id)
        if pgn_content is None:
            logger.info(
                "analysis.task.skipped",
                extra={
                    "game_id": game_id,
                    "status": "not_claimable",
                    "duration_ms": round((perf_counter() - started_at) * 1000, 3),
                },
            )
            return None

        try:
            wrapper = _stockfish_wrapper()
            engine = get_worker_engine(wrapper)
            raw_moves = wrapper.analyse_game(pgn_content, engine=engine)
            analysis_data = build_analysis_data(raw_moves)
        except Exception as exc:
            logger.error(
                "analysis.task.failed",
                extra={
                    "game_id": game_id,
                    "status": "failed",
                    "failure_kind": "analysis",
                    "duration_ms": round((perf_counter() - started_at) * 1000, 3),
                },
                exc_info=True,
            )
            _mark_analysis_failed(game_id, exc)
            raise

        _save_analysis_result(game_id, analysis_data)

        logger.info(
            "analysis.task.succeeded",
            extra={
                "game_id": game_id,
                "status": "succeeded",
                "moves_count": len(raw_moves),
                "duration_ms": round((perf_counter() - started_at) * 1000, 3),
            },
        )

        return None


def _mark_report_failed(player_name: str, language: str) -> None:
    """Flag a report `failed` in a fresh session.

    The session that ran the LLM call is already gone (or rolled back), so the
    failure gets its own short transaction.
    """
    try:
        with get_sync_db_session() as session:
            mark_failed_sync(session, player_name, language)
    except Exception:
        logger.error(
            "report.task.failure_persist_failed",
            extra={
                "player_name_normalized": player_name.casefold(),
                "language": language,
                "status": "failed",
                "failure_kind": "database",
            },
            exc_info=True,
        )


@celery_app.task(
    bind=True,
    autoretry_for=(LLMError,),
    max_retries=REPORT_LLM_MAX_RETRIES,
    retry_backoff=True,
    retry_jitter=True,
)
def generate_player_report(
    self: Task,
    player_name: str,
    language: str,
) -> None:
    """Generate a player's scouting report via the LLM and persist it.

    Background twin of :func:`analyze_game`, and phased the same way, because
    the LLM call can take minutes and must not hold a DB connection:

    A. context — the Phase 4 aggregations for this player (short txn)
    B. generate — prompt + provider call, no session open
    C. save — ``report_text`` + snapshot counters, status ``ready`` (short txn)

    The row is already ``generating`` when we get here — the router claims it
    before enqueueing, so the task only ever writes the outcome. Progress is
    tracked purely through ``PlayerReport.status`` (no Celery result backend).
    Numbers come from :func:`build_report_context`; the model only narrates them.
    Transient LLM failures get three bounded retries while the atomic report
    claim remains ``generating``. The final LLM failure and every non-LLM
    exception mark the row ``failed`` and propagate to Celery.
    """
    started_at = perf_counter()
    with log_context(
        task_id=self.request.id,
        player_name_normalized=player_name.casefold(),
        language=language,
    ):
        logger.info(
            "report.task.started",
            extra={"status": "started", "retry_number": self.request.retries},
        )

        try:
            with get_sync_db_session() as session:
                ctx = build_report_context(session, player_name, language)

            # Nothing analyzed yet → no facts to narrate; don't poke the model.
            if ctx.analyzed_games_count == 0:
                logger.warning(
                    "report.task.failed",
                    extra={
                        "status": "failed",
                        "failure_kind": "insufficient_data",
                        "duration_ms": round((perf_counter() - started_at) * 1000, 3),
                    },
                )
                _mark_report_failed(player_name, language)
                return None

            system, user = build_messages(ctx)
            text = get_llm_provider().generate(system, user)

            with get_sync_db_session() as session:
                save_report_result_sync(
                    session,
                    player_name,
                    language,
                    report_text=text,
                    analyzed_games_count=ctx.analyzed_games_count,
                    last_game_played_at=ctx.last_game_played_at,
                )
        except LLMError:
            final_attempt = self.request.retries >= REPORT_LLM_MAX_RETRIES
            logger.warning(
                "report.task.failed" if final_attempt else "report.task.retrying",
                extra={
                    "status": "failed" if final_attempt else "retrying",
                    "failure_kind": "llm",
                    "retry_number": self.request.retries,
                    "duration_ms": round((perf_counter() - started_at) * 1000, 3),
                },
                exc_info=True,
            )
            if final_attempt:
                _mark_report_failed(player_name, language)
            raise
        except Exception:
            logger.error(
                "report.task.failed",
                extra={
                    "status": "failed",
                    "failure_kind": "unexpected",
                    "duration_ms": round((perf_counter() - started_at) * 1000, 3),
                },
                exc_info=True,
            )
            _mark_report_failed(player_name, language)
            raise

        logger.info(
            "report.task.succeeded",
            extra={
                "status": "succeeded",
                "analyzed_games_count": ctx.analyzed_games_count,
                "duration_ms": round((perf_counter() - started_at) * 1000, 3),
            },
        )

        return None
