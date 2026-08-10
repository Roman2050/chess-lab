from __future__ import annotations

from app.schemas.report import ReportContext
from app.schemas.stats import OpeningStat, PhaseStats

# Phases are rendered in a fixed order so the digest is fully deterministic
# regardless of how the upstream dict happens to be ordered.
_PHASE_ORDER: tuple[str, ...] = ("opening", "middlegame", "endgame")

# Marker strings for missing slices. Kept as constants so the "never silently
# skip, never fake a zero" rule is enforced from one place.
_NO_DATA = "no data"
_NOT_ENOUGH = "not enough analyzed games"

_SYSTEM_PROMPT = """You are a chess coach writing a concise scouting report about an \
opponent, to help a player prepare for an upcoming game.

You are a narrator, not an analyst. Follow these HARD rules:
- Rely EXCLUSIVELY on the facts provided in the user message. Treat them as the \
only source of truth.
- NEVER invent or guess numbers, openings, games, or trends. Do not compute \
anything yourself and do not extrapolate beyond the given facts.
- If a section is marked "{no_data}" or "{not_enough}", say so plainly and move on \
— do not fill the gap with assumptions or zeros.
- Do not contradict or re-derive the numbers; quote them as given.

Structure the report in exactly these four sections:
1. General characterization of the player.
2. Strengths.
3. Weaknesses.
4. Concrete recommendations for preparation.

Tone: concise and to the point, no filler.

Write the report in {language}."""


def build_system_prompt(language: str) -> str:
    """Render the system prompt, injecting the target report language.

    Pure and deterministic — same ``language`` always yields the same string.
    """
    return _SYSTEM_PROMPT.format(language=language, no_data=_NO_DATA, not_enough=_NOT_ENOUGH)


def render_context_to_prompt(ctx: ReportContext) -> str:
    """Render a :class:`ReportContext` into a compact, language-neutral digest.

    Pure function: every number is taken verbatim from ``ctx`` (already rounded
    upstream), nothing is recomputed, and missing slices are surfaced explicitly
    as ``"no data"`` / ``"not enough analyzed games"`` rather than skipped or
    faked as zeros. The output carries only facts — all instructions live in the
    system prompt.
    """
    sections = [
        _render_player(ctx),
        _render_overall_wp(ctx),
        _render_by_phase(ctx),
        _render_error_profile(ctx),
        _render_openings(ctx),
        _render_insights(ctx),
    ]
    return "\n\n".join(sections)


def build_messages(ctx: ReportContext) -> tuple[str, str]:
    """Return ``(system, user)`` messages ready for the LLM provider."""
    system = build_system_prompt(ctx.language)
    user = render_context_to_prompt(ctx)
    return system, user


def _render_player(ctx: ReportContext) -> str:
    covers = (
        ctx.last_game_played_at.isoformat() if ctx.last_game_played_at is not None else _NO_DATA
    )
    return (
        "PLAYER / GAMES\n"
        f"player: {ctx.player}\n"
        f"analyzed {ctx.analyzed_games_count} of {ctx.total_games_count} total games\n"
        f"covers games up to: {covers}"
    )


def _render_overall_wp(ctx: ReportContext) -> str:
    wp = ctx.wp
    overall = _fmt(wp.wp_loss) if wp.wp_loss is not None else _NOT_ENOUGH
    white = wp.wp_loss_by_color.get("white")
    black = wp.wp_loss_by_color.get("black")
    return (
        "OVERALL WIN-PROBABILITY LOSS\n"
        f"overall: {overall} "
        "(average win probability lost per move, in percentage points; "
        "lower is better — <=2.5 strong, >2.5-4 solid, "
        ">4-6 inconsistent, >6 weak)\n"
        f"as white: {_fmt(white) if white is not None else _NO_DATA}\n"
        f"as black: {_fmt(black) if black is not None else _NO_DATA}"
    )


def _render_by_phase(ctx: ReportContext) -> str:
    lines = ["BY PHASE (win-prob loss + error rates + moves analyzed)"]
    for phase in _PHASE_ORDER:
        wp = ctx.wp.wp_loss_by_phase.get(phase)
        stats = ctx.accuracy_by_phase.get(phase)
        lines.append(f"{phase}: {_phase_line(wp, stats)}")
    return "\n".join(lines)


def _phase_line(wp: float | None, stats: PhaseStats | None) -> str:
    if stats is None or stats.moves_count == 0 or wp is None:
        return f"{_NO_DATA} (player did not reach this phase in analyzed games)"
    return (
        f"wp loss {_fmt(wp)}; "
        f"inaccuracies {_fmt(stats.inaccuracy_rate)}%, "
        f"mistakes {_fmt(stats.mistake_rate)}%, "
        f"blunders {_fmt(stats.blunder_rate)}%; "
        f"moves {stats.moves_count}"
    )


def _render_error_profile(ctx: ReportContext) -> str:
    lines = ["ERROR PROFILE"]

    by_piece = ctx.errors.errors_by_piece
    if by_piece:
        lines.append("errors by piece:")
        for row in by_piece:
            lines.append(
                f"  {row.piece_name}: {row.error_count} errors "
                f"({_fmt(row.error_pct)}% of all errors)"
            )
    else:
        lines.append(f"errors by piece: {_NO_DATA}")

    hotspots = [row.move_num for row in ctx.errors.errors_by_move_number]
    if hotspots:
        lines.append("error hotspot move numbers: " + ", ".join(str(m) for m in hotspots))
    else:
        lines.append(f"error hotspot move numbers: {_NO_DATA}")

    return "\n".join(lines)


def _render_openings(ctx: ReportContext) -> str:
    lines = ["OPENINGS (top by games played)"]
    if not ctx.openings:
        lines.append(_NO_DATA)
        return "\n".join(lines)
    for opening in ctx.openings:
        lines.append(f"  {_opening_line(opening)}")
    return "\n".join(lines)


def _opening_line(opening: OpeningStat) -> str:
    wp = _fmt(opening.wp_loss_in_opening) if opening.wp_loss_in_opening is not None else _NO_DATA
    return (
        f"{opening.opening_name}: {opening.games_count} games, "
        f"win rate {_fmt(opening.win_rate)}%, wp loss in opening {wp}"
    )


def _render_insights(ctx: ReportContext) -> str:
    ins = ctx.insights
    hotspots = (
        ", ".join(str(m) for m in ins.error_hotspot_moves) if ins.error_hotspot_moves else _NO_DATA
    )
    return (
        "DETECTED INSIGHTS (deterministic, code-derived facts)\n"
        f"overall skill level: {ins.overall_skill}\n"
        f"weakest phase: {ins.weakest_phase or _NO_DATA}\n"
        f"strongest phase: {ins.strongest_phase or _NO_DATA}\n"
        f"weaker color: {ins.weaker_color or _NO_DATA}\n"
        f"dominant error piece: {ins.dominant_error_piece or _NO_DATA}\n"
        f"error hotspot moves: {hotspots}\n"
        f"best-performing openings: {_join_or_no_data(ins.best_openings)}\n"
        f"worst-performing openings: {_join_or_no_data(ins.worst_openings)}"
    )


def _join_or_no_data(names: list[str]) -> str:
    return ", ".join(names) if names else _NO_DATA


def _fmt(value: float) -> str:
    """Render a number exactly as stored (already rounded upstream)."""
    return f"{value}"
