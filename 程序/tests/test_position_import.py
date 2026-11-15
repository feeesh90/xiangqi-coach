import pytest
from position_import import BoardDraft, ImportErrorDetail, normalize_board, validate_position
from rules import Position, START_FEN

ENDGAME="1r2k4/9/9/9/4p4/9/2N1P4/9/4C4/4K1R2 w - - 0 1"


@pytest.mark.parametrize("red_bottom",[True,False])
@pytest.mark.parametrize("turn",[True,False])
def test_draft_orientation_and_turn_are_independent(red_bottom,turn):
    b=Position(ENDGAME).b
    image=b if red_bottom else [row[::-1] for row in b[::-1]]
    d=BoardDraft.from_result({"board":image,"uncertain":[]})
    assert d.red_bottom==red_bottom
    q=d.position(turn)
    assert q.b==b and q.red==turn


@pytest.mark.parametrize("alter",[
    lambda b:b.pop(), lambda b:b[0].pop(),lambda b:b[0].__setitem__(0,"X"),
    lambda b:b[0].__setitem__(4,""),lambda b:b[0].__setitem__(3,"k"),
    lambda b:b[9].__setitem__(0,"A"),lambda b:b[4].__setitem__(2,"B"),
    lambda b:b[7].__setitem__(0,"P"),lambda b:b[0].__setitem__(0,"p"),
])
def test_bad_static_positions_block_import(alter):
    b=Position(ENDGAME).b;alter(b)
    with pytest.raises(ImportErrorDetail):validate_position(b,True)


def test_facing_kings_and_nonmoving_check_are_rejected():
    with pytest.raises(ImportErrorDetail,match="照面"):
        validate_position(Position("4k4/9/9/9/9/9/9/9/9/4K4 w - - 0 1").b,True)
    b=Position(ENDGAME).b;b[9][6]="";b[1][4]="R"
    with pytest.raises(ImportErrorDetail,match="非行棋方"):
        validate_position(b,True)
    assert validate_position(b,False).red is False  # being in check is allowed


def test_checkmate_is_valid_import_but_no_legal_moves():
    from rules import legal_moves
    p=Position("3k5/3RR4/9/9/4p4/9/9/9/9/4K4 b - - 0 1")
    q=validate_position(p.b,False)
    assert not legal_moves(q)


def test_uncertainty_requires_explicit_review_and_edit():
    d=BoardDraft.from_result({"board":Position(ENDGAME).b,"uncertain":[[6,2]]})
    with pytest.raises(ImportErrorDetail,match="黄色"):d.position(True)
    d.set_piece(6,2,"N")
    assert d.position(True).fen()==ENDGAME


@pytest.mark.parametrize("unknown",[[[-1,0]],[[0,9]],[[0,0,0]],[[True,0]],"bad"])
def test_worker_unknown_coordinates_checked(unknown):
    with pytest.raises(ImportErrorDetail):
        BoardDraft.from_result({"board":Position().b,"uncertain":unknown})


def test_worker_corner_nan_and_unknown_orientation_rejected():
    with pytest.raises(ImportErrorDetail):
        BoardDraft.from_result({"board":Position().b,"corners":[[float('nan'),0]]*4,"image_size":[800,900]})
    with pytest.raises(ImportErrorDetail):normalize_board(Position().b,None)
    assert validate_position(Position().b,True).fen()==START_FEN
