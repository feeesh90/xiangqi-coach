import random

from chinese_pgn import parse_chinese_move
from rules import Position, chinese_notation, legal_moves


def test_fast_parser_round_trips_unambiguous_legal_moves():
    """Compare the optimized token parser against legal play positions."""
    rng = random.Random(20260908)
    pos = Position()
    checked = 0

    for _ in range(80):
        moves = legal_moves(pos)
        if not moves:
            break
        move = rng.choice(moves)
        token = chinese_notation(pos, move)
        parsed = parse_chinese_move(pos, token)
        if parsed is not None:
            assert parsed == move, (token, parsed.ucci(), move.ucci())
            checked += 1
        pos.apply_unchecked(move)

    assert checked >= 60
