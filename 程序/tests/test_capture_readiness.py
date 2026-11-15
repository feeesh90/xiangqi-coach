"""Initial capture transactions, visible failure recovery and worker identity."""
import io
import json
import time
from types import SimpleNamespace

import pytest
from PIL import Image
from rules import Position
from test_board_orientation import gui
import screen_follow.capture as cap
import screen_follow.setup as setup


def wait(app,predicate,timeout=3):
    end=time.monotonic()+timeout
    while not predicate() and time.monotonic()<end:
        app.update();time.sleep(.005)
    assert predicate(),app.follow_setup.status_var.get() if app.follow_setup else "setup closed"


def source(monkeypatch,checks=()):
    events=[];remaining=list(checks)
    class Source:
        def __init__(self,hwnd):self.hwnd=hwnd
        def check_ready(self,**kw):
            events.append(("check",kw))
            if remaining:
                value=remaining.pop(0)
                if isinstance(value,Exception):raise value
        def grab(self,**kw):
            events.append(("grab",kw))
            return Image.new("RGB",(320,400),"tan")
    monkeypatch.setattr(setup,"WindowCapture",Source)
    monkeypatch.setattr(setup,"activate_source",lambda hwnd,**kw:events.append(("activate",hwnd)))
    return events


@pytest.mark.parametrize("temporary",[None,"not_foreground","occluded","target_unavailable","size_changed"])
def test_two_ready_samples_after_transient_error(gui,monkeypatch,temporary):
    checks=[] if temporary is None else [cap.CaptureError("temporary",temporary)]
    events=source(monkeypatch,checks)
    gui.open_follow();w=gui.follow_setup
    monkeypatch.setattr(w,"recognize",lambda:events.append(("recognize",None)))
    before=(gui.official.fen(),gui.move_history[:])
    w.capture_selected(123)
    assert events==[("activate",123)]  # activation is synchronous, before hiding
    assert w.capture_pending and w.apply_button.instate(["disabled"])
    w.capture_selected(456)  # a rapid second press cannot launch another capture
    wait(gui,lambda:any(e[0]=="recognize" for e in events))
    assert [e[0] for e in events].count("activate")==1
    assert [e[0] for e in events].count("grab")==1
    assert [e[0] for e in events].count("check")==2+bool(temporary)
    assert w.frame.size==(320,400) and not w.frame_stale
    assert w.retry_button.instate(["!disabled"])
    assert (gui.official.fen(),gui.move_history)==before
    w._poll_capture(w.capture_generation)
    assert [e[0] for e in events].count("grab")==1  # completed operations are idempotent


@pytest.mark.parametrize("code",["not_foreground","occluded","target_closed"])
def test_failure_restores_ui_and_retry_target(gui,monkeypatch,code):
    source(monkeypatch,[cap.CaptureError("blocked by example.exe",code)]*10)
    gui.deiconify();gui.update();gui.open_follow();w=gui.follow_setup;w.capture_selected(123)
    w._capture_deadline=time.monotonic()+.15
    wait(gui,lambda:w.capture_stage=="capture_failed")
    assert "example.exe" in w.progress_var.get()
    assert not w.capture_pending and w.state()=="normal"
    assert w.apply_button.instate(["disabled"])
    assert w.image_canvas.find_withtag("capture_status")
    assert (w.selected_hwnd is None)==(code=="target_closed")
    assert w.retry_button.instate(["disabled"])==(code=="target_closed")
    if code!="target_closed":
        events=source(monkeypatch)
        monkeypatch.setattr(w,"recognize",lambda:events.append(("recognize",None)))
        w.retry_button.invoke()
        wait(gui,lambda:any(e[0]=="recognize" for e in events))
        assert events[0]==("activate",123)


def test_capture_click_requests_foreground_before_destroying_picker(gui,monkeypatch):
    events=source(monkeypatch)
    monkeypatch.setattr(setup,"phone_window_choices",lambda:[(123,"Xiaomi")])
    gui.deiconify();gui.update();gui.open_follow();w=gui.follow_setup;w.choose_image()
    def activate(hwnd,**kw):
        assert w.window_picker.winfo_exists()
        assert w.state()!="withdrawn"
        assert kw==dict(verify=False)
        events.append(("activate",hwnd))
    monkeypatch.setattr(setup,"activate_source",activate)
    w.capture_button.invoke()
    assert not w.window_picker.winfo_exists()
    w.cancel_recognition()
    assert not w.capture_pending and w.state()=="normal"


def test_cancel_close_and_recapture_ignore_stale_callbacks(gui,monkeypatch):
    events=source(monkeypatch)
    gui.deiconify();gui.update();gui.open_follow();w=gui.follow_setup
    monkeypatch.setattr(w,"recognize",lambda:events.append(("recognize",None)))
    w.capture_selected(123);old=w.capture_generation
    w.cancel_recognition();w._poll_capture(old);w._recognize_captured(old)
    assert [e[0] for e in events]==["activate"]
    assert w.capture_stage=="cancelled" and w.state()=="normal"
    w.capture_selected(456);new=w.capture_generation
    assert new!=old
    w._poll_capture(old);w._recognize_captured(old)
    wait(gui,lambda:any(e[0]=="recognize" for e in events))
    assert w.capture.hwnd==456
    w.capture_selected(789);old=w.capture_generation;w.close()
    w._poll_capture(old);w._recognize_captured(old)
    gui.update()
    assert gui.follow_setup is None and not w.alive


def test_cancel_between_image_and_automatic_recognition(gui,monkeypatch):
    events=source(monkeypatch)
    gui.open_follow();w=gui.follow_setup
    original=setup.WindowCapture.grab
    def grab(self,**kw):
        image=original(self,**kw)
        w.after(0,w.cancel_recognition)  # runs before the scheduled idle inference
        return image
    monkeypatch.setattr(setup.WindowCapture,"grab",grab)
    monkeypatch.setattr(w,"recognize",lambda:events.append(("recognize",None)))
    w.capture_selected(123)
    wait(gui,lambda:w.capture_stage=="cancelled")
    assert w.frame is not None and not any(e[0]=="recognize" for e in events)


def test_failed_recapture_keeps_old_image_marked_stale_and_cannot_start(gui,monkeypatch):
    source(monkeypatch,[cap.CaptureError("blocked","occluded")]*10)
    gui.open_follow();w=gui.follow_setup
    with Image.new("RGB",(320,400),"tan") as image:w.set_frame(image)
    w.set_result(dict(board=Position().b,uncertain=[]))
    w.manual_choices.add("turn");w.turn_var.set("黑方")
    w.cropping=True;w.crop_start=(1,1)
    w.capture_selected(123);w._capture_deadline=time.monotonic()
    wait(gui,lambda:w.capture_stage=="capture_failed")
    assert w.frame is not None and w.frame_stale and w.draft is not None
    assert w.manual_choices=={"turn"} and w.turn_var.get()=="黑方"
    assert "上次画面" in w.status_var.get() and w.image_canvas.find_withtag("stale_frame")
    assert w.apply_button.instate(["disabled"]) and gui.follow_session is None
    assert not w.cropping and "crop_start" not in w.__dict__
    w.release(SimpleNamespace(x=20,y=20))  # stale image has no usable capture ROI
    errors=[];monkeypatch.setattr(setup.messagebox,"showerror",lambda *a,**kw:errors.append(a))
    w.apply();assert errors and gui.follow_session is None


def test_recognition_errors_visible_and_old_worker_cannot_fail_new_request(gui,monkeypatch):
    gui.open_follow();w=gui.follow_setup
    with Image.new("RGB",(320,400),"tan") as image:w.set_frame(image)
    old=object();current=w.worker_token=object();w.request="active"
    w.receive(dict(kind="error",fatal=True,message="old exit"),old)
    assert w.request=="active"
    w.receive(dict(kind="error",fatal=True,message="current exit"),current)
    assert w.request is None and w.capture_stage=="recognition_failed"
    assert "current exit" in w.progress_var.get() and w.frame is not None
    assert w.recognize_button.instate(["!disabled"]) and w.apply_button.instate(["disabled"])
    w.worker_token=current;w.request="again"
    monkeypatch.setattr(w,"set_result",lambda result:(_ for _ in ()).throw(ValueError("bad result")))
    w.receive(dict(id="again",kind="recognized",result={}),current)
    assert "bad result" in w.progress_var.get() and w.request is None
    w.draft=SimpleNamespace(uncertain=set())
    w.worker=SimpleNamespace(close=lambda:None)
    w._capture_controls();assert w.apply_button.instate(["!disabled"])
    w.cancel_recognition()
    assert w.worker is None and w.apply_button.instate(["disabled"]) and w.capture_stage=="cancelled"
    w.draft=None


def test_diagnostics_are_bounded_and_only_export_on_explicit_request(gui,monkeypatch,tmp_path):
    gui.open_follow();w=gui.follow_setup
    for i in range(210):w._record("test",index=i)
    assert len(w.capture_diagnostics)==200 and w.capture_diagnostics[0]["index"]==10
    path=tmp_path/'export.json'
    monkeypatch.setattr(setup.filedialog,"asksaveasfilename",lambda **kw:str(path))
    assert not path.exists()
    w.export_diagnostics()
    records=json.loads(path.read_text(encoding="utf-8"))
    assert len(records)==200 and records[-1]["index"]==209


@pytest.mark.parametrize("cloaked,expected",[(True,False),(False,True),(None,True)])
def test_unnamed_blocker_details_and_cloaked_fail_closed(monkeypatch,cloaked,expected):
    class Api:
        def GetWindow(self,h,kind):return 2 if h==1 and kind==3 else 0
        def IsWindowVisible(self,h):return True
        def IsIconic(self,h):return False
        def GetWindowLongPtrW(self,*a):return 0x80000  # layered alone is a blocker
        def GetWindowRect(self,h,p):
            p._obj.left,p._obj.top,p._obj.right,p._obj.bottom=20,30,100,120
            return 1
        def GetWindowTextW(self,h,buf,size):buf.value=""
        def GetClassNameW(self,h,buf,size):buf.value="ExamplePopup"
    monkeypatch.setattr(cap,"api",Api)
    monkeypatch.setattr(cap,"window_cloaked",lambda h:cloaked)
    monkeypatch.setattr(cap,"process_id",lambda h:h)
    monkeypatch.setattr(cap,"process_name",lambda h:"example.exe")
    details=cap.occlusion_details(1,(0,0,80,90),True)
    assert bool(details)==expected
    if expected:
        assert details[0]["label"]=="example.exe／无标题窗口"
        assert details[0]["window_class"]=="ExamplePopup"
        assert details[0]["intersection"]==(20,30,80,90)
        assert details[0]["checked_at"]


def test_terminal_worker_error_carries_request_and_is_not_emitted_twice():
    from screen_follow.process import FollowProcess
    worker=FollowProcess.__new__(FollowProcess)
    worker.closed=False;worker.context={};worker.pending_recognition="request"
    worker.p=SimpleNamespace(stdout=io.BytesIO(b'not json\n'))
    events=[];worker.callback=events.append
    worker._read()
    assert len(events)==1 and events[0]["fatal"] and events[0]["id"]=="request"


def test_real_recognition_process_death_finishes_current_request(gui):
    gui.open_follow();w=gui.follow_setup
    with Image.new("RGB",(320,400),"tan") as image:w.set_frame(image)
    w.recognize();worker=w.worker
    worker.p.kill()
    wait(gui,lambda:w.capture_stage=="recognition_failed",timeout=5)
    assert w.request is None and w.worker is None and w.frame is not None
    assert w.recognition_job is None and w.recognize_button.instate(["!disabled"])
