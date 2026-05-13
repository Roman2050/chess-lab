import pytest

from app.services.analysis.classifier import build_analysis_data, classify_move


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
        {"ply": 1, "san": "e4",  "color": "White", "eval_before":  30, "eval_after":  10},
        {"ply": 2, "san": "e5",  "color": "Black", "eval_before":  10, "eval_after":  50},
        {"ply": 3, "san": "Nf3", "color": "White", "eval_before":  50, "eval_after": -10},
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
            "ply": 1, "san": "Nf3", "color": "White",
            "eval_before": 30, "eval_after": -30,
            "best_move": "e2e4",
        },
        # cp_loss=0 → best; should stay lightweight.
        {
            "ply": 2, "san": "e5", "color": "Black",
            "eval_before": -30, "eval_after": -30,
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
            "ply": 1, "san": "Nf3", "color": "White",
            "eval_before": 30, "eval_after": -30,
            "best_move": "h2h5",
        },
        {
            "ply": 2, "san": "Nf6", "color": "Black",
            "eval_before": -30, "eval_after": 80,
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
