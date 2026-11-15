import gc
import threading
import time
from types import SimpleNamespace
import pytest
import app as app_module
from app import App
from endgame_ui import EndgameWindow
from engine import Analysis,Candidate
from rules import Position,START_FEN,Move,legal_moves
from test_position_import import ENDGAME


@pytest.fixture
def gui(monkeypatch,tmp_path):
    monkeypatch.setattr(App,"_engine_check",lambda self:None)
    monkeypatch.setattr(App,"_schedule_opponent",lambda self:None)
    a=App(preferences_path=tmp_path/"ui_preferences.json",engine_config_path=(tmp_path/"ui_preferences.json").with_suffix(".engine.json"));a.update();a.withdraw()
    monkeypatch.setattr(a.engine,"installed",lambda:False)
    monkeypatch.setattr(a.api,"configured",lambda:False)
    try:yield a
    finally:
        a._close();monkeypatch.undo();a.__dict__.clear();gc.collect()


def pump(a,predicate):
    deadline=time.monotonic()+4
    while time.monotonic()<deadline:
        a.update()
        if predicate():return
        time.sleep(.01)
    raise AssertionError("GUI callback timed out")


@pytest.mark.parametrize("side",[True,False])
@pytest.mark.parametrize("view",["auto","red_bottom","black_bottom"])
def test_import_identity_view_pixels_undo_and_restart(gui,side,view):
    a=gui
    pos=Position(ENDGAME);pos.red=side
    a.import_position(pos,side,view)
    assert a.game_start_fen==pos.fen() and a.human_red==side
    assert a.board_view_mode==view and a.game_source=="image"
    a.mode="pvp"  # local click identity restrictions don't apply
    m=legal_moves(a.official)[0]
    ox,oy,dx,dy=a._geo()
    flipped=view=="black_bottom"  # pvp auto is red bottom
    for r,c in ((m.fr,m.fc),(m.tr,m.tc)):
        vr,vc=(9-r,8-c) if flipped else (r,c)
        a.canvas.event_generate("<Button-1>",x=round(ox+vc*dx),y=round(oy+vr*dy))
    assert a.move_history==[m.ucci()]
    a.undo();assert a.official.fen()==pos.fen() and not a.history
    a.undo();assert a.official.fen()==pos.fen()
    a.make_move(m,auto_ai=False);a.restart_current_game()
    assert a.official.fen()==pos.fen() and not a.history
    a._set_mode("ai");assert a.game_start_fen==pos.fen()
    assert a.board_view_mode==view
    a.new_game();assert a.game_start_fen==START_FEN and a.game_source=="standard"


def test_failed_import_leaves_all_state_and_lan_guard(gui):
    a=gui
    before=(a.official.fen(),a.human_red,a.board_view_mode,a.task_token,a.api_request_token)
    bad=Position(ENDGAME);bad.b[0][4]=""
    with pytest.raises(ValueError):a.import_position(bad,False,"black_bottom")
    assert before==(a.official.fen(),a.human_red,a.board_view_mode,a.task_token,a.api_request_token)
    a.lan_connecting=True
    with pytest.raises(ValueError,match="联机"):a.import_position(Position(ENDGAME),False,"auto")
    a.lan_connecting=False
    a.mode="lan";a.open_endgame();assert a.endgame_window is None


def test_master_prefix_guard_allows_only_standard_start(gui,monkeypatch):
    called=[]
    monkeypatch.setattr(app_module,"master_lookup",lambda moves:called.append(moves) or ["reference"])
    assert gui._master_references()==["reference"]
    gui.import_position(Position(ENDGAME),True,"auto")
    assert gui._master_references()==[] and len(called)==1
    gui.toggle_sim();assert gui._master_references()==[]
    gui.toggle_sim();gui.new_game()
    assert gui._master_references()==["reference"]


def test_switch_side_preserves_endgame_root_and_failure_identity(gui,monkeypatch):
    a=gui;a.import_position(Position(ENDGAME),True,"red_bottom")
    monkeypatch.setattr(a.engine,"switch",lambda *args:"Pikafish")
    a._switch_engine("pikafish",{},False)
    pump(a,lambda:not a.busy)
    assert a.human_red is False and a.official.fen()==ENDGAME
    def bad(*args):raise ValueError("missing model")
    monkeypatch.setattr(a.engine,"switch",bad)
    a._switch_engine("px0",{},True);pump(a,lambda:not a.busy)
    assert a.human_red is False and a.game_start_fen==ENDGAME


def test_identical_fen_import_rejects_old_engine_callback(gui):
    a=gui;a.import_position(Position(ENDGAME),True,"auto")
    gate=threading.Event();started=threading.Event();done=[]
    def work():started.set();gate.wait(3);return "old"
    a._run_task("old",work,lambda result:done.append(result))
    assert started.wait(1)
    token=a.task_token
    a.import_position(Position(ENDGAME),True,"black_bottom")
    gate.set()
    pump(a,lambda:not a.busy)
    for _ in range(20):a.update();time.sleep(.01)
    assert a.task_token>token and not done and not a.move_history


def test_identical_fen_import_rejects_old_api_callback(gui,monkeypatch):
    a=gui;a.import_position(Position(ENDGAME),True,"auto")
    gate=threading.Event();started=threading.Event();returned=threading.Event()
    monkeypatch.setattr(a.api,"configured",lambda:True)
    def explain(context):
        started.set();gate.wait(3);returned.set();return "OLD_API_RESULT"
    monkeypatch.setattr(a.api,"explain_xiangqi",explain)
    a._request_api_context({"fen":ENDGAME})
    assert started.wait(1)
    token=a.api_request_token
    a.import_position(Position(ENDGAME),True,"red_bottom")
    gate.set();assert returned.wait(1)
    for _ in range(20):a.update();time.sleep(.01)
    assert a.api_request_token>token
    assert "OLD_API_RESULT" not in a.coach_text.get("1.0","end")


def test_changing_image_discards_pending_recognition(gui,monkeypatch,tmp_path):
    import endgame_ui
    from PIL import Image
    first,second=tmp_path/"第一张.png",tmp_path/"第二张.png"
    for path in (first,second):Image.new("RGB",(500,600),"white").save(path)
    gate=threading.Event()
    class Job:
        def __init__(self):self.finished=threading.Event()
        def cancel(self):gate.set()
        def run(self,*args):
            gate.wait(2);self.finished.set()
            return {"board":Position(ENDGAME).b,"uncertain":[]}
    monkeypatch.setattr(endgame_ui,"RecognitionJob",Job)
    gui.open_endgame();win=gui.endgame_window
    win.load_image(first);win.recognize();win.load_image(second)
    for _ in range(20):gui.update();time.sleep(.01)
    assert win.path==second and win.draft is None
    assert str(win.apply_button.cget("state"))=="disabled"
    assert gui.official.fen()==START_FEN


def test_import_controls_fit_minimum_window(gui):
    import tkinter.ttk as ttk
    gui.deiconify();gui.geometry("1080x720");gui.update();gui.open_endgame()
    win=gui.endgame_window;win.geometry("960x640");win.update()
    def descend(widget):
        for child in widget.winfo_children():
            yield child;yield from descend(child)
    combos=[w for w in descend(win) if isinstance(w,ttk.Combobox)]
    assert len(combos)==4
    for widget in combos+[win.apply_button,win.flip_board_button]:
        right=widget.winfo_rootx()-win.winfo_rootx()+widget.winfo_width()
        bottom=widget.winfo_rooty()-win.winfo_rooty()+widget.winfo_height()
        assert right<=win.winfo_width() and bottom<=win.winfo_height()
        assert widget.winfo_width()>=widget.winfo_reqwidth()


def test_terminal_import_does_not_schedule_ai(gui,monkeypatch):
    calls=[];monkeypatch.setattr(gui,"_schedule_opponent",lambda:calls.append(True))
    gui.import_position(Position("3k5/3RR4/9/9/4p4/9/9/9/9/4K4 b - - 0 1"),True,"auto")
    assert not calls and "将死" in gui.turn_label.cget("text")


def test_preview_edit_confirm_cancel_and_no_file_writes(gui,monkeypatch,tmp_path):
    from PIL import Image
    path=tmp_path/"中文截图 (1).png";Image.new("RGB",(500,600),"white").save(path)
    gui.deiconify();gui.update()
    gui.open_endgame();win=gui.endgame_window;win.update()
    before=gui.official.fen()
    win.load_image(path)
    result={"board":Position(ENDGAME).b,"uncertain":[[6,2]]}
    win.set_result(result)
    win.piece_var.set("红马")
    ox,oy,d=win.board_geo()
    # Initial auto view now has the red player at top: image c3 -> (3, 6).
    win.board_canvas.event_generate("<Button-1>",x=round(ox+6*d),y=round(oy+3*d))
    assert not win.draft.uncertain
    win.turn_var.set("红方");win.human_var.set("黑方");win.view_var.set("红方在下")
    win.apply()
    assert gui.game_start_fen==ENDGAME and not gui.human_red and gui.board_view_mode=="red_bottom"
    assert gui.endgame_window is None and not win.alive
    assert win.photo is None and win.preview is None and win.path is None
    assert sorted(p.name for p in tmp_path.iterdir())==[path.name]
    gui.open_endgame();gui.endgame_window.load_image(path)
    state=gui.official.fen();gui.endgame_window.close()
    assert gui.official.fen()==state


def test_queued_recognition_does_not_touch_closed_window(gui,monkeypatch):
    import endgame_ui
    gate=threading.Event()
    class Job:
        def __init__(self):self.finished=threading.Event()
        def cancel(self):gate.set()
        def run(self,*args):
            gate.wait(2);self.finished.set()
            return {"board":Position(ENDGAME).b,"uncertain":[]}
    monkeypatch.setattr(endgame_ui,"RecognitionJob",Job)
    gui.open_endgame();win=gui.endgame_window;win.path="test.png"
    before=gui.official.fen();win.recognize();win.close()
    for _ in range(20):gui.update();time.sleep(.01)
    assert gui.official.fen()==before and gui.endgame_window is None


@pytest.mark.parametrize("human_red",[True,False])
def test_auto_preview_follows_player_and_applies_auto_without_freezing_view(gui,human_red):
    a=gui
    a.deiconify();a.open_endgame()
    win=a.endgame_window;win.update()
    win.set_result({"board":Position(ENDGAME).b,"uncertain":[]})
    assert win.view_mode=="auto" and win.view_var.get()=="黑方在下"
    win.human_var.set("红方" if human_red else "黑方")
    assert win.view_mode=="auto"
    assert win.view_var.get()==("黑方在下" if human_red else "红方在下")
    # Source orientation is independent of the viewing choice.
    source=win.orientation_var.get()
    win.turn_var.set("红方")
    win.apply()
    assert a.game_start_fen==ENDGAME and a.board_view_mode=="auto"
    assert a.human_red==human_red and a._view_flipped()==human_red
    a.player_side_button.invoke()
    assert a._view_flipped()==a.human_red and a.board_view_mode=="auto"
    assert source=="红方"


def test_manual_preview_keeps_direction_when_player_changes(gui):
    a=gui
    a._set_board_view_mode("red_bottom");a.open_endgame()
    win=a.endgame_window;win.update()
    win.set_result({"board":Position(ENDGAME).b,"uncertain":[]})
    assert win.view_mode=="red_bottom"
    win.human_var.set("黑方")
    assert win.view_var.get()=="红方在下"
    win.flip_board_button.invoke()
    assert win.view_mode=="black_bottom" and win.view_var.get()=="黑方在下"
    win.human_var.set("红方")
    assert win.view_var.get()=="黑方在下"
    win.turn_var.set("红方");win.apply()
    assert a.board_view_mode=="black_bottom" and a._view_flipped()


def test_local_two_player_auto_preview_matches_red_bottom_board(gui):
    a=gui;a._set_mode("pvp");a.open_endgame()
    win=a.endgame_window;win.update()
    win.set_result({"board":Position(ENDGAME).b,"uncertain":[]})
    win.human_var.set("黑方")
    assert win.view_mode=="auto" and win.view_var.get()=="红方在下"
    win.turn_var.set("红方");win.apply()
    assert a.board_view_mode=="auto" and not a._view_flipped() and a.mode=="pvp"


@pytest.mark.parametrize("source_red",[True,False])
def test_preview_flip_moves_clicks_and_uncertain_marks_without_changing_draft(gui,source_red):
    gui.deiconify();gui.open_endgame()
    win=gui.endgame_window;win.update()
    canonical=Position(ENDGAME).b
    image=canonical if source_red else [row[::-1] for row in canonical[::-1]]
    cell=(6,2) if source_red else (3,6)
    win.set_result({"board":image,"uncertain":[list(cell)]})
    win.view_var.set("红方在下")
    before=[row[:] for row in win.draft.board]
    original=gui.official.fen()
    win.flip_board_button.invoke()
    assert win.view_var.get()=="黑方在下" and win.draft.board==before
    ox,oy,d=win.board_geo()
    # Canonical red knight c3 is at screen row 3, column 6 in black-bottom view.
    item,=win.board_canvas.find_withtag(f"piece:{cell[0]}:{cell[1]}")
    assert win.board_canvas.coords(item)==pytest.approx((ox+6*d,oy+3*d))
    win.piece_var.set("红马")
    win.board_canvas.event_generate("<Button-1>",x=round(ox+6*d),y=round(oy+3*d))
    assert win.draft.board==before and not win.draft.uncertain
    assert gui.official.fen()==original
    win.turn_var.set("红方")
    win.apply_button.invoke()
    assert gui.game_start_fen==ENDGAME and gui.board_view_mode=="black_bottom"
