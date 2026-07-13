from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.report import ReportAction, decide_report_action

THRESHOLD = 20


def _report(
    *,
    status: str = "ready",
    report_text: str | None = "cached text",
    analyzed_games_count: int = 0,
) -> SimpleNamespace:
    """Stand-in for a PlayerReport row (decision logic never touches the DB)."""
    return SimpleNamespace(
        status=status,
        report_text=report_text,
        analyzed_games_count=analyzed_games_count,
    )


@pytest.mark.unit
def test_no_report_insufficient():
    assert (
        decide_report_action(5, None, THRESHOLD)
        == ReportAction.INSUFFICIENT_GAMES
    )


@pytest.mark.unit
def test_no_report_enough():
    assert decide_report_action(20, None, THRESHOLD) == ReportAction.GENERATE


@pytest.mark.unit
def test_deleted_text_regenerates():
    report = _report(report_text=None, analyzed_games_count=0)
    assert decide_report_action(50, report, THRESHOLD) == ReportAction.GENERATE


@pytest.mark.unit
def test_existing_below_threshold():
    report = _report(analyzed_games_count=40)
    assert decide_report_action(55, report, THRESHOLD) == ReportAction.UP_TO_DATE


@pytest.mark.unit
def test_existing_at_threshold():
    report = _report(analyzed_games_count=40)
    assert decide_report_action(60, report, THRESHOLD) == ReportAction.GENERATE


@pytest.mark.unit
def test_already_generating():
    report = _report(status="generating", report_text=None)
    assert (
        decide_report_action(100, report, THRESHOLD)
        == ReportAction.ALREADY_GENERATING
    )
