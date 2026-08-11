import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_async_db
from app.schemas.report import (
    ReportRequestResponse,
    ReportResponse,
    ReportStatusResponse,
)
from app.security import require_report_quota
from app.services.report import ReportAction, decide_report_action
from app.services.report_repository import (
    count_analyzed_games,
    get_report,
    is_generation_stale,
    release_generating,
    upsert_generating,
)
from app.tasks.celery_app import generate_player_report

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/report")

REPORT_LANGUAGE_RESPONSE = {
    422: {"description": "The language is not in `REPORT_ALLOWED_LANGUAGES`."}
}
OPERATOR_ERROR_RESPONSES = {
    401: {"description": "Missing or invalid operator API key."},
    429: {"description": "Report quota exhausted. Retry after the seconds in `Retry-After`."},
    503: {"description": "The quota backend or report queue is unavailable."},
}


def require_allowed_report_language(
    language: str = Query(default=settings.REPORT_LANGUAGE),
) -> str:
    """Return an allowed report language or reject the request."""
    if language not in settings.REPORT_ALLOWED_LANGUAGES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Unsupported report language",
        )
    return language


@router.post(
    "/{username}",
    response_model=ReportRequestResponse,
    dependencies=[Depends(require_report_quota)],
    tags=["Reports"],
    summary="Request a scouting report",
    description=(
        "**Operator-only.** Decide whether a cached report needs generation or "
        "refresh. A new/background generation returns `202`; poll the report status, "
        "then use the public `GET` endpoint. Up-to-date or insufficient-data decisions "
        "return `200` without enqueueing work."
    ),
    response_description="Generation decision and cache-refresh counters.",
    responses={
        **OPERATOR_ERROR_RESPONSES,
        **REPORT_LANGUAGE_RESPONSE,
        202: {"description": "Report generation was queued or is already running."},
    },
)
async def request_report(
    username: str,
    response: Response,
    language: str = Depends(require_allowed_report_language),
    db: AsyncSession = Depends(get_async_db),
):
    """Decide whether to (re)generate the report and act on it.

    Thin HTTP glue: read state, run the pure decision, claim and enqueue when
    needed. The row is flipped to ``generating`` here, before the task exists —
    the worker only ever writes the outcome (``ready`` / ``failed``).
    """
    threshold = settings.REPORT_REFRESH_THRESHOLD
    current = await count_analyzed_games(db, username)
    report = await get_report(db, username, language)
    action = decide_report_action(
        current,
        report,
        threshold,
        generation_is_stale=await is_generation_stale(db, report),
    )

    # The read above can be stale by the time we act on it; the claim is the
    # authority on who generates, so a lost race downgrades the decision.
    if action is ReportAction.GENERATE and not await upsert_generating(
        db, username, language, current
    ):
        action = ReportAction.ALREADY_GENERATING

    report_games_count = report.analyzed_games_count if report is not None else None
    games_until_next_report: int | None = None

    match action:
        case ReportAction.GENERATE:
            try:
                await asyncio.to_thread(generate_player_report.delay, username, language)
            except Exception as exc:
                # The row is already `generating` but no task will ever finish
                # it, so hand the claim back instead of blocking the player
                # until the lease expires.
                await release_generating(db, username, language)
                logger.error(
                    "report.enqueue.failed",
                    extra={
                        "player_name_normalized": username.casefold(),
                        "language": language,
                        "status": "failed",
                        "failure_kind": "queue",
                    },
                    exc_info=True,
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Report generation queue is unavailable, try again later",
                ) from exc
            response.status_code = status.HTTP_202_ACCEPTED
            message = "Report generation started"
        case ReportAction.ALREADY_GENERATING:
            response.status_code = status.HTTP_202_ACCEPTED
            message = "Report is already being generated"
        case ReportAction.UP_TO_DATE:
            response.status_code = status.HTTP_200_OK
            games_until_next_report = threshold - (current - report.analyzed_games_count)
            message = "Report is up to date"
        case ReportAction.INSUFFICIENT_GAMES:
            response.status_code = status.HTTP_200_OK
            games_until_next_report = threshold - current
            message = f"Not enough analyzed games (need {threshold}, have {current})"

    return ReportRequestResponse(
        player=username,
        language=language,
        action=action.value,
        message=message,
        current_analyzed_games_count=current,
        report_games_count=report_games_count,
        games_until_next_report=games_until_next_report,
    )


@router.get(
    "/{username}",
    response_model=ReportResponse,
    tags=["Reports"],
    summary="Read a cached scouting report",
    description=(
        "**Public read.** Return the latest cached one-shot report and freshness "
        "metadata. The LLM narrates deterministic facts computed by Chess Lab; it "
        "does not perform the chess analysis."
    ),
    response_description="Cached narrative report and freshness metadata.",
    responses={
        **REPORT_LANGUAGE_RESPONSE,
        404: {"description": "No cached report exists for this player and language."},
    },
)
async def read_report(
    username: str,
    language: str = Depends(require_allowed_report_language),
    db: AsyncSession = Depends(get_async_db),
):
    """Return the cached report text, or 404 when nothing has been generated yet."""
    report = await get_report(db, username, language)
    if report is None or report.report_text is None:
        raise HTTPException(
            status_code=404,
            detail="No report available; trigger generation via POST",
        )

    current = await count_analyzed_games(db, username)
    is_stale = (current - report.analyzed_games_count) >= settings.REPORT_REFRESH_THRESHOLD

    return ReportResponse(
        player=username,
        language=language,
        report_text=report.report_text,
        status=report.status,
        analyzed_games_count=report.analyzed_games_count,
        current_analyzed_games_count=current,
        is_stale=is_stale,
        created_at=report.created_at,
        updated_at=report.updated_at,
        last_game_played_at=report.last_game_played_at,
    )


@router.get(
    "/{username}/status",
    response_model=ReportStatusResponse,
    tags=["Reports"],
    summary="Read report generation status",
    description=(
        "**Public read.** Poll the database-backed report state without transferring "
        "the potentially large report text."
    ),
    response_description="Current report state and refresh counters.",
    responses=REPORT_LANGUAGE_RESPONSE,
)
async def read_report_status(
    username: str,
    language: str = Depends(require_allowed_report_language),
    db: AsyncSession = Depends(get_async_db),
):
    """Report the generation state without returning the (potentially large) text."""
    threshold = settings.REPORT_REFRESH_THRESHOLD
    report = await get_report(db, username, language)
    current = await count_analyzed_games(db, username)

    if report is None:
        return ReportStatusResponse(
            player=username,
            language=language,
            status="none",
            has_report=False,
            analyzed_games_count=None,
            current_analyzed_games_count=current,
            games_until_next_report=threshold - current,
        )

    has_report = report.report_text is not None
    games_until_next_report = (
        threshold - (current - report.analyzed_games_count) if has_report else threshold - current
    )

    return ReportStatusResponse(
        player=username,
        language=language,
        status=report.status,
        has_report=has_report,
        analyzed_games_count=report.analyzed_games_count,
        current_analyzed_games_count=current,
        games_until_next_report=games_until_next_report,
    )
