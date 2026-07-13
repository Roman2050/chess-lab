from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.db import Game, PlayerReport


async def get_report(
    db: AsyncSession,
    player_name: str,
    language: str,
) -> PlayerReport | None:
    """Fetch the persisted report for a (player, language) pair, if any."""
    stmt = select(PlayerReport).where(
        PlayerReport.player_name == player_name,
        PlayerReport.language == language,
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def count_analyzed_games(db: AsyncSession, player_name: str) -> int:
    """Count the player's analyzed games (either color) without materializing rows."""
    stmt = (
        select(func.count())
        .select_from(Game)
        .where(
            or_(
                Game.white_player == player_name,
                Game.black_player == player_name,
            ),
            Game.is_analyzed.is_(True),
        )
    )
    result = await db.execute(stmt)
    return result.scalar_one()


def get_report_sync(
    db: Session,
    player_name: str,
    language: str,
) -> PlayerReport | None:
    """Sync twin of :func:`get_report` for Celery tasks."""
    stmt = select(PlayerReport).where(
        PlayerReport.player_name == player_name,
        PlayerReport.language == language,
    )
    return db.execute(stmt).scalar_one_or_none()


def upsert_generating_sync(
    db: Session,
    player_name: str,
    language: str,
    analyzed_games_count: int,
) -> PlayerReport:
    """Mark a (player, language) report as ``generating``.

    Creates a fresh row (``report_text`` left NULL) when none exists, otherwise
    flips the existing one to ``generating`` while keeping the old
    ``report_text`` so callers can still serve the previous report meanwhile.
    Does not commit — the surrounding ``get_sync_db_session`` owns the txn.
    """
    report = get_report_sync(db, player_name, language)
    if report is None:
        report = PlayerReport(
            player_name=player_name,
            language=language,
            status="generating",
            analyzed_games_count=analyzed_games_count,
        )
        db.add(report)
    else:
        report.status = "generating"
        report.analyzed_games_count = analyzed_games_count
    db.flush()
    return report


def save_report_result_sync(
    db: Session,
    player_name: str,
    language: str,
    *,
    report_text: str,
    analyzed_games_count: int,
    last_game_played_at: datetime | None,
) -> None:
    """Persist a successful generation: text + snapshot counters, status ``ready``."""
    report = get_report_sync(db, player_name, language)
    if report is None:
        report = PlayerReport(player_name=player_name, language=language)
        db.add(report)
    report.report_text = report_text
    report.analyzed_games_count = analyzed_games_count
    report.last_game_played_at = last_game_played_at
    report.status = "ready"
    db.flush()


def mark_failed_sync(db: Session, player_name: str, language: str) -> None:
    """Flag the report as ``failed``, preserving any previous ``report_text``."""
    report = get_report_sync(db, player_name, language)
    if report is None:
        report = PlayerReport(
            player_name=player_name,
            language=language,
            status="failed",
        )
        db.add(report)
    else:
        report.status = "failed"
    db.flush()
