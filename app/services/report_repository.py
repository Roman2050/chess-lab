from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, Insert, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.config import settings
from app.models.db import Game, PlayerReport


async def get_report(
    db: AsyncSession,
    player_name: str,
    language: str,
) -> PlayerReport | None:
    """Fetch the persisted report for a (player, language) pair, if any."""
    stmt = select(PlayerReport).where(
        func.lower(PlayerReport.player_name) == func.lower(player_name),
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
                func.lower(Game.white_player) == func.lower(player_name),
                func.lower(Game.black_player) == func.lower(player_name),
            ),
            Game.is_analyzed.is_(True),
        )
    )
    result = await db.execute(stmt)
    return result.scalar_one()


def _lease_expired() -> ColumnElement[bool]:
    """SQL predicate: the row has sat in `generating` longer than its lease.

    Evaluated by Postgres against the same clock that wrote ``updated_at``;
    ``updated_at`` is naive, so comparing it to the application's clock would
    silently depend on how the two hosts are configured.
    """
    lease = timedelta(seconds=settings.REPORT_GENERATION_LEASE_SECONDS)
    return PlayerReport.updated_at < func.now() - lease


def generating_claim_stmt(
    player_name: str,
    language: str,
    analyzed_games_count: int,
) -> Insert:
    """The claim INSERT: take ownership of a (player, language) report.

    A single statement, so two requests racing for the same player cannot both
    win — the loser's ``ON CONFLICT`` predicate no longer matches and it gets no
    row back. A row that is already ``generating`` is reclaimable only once its
    lease expired: nothing else would ever move it out of that state, because
    the worker that owned it is gone.

    ``analyzed_games_count`` is written on insert only. On an existing row the
    snapshot still describes the ``report_text`` we are about to replace, and
    bumping it here would make a failed generation look up to date.

    Exposed separately from :func:`upsert_generating` so tests can inspect the
    statement without a database.
    """
    return (
        pg_insert(PlayerReport)
        .values(
            player_name=player_name,
            language=language,
            status="generating",
            analyzed_games_count=analyzed_games_count,
        )
        .on_conflict_do_update(
            index_elements=(
                func.lower(PlayerReport.player_name),
                PlayerReport.language,
            ),
            # A core INSERT bypasses the ORM's `onupdate`, and the lease is read
            # off `updated_at` — so it is set explicitly.
            set_={"status": "generating", "updated_at": func.now()},
            where=or_(PlayerReport.status != "generating", _lease_expired()),
        )
        .returning(PlayerReport.id)
    )


async def upsert_generating(
    db: AsyncSession,
    player_name: str,
    language: str,
    analyzed_games_count: int,
) -> bool:
    """Claim the report for generation; ``True`` when the caller may enqueue.

    Commits: the FastAPI session dependency never does, and the worker must not
    start before the ``generating`` row is visible to other requests.
    """
    claimed = await db.scalar(
        generating_claim_stmt(player_name, language, analyzed_games_count)
    )
    await db.commit()
    return claimed is not None


async def release_generating(
    db: AsyncSession,
    player_name: str,
    language: str,
) -> None:
    """Undo a claim whose task never made it onto the queue.

    A placeholder row (no ``report_text`` yet) is deleted — it describes a report
    that will never exist; a row still holding an older text is only flagged
    ``failed``, so that text stays servable. Without this the row would answer
    every request with ALREADY_GENERATING until its lease ran out.
    """
    deleted = await db.execute(
        delete(PlayerReport)
        .where(
            func.lower(PlayerReport.player_name) == func.lower(player_name),
            PlayerReport.language == language,
            PlayerReport.status == "generating",
            PlayerReport.report_text.is_(None),
        )
        .execution_options(synchronize_session=False)
    )
    if deleted.rowcount == 0:
        await db.execute(
            update(PlayerReport)
            .where(
                func.lower(PlayerReport.player_name) == func.lower(player_name),
                PlayerReport.language == language,
                PlayerReport.status == "generating",
            )
            .values(status="failed")
            .execution_options(synchronize_session=False)
        )
    await db.commit()


async def is_generation_stale(db: AsyncSession, report: PlayerReport | None) -> bool:
    """True when a `generating` row has outlived its lease (its worker is gone)."""
    if report is None or report.status != "generating":
        return False
    stmt = select(_lease_expired()).where(PlayerReport.id == report.id)
    return bool(await db.scalar(stmt))


def get_report_sync(
    db: Session,
    player_name: str,
    language: str,
) -> PlayerReport | None:
    """Sync twin of :func:`get_report` for Celery tasks."""
    stmt = select(PlayerReport).where(
        func.lower(PlayerReport.player_name) == func.lower(player_name),
        PlayerReport.language == language,
    )
    return db.execute(stmt).scalar_one_or_none()


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
