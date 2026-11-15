import threading,time
from types import SimpleNamespace
import pytest
from rules import Position,Move,legal_moves
from screen_follow.session import FollowSession
from test_board_orientation import gui,wait
from test_engine_memory import factory


def test_proposal_is_not_confirmed_and_actual_alternative_wins():
    s=FollowSession(Position(),True);before=s.position.fen();stamp=s.stamp
    assert s.offer(Move.from_ucci("b0c2"),stamp)
    assert s.position.fen()==before and s.version==0
    assert s.displayed().fen()!=before
    actual=Move.from_ucci("h0g2")
    assert s.commit(actual,observed=True)
    assert s.proposal is None and s.last_observed_own and s.version==1
    assert not s.offer(Move.from_ucci("b0c2"),stamp)
    opponent=Move.from_ucci("b9c7")
    assert s.commit(opponent,observed=True) and not s.last_observed_own


def test_match_only_exact_single_basic_move_and_duplicates():
    s=FollowSession(Position(),True)
    assert s.match_board(s.position.b)==("unchanged",None)
    p=s.position.copy();m=Move.from_ucci("b0c2");p.apply(m)
    assert s.match_board(p.b)==("move",m)
    p.apply(Move.from_ucci("b9c7"))
    assert s.match_board(p.b)==("uncertain",None)


def prepare(app,monkeypatch,own=True):
    monkeypatch.setattr(app,"_follow_request",lambda:None)
    app._follow_begin(Position(),own)
    assert app.mode=="follow"
    return app.follow_session


def event(app,move):
    s=app.follow_session
    app._follow_event(dict(kind="move",epoch=s.epoch,version=s.version,move=move))


def test_gui_actual_move_alternative_rings_and_flip(gui,monkeypatch):
    a=gui;s=prepare(a,monkeypatch)
    # Controlled observations use the same commit callback, without a capture worker.
    a.follow_process=SimpleNamespace(send=lambda *x,**kw:None,close=lambda:None)
    assert not a._view_flipped() and a.human_red
    before=a.official.fen()
    assert s.offer(Move.from_ucci("b0c2"),s.stamp)
    a.draw()
    assert a.official.fen()==before and not a.move_history
    assert a.canvas.itemcget(a.canvas.find_withtag("last_move_ring")[0],"outline")=="#FFFFFF"
    event(a,"h0g2")
    assert a.move_history==["h0g2"] and s.position.fen()==a.official.fen()
    assert a.canvas.itemcget(a.canvas.find_withtag("last_move_ring")[0],"outline")=="#000000"
    old=a.official.fen();a.flip_board_button.invoke();assert a.official.fen()==old
    event(a,"b9c7")
    assert a.canvas.itemcget(a.canvas.find_withtag("last_move_ring")[0],"outline")=="#FFFFFF"


@pytest.mark.parametrize("own",[True,False])
def test_stop_reuses_proposal_once_and_preserves_identity(gui,monkeypatch,own):
    a=gui;s=prepare(a,monkeypatch,own)
    if not own:a._follow_commit(Move.from_ucci("b0c2"),True)
    m=legal_moves(s.position)[0];assert s.offer(m,s.stamp)
    history=a.move_history[:];identity=a.human_red
    a.stop_follow();a.stop_follow()
    assert a.move_history==history+[m.ucci()]
    assert s.phase=="manual" and s.proposal is None and not s.last_observed_own
    assert a.human_red==identity and s.own_red==own and not a._is_ai_turn()
    assert a.official.fen()==s.position.fen()


def test_paused_capture_keeps_proposal_and_blocks_old_events(gui,monkeypatch):
    a=gui;s=prepare(a,monkeypatch)
    s.offer(Move.from_ucci("b0c2"),s.stamp)
    a._follow_pause("遮挡")
    event(a,"h0g2")
    assert not a.move_history and s.proposal
    assert not a.make_move(Move.from_ucci("h0g2"))
    a.stop_follow()
    assert a.move_history==["b0c2"]


def test_manual_opponent_click_and_no_repeated_old_recognition(gui,monkeypatch):
    a=gui;s=prepare(a,monkeypatch,False);a.stop_follow()
    assert not a._is_ai_turn()
    # Explicit expected pixels: black-own auto view rotates red to screen top.
    geo=a._geo();ox,oy,dx,dy=geo
    for r,c in [(0,7),(2,6)]:
        a.canvas.event_generate("<Button-1>",x=round(ox+c*dx),y=round(oy+r*dy))
        a.update()
    assert a.move_history==["b0c2"] and s.own_turn
    event(a,"h0g2");assert len(a.move_history)==1


def test_live_observation_cancels_delayed_search(gui,monkeypatch):
    a=gui;s=prepare(a,monkeypatch)
    a.follow_process=SimpleNamespace(send=lambda *x,**kw:None,close=lambda:None)
    release=threading.Event();search=threading.Event()
    def analyze(*args):search.set();release.wait(3);return SimpleNamespace(best=Move.from_ucci("b0c2"))
    monkeypatch.setattr(a,"_analyze_position",analyze)
    from screen_follow.ui import FollowUI
    FollowUI._follow_request(a)
    assert search.wait(1)
    event(a,"h0g2");release.set();wait(a,lambda:not a.busy)
    for _ in range(20):a.update();time.sleep(.005)
    assert a.move_history==["h0g2"] and s.proposal is None


def test_takeover_during_search_keeps_same_search(gui,monkeypatch):
    a=gui;s=prepare(a,monkeypatch)
    release=threading.Event();search=threading.Event()
    def analyze(*args):search.set();release.wait(3);return SimpleNamespace(best=Move.from_ucci("b0c2"))
    monkeypatch.setattr(a,"_analyze_position",analyze)
    from screen_follow.ui import FollowUI
    FollowUI._follow_request(a);assert search.wait(1)
    token=a.task_token;a.stop_follow();assert a.task_token==token and a.busy
    release.set();wait(a,lambda:not a.busy)
    assert a.move_history==["b0c2"] and s.phase=="manual"


def test_real_tracker_synthetic_sixty_plies_both_views():
    import subprocess,json
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([str(root/".recognition-venv/Scripts/python.exe"),"-X","utf8",str(root/"tests/verify_follow_tracking.py")],cwd=root,capture_output=True,timeout=60)
    assert result.returncode==0,result.stderr.decode("utf-8")
    report=json.loads(result.stdout)
    assert len(report["cases"])==2
    assert all(c["plies"]==60 and c["false_commits"]==0 for c in report["cases"])


def test_observed_external_cycle_is_not_blocked_and_stale_frames_ignored(gui,monkeypatch):
    a=gui;s=prepare(a,monkeypatch)
    a.follow_process=SimpleNamespace(send=lambda *x,**kw:None,close=lambda:None)
    sequence=["b0c2","b9c7","c2b0","c7b9"]*2
    for m in sequence:event(a,m)
    assert a.move_history==sequence and s.version==8
    before=a.official.fen()
    a._follow_event(dict(kind="move",epoch=s.epoch,version=7,move="c7b9"))
    assert a.official.fen()==before and len(a.move_history)==8


def test_manual_undo_keeps_formal_and_session_state_equal(gui,monkeypatch):
    a=gui;s=prepare(a,monkeypatch,False)
    a.stop_follow()
    assert a.make_move(Move.from_ucci("b0c2"),auto_ai=False)
    assert s.own_turn
    assert a.make_move(Move.from_ucci("b9c7"),auto_ai=False,actor="ai")
    a.undo()
    assert a.move_history==["b0c2"] and a.official.fen()==s.position.fen()
    assert s.context==a._cycle_context() and s.proposal is None


def test_resync_same_position_preserves_history_and_pending_proposal(gui,monkeypatch):
    a=gui;s=prepare(a,monkeypatch)
    a._follow_commit(Move.from_ucci("b0c2"),True)
    a._follow_commit(Move.from_ucci("b9c7"),True)
    assert s.offer(Move.from_ucci("h0g2"),s.stamp)
    old_moves=a.move_history[:]
    a._follow_pause("测试")
    assert a._follow_begin(a.official.copy(),True)
    assert a.move_history==old_moves and a.follow_session is s
    # The source epoch changed. A recommendation must be rebased or recomputed.
    assert s.proposal is None or s.proposal.stamp==s.stamp


def test_setup_can_be_cancelled_without_changing_game(gui):
    a=gui;before=a.official.fen()
    a.open_follow()
    setup=a.follow_setup
    assert setup.alive and setup.frame is None
    setup.close();a.update()
    assert a.follow_setup is None and a.official.fen()==before and a.mode=="ai"


def test_engine_switch_finishes_when_observed_position_changes(factory,gui,monkeypatch):
    _,_,_,probes,_=factory
    a=gui;s=prepare(a,monkeypatch)
    a.follow_process=SimpleNamespace(send=lambda *x,**kw:None,close=lambda:None)
    started=threading.Event();release=threading.Event()
    def trial():started.set();release.wait(3)
    probes["px0"]=trial
    a._switch_engine("px0",{},human_red=False)
    assert started.wait(1) and a.busy and a.switching_engine
    event(a,"h0g2")
    assert a.move_history==["h0g2"] and a.busy
    release.set();wait(a,lambda:not a.busy)
    assert a.engine.selected=="px0" and "Px0 已就绪" in a.engine_badge.cget("text")
    assert a.human_red and a.follow_session.own_red and a.move_history==["h0g2"]


def test_capture_repositions_and_requires_recalibration_on_resize(monkeypatch):
    from contextlib import nullcontext
    from PIL import Image
    import screen_follow.capture as cap
    dimensions=[10,20,600,700,144]
    monkeypatch.setattr(cap,"geometry",lambda hwnd:tuple(dimensions))
    monkeypatch.setattr(cap,"physical_pixels",nullcontext)
    boxes=[]
    def grab(**kw):
        boxes.append(kw["bbox"]);image=Image.new("RGB",(200,300),"white");image.putpixel((5,5),(0,0,0));return image
    monkeypatch.setattr(cap.ImageGrab,"grab",grab)
    source=cap.WindowCapture(123,(30,40,230,340))
    source.grab(check_occlusion=False)
    dimensions[:2]=[100,200];source.grab(check_occlusion=False)
    assert boxes==[(40,60,240,360),(130,240,330,540)]
    dimensions[2]=601
    with pytest.raises(cap.CaptureError,match="尺寸或 DPI"):source.grab(check_occlusion=False)
    with pytest.raises(cap.CaptureError,match="越界"):cap.WindowCapture(123,(0,0,800,800))


def test_setup_real_candidate_worker_can_be_cancelled_without_disk_frames(gui):
    from pathlib import Path
    from PIL import Image
    a=gui;before=a.official.fen();a.open_follow();setup=a.follow_setup
    source=Path(__file__).resolve().parents[1]/"data/screen_follow_verification/20261028/jj_original.jpg"
    original=source.read_bytes()
    with Image.open(source) as image:setup.set_frame(image)
    setup.model_var.set("JJ截图候选（需确认）")
    setup.recognize();worker=setup.worker
    deadline=time.monotonic()+15
    while setup.draft is None and time.monotonic()<deadline:
        a.update();time.sleep(.01)
    assert setup.draft is not None,setup.status_var.get()
    assert setup.draft.board[9][7]=="N" and len(setup.draft.uncertain)==0
    setup.close();a.update()
    assert worker.p.poll() is not None and a.official.fen()==before and source.read_bytes()==original
