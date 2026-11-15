"""Real Tk/Canvas events and frozen-context worker regressions."""
import threading

import pytest

from engine import Analysis, Candidate
from rules import Move, Position, START_FEN, legal_moves
from repetition import blocked_cycle_move, MESSAGE, FORCED_MESSAGE
from test_board_orientation import gui, click, wait
from test_repetition import CYCLE, FORCED_FEN, FORCED_CYCLE, replay


def seed(app, count=7, fen=START_FEN, sequence=CYCLE):
    app._set_mode("pvp")
    app._reset_game_from_fen(fen,"standard" if fen==START_FEN else "creative")
    for value in (sequence*3)[:count]:
        assert app.make_move(Move.from_ucci(value),auto_ai=False)
    return app.cur().copy(), app._cycle_context()


@pytest.mark.parametrize("view", ["red_bottom","black_bottom"])
def test_real_canvas_rejects_eighth_ply_keeps_selection_markers_and_allows_change(gui,view):
    app=gui
    seed(app)
    app._set_board_view_mode(view)
    before=(app.cur().fen(),app.move_history[:],app.last,len(app.history),app.task_token)
    white=[app.canvas.coords(item) for item in app.canvas.find_withtag("last_move_ring")]
    click(app,2,2)  # black knight c7
    assert Move.from_ucci("c7b9") not in app.targets
    assert app.selected==(2,2)
    click(app,0,1)
    assert app.status.cget("text")==MESSAGE
    assert app.selected==(2,2)
    assert (app.cur().fen(),app.move_history,app.last,len(app.history),app.task_token)==before
    assert [app.canvas.coords(item) for item in app.canvas.find_withtag("last_move_ring")]==white
    click(app,1,4)  # c7e8: independent expected internal coordinates.
    assert app.move_history[-1]=="c7e8" and len(app.move_history)==8
    assert len(app.canvas.find_withtag("last_move_ring"))==1


def test_only_real_legal_evasion_is_allowed_then_other_side_must_change(gui):
    app=gui
    seed(app,7,FORCED_FEN,FORCED_CYCLE)
    assert len(legal_moves(app.cur()))==1
    click(app,0,5);click(app,0,4)  # f9e9
    assert app.move_history[-1]=="f9e9"
    assert app.status.cget("text")==FORCED_MESSAGE
    before=app.cur().fen(),app.move_history[:]
    assert not app.make_move(Move.from_ucci("f7e7"),auto_ai=False)
    assert (app.cur().fen(),app.move_history)==before


def test_undo_restart_custom_start_and_creative_cancel_do_not_leak_cycle_history(gui):
    app=gui
    seed(app)
    saved=app.cur().fen(),app._cycle_context(),app.move_history[:]
    app.enter_creative()
    app.editor.board[5][0]="R"
    app.flip_board_button.invoke()
    app.cancel_creative()
    assert (app.cur().fen(),app._cycle_context(),app.move_history)==saved
    assert blocked_cycle_move(app.cur(),app._cycle_context())==Move.from_ucci("c7b9")
    app.undo()
    assert len(app.move_history)==6 and blocked_cycle_move(app.cur(),app._cycle_context()) is None
    assert app.make_move(Move.from_ucci("a3a4"),auto_ai=False)
    assert app.make_move(Move.from_ucci("c7b9"),auto_ai=False)
    app.restart_current_game()
    assert not app._cycle_context() and not app.move_history and app.last is None
    seed(app,7,FORCED_FEN,FORCED_CYCLE)
    app.enter_creative();app.apply_creative()
    assert not app._cycle_context() and not app.move_history and app.game_source=="creative"
    root=app.game_start_fen
    app.restart_current_game()
    assert app.cur().fen()==root and not app._cycle_context()


def test_simulation_inherits_tail_undo_restores_and_exit_leaves_official_unchanged(gui):
    app=gui
    seed(app)
    official=app.official.fen(),app._cycle_context(),app.move_history[:]
    app.toggle_sim()
    assert app._cycle_context()==official[1]
    assert not app.make_move(Move.from_ucci("c7b9"),auto_ai=False)
    assert app.make_move(Move.from_ucci("c7e8"),auto_ai=False)
    app.undo()
    assert app._cycle_context()==official[1]
    app.toggle_sim()
    assert (app.official.fen(),app._cycle_context(),app.move_history)==official


def test_search_restricts_root_and_trims_cached_pv_on_a_copy(gui):
    app=gui
    snap,context=seed(app,3)
    best=Move.from_ucci("c7b9")
    pv=["c7b9",*CYCLE,"a3a4"]
    original=Analysis(best,[Candidate(1,best,pv[:],12,44)],12,.1)
    calls=[]
    def search(fen,ms,multipv,**kwargs):
        calls.append(kwargs)
        return original
    app.engine.analyze=search
    checked=app._analyze_position(snap,100,1,context)
    assert calls==[{}]
    assert checked.candidates[0].pv==["c7b9",*CYCLE[:3]]
    assert checked.cycle_truncated and original.candidates[0].pv==pv
    snap,context=seed(app,7)
    other=Move.from_ucci("c7e8")
    original=Analysis(other,[Candidate(1,other,[other.ucci()],5,13)],5,.1)
    checked=app._analyze_position(snap,100,1,context)
    assert "c7b9" not in calls[-1]["searchmoves"] and "c7e8" in calls[-1]["searchmoves"]
    assert len(calls[-1]["searchmoves"])==len(legal_moves(snap))-1
    assert checked.best==other


def test_no_result_application_or_success_message_for_blocked_ai_or_api(gui):
    app=gui
    snap,context=seed(app)
    blocked=Move.from_ucci("c7b9")
    analysis=Analysis(blocked,[Candidate(1,blocked,[blocked.ucci()],5)],5,.1)
    before=app.cur().fen(),app.move_history[:],app.last
    app._apply_ai_result(analysis,snap,context=context)
    app._apply_api_guided_take(dict(analysis=analysis,move=blocked,source="api"),snap,context)
    assert (app.cur().fen(),app.move_history,app.last)==before
    assert "落子完成" not in app.coach_title.cget("text")
    assert "代走" not in app.coach_title.cget("text")


def test_same_fen_different_history_worker_and_display_result_are_discarded(gui):
    app=gui
    snap,context=seed(app)
    started=threading.Event();release=threading.Event()
    delivered=[]
    def work():
        started.set()
        assert release.wait(2)
        return "old-context"
    app._run_task("fixture",work,delivered.append)
    assert started.wait(1)
    # Same board after one round, but a different cycle history.
    app.official=app.history[3][0].copy()
    app.history=app.history[:3];app.move_history=app.move_history[:3]
    assert app.official.fen()==snap.fen() and app._cycle_context()!=context
    release.set()
    wait(app,lambda:not app.busy)
    assert not delivered
    other=Move.from_ucci("c7e8")
    analysis=Analysis(other,[Candidate(1,other,[other.ucci()],5)],5,.1)
    app._show_recommendation(analysis,snap,context)
    assert app.analysis_result is None


def test_playback_checks_inherited_context_and_back_restores_it(gui):
    app=gui
    seed(app,6);app.toggle_sim()
    snap=app.sim.copy();context=app._cycle_context()
    move=Move.from_ucci("c2b0")
    analysis=Analysis(move,[Candidate(1,move,["c2b0","c7b9"],3)],3,.1)
    app._apply_pv_four(analysis,snap,context)
    assert [m.ucci() for m in app.playback_moves]==["c2b0"]
    assert "截断" in app.coach_text.get("1.0","end")
    app.playback_step_next()
    assert app.playback_index==1
    assert blocked_cycle_move(app.cur(),app._cycle_context())==Move.from_ucci("c7b9")
    app.playback_step_back()
    assert app._cycle_context()==context


def test_book_reference_marked_unplayable(gui,monkeypatch):
    app=gui
    seed(app)
    monkeypatch.setattr("app.master_lookup",lambda moves:[dict(next="c7b9",name="fixture",source="fixture")])
    row,=app._master_references()
    assert row["cycle_blocked"] and row["next"]=="c7b9"
