"""Draft isolation and real canvas input, including cancellation during search."""
import threading
import time

import pytest

import app as app_module
from board_editor import BoardEditor
from engine import Analysis, Candidate
from rules import Move, Position, START_FEN, legal_moves, piece_name
from test_board_orientation import gui, click, pixel, wait


def test_draft_free_move_swap_replace_delete_and_validation():
    pos = Position()
    draft = BoardEditor.from_position(pos)
    draft.click(0, 0)
    draft.click(4, 4)
    assert draft.board[4][4] == "r" and not draft.board[0][0]
    draft.click(4, 4)
    draft.click(9, 0)
    assert draft.board[9][0] == "r" and draft.board[4][4] == "R"
    draft.tool, draft.piece = "add", "K"
    draft.click(4, 4)
    with pytest.raises(ValueError, match="恰好"):
        draft.position()
    draft.tool = "delete"
    draft.click(4, 4)
    draft.click(0, 4)
    assert draft.board[0][4] == ""
    assert pos.fen() == START_FEN


def state(a):
    return (a.official.fen(), a.game_start_fen, a.game_source,
            tuple(a.move_history), tuple((p.fen(), tuple(h), m) for p, h, m in a.history),
            a.mode, a.human_red, a.manual_takeover)


@pytest.mark.parametrize("human_red", [True, False])
@pytest.mark.parametrize("flipped", [True, False])
def test_free_canvas_edit_both_sides_and_directions(gui, human_red, flipped):
    a = gui
    a.human_red = human_red
    a._set_board_view_mode("black_bottom" if flipped else "red_bottom")
    before = state(a)
    a.creative_button.invoke()
    assert a._editing() and a.editor_toolbar.winfo_manager() == "grid"
    # Move either side in any direction, even though it is red's turn.
    click(a, 0, 1)
    assert a.editor.selected == (0, 1)
    click(a, 5, 4)
    assert a.editor.board[5][4] == "n"
    a.editor_piece_selector.set("红帅")
    a.editor_piece_selector.event_generate("<<ComboboxSelected>>")
    click(a, 3, 3)
    assert a.editor.board[3][3] == "K"
    a.editor_tool_var.set("delete")
    a._editor_tool_changed()
    click(a, 0, 4)
    assert not a.editor.board[0][4]
    assert state(a) == before
    for r, row in enumerate(a.editor.board):
        for c, piece in enumerate(row):
            items = a.canvas.find_withtag(f"piece:{r}:{c}")
            assert len(items) == bool(piece)
            if piece:
                assert a.canvas.coords(items[0]) == pytest.approx(pixel(a, r, c))
                assert a.canvas.itemcget(items[0], "text") == piece_name(piece)
    a.editor_cancel_button.invoke()
    assert not a._editing() and state(a) == before


def test_flip_twice_is_view_only_and_clears_edit_selection(gui):
    a = gui
    original = state(a)
    first = a._view_flipped()
    a.flip_board_button.invoke()
    assert a._view_flipped() != first
    a.flip_board_button.invoke()
    assert a._view_flipped() == first and state(a) == original
    a.enter_creative()
    click(a, 9, 1)
    board = [r[:] for r in a.editor.board]
    a.flip_board_button.invoke()
    assert a.editor.selected is None and a.editor.board == board
    a.cancel_creative()
    assert a._view_flipped() != first and state(a) == original


def test_illegal_draft_never_reaches_rules_or_engine_until_apply(gui, monkeypatch):
    a = gui
    original = state(a)
    a.enter_creative()
    def forbidden(*args, **kwargs):
        raise AssertionError("incomplete draft must not be analyzed")
    monkeypatch.setattr(a.engine, "analyze", forbidden)
    monkeypatch.setattr(app_module, "in_check", forbidden)
    monkeypatch.setattr(app_module, "legal_moves", forbidden)
    a.editor_tool_var.set("delete")
    a._editor_tool_changed()
    click(a, 0, 4)
    click(a, 9, 4)
    a._update_turn()
    a.flip_board_button.invoke()
    a.editor_apply_button.invoke()
    assert a._editing() and state(a) == original
    assert "恰好" in a.coach_text.get("1.0", "end")
    monkeypatch.undo()


def test_apply_new_root_history_undo_book_restart_and_switch(gui, monkeypatch):
    a = gui
    a.mode = "pvp"
    a.make_move(Move.from_ucci("b0c2"), auto_ai=False)
    assert a.history
    a.enter_creative()
    click(a, 0, 1)
    click(a, 2, 2)
    a.editor_turn_var.set("红方")
    a.editor_turn_selector.event_generate("<<ComboboxSelected>>")
    expected = a.editor.position().fen()
    a.editor_apply_button.invoke()
    assert a.official.fen() == a.game_start_fen == expected
    assert a.game_source == "creative" and "自定义" in a.sim_label.cget("text")
    assert not a.history and not a.move_history and a._master_references() == []
    a.undo()
    assert a.official.fen() == expected
    a.make_move(legal_moves(a.official)[0], auto_ai=False)
    a.undo()
    assert a.official.fen() == expected and not a.history
    a.restart_current_game()
    assert a.official.fen() == expected
    monkeypatch.setattr(a.engine, "switch", lambda *args: "Pikafish")
    a._switch_engine("pikafish", {}, human_red=False)
    wait(a, lambda: not a.busy)
    assert not a.human_red and a.game_start_fen == expected
    a.new_game()
    assert a.game_start_fen == START_FEN


def test_cancel_restores_history_and_keeps_new_display_direction(gui):
    a = gui
    a.make_move(Move.from_ucci("b0c2"), auto_ai=False)
    before, body = state(a), a.coach_text.get("1.0", "end")
    a.enter_creative()
    click(a, 0, 0)
    click(a, 5, 3)
    a.flip_board_button.invoke()
    direction = a.board_view_mode
    assert a._escape_mode() == "break"
    assert not a._editing() and state(a) == before and a.board_view_mode == direction
    assert a.coach_text.get("1.0", "end") == body


@pytest.mark.parametrize("apply", [False, True])
def test_enter_while_searching_invalidates_even_identical_position(gui, apply):
    a = gui
    original = state(a)
    gate, started, returned = threading.Event(), threading.Event(), threading.Event()
    results = []
    def old_work():
        started.set()
        try:
            assert gate.wait(4)
            return "OLD"
        finally:
            returned.set()
    a._run_task("old", old_work, results.append)
    assert started.wait(1)
    token = a.task_token
    a.enter_creative()
    assert not a.busy and a.task_token > token
    if apply:
        a.apply_creative()
    else:
        a.cancel_creative()
    gate.set()
    assert returned.wait(1)
    for _ in range(12):
        a.update()
        time.sleep(.01)
    assert results == [] and not a.busy
    assert a.official.fen() == original[0]


def test_api_and_deferred_feedback_cannot_pollute_editor_or_applied_root(gui, monkeypatch):
    a = gui
    monkeypatch.setattr(a.api, "configured", lambda: True)
    started, gate, returned = threading.Event(), threading.Event(), threading.Event()
    def explain(ctx):
        started.set()
        assert gate.wait(3)
        returned.set()
        return "OLD_API"
    monkeypatch.setattr(a.api, "explain_xiangqi", explain)
    a._request_api_context({"fen": a.official.fen()})
    assert started.wait(1)
    callbacks = []
    a._defer_api_feedback(lambda: callbacks.append("old deferred"))
    a.enter_creative()
    a.apply_creative()
    gate.set()
    assert returned.wait(1)
    for _ in range(15):
        a.update()
        time.sleep(.01)
    assert not callbacks and "OLD_API" not in a.coach_text.get("1.0", "end")


def test_old_connection_test_does_not_open_dialog_after_editor_entry(gui, monkeypatch):
    a=gui
    monkeypatch.setattr(a.api,"configured",lambda:True)
    started,gate,returned=threading.Event(),threading.Event(),threading.Event()
    messages=[]
    def check():
        started.set()
        assert gate.wait(3)
        returned.set()
        return "connected"
    monkeypatch.setattr(a.api,"test",check)
    monkeypatch.setattr(app_module.messagebox,"showinfo",lambda *args,**kwargs:messages.append(args))
    a.api_test()
    assert started.wait(1)
    a.enter_creative();a.cancel_creative()
    gate.set();assert returned.wait(1)
    for _ in range(15):
        a.update();time.sleep(.01)
    assert not messages


def test_mutating_and_guidance_entrypoints_are_guarded(gui):
    a = gui
    a.enter_creative()
    before, token = state(a), a.task_token
    for action in (
        a.new_game, a.restart_current_game, a.undo, a.toggle_sim, a.ai_take,
        a.opponent_move, a.recommend, a.scan_threats, a.analyze_my_move,
        a.toggle_premove, a.take_over_turn, a.open_endgame, a.lan_dialog,
        a.engine_settings, a.api_settings, a.api_test,
        lambda: a.make_move(Move.from_ucci("b0c2")),
        lambda: a._set_mode("pvp"), lambda: a._switch_engine("px0", {}),
        lambda: a._run_task("forbidden", lambda: None, lambda _: None),
        a._schedule_opponent, a._run_scheduled_opponent,
    ):
        action()
    assert state(a) == before and a.task_token == token and not a.busy
    assert str(a.flip_board_button.cget("state")) == "normal"
    assert str(a.ai_btn.cget("state")) == "disabled"
    assert str(a.editor_apply_button.cget("state")) == "normal"
    a.cancel_creative()
    assert str(a.ai_btn.cget("state")) == "normal"


def test_entry_restrictions(gui):
    a = gui
    for mode, connecting in (("lan", False), ("ai", True)):
        a.mode, a.lan_connecting = mode, connecting
        a.enter_creative()
        assert not a._editing()
    a.mode, a.lan_connecting = "ai", False
    a.toggle_sim()
    a.enter_creative()
    assert not a._editing()
    a.toggle_sim()
    a.open_endgame()
    a.enter_creative()
    assert not a._editing()


@pytest.mark.parametrize("human_red,turn", [(False, True), (True, False)])
def test_apply_schedules_correct_ai_side_and_terminal_does_not(gui, monkeypatch, human_red, turn):
    a = gui
    a.human_red = human_red
    scheduled = []
    monkeypatch.setattr(a, "_schedule_opponent", lambda: scheduled.append(a.official.red))
    a.enter_creative()
    a.editor_turn_var.set("红方" if turn else "黑方")
    a.apply_creative()
    assert scheduled == [turn] and a.human_red == human_red
    scheduled.clear()
    a.enter_creative()
    a.editor = BoardEditor.from_position(Position("3k5/3RR4/9/9/4p4/9/9/9/9/4K4 b - - 0 1"))
    a.editor_turn_var.set("黑方")
    a.apply_creative()
    assert not scheduled and "将死" in a.turn_label.cget("text")


def test_editor_controls_fit_minimum_window(gui):
    a = gui
    a.deiconify()
    a.enter_creative()
    for size in ("1080x720", "1400x850"):
        a.geometry(size)
        a.update()
        for widget in (a.editor_toolbar, a.editor_piece_selector, a.editor_turn_selector,
                       a.editor_apply_button, a.editor_cancel_button, a.flip_board_button,
                       a.creative_button, a.canvas, a.analysis_panel):
            x = widget.winfo_rootx()-a.winfo_rootx()
            y = widget.winfo_rooty()-a.winfo_rooty()
            assert x >= 0 and y >= 0
            assert x+widget.winfo_width() <= a.winfo_width()
            assert y+widget.winfo_height() <= a.winfo_height()
        assert a.canvas.winfo_height() > 180
        assert not a.action_bar.winfo_ismapped() and not a.playbar.winfo_ismapped()
        assert a.flip_board_button.winfo_width() >= a.flip_board_button.winfo_reqwidth()
        assert a.creative_button.winfo_width() >= a.creative_button.winfo_reqwidth()
    a.cancel_creative()
    a.update()
    assert a.action_bar.winfo_ismapped() and a.playbar.winfo_ismapped()
