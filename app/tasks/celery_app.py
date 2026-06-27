import logging

from celery import Celery

from app.config import settings
from app.database import get_sync_db_session
from app.models.db import Game
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


@celery_app.task
def analyze_game(game_id: int) -> None:
    """Run Stockfish on a single Game row and persist the result.

    Pipeline (per ARCHITECTURE.md §7):
        load Game (sync session) → engine.analyse_game(pgn) →
        build_analysis_data(...) → write analysis_data + is_analyzed=True.
    The sync session context manager handles commit/rollback.
    """
    logger.info("analyze_game: starting for game_id=%s", game_id)

    if not settings.STOCKFISH_PATH:
        logger.error(
            "analyze_game: STOCKFISH_PATH is not configured; aborting game_id=%s",
            game_id,
        )
        return None

    with get_sync_db_session() as session:
        game = session.get(Game, game_id)
        if game is None:
            logger.warning("analyze_game: game_id=%s not found, skipping", game_id)
            return None

        # Idempotency guard: the batch endpoint may enqueue the same game twice
        # before the worker drains the queue. Stockfish is the most expensive
        # operation in the system — a repeated run is unacceptable.
        if game.is_analyzed:
            logger.info(
                "analyze_game: game_id=%s already analyzed, skipping", game_id
            )
            return None

        engine = StockfishEngine(
            settings.STOCKFISH_PATH,
            depth=settings.STOCKFISH_DEPTH,
            multipv=settings.STOCKFISH_MULTIPV,
            threads=settings.STOCKFISH_THREADS,
            hash_mb=settings.STOCKFISH_HASH_MB,
        )
        raw_moves = engine.analyse_game(game.pgn_content)

        game.analysis_data = build_analysis_data(raw_moves)
        game.is_analyzed = True

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
