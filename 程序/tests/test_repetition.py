from types import SimpleNamespace

import pytest

from engine import Analysis, Candidate
from engine_manager import EngineManager
from lan_protocol import StateVerifier, ProtocolError, VERSION
from lan_server import Room
from repetition import (advance, blocked_cycle_move, cycle_move, playable_moves,
                        trim_pv)
from rules import Position, Move, legal_moves

CYCLE = ["b0c2", "b9c7", "c2b0", "c7b9"]
FORCED_FEN = "4k4/9/5R3/9/4P4/9/9/9/9/3RK4 w - - 0 1"
FORCED_CYCLE = ["f7e7", "e9f9", "e7f7", "f9e9"]


def replay(moves, fen=None):
    position = Position(fen) if fen else Position()
    context = ()
    for value in moves:
        move = Move.from_ucci(value)
        assert move in legal_moves(position)
        context = advance(context, position, move)
        position.apply_unchecked(move)
    return position, context


def command(room, kind="move", **values):
    return dict(type=kind, game=room.game, revision=room.revision, **values)


def test_eighth_ply_is_blocked_without_modifying_position_or_context():
    p, context = replay((CYCLE*2)[:7])
    before = p.fen(), context
    target = Move.from_ucci("c7b9")
    basic = legal_moves(p)
    assert target in basic and len(basic) > 1
    assert blocked_cycle_move(p, context) == target
    assert playable_moves(p, context) == [m for m in basic if m != target]
    assert (p.fen(), context) == before


@pytest.mark.parametrize("count", range(7))
def test_first_round_and_incomplete_cycles_are_allowed(count):
    p, context = replay((CYCLE*2)[:count])
    assert blocked_cycle_move(p, context) is None


def test_both_phase_colours_can_be_blocked():
    p, context = replay((FORCED_CYCLE*2)[:7], FORCED_FEN)
    only = Move.from_ucci("f9e9")
    assert legal_moves(p) == [only]  # Actual rules, not an injected legal list.
    assert cycle_move(p, context) == only
    assert blocked_cycle_move(p, context) is None
    assert playable_moves(p, context) == [only]
    context = advance(context, p, only)
    p.apply_unchecked(only)
    assert blocked_cycle_move(p, context) == Move.from_ucci("f7e7")
    assert len(playable_moves(p, context)) > 0


def test_one_side_changes_route_and_three_point_cycles_are_allowed():
    # Matching must not broaden to arbitrary repeated positions.
    a, context = replay(CYCLE)
    for value in ["h0g2", "b9c7", "g2h0"]:
        move = Move.from_ucci(value)
        assert move in legal_moves(a)
        assert blocked_cycle_move(a, context) is None
        context = advance(context, a, move); a.apply_unchecked(move)
    assert blocked_cycle_move(a, context) is None
    # Three collinear rook points: legal history is built independently.
    p = Position("4k3r/9/9/9/4p4/9/9/9/9/R3K4 w - - 0 1")
    context = ()
    values = ["a0a1","i9i8","a1a2","i8i7","a2a0","i7i9"]*2
    for value in values:
        move = Move.from_ucci(value)
        assert move in legal_moves(p)
        assert blocked_cycle_move(p, context) is None
        context = advance(context,p,move); p.apply_unchecked(move)


def test_capture_interrupts_the_window_but_later_clean_cycles_count():
    fen = "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1Cp4C1/9/RNBAKABNR w - - 0 1"
    p, context = replay((CYCLE*2)[:7], fen)
    assert context[0].captured == "p"
    assert blocked_cycle_move(p, context) is None
    move = Move.from_ucci("c7b9")
    context = advance(context,p,move); p.apply_unchecked(move)
    assert blocked_cycle_move(p, context) == Move.from_ucci("b0c2")


def test_pv_stops_before_cycle_and_does_not_change_inputs():
    p, context = replay(CYCLE[:3])
    pv = ["c7b9", *CYCLE, "a3a4"]
    result, stopped = trim_pv(p, context, pv)
    assert result == ["c7b9", *CYCLE[:3]] and stopped
    assert pv == ["c7b9", *CYCLE, "a3a4"] and len(context) == 3


def manager_with_result(best, candidates=None):
    manager = EngineManager.__new__(EngineManager)
    manager._engine = SimpleNamespace(name="fixture")
    result = Analysis(best, candidates or [Candidate(1,best,[best.ucci()],1)],1,.01)
    manager.calls = []
    def invoke(*args):
        manager.calls.append(args)
        return result
    manager._invoke = invoke
    return manager, result


def test_manager_rejects_empty_invalid_and_out_of_set_searchmoves():
    best = Move.from_ucci("b0c2")
    manager, _ = manager_with_result(best)
    for values in ([], ["a0a9"], ["bad"], [123]):
        with pytest.raises(ValueError):
            manager.analyze(Position().fen(),100,1,values)
    assert not manager.calls
    with pytest.raises(RuntimeError,match="候选集合"):
        manager.analyze(Position().fen(),100,1,["h0g2"])


def test_manager_filters_candidates_on_a_copy_and_keeps_real_scores():
    best, other = Move.from_ucci("b0c2"), Move.from_ucci("h0g2")
    candidates = [Candidate(1,best,[best.ucci()],5,23),
                  Candidate(2,other,[other.ucci()],4,12)]
    manager, original = manager_with_result(best,candidates)
    result = manager.analyze(Position().fen(),100,3,[best.ucci()])
    assert len(result.candidates) == 1 and result.candidates[0].score_cp == 23
    assert len(original.candidates) == 2
    assert manager.calls[0][-1] == [best.ucci()]


def test_room_rejects_cycle_without_revision_or_history_changes_and_undo_restores():
    room = Room(); room.connection(True)
    verifier = StateVerifier()
    for value in (CYCLE*2)[:7]:
        role = "host" if room.position.red else "guest"
        room.apply(role,command(room,move=value))
        verifier.check(room.snapshot())
    before = room.snapshot()
    with pytest.raises(ProtocolError,match="重复往返"):
        room.apply("guest",command(room,move="c7b9"))
    assert room.snapshot() == before
    room.connection(False); room.connection(True)
    verifier.check(room.snapshot())  # Reconnect retains all seven plies.
    with pytest.raises(ProtocolError):
        room.apply("guest",command(room,move="c7b9"))
    room.apply("host",command(room,"request",action="undo"))
    room.apply("guest",command(room,"reply",id=room.pending["id"],accept=True))
    verifier.check(room.snapshot())
    assert len(room.moves) == 6
    room.apply("host",command(room,move="a3a4"))
    verifier.check(room.snapshot())
    room.apply("guest",command(room,move="c7b9"))
    verifier.check(room.snapshot())
    assert room.moves[-1] == "c7b9"


def test_verifier_rejects_forged_cycle_snapshot():
    room = Room(); room.connection(True)
    for value in (CYCLE*2)[:7]:
        room.apply("host" if room.position.red else "guest",command(room,move=value))
    verifier = StateVerifier(); verifier.check(room.snapshot())
    forged = room.snapshot()
    move = Move.from_ucci("c7b9")
    position = room.position.copy(); position.apply_unchecked(move)
    forged.update(moves=[*room.moves,move.ucci()],fen=position.fen(),revision=room.revision+1)
    with pytest.raises(ProtocolError,match="往返历史"):
        verifier.check(forged)
    assert verifier.moves == room.moves


def test_lan_version_is_new_rule_version():
    assert VERSION == 2
