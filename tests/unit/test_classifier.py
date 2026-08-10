import pytest

from app.services.analysis.classifier import (
    CP_LOSS_CAP,
    ONLY_MOVE_GAP_CP,
    _cp_loss_for_move,
    _is_only_move,
    build_analysis_data,
    classify_move,
)


@pytest.mark.unit
def test_classify_blunder() -> None:
    # cp_loss above the mistake ceiling (300) → blunder.
    assert classify_move(350) == "blunder"


@pytest.mark.unit
def test_classify_mistake() -> None:
    # 101..300 cp_loss falls in the mistake band (ARCHITECTURE.md §5.3).
    assert classify_move(150) == "mistake"


@pytest.mark.unit
def test_classify_best() -> None:
    # 0..10 cp_loss is the "best" band.
    assert classify_move(5) == "best"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("cp_loss", "expected"),
    [
        (0, "best"),
        (10, "best"),
        (11, "excellent"),
        (25, "excellent"),
        (26, "good"),
        (50, "good"),
        (51, "inaccuracy"),
        (100, "inaccuracy"),
        (101, "mistake"),
        (300, "mistake"),
        (301, "blunder"),
        (5000, "blunder"),
    ],
)
def test_classify_move_threshold_boundaries(cp_loss: int, expected: str) -> None:
    assert classify_move(cp_loss) == expected


@pytest.mark.unit
def test_cp_loss_capped_at_limit() -> None:
    """Mate-bearing moves (eval ±10000) are clamped to CP_LOSS_CAP, not ~10050.

    Without the upper clamp a single walk-into-mate move would dominate any
    ACPL average and push a strong player's number past 1000.
    """
    # White blunders from +50 into a forced mate for Black (-10000).
    assert _cp_loss_for_move("White", 50, -10000) == CP_LOSS_CAP
    # Black blunders from -50 into a forced mate for White (+10000).
    assert _cp_loss_for_move("Black", -50, 10000) == CP_LOSS_CAP


@pytest.mark.unit
def test_cp_loss_below_cap_unchanged() -> None:
    """Ordinary losses below the cap pass through untouched."""
    # White: 50 → -250 is a 300 cp loss, well under the cap.
    assert _cp_loss_for_move("White", 50, -250) == 300


@pytest.mark.unit
def test_cp_loss_floor_still_zero() -> None:
    """The lower clamp survives: an improving (negative) delta stays 0."""
    # Black move where White-relative eval drops (good for Black) → no loss.
    assert _cp_loss_for_move("Black", 30, -30) == 0


@pytest.mark.unit
@pytest.mark.parametrize(
    ("second_eval_cp", "expected"),
    [
        (-ONLY_MOVE_GAP_CP, True),
        (-ONLY_MOVE_GAP_CP + 1, False),
        (None, False),
    ],
)
def test_only_move_gap_threshold(
    second_eval_cp: int | None,
    expected: bool,
) -> None:
    """The 200 cp boundary is inclusive; no second PV is not a signal."""
    assert _is_only_move("White", 0, second_eval_cp) is expected


@pytest.mark.unit
def test_black_perspective_gap() -> None:
    """For Black, a lower White-relative primary evaluation is better."""
    assert _is_only_move("Black", -100, 100) is True
    assert _is_only_move("Black", 100, -100) is False


@pytest.mark.unit
def test_only_move_is_stored_without_second_eval() -> None:
    """The derived flag is stored on an error, but the raw second PV is not."""
    raw_moves = [
        {
            "ply": 1,
            "san": "Nf3",
            "color": "White",
            "eval_before": 100,
            "eval_after": 0,
            "best_move": "e2e4",
            "second_eval_cp": -100,
        }
    ]

    move = build_analysis_data(raw_moves)["moves"][0]

    assert move["classification"] == "inaccuracy"
    assert move["is_only_move"] is True
    assert "second_eval_cp" not in move


@pytest.mark.unit
def test_acpl_weighted() -> None:
    """ACPL = mean cp_loss across that side's moves only.

    Constructed eval deltas (White-relative cp, see engine.py):
        ply 1  e4  White: 30 → 10   → cp_loss 20
        ply 2  e5  Black: 10 → 50   → cp_loss 40
        ply 3  Nf3 White: 50 → -10  → cp_loss 60
        ply 4  Nc6 Black: -10 → -30 → cp_loss 0  (improves for Black, clamped)

        white_acpl = round((20 + 60) / 2) = 40
        black_acpl = round((40 +  0) / 2) = 20
    """
    raw_moves = [
        {"ply": 1, "san": "e4", "color": "White", "eval_before": 30, "eval_after": 10},
        {"ply": 2, "san": "e5", "color": "Black", "eval_before": 10, "eval_after": 50},
        {"ply": 3, "san": "Nf3", "color": "White", "eval_before": 50, "eval_after": -10},
        {"ply": 4, "san": "Nc6", "color": "Black", "eval_before": -10, "eval_after": -30},
    ]

    data = build_analysis_data(raw_moves)

    summary = data["summary"]
    assert summary["white_acpl"] == 40
    assert summary["black_acpl"] == 20

    moves = data["moves"]
    assert [m["cp_loss"] for m in moves] == [20, 40, 60, 0]
    # Sanity: the only error-class move (cp_loss=60 → inaccuracy) carries FENs;
    # quiet moves stay lightweight per ARCHITECTURE.md §3.4.
    assert moves[2]["classification"] == "inaccuracy"
    assert "fen_before" in moves[2] and "fen_after" in moves[2]
    assert "fen_before" not in moves[0]


@pytest.mark.unit
def test_best_move_engine_propagated_as_san_for_error_moves() -> None:
    """Engine's UCI `best_move` is stored as SAN on error moves only.

    Raw row simulates `StockfishEngine.analyse_game` output: a White inaccuracy
    on move 1 plus a quiet Black reply. `best_move` on the inaccuracy is given
    in UCI (`e2e4`) and must surface as `best_move_engine="e4"` on the entry;
    the quiet move must not gain the field at all (ARCHITECTURE.md §3.4).
    """
    raw_moves = [
        # cp_loss=60 → inaccuracy; engine recommends 1.e4.
        {
            "ply": 1,
            "san": "Nf3",
            "color": "White",
            "eval_before": 30,
            "eval_after": -30,
            "best_move": "e2e4",
        },
        # cp_loss=0 → best; should stay lightweight.
        {
            "ply": 2,
            "san": "e5",
            "color": "Black",
            "eval_before": -30,
            "eval_after": -30,
            "best_move": "e7e5",
        },
    ]

    data = build_analysis_data(raw_moves)
    moves = data["moves"]

    assert moves[0]["classification"] == "inaccuracy"
    assert moves[0]["best_move_engine"] == "e4"

    assert moves[1]["classification"] == "best"
    assert "best_move_engine" not in moves[1]


@pytest.mark.unit
def test_best_move_engine_falls_back_to_none_on_invalid_uci() -> None:
    """Malformed / illegal UCI from the engine is tolerated.

    If the engine packet is degraded (no PV, garbage string, or a UCI that's
    illegal in `board_before`), we still emit the entry — `best_move_engine`
    just stays `None` and tactical detectors that need it opt out.
    """
    raw_moves = [
        # Illegal in the starting position (no piece on h2 going to h5).
        {
            "ply": 1,
            "san": "Nf3",
            "color": "White",
            "eval_before": 30,
            "eval_after": -30,
            "best_move": "h2h5",
        },
        {
            "ply": 2,
            "san": "Nf6",
            "color": "Black",
            "eval_before": -30,
            "eval_after": 80,
            "best_move": "not-a-uci-string",
        },
    ]

    data = build_analysis_data(raw_moves)
    moves = data["moves"]

    assert moves[0]["classification"] == "inaccuracy"
    assert moves[0]["best_move_engine"] is None
    assert moves[1]["classification"] == "mistake"
    assert moves[1]["best_move_engine"] is None


@pytest.mark.unit
def test_acpl_handles_empty_move_list() -> None:
    data = build_analysis_data([])

    assert data["moves"] == []
    assert data["summary"]["white_acpl"] == 0
    assert data["summary"]["black_acpl"] == 0
    assert data["summary"]["advantage_lost"] == {"white": False, "black": False}


# Reusable short opening for the phase tests below: 1.e4 e5 2.Nf3 Nc6 3.Bc4 Bc5
# 4.O-O Nf6 5.d3 d6. Five plies in we're still developing; from ply 7 onward
# four minors have left the back rank, so detect_phase flips to "middlegame".
_PHASE_RAW_MOVES: list[dict] = [
    {"ply": 1, "san": "e4", "color": "White", "eval_before": 0, "eval_after": 0},
    {"ply": 2, "san": "e5", "color": "Black", "eval_before": 0, "eval_after": 0},
    {"ply": 3, "san": "Nf3", "color": "White", "eval_before": 0, "eval_after": 0},
    {"ply": 4, "san": "Nc6", "color": "Black", "eval_before": 0, "eval_after": 0},
    {"ply": 5, "san": "Bc4", "color": "White", "eval_before": 0, "eval_after": 0},
    {"ply": 6, "san": "Bc5", "color": "Black", "eval_before": 0, "eval_after": 0},
    {"ply": 7, "san": "O-O", "color": "White", "eval_before": 0, "eval_after": 0},
    {"ply": 8, "san": "Nf6", "color": "Black", "eval_before": 0, "eval_after": 0},
    {"ply": 9, "san": "d3", "color": "White", "eval_before": 0, "eval_after": 0},
    {"ply": 10, "san": "d6", "color": "Black", "eval_before": 0, "eval_after": 0},
]


@pytest.mark.unit
def test_phase_present_on_every_move() -> None:
    """Every move entry must carry a `phase` field with a valid label.

    Unlike `fen_before` / `best_move_engine` (error-only per §3.4), `phase`
    is universal — quiet best moves still need it for downstream aggregation.
    """
    data = build_analysis_data(_PHASE_RAW_MOVES)

    moves = data["moves"]
    assert len(moves) == len(_PHASE_RAW_MOVES)

    for move in moves:
        assert "phase" in move
        assert move["phase"] in ("opening", "middlegame", "endgame")


@pytest.mark.unit
def test_phase_boundaries_present() -> None:
    """Summary exposes the two phase boundary plies as ints."""
    data = build_analysis_data(_PHASE_RAW_MOVES)

    boundaries = data["summary"]["phase_boundaries"]
    assert set(boundaries.keys()) == {"opening_end_ply", "middlegame_end_ply"}
    assert isinstance(boundaries["opening_end_ply"], int)
    assert isinstance(boundaries["middlegame_end_ply"], int)


@pytest.mark.unit
def test_phase_consistency() -> None:
    """Phases progress monotonically in chess time.

    Once a position is classified as middlegame (or endgame), the next ply
    cannot fall back to opening — development can only grow, queens can only
    leave the board, and the ply counter only advances. This guards against
    accidental phase oscillations from a future detector tweak.
    """
    data = build_analysis_data(_PHASE_RAW_MOVES)

    _RANK: dict[str, int] = {"opening": 0, "middlegame": 1, "endgame": 2}
    moves = data["moves"]

    for prev, curr in zip(moves, moves[1:], strict=False):
        assert _RANK[curr["phase"]] >= _RANK[prev["phase"]], (
            f"phase regressed at ply {curr['ply']}: {prev['phase']!r} → {curr['phase']!r}"
        )
