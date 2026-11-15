"""Exact opening matching and actual Tk controls, using isolated user settings."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PIL import Image

from endgame_ui import EndgameWindow
from rules import Move, Position, legal_moves
from screen_follow.opening import infer_opening
from test_board_orientation import gui


def image_board(position, red_bottom):
    board=deepcopy(position.b)
    return board if red_bottom else [row[::-1] for row in board[::-1]]


def result(position=None,red_bottom=True,uncertain=()):
    return dict(board=image_board(position or Position(),red_bottom),uncertain=list(uncertain),total_seconds=.1)


def selected(app,selector,value):
    selector.set(value)
    selector.event_generate("<<ComboboxSelected>>",when="tail")
    app.update()


@pytest.mark.parametrize("red_bottom",[True,False])
def test_initial_defaults_and_pure_input(red_bottom):
    board=image_board(Position(),red_bottom);before=deepcopy(board)
    guess=infer_opening(board,red_bottom)
    assert guess.own_red is red_bottom and guess.red_to_move is True and guess.kind=="initial"
    assert board==before


@pytest.mark.parametrize("red_bottom",[True,False])
def test_every_legal_non_capturing_red_first_move(red_bottom):
    start=Position();count=0
    for move in legal_moves(start):
        if start.b[move.tr][move.tc]:continue
        position=start.copy();position.apply_unchecked(move)
        board=image_board(position,red_bottom);before=deepcopy(board)
        guess=infer_opening(board,red_bottom)
        assert guess and guess.kind=="red_first_move" and not guess.red_to_move,move.ucci()
        assert guess.own_red is red_bottom and board==before
        count+=1
    assert count==42


@pytest.mark.parametrize("red_bottom",[True,False])
@pytest.mark.parametrize("case",["capture","black_first","two_plies","two_red","missing","wrong_piece","illegal_jump"])
def test_other_boards_are_not_guessed(red_bottom,case):
    position=Position()
    if case=="capture":assert position.apply(Move.from_ucci("b2b9"))
    elif case=="black_first":
        position.red=False;assert position.apply(Move.from_ucci("b9c7"))
    elif case=="two_plies":
        assert position.apply(Move.from_ucci("b0c2"));assert position.apply(Move.from_ucci("b9c7"))
    elif case=="two_red":
        assert position.apply(Move.from_ucci("b0c2"));position.red=True
        assert position.apply(Move.from_ucci("h0g2"))
    elif case=="missing":position.b[0][1]=""
    elif case=="wrong_piece":position.b[9][1]="C"
    elif case=="illegal_jump":position.b[4][0],position.b[6][0]="P",""
    assert infer_opening(image_board(position,red_bottom),red_bottom) is None


def test_unknown_orientation_and_malformed_data_do_not_guess():
    assert infer_opening(Position().b,None) is None
    assert infer_opening(Position().b,"红方") is None
    assert infer_opening([[""]*9]*9,True) is None
    assert infer_opening(None,True) is None
    board=Position().b;board[5][0]="X"
    assert infer_opening(board,True) is None
    assert infer_opening(image_board(Position(),False),True) is None


@pytest.mark.parametrize("red_bottom",[True,False])
@pytest.mark.parametrize("first_move",[False,True])
def test_gui_prefills_without_loading_or_starting(gui,monkeypatch,red_bottom,first_move):
    calls=[];monkeypatch.setattr(gui,"_follow_request",lambda:calls.append("search"))
    before=gui.official.fen(),gui.move_history[:],gui.human_red
    gui.open_follow();window=gui.follow_setup
    position=Position()
    if first_move:assert position.apply(Move.from_ucci("b0c2"))
    window.set_result(result(position,red_bottom))
    assert window.orientation_var.get()==("红方" if red_bottom else "黑方")
    assert window.human_var.get()==("红方" if red_bottom else "黑方")
    assert window.turn_var.get()==("黑方" if first_move else "红方")
    assert window.view_var.get()==("红方在下" if red_bottom else "黑方在下")
    assert "已识别" in window.status_var.get() and "可手动修改" in window.status_var.get()
    assert gui.follow_session is None and not calls
    window.flip_board_button.invoke()
    assert window.human_var.get()==("红方" if red_bottom else "黑方")
    assert window.turn_var.get()==("黑方" if first_move else "红方")
    window.close();gui.update()
    assert before==(gui.official.fen(),gui.move_history,gui.human_red)


def test_manual_choices_survive_repeated_recognition_and_source_correction(gui):
    gui.open_follow();window=gui.follow_setup
    window.set_result(result())
    selected(gui,window.human_selector,"黑方")
    selected(gui,window.turn_selector,"黑方")
    assert window.manual_choices=={"human","turn"}
    window.set_result(result())
    assert window.human_var.get()==window.turn_var.get()=="黑方"
    # A user-selected source direction is also preserved when rerunning a model.
    selected(gui,window.orientation_selector,"黑方")
    window.set_result(result())
    assert window.orientation_var.get()=="黑方"
    assert window.human_var.get()==window.turn_var.get()=="黑方"
    selected(gui,window.orientation_selector,"红方")
    assert window.human_var.get()==window.turn_var.get()=="黑方"
    assert "保留手动选择" in window.status_var.get()


def test_programmatic_values_do_not_lock_automatic_fields(gui):
    gui.open_follow();window=gui.follow_setup
    window.human_var.set("黑方");window.turn_var.set("黑方")
    window.set_result(result())
    assert not window.manual_choices
    assert window.human_var.get()==window.turn_var.get()=="红方"


def test_canvas_corrections_clear_stale_turn_and_reinfer(gui):
    gui.deiconify();gui.open_follow();window=gui.follow_setup;gui.update()
    window.set_result(result());gui.update()
    ox,oy,d=window.board_geo()
    # Independent expected pixels: raw and preview both have red at bottom.
    window.piece_var.set("清空")
    window.board_canvas.event_generate("<Button-1>",x=round(ox),y=round(oy+6*d))
    gui.update()
    assert not window.draft.board[6][0] and window.turn_var.get()=="请选择"
    window.piece_var.set("红兵")
    window.board_canvas.event_generate("<Button-1>",x=round(ox),y=round(oy+5*d))
    gui.update()
    assert window.draft.board[5][0]=="P" and window.turn_var.get()=="黑方"
    assert "红方已走一步" in window.status_var.get()
    # A deliberate override remains even when the next edit is not an opening.
    selected(gui,window.turn_selector,"红方")
    window.piece_var.set("清空")
    window.board_canvas.event_generate("<Button-1>",x=round(ox),y=round(oy+5*d))
    gui.update()
    assert window.turn_var.get()=="红方"


def test_crop_and_grid_recheck_keep_choices_but_new_capture_resets(gui,monkeypatch):
    gui.deiconify();gui.open_follow();window=gui.follow_setup
    with Image.new("RGB",(500,600),"tan") as frame:window.set_frame(frame)
    window.set_result(result())
    selected(gui,window.human_selector,"黑方");selected(gui,window.turn_selector,"黑方")
    window.capture=SimpleNamespace(roi=(0,0,500,600))
    requests=[];monkeypatch.setattr(window,"recognize",lambda:requests.append("recognize"))
    gui.update();left,top,width,height=window.image_rect
    window.crop_mode()
    for event,x,y in (("<ButtonPress-1>",left+.1*width,top+.1*height),
            ("<ButtonRelease-1>",left+.9*width,top+.9*height)):
        window.image_canvas.event_generate(event,x=round(x),y=round(y));gui.update()
    assert requests and window.manual_choices=={"human","turn"}
    window.set_result(result());window.mark_corners();window.set_result(result())
    assert window.human_var.get()==window.turn_var.get()=="黑方"
    with Image.new("RGB",(500,600),"tan") as frame:window.set_frame(frame)
    window.set_result(result())
    assert not window.manual_choices and window.human_var.get()==window.turn_var.get()=="红方"


def test_repairing_missing_king_can_restore_automatic_source_direction(gui):
    gui.deiconify();gui.open_follow();window=gui.follow_setup
    position=Position();position.b[0][4]=""
    window.set_result(result(position));gui.update()
    assert window.orientation_var.get()==window.turn_var.get()=="请选择"
    ox,oy,d=window.board_geo();window.piece_var.set("黑将")
    window.board_canvas.event_generate("<Button-1>",x=round(ox+4*d),y=round(oy))
    gui.update()
    assert window.orientation_var.get()==window.human_var.get()==window.turn_var.get()=="红方"


def test_uncertainty_is_not_cleared_or_imported_automatically(gui,monkeypatch):
    errors=[];monkeypatch.setattr("screen_follow.setup.messagebox.showerror",lambda *a,**k:errors.append(a))
    gui.open_follow();window=gui.follow_setup;window.set_result(result(uncertain=[[9,1]]))
    window.capture=SimpleNamespace(hwnd=123,roi=(0,0,10,10),baseline=(0,0,10,10,96))
    window.worker=SimpleNamespace(close=lambda:None)
    window.apply_button.invoke()
    assert errors and window.draft.uncertain=={(9,1)} and gui.follow_session is None
    # Only the existing explicit review action clears occupied marked cells.
    window.confirm_uncertain()
    assert not window.draft.uncertain and window.turn_var.get()=="红方"


@pytest.mark.parametrize("red_bottom",[True,False])
@pytest.mark.parametrize("first_move",[False,True])
def test_start_button_passes_inferred_snapshot_without_fabricating_history(gui,monkeypatch,red_bottom,first_move):
    requests=[];monkeypatch.setattr(gui,"_follow_request",lambda:requests.append("request"))
    gui.open_follow();window=gui.follow_setup
    position=Position()
    if first_move:assert position.apply(Move.from_ucci("b0c2"))
    window.set_result(result(position,red_bottom))
    sent=[]
    window.worker=SimpleNamespace(send=lambda *a,**k:sent.append((a,k)),close=lambda:None)
    window.capture=SimpleNamespace(hwnd=123,roi=(0,0,10,10),baseline=(0,0,10,10,96))
    assert gui.follow_session is None and not requests
    window.apply_button.invoke()
    assert gui.follow_setup is None and gui.follow_session.own_red is red_bottom
    assert gui.official.fen()==position.fen() and gui.game_start_fen==position.fen()
    assert not gui.move_history and gui.follow_session.position.red is (not first_move)
    assert sent[0][0]==("start",) and sent[0][1]["red_bottom"] is red_bottom
    assert requests


def test_normal_image_import_keeps_manual_turn_and_existing_identity(gui):
    window=EndgameWindow(gui);gui.endgame_window=window
    window.set_result(result(red_bottom=False))
    assert window.orientation_var.get()=="黑方"
    assert window.turn_var.get()=="请选择"
    assert window.human_var.get()==("红方" if gui.human_red else "黑方")
    window.close()


def test_confirmation_button_is_visible_at_minimum_setup_size(gui):
    gui.deiconify();gui.open_follow();window=gui.follow_setup
    window.set_result(result());window.geometry("960x640");gui.update()
    button=window.apply_button
    assert button.winfo_ismapped()
    assert button.winfo_rooty()-window.winfo_rooty()+button.winfo_height()<=window.winfo_height()
    assert button.winfo_rootx()-window.winfo_rootx()+button.winfo_width()<=window.winfo_width()
