import logging

from celery import Celery

from app.config import settings

logger = logging.getLogger(__name__)

celery_app = Celery(
    "chess_lab",
    broker=settings.redis_url,
)


@celery_app.task
def analyze_game(game_id: int) -> None:
    logger.info("analyzing %s", game_id)
    return None
