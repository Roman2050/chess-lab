from unittest.mock import MagicMock, patch

import chess
import chess.engine
import pytest

from app.services.analysis.engine import StockfishEngine


_PGN = "1. e4 e5 2. Nf3 Nc6"
_EVALS = [10, 20, 30, 40, 50]
_BEST_MOVES = ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"]


def _analysis_infos() -> list[list[dict]]:
    return [
        [
            {
                "score": chess.engine.PovScore(
                    chess.engine.Cp(eval_cp), chess.WHITE
                ),
                "pv": [chess.Move.from_uci(best_move)],
            }
        ]
        for eval_cp, best_move in zip(_EVALS, _BEST_MOVES, strict=True)
    ]


def _mock_uci_engine() -> tuple[MagicMock, MagicMock]:
    engine = MagicMock()
    engine.options = {}
    engine.analyse.side_effect = _analysis_infos()

    context = MagicMock()
    context.__enter__.return_value = engine
    return engine, context


@pytest.mark.unit
def test_engine_called_n_plus_1_times() -> None:
    engine, context = _mock_uci_engine()

    with patch(
        "app.services.analysis.engine.chess.engine.SimpleEngine.popen_uci",
        return_value=context,
    ):
        result = StockfishEngine("stockfish").analyse_game(_PGN)

    assert len(result) == 4
    assert engine.analyse.call_count == len(result) + 1


@pytest.mark.unit
def test_eval_chaining() -> None:
    _, context = _mock_uci_engine()

    with patch(
        "app.services.analysis.engine.chess.engine.SimpleEngine.popen_uci",
        return_value=context,
    ):
        result = StockfishEngine("stockfish").analyse_game(_PGN)

    assert [move["eval_before"] for move in result] == _EVALS[:-1]
    assert [move["eval_after"] for move in result] == _EVALS[1:]
    assert all(
        current["eval_after"] == following["eval_before"]
        for current, following in zip(result, result[1:])
    )


@pytest.mark.unit
def test_best_move_comes_from_pre_move_position() -> None:
    _, context = _mock_uci_engine()

    with patch(
        "app.services.analysis.engine.chess.engine.SimpleEngine.popen_uci",
        return_value=context,
    ):
        result = StockfishEngine("stockfish").analyse_game(_PGN)

    assert [move["best_move"] for move in result] == _BEST_MOVES[:-1]


@pytest.mark.unit
@pytest.mark.parametrize("pgn_content", ["", "this is not valid PGN"])
def test_empty_and_unparseable_pgn(pgn_content: str) -> None:
    with patch(
        "app.services.analysis.engine.chess.engine.SimpleEngine.popen_uci"
    ) as popen_uci:
        result = StockfishEngine("stockfish").analyse_game(pgn_content)

    assert result == []
    popen_uci.assert_not_called()
