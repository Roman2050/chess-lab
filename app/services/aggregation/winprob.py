from __future__ import annotations

import math

from app.models.db import Game
from app.services.aggregation.helpers import (
    iter_player_moves,
    resolve_player_color,
)

_PHASES: tuple[str, ...] = ("opening", "middlegame", "endgame")

# Логістична константа (Lichess-калібрування) — переводить перевагу в cp у
# виграшні шанси. Винесена константою; калібрується у Phase 6 Чат 5.
WP_SCALE = 0.00368208

# Каліброване на реальних даних вікно «живої» позиції. Ходи, де шанси
# сторони, що ходить, уже <= 20% або >= 90%, не входять у WP-середні:
# насичена сигмоїда інакше розбавляє метрику майже нульовими втратами.
LIVE_WP_MIN = 20.0
LIVE_WP_MAX = 90.0


def win_prob(cp_mover: float) -> float:
    """Виграшні шанси сторони, що ходить (0..100 %), з її переваги в cp."""
    return 100.0 / (1.0 + math.exp(-WP_SCALE * cp_mover))


def move_wp_loss(move: dict) -> float | None:
    """Втрачені % шансів у живій позиції, або ``None`` якщо хід не враховується.

    eval_before / eval_after — White-relative (див. engine.py/classifier.py),
    тож для чорних інвертуємо знак. Ходи без оцінок і ходи поза каліброваним
    вікном ``LIVE_WP_MIN < WP_before < LIVE_WP_MAX`` пропускаються. Читаємо
    СИРІ eval (не кламповані); cap не потрібен.
    """
    eb = move.get("eval_before")
    ea = move.get("eval_after")
    if eb is None or ea is None:
        return None
    sign = 1 if move.get("color") == "White" else -1
    wp_before = win_prob(sign * eb)
    if not LIVE_WP_MIN < wp_before < LIVE_WP_MAX:
        return None
    return max(0.0, wp_before - win_prob(sign * ea))


def compute_player_wp_loss(games: list[Game], player_name: str) -> dict:
    """WP-двійник compute_player_acpl: overall / by_color / by_phase.

    - overall та by_color — середнє з per-game середніх wp_loss (per-game =
      mean(wp_loss) по ходах гравця), як у compute_player_acpl.
    - by_phase — плоске середнє wp_loss по всіх ходах фази.
    - Порожні зрізи → None (не 0).
    Ходи без eval_before/eval_after та ходи у вирішених позиціях
    (move_wp_loss -> None) пропускаються.
    Партія без жодного придатного ходу не потрапляє у per-game середні.
    """
    if not games:
        return _empty_result(player_name)

    per_game_wp_losses: list[float] = []
    per_game_white_wp_losses: list[float] = []
    per_game_black_wp_losses: list[float] = []
    phase_losses: dict[str, list[float]] = {phase: [] for phase in _PHASES}
    total_moves_analyzed = 0
    games_count = 0

    for game in games:
        color = resolve_player_color(game, player_name)
        moves = list(iter_player_moves(game, color))

        game_wp_losses: list[float] = []
        for move in moves:
            wp = move_wp_loss(move)
            if wp is None:
                continue
            game_wp_losses.append(wp)
            phase = move.get("phase")
            if phase in phase_losses:
                phase_losses[phase].append(wp)

        # Партія без жодного придатного ходу (усі без eval) не дає per-game
        # точки, щоб не забруднювати середнє «привидом».
        if not game_wp_losses:
            continue

        game_wp = sum(game_wp_losses) / len(game_wp_losses)
        per_game_wp_losses.append(game_wp)
        if color == "White":
            per_game_white_wp_losses.append(game_wp)
        else:
            per_game_black_wp_losses.append(game_wp)

        total_moves_analyzed += len(game_wp_losses)
        games_count += 1

    if games_count == 0:
        return _empty_result(player_name)

    return {
        "player": player_name,
        "games_count": games_count,
        "total_moves_analyzed": total_moves_analyzed,
        "wp_loss": _mean_rounded(per_game_wp_losses),
        "wp_loss_by_color": {
            "white": _mean_rounded(per_game_white_wp_losses),
            "black": _mean_rounded(per_game_black_wp_losses),
        },
        "wp_loss_by_phase": {
            phase: _mean_rounded(phase_losses[phase]) for phase in _PHASES
        },
    }


def _mean_rounded(values: list[float], ndigits: int = 2) -> float | None:
    """Average a list to 2 decimals, or ``None`` for empty input.

    ``None`` rather than ``0`` because a missing slice (no black games, no
    endgame moves) is qualitatively different from "averaged to zero". WP is
    finer-grained than ACPL, so we keep 2 decimals by default.
    """
    if not values:
        return None
    return round(sum(values) / len(values), ndigits)


def _empty_result(player_name: str) -> dict:
    """Zero-game response shape, kept in sync with the populated branch."""
    return {
        "player": player_name,
        "games_count": 0,
        "total_moves_analyzed": 0,
        "wp_loss": None,
        "wp_loss_by_color": {"white": None, "black": None},
        "wp_loss_by_phase": {phase: None for phase in _PHASES},
    }
