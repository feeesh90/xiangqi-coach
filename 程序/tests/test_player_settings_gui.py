"""Side switching and persistence through real Tk widget events."""
import gc
import json
import threading
import time

import pytest

from app import App, COACH_MS
from engine import Analysis, Candidate
from rules import Move, Position, START_FEN, legal_moves
from test_board_orientation import gui, wait


def enabled(a):
    return str(a.player_side_button.cget("state")) == "normal"


@pytest.mark.parametrize("view", ["auto", "red_bottom", "black_bottom"])
def test_side_button_restarts_and_preserves_preferences_and_manual_view(gui, monkeypatch, view):
    a = gui
    a.board_view_mode = view
    a.level.set("高手");a.coach.set("深入")
    a.make_move(Move.from_ucci("b0c2"), auto_ai=False)
    a.reco = a.premove_candidate = Move.from_ucci("b9c7")
    a.analysis_result = object()
    # A pending AI timer is cancelled even before its callback starts.
    queued = []
    a.opponent_job = a.after(500, lambda: queued.append("old"))
    token, api_token = a.task_token, a.api_request_token
    scheduled = []
    monkeypatch.setattr(a, "_schedule_opponent", lambda: scheduled.append(a.human_red))
    a.engine.switch = lambda *args, **kwargs: pytest.fail("side button must not switch engines")
    a.player_side_button.invoke()
    assert not a.human_red and a.player_side_button.cget("text") == "我执黑方"
    assert a.official.fen() == START_FEN and not a.move_history and not a.history
    assert a.last is None and not a.reco and not a.premove_candidate and a.analysis_result is None
    assert not a.canvas.find_withtag("last_move_ring")
    assert a.task_token > token and a.api_request_token > api_token
    assert a.opponent_job is None and scheduled == [False]
    assert a.board_view_mode == view
    assert a._view_flipped() == (view == "black_bottom")
    assert (a.level.get(), a.coach.get()) == ("高手", "深入")
    a.player_side_button.invoke()
    assert a.human_red and a.player_side_button.cget("text") == "我执红方"
    assert scheduled == [False] and a.official.fen() == START_FEN


@pytest.mark.parametrize("source,red_to_move", [("image", True), ("creative", False)])
def test_switch_preserves_custom_root_and_its_turn(gui, monkeypatch, source, red_to_move):
    a = gui
    root = Position("4k4/9/9/9/4p4/9/2N1P4/9/4C4/4K4 w - - 0 1")
    root.red = red_to_move
    a._reset_game_from_fen(root.fen(), source)
    a.make_move(legal_moves(a.official)[0], auto_ai=False)
    scheduled = []
    monkeypatch.setattr(a, "_schedule_opponent", lambda: scheduled.append(True))
    a.player_side_button.invoke()
    assert a.game_source == source and a.game_start_fen == root.fen()
    assert a.official.fen() == root.fen() and a.official.red == red_to_move
    assert not a.history and not a.move_history
    assert bool(scheduled) == red_to_move


@pytest.mark.parametrize("field,value", [
    ("mode", "pvp"), ("mode", "lan"), ("lan_connecting", True),
    ("busy", True), ("sim", Position()), ("playback_moves", ["b0c2"]),
])
def test_guard_cannot_be_bypassed_by_direct_callback(gui, field, value):
    a = gui
    before = a.human_red, a.official.fen(), a.task_token
    previous = getattr(a, field)
    setattr(a, field, value)
    a._refresh_player_side()
    assert not enabled(a)
    a._toggle_player_side()
    assert before == (a.human_red, a.official.fen(), a.task_token)
    setattr(a, field, previous)
    a._refresh_player_side()
    assert enabled(a)


def test_windows_and_editor_disable_then_restore_side_button(gui):
    a = gui
    a.engine_settings()
    window = a.engine_settings_window
    assert not enabled(a)
    a.engine_settings()
    assert a.engine_settings_window is window
    window.destroy()
    assert a.engine_settings_window is None and enabled(a)
    a.open_endgame()
    assert not enabled(a)
    a.endgame_window.close()
    assert enabled(a)
    a.creative_button.invoke()
    assert not enabled(a)
    before = a.human_red
    a._toggle_player_side()
    assert a.human_red == before
    a.editor_cancel_button.invoke()
    assert enabled(a)
    a._set_mode("pvp")
    a._lan_refresh_controls()
    assert not enabled(a)
    a._set_mode("ai")
    assert enabled(a)


def test_engine_switch_success_and_failure_refresh_identity(gui, monkeypatch):
    a = gui
    monkeypatch.setattr(a.engine, "switch", lambda *args: "Pikafish")
    a._switch_engine("pikafish", {}, human_red=False)
    assert not enabled(a)
    wait(a, lambda: not a.busy)
    assert a.player_side_button.cget("text") == "我执黑方" and enabled(a)
    def fail(*args):raise ValueError("bad model")
    monkeypatch.setattr(a.engine, "switch", fail)
    a._switch_engine("px0", {}, human_red=True)
    wait(a, lambda: not a.busy)
    assert not a.human_red and a.player_side_button.cget("text") == "我执黑方" and enabled(a)


def test_real_combobox_events_persist_and_new_app_restores(gui):
    a = gui
    path = a.ui_preferences.path
    assert not path.exists()
    a.level.set("大师");a.coach.set("深入")
    assert not path.exists()  # ordinary code assignment is not a save action
    a.level_selector.event_generate("<<ComboboxSelected>>")
    assert json.loads(path.read_text(encoding="utf-8")) == {"level": "大师", "coach": "深入"}
    a.coach_selector.set("极速")
    a.coach_selector.event_generate("<<ComboboxSelected>>")
    before = path.read_bytes()
    b = App(preferences_path=path,engine_config_path=(path).with_suffix(".engine.json"))
    try:
        b.update();b.withdraw()
        assert (b.level.get(), b.coach.get()) == ("大师", "极速")
        assert b.human_red and b.board_view_mode == "auto"
        assert path.read_bytes() == before
    finally:
        b._close();b.__dict__.clear();gc.collect()


def test_save_failure_keeps_session_choices(gui, monkeypatch):
    def fail(*args):raise PermissionError("denied")
    monkeypatch.setattr(gui.ui_preferences, "save", fail)
    gui.level_selector.set("高手")
    gui.level_selector.event_generate("<<ComboboxSelected>>")
    assert gui.level.get() == "高手"
    assert "本次设置已生效，但未能保存" in gui.status.cget("text")


def test_preference_change_does_not_restart_running_analysis(gui, monkeypatch):
    a = gui
    gate, started = threading.Event(), threading.Event()
    calls = []
    m = legal_moves(a.official)[0]
    result = Analysis(m, [Candidate(1, m, [m.ucci()], 1)], 1, .01)
    def analyze(fen, ms, multipv):
        calls.append(ms);started.set();gate.wait(3);return result
    monkeypatch.setattr(a.engine, "analyze", analyze)
    a.recommend()
    assert started.wait(1) and not enabled(a)
    token = a.task_token
    a.coach_selector.set("深入")
    a.coach_selector.event_generate("<<ComboboxSelected>>")
    assert a.task_token == token and a.busy and calls == [COACH_MS["标准"]]
    gate.set()
    wait(a, lambda: not a.busy)
    assert enabled(a)
    a.recommend()
    wait(a, lambda: not a.busy)
    assert calls == [COACH_MS["标准"], COACH_MS["深入"]]


def test_cancelled_old_callback_cannot_apply_after_switch(gui):
    a = gui
    gate, started, finished = threading.Event(), threading.Event(), threading.Event()
    received = []
    def work():
        started.set();gate.wait(3);finished.set();return "old"
    a._run_task("controlled search", work, received.append)
    assert started.wait(1) and not enabled(a)
    a._cancel_current_task()
    assert enabled(a)
    a.player_side_button.invoke()
    gate.set();assert finished.wait(1)
    for _ in range(10):a.update();time.sleep(.01)
    assert not received and not a.move_history and not a.human_red


def test_minimum_window_controls_fit(gui):
    a = gui
    a.deiconify();a.geometry("1080x720");a.update()
    for widget in (a.flip_board_button, a.player_side_button, a.board_view_label,
                   a.creative_button, a.level_selector, a.coach_selector):
        assert widget.winfo_ismapped()
        assert widget.winfo_width() >= widget.winfo_reqwidth()
        assert widget.winfo_rootx()+widget.winfo_width() <= a.winfo_rootx()+a.winfo_width()
        assert widget.winfo_rooty()+widget.winfo_height() <= a.canvas.winfo_rooty()
    assert a.player_side_button.winfo_rootx() >= a.flip_board_button.winfo_rootx()+a.flip_board_button.winfo_width()
    assert a.level_selector.winfo_rooty() > a.player_side_button.winfo_rooty()
