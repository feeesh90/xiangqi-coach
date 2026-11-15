"""Two actual Tk applications using real localhost TCP, isolated network policy."""
import gc
import socket
import time
import tkinter as tk
from tkinter import ttk

import pytest
import lan_network
import lan_ui
from app import App
from lan_protocol import close_socket
from rules import Move, Position


class LocalTestInterface:
    ip = "127.0.0.1"
    prefix = 8
    category = "Private"
    name = "Local test"

    def validate(self):
        pass

    def contains(self, ip):
        return ip == self.ip


def children(widget, cls):
    out = []
    for child in widget.winfo_children():
        if isinstance(child, cls):
            out.append(child)
        out.extend(children(child, cls))
    return out


def button(window, label):
    return next(b for b in children(window, ttk.Button) if b.cget("text") == label)


@pytest.mark.parametrize("host_red,guidance,a_view,b_view", [
    (True, False, "auto", "auto"), (False, True, "auto", "auto"),
    (True, False, "red_bottom", "red_bottom"), (False, True, "black_bottom", "red_bottom"),
])
def test_create_join_moves_undo_restart_disconnect_reconnect_and_leave(monkeypatch, tmp_path, host_red, guidance, a_view, b_view):
    iface = LocalTestInterface()
    monkeypatch.setattr(lan_network, "current_interface", lambda ip: iface)
    monkeypatch.setattr(lan_ui, "interfaces", lambda: [iface])
    monkeypatch.setattr(lan_ui, "ensure_firewall", lambda *a: None)
    monkeypatch.setattr(App, "_engine_check", lambda self: None)
    monkeypatch.setattr(lan_ui.messagebox, "askyesno", lambda *a, **kw: True)
    with socket.socket() as probe:
        probe.bind((iface.ip, 0))
        port = probe.getsockname()[1]
    a, b = App(preferences_path=tmp_path/"host_ui.json",engine_config_path=(tmp_path/"host_ui.json").with_suffix(".engine.json")), App(preferences_path=tmp_path/"guest_ui.json",engine_config_path=(tmp_path/"guest_ui.json").with_suffix(".engine.json"))
    apps = (a, b)
    for app in apps:
        app.update()
        app.withdraw()

    def wait(predicate, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for app in apps:
                app.update()
            if predicate():
                return
            time.sleep(.005)
        raise AssertionError("GUI condition timed out: " + str([app.status.cget("text") for app in apps]))

    sessions = []
    expected_views = {a: a_view, b: b_view}
    def flipped(app):
        return {"red_bottom": False, "black_bottom": True}.get(expected_views[app], app.human_red)

    def choose(app, view):
        expected_views[app] = view
        if view == "auto":
            app._set_board_view_mode("auto")
        elif app._view_flipped() != (view == "black_bottom"):
            app.flip_board_button.invoke()
        elif app.board_view_mode == "auto":
            app.flip_board_button.invoke()
            app.flip_board_button.invoke()
        assert app.board_view_mode == view

    def assert_views():
        for app in apps:
            assert str(app.player_side_button.cget("state"))=="disabled"
            assert app.player_side_button.cget("text")==("我执红方" if app.human_red else "我执黑方")
            assert app.board_view_mode == expected_views[app]
            assert app._view_flipped() == flipped(app)
            assert ("黑方在下" if flipped(app) else "红方在下") in app.sim_label.cget("text")
            r = 0 if flipped(app) else 9
            item, = app.canvas.find_withtag(f"piece:{r}:4")
            assert app.canvas.coords(item)[1] > app.canvas.winfo_height()/2

    def click_move(app, value):
        move = Move.from_ucci(value)
        ox, oy, dx, dy = app._geo()
        for r, c in ((move.fr, move.fc), (move.tr, move.tc)):
            vr, vc = (9-r, 8-c) if flipped(app) else (r, c)
            app.canvas.event_generate("<Button-1>", x=round(ox+vc*dx), y=round(oy+vr*dy))

    try:
        choose(a, a_view)
        choose(b, b_view)
        a.lan_dialog()
        a.lan_window.withdraw()
        wait(lambda: bool(children(a.lan_window, ttk.Combobox)[0].get()))
        fields = [e for e in children(a.lan_window, ttk.Entry) if not isinstance(e, ttk.Combobox)]
        fields[0].delete(0, "end")
        fields[0].insert(0, str(port))
        children(a.lan_window, ttk.Combobox)[1].set("红方" if host_red else "黑方")
        if guidance:
            children(a.lan_window, ttk.Checkbutton)[0].invoke()
        button(a.lan_window, "创建房间").invoke()
        wait(lambda: a.mode == "lan" and a.lan_state is not None)
        a.lan_window.withdraw()
        sessions.append(a.lan_session)
        assert not a._lan_can_move()
        # Display controls remain available while waiting for an opponent.
        assert str(a.flip_board_button.cget("state")) == "normal"
        assert str(a.creative_button.cget("state")) == "disabled"
        b.lan_dialog()
        b.lan_window.withdraw()
        wait(lambda: bool(children(b.lan_window, ttk.Combobox)[0].get()))
        fields = [e for e in children(b.lan_window, ttk.Entry) if not isinstance(e, ttk.Combobox)]
        for entry, value in zip(fields, [str(port), iface.ip, a.lan_session.code]):
            entry.delete(0, "end")
            entry.insert(0, value)
        button(b.lan_window, "加入房间").invoke()
        wait(lambda: b.mode == "lan" and b.lan_state is not None and a.lan_state["connected"])
        b.lan_window.withdraw()
        sessions.append(b.lan_session)
        assert a.human_red == host_red and b.human_red != host_red
        assert_views()
        assert a.lan_state["allow_guidance"] == b.lan_state["allow_guidance"] == guidance
        assert a.ai_take_btn.cget("state") == "disabled"
        assert b.sim_btn.cget("state") == ("normal" if guidance else "disabled")
        # A local rotation neither sends a state change nor alters the peer.
        if a_view != "auto":
            peer_view = (b.board_view_mode, b._view_flipped(), b.sim_label.cget("text"))
            before = (a.official.fen(), a.lan_state["revision"], a.task_token, a.lan_sending)
            choose(a, "black_bottom" if a_view == "red_bottom" else "red_bottom")
            assert (a.official.fen(), a.lan_state["revision"], a.task_token, a.lan_sending) == before
            assert (b.board_view_mode, b._view_flipped(), b.sim_label.cget("text")) == peer_view
            choose(a, a_view)
            assert_views()
        sequence = ["b0c2", "b9c7", "c2b0", "c7b9",
                    "b0c2", "b9c7", "c2b0", "c7e8",
                    "h0g2", "h9g7", "b0c2", "g7h9",
                    "a3a4", "a6a5", "i3i4", "i6i5"]
        for index, value in enumerate(sequence):
            mover = a if a.official.red == a.human_red else b
            other = b if mover is a else a
            before = len(mover.move_history)
            if index == 7:
                snapshot = (a.official.fen(), a.move_history[:], a.lan_state["revision"])
                click_move(mover, "c7b9")
                assert "重复往返" in mover.status.cget("text")
                assert (a.official.fen(),a.move_history,a.lan_state["revision"]) == snapshot
                assert a.official.fen() == b.official.fen() and a.move_history == b.move_history
            other.make_move(Move.from_ucci(value))
            assert len(other.move_history) == before
            click_move(mover, value)
            mover.make_move(Move.from_ucci(value))  # Duplicate clicks cannot submit twice.
            wait(lambda: len(a.move_history) == len(b.move_history) == index + 1)
            assert a.official.fen() == b.official.fen()
            assert a.move_history == b.move_history
            assert_views()
            assert not a._is_ai_turn() and not b._is_ai_turn()
        a.undo()
        wait(lambda: len(a.move_history) == len(b.move_history) == 15)
        assert_views()
        old_game = a.lan_state["game"]
        b.new_game()
        wait(lambda: a.lan_state["game"] != old_game and not a.move_history and not b.move_history)
        assert a.official.fen() == b.official.fen() == Position().fen()
        assert_views()
        if guidance:
            a.toggle_sim()
            assert a.sim is not None
            a.make_move(Move.from_ucci("b0c2"))
            assert not a.move_history and len(a.sim_move_history) == 1 and not b.move_history
            a.toggle_sim()
        else:
            a.toggle_sim()
            assert a.sim is None
        close_socket(b.lan_session.sock)
        wait(lambda: not a.lan_state["connected"] and not b.lan_state["connected"])
        assert not a._lan_can_move() and not b._lan_can_move()
        assert str(b.flip_board_button.cget("state")) == "normal"
        choose(b, "black_bottom")
        assert_views()
        b._lan_reconnect()
        wait(lambda: a.lan_state["connected"] and b.lan_state["connected"])
        assert_views()
        a._lan_send("resign")
        wait(lambda: bool(a.lan_state["result"]) and bool(b.lan_state["result"]))
        assert not a._lan_can_move()
        a._set_mode("pvp")
        wait(lambda: a.mode == "pvp" and not b.lan_state["connected"])
        assert a.ai_take_btn.cget("state") == "normal"
        assert a.lan_session is None
        assert a.board_view_mode == a_view
        assert a._view_flipped() == (a_view == "black_bottom")
        assert ("黑方在下" if a_view == "black_bottom" else "红方在下") in a.sim_label.cget("text")
    finally:
        for app in apps:
            app._close()
        for session in sessions:
            session.close()
    assert not any(t.is_alive() for session in sessions for t in session.threads)
    # Test-owned windows must release their Tcl references on the UI thread.
    for app in apps:
        app.__dict__.clear()
    gc.collect()
