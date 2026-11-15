"""View-only rotation, real Tk canvas rendering and pixel input regressions."""
import gc
import threading
import time

import pytest

from app import App
from engine import Analysis, Candidate
from rules import Move, Position, legal_from, legal_moves, piece_name


@pytest.mark.parametrize("mode,human_red,flipped", [
    ("ai", True, True), ("ai", False, False),
    ("lan", True, True), ("lan", False, False),
    ("pvp", True, False), ("pvp", False, False),
])
@pytest.mark.parametrize("view,forced", [("auto", None), ("red_bottom", False), ("black_bottom", True)])
def test_all_intersections_round_trip_without_turn_dependency(mode, human_red, flipped, view, forced):
    app = App.__new__(App)
    app.mode, app.human_red = mode, human_red
    app.board_view_mode = view
    if forced is not None:
        flipped = forced
    app.official = Position()
    for turn in (True, False):
        app.official.red = turn
        assert app._view_flipped() == flipped
        for r in range(10):
            for c in range(9):
                expected = (9-r, 8-c) if flipped else (r, c)
                assert app._board_to_view(r, c) == expected
                assert app._view_to_board(*expected) == (r, c)
                assert app._board_to_canvas(r, c, (20, 30, 50, 50)) == (
                    20+expected[1]*50, 30+expected[0]*50)


def test_uninitialized_tk_test_double_defaults_to_red_player_at_top():
    app = App.__new__(App)
    app.mode = "ai"
    assert app._board_to_view(0, 0) == (9, 8)
    assert app._view_name() == "黑方在下"


@pytest.fixture
def gui(monkeypatch,tmp_path):
    monkeypatch.setattr(App, "_engine_check", lambda self: None)
    # Tests drive legal positions explicitly, so no engine or delayed AI runs.
    monkeypatch.setattr(App, "_schedule_opponent", lambda self: None)
    app = App(preferences_path=tmp_path/"ui_preferences.json",engine_config_path=(tmp_path/"ui_preferences.json").with_suffix(".engine.json"))
    monkeypatch.setattr(app.engine, "installed", lambda: False)
    monkeypatch.setattr(app.api, "configured", lambda: False)
    app.update()
    app.withdraw()
    try:
        yield app
    finally:
        app._close()
        # Release Tk variables on the UI thread before later network workers
        # can trigger collection of an old, cyclic application object.
        monkeypatch.undo()
        app.__dict__.clear()
        gc.collect()


def pixel(app, r, c):
    """Independent expected rotation; never call the production mapping."""
    flipped = {"red_bottom": False, "black_bottom": True}.get(
        app.board_view_mode, app.mode in ("ai", "lan") and app.human_red)
    vr, vc = (9-r, 8-c) if flipped else (r, c)
    ox, oy, dx, dy = app._geo()
    return ox+vc*dx, oy+vr*dy


def click(app, r, c):
    x, y = pixel(app, r, c)
    app.canvas.event_generate("<Button-1>", x=round(x), y=round(y))


def choose(app, mode):
    label = {"auto": "自动（己方在上）", "red_bottom": "红方在下", "black_bottom": "黑方在下"}[mode]
    if mode == "auto":
        app._set_board_view_mode("auto")  # Internal default remains supported.
    elif app._view_flipped() != (mode == "black_bottom"):
        app.flip_board_button.invoke()
    elif app.board_view_mode == "auto":
        app.flip_board_button.invoke()
        app.flip_board_button.invoke()
    assert app.board_view_mode == mode
    assert app.board_view_var.get() == app._view_name()


def wait(app, predicate):
    deadline = time.monotonic()+3
    while time.monotonic() < deadline:
        app.update()
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError("GUI condition timed out")


def centers(app, tag):
    out = []
    for item in app.canvas.find_withtag(tag):
        x1, y1, x2, y2 = app.canvas.coords(item)
        out.append(((x1+x2)/2, (y1+y2)/2))
    return out


def assert_piece_positions(app):
    for r in range(10):
        for c in range(9):
            q = app.cur().b[r][c]
            items = app.canvas.find_withtag(f"piece:{r}:{c}")
            assert len(items) == bool(q)
            if q:
                assert app.canvas.coords(items[0]) == pytest.approx(pixel(app, r, c))
                assert app.canvas.itemcget(items[0], "text") == piece_name(q)
                assert app.canvas.itemcget(items[0], "angle") == "0.0"


@pytest.mark.parametrize("human_red", [True, False])
@pytest.mark.parametrize("view", ["auto", "red_bottom", "black_bottom"])
def test_initial_board_rotation_does_not_change_game_or_history(gui, human_red, view):
    app = gui
    before = app.official.fen(), list(app.move_history), app.official.red
    app.human_red = human_red
    choose(app, view)
    assert_piece_positions(app)
    red_bottom = view == "red_bottom" or (view == "auto" and not human_red)
    king = (9, 4) if red_bottom else (0, 4)
    assert pixel(app, *king)[1] > app.canvas.winfo_height()/2
    assert before == (app.official.fen(), app.move_history, app.official.red)


@pytest.mark.parametrize("human_red,encoded,view,flipped", [
    (True, "b0c2", "auto", True), (False, "b9c7", "auto", False),
    (True, "b0c2", "black_bottom", True), (False, "b9c7", "red_bottom", False),
])
def test_mouse_selection_targets_and_legal_move(gui, human_red, encoded, view, flipped):
    app = gui
    app.human_red = human_red
    if not human_red:
        app.official.apply_unchecked(Move.from_ucci("b0c2"))
    choose(app, view)
    move = Move.from_ucci(encoded)
    click(app, move.fr, move.fc)
    assert app.selected == (move.fr, move.fc)
    assert centers(app, "selected")[0] == pytest.approx(pixel(app, move.fr, move.fc))
    assert any(p == pytest.approx(pixel(app, move.tr, move.tc)) for p in centers(app, "target"))
    app.canvas.event_generate("<Button-1>", x=-100, y=-100)
    assert app.selected == (move.fr, move.fc)
    click(app, move.tr, move.tc)
    assert app.move_history == [encoded]
    assert app.official.b[move.tr][move.tc] == ("N" if human_red else "n")
    assert app.last == move
    assert app._view_flipped() == flipped
    assert centers(app, "last") == pytest.approx([
        pixel(app, move.fr, move.fc), pixel(app, move.tr, move.tc)])
    assert_piece_positions(app)


@pytest.mark.parametrize("view", ["red_bottom", "black_bottom"])
def test_capture_check_highlights_and_arrows_follow_view(gui, view):
    app = gui
    choose(app, view)
    app.official = Position("3k5/9/9/9/9/9/9/9/3Rr4/4K4 w - - 0 1")
    app.selected = (8, 3)
    app.targets = legal_from(app.official, 8, 3)
    capture = Move.from_ucci("d1e1")
    assert capture in app.targets
    app.last = Move.from_ucci("e2e1")
    app.reco = Move.from_ucci("e0f0")
    app.premove_candidate = capture
    before = app.official.fen()
    app.draw()
    assert_piece_positions(app)
    assert centers(app, "selected")[0] == pytest.approx(pixel(app, 8, 3))
    assert centers(app, "capture")[0] == pytest.approx(pixel(app, capture.tr, capture.tc))
    for m in app.targets:
        assert any(p == pytest.approx(pixel(app, m.tr, m.tc)) for p in centers(app, "target"))
    expected_sources = {(m.fr, m.fc) for m in legal_moves(app.official)}
    highlighted = centers(app, "check_escape")
    assert len(highlighted) == len(expected_sources)
    for rc in expected_sources:
        assert any(p == pytest.approx(pixel(app, *rc)) for p in highlighted)
    for tag, m in (("recommendation", app.reco), ("premove", capture)):
        item, = app.canvas.find_withtag(tag)
        assert app.canvas.coords(item) == pytest.approx((*pixel(app, m.fr, m.fc), *pixel(app, m.tr, m.tc)))
        assert app.canvas.itemcget(item, "arrow") == "last"
    labels = {app.canvas.itemcget(i, "text"): app.canvas.coords(i)
              for i in app.canvas.find_all() if app.canvas.type(i) == "text"}
    for label, m, offset in (("AI首选", app.reco, 14), ("你想试", capture, -12)):
        x1, y1 = pixel(app, m.fr, m.fc)
        x2, y2 = pixel(app, m.tr, m.tc)
        assert labels[label] == pytest.approx(((x1+x2)/2, (y1+y2)/2+offset))
    assert app.official.fen() == before


def test_black_simulation_replay_undo_restart_and_local_modes(gui):
    app = gui
    app.human_red = False
    app.new_game()
    assert "红方在下" in app.sim_label.cget("text")
    assert app._is_ai_turn()  # Red opens, regardless of bottom side.
    app.toggle_sim()
    pv = ["b0c2", "b9c7", "c2b0", "c7b9"]
    move = Move.from_ucci(pv[0])
    a = Analysis(move, [Candidate(1, move, pv, 12)], 12, .2)
    app._apply_pv_four(a, app.sim.copy())
    app.toggle_playback_pause()
    for _ in range(4):
        app.playback_step_next()
        assert not app._view_flipped()
        assert_piece_positions(app)
    app.playback_step_back()
    assert app.playback_index == 3
    app.undo()
    assert not app._view_flipped()
    app.toggle_sim()
    assert app.official.fen() == Position().fen()
    assert "红方在下" in app.sim_label.cget("text")
    # Manual takeover changes turn ownership, not the view.
    app.manual_takeover = True
    click(app, 9, 1)
    click(app, 7, 2)
    assert app.move_history == ["b0c2"]
    assert not app._view_flipped()
    app.new_game()
    app._set_mode("pvp")
    assert not app._view_flipped()
    assert "红方在下" in app.sim_label.cget("text")
    app._set_mode("ai")
    assert not app._view_flipped()


def test_switch_failure_keeps_side_success_restarts_and_schedules_red(gui, monkeypatch):
    app = gui
    opening = Move.from_ucci("b0c2")
    app.make_move(opening, auto_ai=False)
    before = app.official.fen(), list(app.move_history)
    def fail(*args):
        raise RuntimeError("missing network")
    monkeypatch.setattr(app.engine, "switch", fail)
    app._switch_engine("px0", {}, human_red=False)
    deadline = time.monotonic()+3
    while app.busy and time.monotonic() < deadline:
        app.update()
        time.sleep(.01)
    assert not app.busy
    assert app.human_red and app._view_flipped()
    assert (app.official.fen(), app.move_history) == before
    scheduled = []
    monkeypatch.setattr(app, "_schedule_opponent", lambda: scheduled.append(app.official.red))
    monkeypatch.setattr(app.engine, "switch", lambda *args: "Pikafish")
    app._switch_engine("pikafish", {}, human_red=False)
    deadline = time.monotonic()+3
    while app.busy and time.monotonic() < deadline:
        app.update()
        time.sleep(.01)
    assert not app.busy and not app.human_red
    assert app.official.fen() == Position().fen() and not app.move_history
    assert not app._view_flipped() and scheduled == [True]
    assert "红方在下" in app.sim_label.cget("text")


def test_resizing_preserves_rotation_and_square_cells(gui):
    app = gui
    app.human_red = False
    app.deiconify()
    for size in ("1080x720", "1400x850"):
        app.geometry(size)
        app.update()
        app.draw()
        _, _, dx, dy = app._geo()
        assert dx == pytest.approx(dy)
        assert_piece_positions(app)
        root_x, root_y = app.winfo_rootx(), app.winfo_rooty()
        for widget in (app.flip_board_button, app.creative_button, app.board_view_label, app.analysis_panel, app.action_bar, app.sim_label):
            assert widget.winfo_rootx()-root_x >= 0
            assert widget.winfo_rooty()-root_y >= 0
            assert widget.winfo_rootx()-root_x+widget.winfo_width() <= app.winfo_width()
            assert widget.winfo_rooty()-root_y+widget.winfo_height() <= app.winfo_height()
        from tkinter import font
        measured = font.Font(app, font=app.board_view_label.cget("font")).measure("黑方在下")
        assert measured <= app.board_view_label.winfo_width()


@pytest.mark.parametrize("human_red,view,encoded,screen_source,screen_target", [
    (True, "auto", "b0c2", (0, 7), (2, 6)),
    (False, "auto", "b9c7", (0, 1), (2, 2)),
    (True, "black_bottom", "b0c2", (0, 7), (2, 6)),
    (False, "red_bottom", "b9c7", (0, 1), (2, 2)),
])
def test_opposite_side_mouse_move_with_fixed_expected_pixels(gui, human_red, view, encoded, screen_source, screen_target):
    app = gui
    app.human_red = human_red
    if not human_red:
        app.official.apply_unchecked(Move.from_ucci("b0c2"))
    choose(app, view)
    ox, oy, dx, dy = app._geo()
    for r, c in (screen_source, screen_target):
        app.canvas.event_generate("<Button-1>", x=round(ox+c*dx), y=round(oy+r*dy))
    assert app.move_history == [encoded]
    assert app.human_red == human_red
    assert_piece_positions(app)


def test_view_switch_preserves_game_analysis_and_task_state(gui, monkeypatch):
    app = gui
    app.make_move(Move.from_ucci("b0c2"), auto_ai=False)
    app.selected = (0, 1)
    app.targets = legal_from(app.official, 0, 1)
    app.reco = Move.from_ucci("b9c7")
    app.premove_candidate = Move.from_ucci("h9g7")
    app.analysis_snapshot = app.official.copy()
    app.analysis_result = object()
    app.playback_index = 2
    app.playback_paused = True
    app.busy = True
    app.lan_sending = True
    app.premove_mode = True
    def snapshot():
        return (app.official.fen(), tuple(app.move_history), tuple(app.history),
                app.human_red, app.last, app.reco, app.premove_candidate,
                app.analysis_snapshot.fen(), app.analysis_result,
                app.task_token, app.api_request_token, app.busy, app.lan_sending,
                app.playback_index, app.playback_paused, app.opponent_job,
                app.coach_text.get("1.0", "end"))
    before = snapshot()
    def forbidden(*args, **kw):
        raise AssertionError("view switch must not perform a game/engine/network action")
    with monkeypatch.context() as guards:
        for name in ("new_game", "make_move", "_cancel_current_task", "_lan_send"):
            guards.setattr(app, name, forbidden)
        guards.setattr(app.engine, "stop", forbidden)
        choose(app, "red_bottom")
        choose(app, "black_bottom")
    assert app.selected is None and not app.targets
    assert snapshot() == before
    assert "只分析，不真正落子" in app.sim_label.cget("text")
    assert "黑方在下" in app.sim_label.cget("text")
    for tag, m in (("recommendation", app.reco), ("premove", app.premove_candidate)):
        item, = app.canvas.find_withtag(tag)
        assert app.canvas.coords(item) == pytest.approx((*pixel(app, m.fr, m.fc), *pixel(app, m.tr, m.tc)))
    with pytest.raises(ValueError):
        app._set_board_view_mode("invalid")
    assert app.board_view_mode == "black_bottom" and snapshot() == before


@pytest.mark.parametrize("view", ["red_bottom", "black_bottom"])
def test_manual_view_survives_lifecycle_and_restore_auto(gui, monkeypatch, view):
    app = gui
    choose(app, view)
    expected = view == "black_bottom"
    for human_red in (False, True):
        app.human_red = human_red
        app.new_game()
        assert app.board_view_mode == view and app._view_flipped() == expected
        app.toggle_sim()
        app.make_move(Move.from_ucci("b0c2"))
        app.undo()
        app.toggle_sim()
        app.toggle_premove()
        assert "只分析" in app.sim_label.cget("text")
        app.toggle_premove()
        for mode in ("pvp", "ai"):
            app._set_mode(mode)
            assert app.board_view_mode == view and app._view_flipped() == expected
        monkeypatch.setattr(app.engine, "switch", lambda *a: "Pikafish")
        app._switch_engine("pikafish", {}, human_red=not human_red)
        wait(app, lambda: not app.busy)
        assert app.human_red != human_red
        assert app.board_view_mode == view and app._view_flipped() == expected
    choose(app, "auto")
    assert app._view_flipped() == app.human_red
    app._set_mode("pvp")
    assert not app._view_flipped()


def test_flip_during_search_does_not_cancel_result(gui):
    app = gui
    app.human_red = False
    snap = app.official.copy()
    move = Move.from_ucci("b0c2")
    a = Analysis(move, [Candidate(1, move, ["b0c2"], 12)], 12, .2)
    started, release = threading.Event(), threading.Event()
    def search():
        started.set()
        assert release.wait(3)
        return a
    app._run_task("test thinking", search, lambda result: app._apply_ai_result(result, snap, opponent=True))
    assert started.wait(1)
    token, api_token = app.task_token, app.api_request_token
    try:
        choose(app, "red_bottom")
        assert app.busy and app.task_token == token and app.api_request_token == api_token
        assert app.official.fen() == snap.fen()
    finally:
        release.set()
    wait(app, lambda: not app.busy)
    assert app.move_history == ["b0c2"]
    assert app.board_view_mode == "red_bottom" and not app._view_flipped()
    assert_piece_positions(app)


def test_flip_during_playback_preserves_progress_and_pause(gui):
    app = gui
    app.toggle_sim()
    pv = ["b0c2", "b9c7", "c2b0", "c7b9"]
    move = Move.from_ucci(pv[0])
    a = Analysis(move, [Candidate(1, move, pv, 12)], 12, .2)
    app._apply_pv_four(a, app.sim.copy())
    app._cancel_playback_timer()
    app._advance_playback()
    snapshot = (app.sim.fen(), app.playback_index, app.playback_paused, app.playback_after,
                tuple(app.playback_moves), tuple(app.sim_move_history))
    choose(app, "black_bottom")
    assert (app.sim.fen(), app.playback_index, app.playback_paused, app.playback_after,
            tuple(app.playback_moves), tuple(app.sim_move_history)) == snapshot
    assert "不影响正式棋局" in app.sim_label.cget("text")
    app.toggle_playback_pause()
    paused = (app.playback_index, app.playback_paused)
    choose(app, "red_bottom")
    assert (app.playback_index, app.playback_paused) == paused
    app.playback_step_next()
    assert app.playback_index == 2
    assert_piece_positions(app)
