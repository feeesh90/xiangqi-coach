from rules import Move, Position, in_check, legal_from, legal_moves


def test_checked_side_can_block_with_another_piece():
    """A checked player may select any piece that has a legal check escape."""
    pos = Position("3kr4/9/9/9/9/9/9/9/3R5/4K4 w - - 0 1")
    block = Move.from_ucci("d1e1")

    assert in_check(pos, True)
    assert block in legal_moves(pos)
    assert block in legal_from(pos, block.fr, block.fc)

    after = pos.copy()
    assert after.apply(block)
    assert not in_check(after, True)


def test_every_move_while_checked_resolves_check():
    pos = Position("3kr4/9/9/9/9/9/9/9/3R5/4K4 w - - 0 1")
    moves = legal_moves(pos)

    assert moves
    for move in moves:
        after = pos.copy()
        after.apply_unchecked(move)
        assert not in_check(after, True), move.ucci()
