import logging

from celery import Celery

from app.config import settings
from app.database import get_sync_db_session
from app.models.db import Game
from app.services.analysis.classifier import build_analysis_data
from app.services.analysis.engine import StockfishEngine

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

    if not settings.stockfish_path:
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

        engine = StockfishEngine(settings.stockfish_path)
        raw_moves = engine.analyse_game(game.pgn_content)

        game.analysis_data = build_analysis_data(raw_moves)
        game.is_analyzed = True

        logger.info(
            "analyze_game: game_id=%s analyzed (%d moves)",
            game_id,
            len(raw_moves),
        )

    return None
