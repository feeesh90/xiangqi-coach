"""Benchmark cases must be static legal positions, with independently checkable tactics."""
import json
from pathlib import Path

from position_import import validate_position
from rules import Position, Move, in_check, legal_moves


def test_benchmark_fixtures_are_legal_and_mate_in_one_answers_are_real():
    cases = json.loads((Path(__file__).parent/"fixtures/performance_positions.json").read_text(encoding="utf-8"))
    assert len(cases) == 12
    assert {case["category"] for case in cases} == {"opening", "middlegame", "endgame", "tactical"}
    assert len({case["id"] for case in cases}) == 12
    for case in cases:
        position = Position(case["fen"])
        assert validate_position(position.b, position.red).fen() == position.fen()
        assert legal_moves(position)
        for encoded in case.get("acceptable", []):
            after = position.copy()
            assert after.apply(Move.from_ucci(encoded))
            assert in_check(after, after.red) and not legal_moves(after)
