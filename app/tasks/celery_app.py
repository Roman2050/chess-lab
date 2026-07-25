import logging

from celery import Celery
from sqlalchemy import Update, func, update

from app.config import settings
from app.database import get_sync_db_session
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

# `analysis_error` is an unbounded text column, but a whole traceback inside a
# status field is noise — the full exception goes to the log instead.
_ANALYSIS_ERROR_MAX_LEN = 1000


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
        logger.exception(
            "analyze_game: could not record failure for game_id=%s "
            "(row stays 'running')",
            game_id,
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


@celery_app.task
def analyze_game(game_id: int) -> None:
    """Run Stockfish on a single Game row and persist the result.

    Three phases (per ARCHITECTURE.md §7), because Stockfish runs for minutes
    and must not hold a DB connection while it does:

    A. claim — atomic `pending|failed -> running`, returns the PGN (short txn)
    B. analyse — engine + classifier, no session open
    C. save — analysis_data + is_analyzed + `completed` (short txn)

    Phase A doubles as the idempotency guard: whoever loses the race gets no
    PGN back and returns without touching the engine.
    """
    logger.info("analyze_game: starting for game_id=%s", game_id)

    # Checked before the claim: a game marked `running` by a worker that then
    # bails out has nothing to move it back — there is no lease reaper.
    if not settings.STOCKFISH_PATH:
        logger.error(
            "analyze_game: STOCKFISH_PATH is not configured; aborting game_id=%s",
            game_id,
        )
        return None

    pgn_content = _claim_game_for_analysis(game_id)
    if pgn_content is None:
        logger.info(
            "analyze_game: game_id=%s not claimable (missing, already analyzed, "
            "or taken by another worker), skipping",
            game_id,
        )
        return None

    try:
        engine = StockfishEngine(
            settings.STOCKFISH_PATH,
            depth=settings.STOCKFISH_DEPTH,
            multipv=settings.STOCKFISH_MULTIPV,
            threads=settings.STOCKFISH_THREADS,
            hash_mb=settings.STOCKFISH_HASH_MB,
        )
        raw_moves = engine.analyse_game(pgn_content)
        analysis_data = build_analysis_data(raw_moves)
    except Exception as exc:
        logger.exception("analyze_game: analysis failed for game_id=%s", game_id)
        _mark_analysis_failed(game_id, exc)
        raise

    _save_analysis_result(game_id, analysis_data)

    logger.info(
        "analyze_game: game_id=%s analyzed (%d moves)",
        game_id,
        len(raw_moves),
    )

    return None


@celery_app.task
def generate_player_report(player_name: str, language: str) -> None:
    """Generate a player's scouting report via the LLM and persist it.

    Background twin of :func:`analyze_game`: the LLM call is slow, so progress is
    tracked purely through ``PlayerReport.status`` (no Celery result backend).
    Numbers are computed deterministically in :func:`build_report_context`; the
    model only narrates them. An :class:`LLMError` must never crash the worker —
    we catch it and mark the report ``failed``.
    """
    logger.info("generate_player_report: starting for player=%s", player_name)

    with get_sync_db_session() as session:
        try:
            ctx = build_report_context(session, player_name, language)

            # Nothing analyzed yet → no facts to narrate; don't poke the model.
            if ctx.analyzed_games_count == 0:
                logger.warning(
                    "generate_player_report: no analyzed games for player=%s, "
                    "marking failed",
                    player_name,
                )
                mark_failed_sync(session, player_name, language)
                return None

            system, user = build_messages(ctx)
            provider = get_llm_provider()
            text = provider.generate(system, user)

            save_report_result_sync(
                session,
                player_name,
                language,
                report_text=text,
                analyzed_games_count=ctx.analyzed_games_count,
                last_game_played_at=ctx.last_game_played_at,
            )
            logger.info(
                "generate_player_report: finished for player=%s (%d analyzed games)",
                player_name,
                ctx.analyzed_games_count,
            )
        except LLMError:
            logger.exception(
                "generate_player_report: report generation failed for %s",
                player_name,
            )
            mark_failed_sync(session, player_name, language)
            return None

    return None
