"""Capture stage regression: actual picker lifetime, async errors and overlays."""
import time,gc
from types import SimpleNamespace
import pytest
from PIL import Image
from test_board_orientation import gui
import screen_follow.capture as cap


def test_overlay_flags_are_narrow():
    assert cap.clickthrough_overlay(0x80800a8)
    assert not cap.clickthrough_overlay(0x8080000)  # ToDesk palette: not click-through.
    assert not cap.clickthrough_overlay(0x80000)    # Ordinary layered dialog.
    assert not cap.clickthrough_overlay(0x20)


def test_occlusion_palette_only_ignored_during_initial_capture(monkeypatch):
    rects={2:(0,0,1920,1080),3:(50,100,120,500),4:(250,250,450,450)}
    styles={2:0x80800a8,3:0x8080000,4:0}
    active=[2,3]
    class Api:
        def GetWindow(self,h,kind):
            if kind==4:return 1 if h==3 else 0
            chain=[1]+active
            return chain[chain.index(h)+1] if h in chain and chain.index(h)+1<len(chain) else 0
        def IsWindowVisible(self,h):return True
        def IsIconic(self,h):return False
        def GetWindowLongPtrW(self,h,index):return styles[h]
        def GetWindowRect(self,h,ptr):
            ptr._obj.left,ptr._obj.top,ptr._obj.right,ptr._obj.bottom=rects[h]
        def GetWindowTextW(self,h,buffer,size):buffer.value={2:"cursor",3:"palette",4:"other app"}[h]
    monkeypatch.setattr(cap,"api",Api)
    monkeypatch.setattr(cap,"process_id",lambda h:10 if h in (1,3) else 20)
    assert cap.occluding_windows(1,(100,100,500,600),initial=True)==[]
    assert cap.occluding_windows(1,(100,100,500,600))==["palette"]
    assert cap.occluding_windows(1,(130,100,500,600))==[]
    active.append(4)
    assert cap.occluding_windows(1,(130,100,500,600),initial=True)==["other app"]


def test_window_choice_retains_stringvar_and_default(gui,monkeypatch):
    import screen_follow.setup as setup
    monkeypatch.setattr(setup,"phone_window_choices",lambda:[(123,"Xiaomi phone")])
    gui.open_follow();w=gui.follow_setup;w.choose_image();gc.collect();gui.update()
    assert w.window_selector.current()==0 and w.window_selector.get().startswith("Xiaomi phone")
    assert w.window_picker.choice_var.get()==w.window_selector.get()
    w.close()


def test_capture_failure_is_visible_and_original_game_unchanged(gui,monkeypatch):
    import screen_follow.setup as setup
    monkeypatch.setattr(setup,"activate_source",lambda h,**kw:None)
    class Blocked:
        def __init__(self,h):pass
        def check_ready(self,**kw):pass
        def grab(self,**kw):raise cap.CaptureError("other window blocks board")
    monkeypatch.setattr(setup,"WindowCapture",Blocked)
    before=gui.official.fen();gui.deiconify();gui.open_follow();w=gui.follow_setup
    w.capture_selected(123)
    deadline=time.monotonic()+3
    while "采集失败" not in w.status_var.get() and time.monotonic()<deadline:gui.update();time.sleep(.01)
    assert "other window" in w.status_var.get() and w.capture is None and w.draft is None
    assert w.apply_button.instate(["disabled"]) and gui.official.fen()==before
    assert gui.state()=="normal" and w.state()=="normal"
    w.close()


def test_capture_automatically_requests_recognition(gui,monkeypatch):
    import screen_follow.setup as setup
    monkeypatch.setattr(setup,"activate_source",lambda h,**kw:None)
    calls=[]
    class Source:
        def __init__(self,h):self.hwnd=h
        def check_ready(self,**kw):pass
        def grab(self,**kw):calls.append(kw);return Image.new("RGB",(300,400),"orange")
    monkeypatch.setattr(setup,"WindowCapture",Source)
    gui.open_follow();w=gui.follow_setup
    monkeypatch.setattr(w,"recognize",lambda:calls.append("recognize"))
    w.capture_selected(123)
    deadline=time.monotonic()+3
    while "recognize" not in calls and time.monotonic()<deadline:gui.update();time.sleep(.01)
    assert calls==[{"initial":True},"recognize"] and w.frame.size==(300,400)
    assert w.model_var.get().startswith("JJ")
    w.close()


def test_blank_capture_rejected(monkeypatch):
    from contextlib import nullcontext
    monkeypatch.setattr(cap,"physical_pixels",nullcontext)
    monkeypatch.setattr(cap,"geometry",lambda h:(0,0,300,400,96))
    monkeypatch.setattr(cap.ImageGrab,"grab",lambda **kw:Image.new("RGB",(300,400),"white"))
    with pytest.raises(cap.CaptureError,match="空白或黑屏"):cap.WindowCapture(1).grab(check_occlusion=False)


def test_local_piece_recovery_stays_uncertain():
    import subprocess
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    code="""
from PIL import Image,ImageDraw
from screen_follow.tchess import TChessRecognizer
m=TChessRecognizer.__new__(TChessRecognizer)
def detect(rgb,threshold=.5):
    return [] if rgb.shape[:2]==(656,592) else [(120,120,.95,'b')]
m._detect=detect
im=Image.new('RGB',(592,656),(20,40,70));d=ImageDraw.Draw(im)
d.ellipse((527,143,577,193),fill='white',outline='black',width=3)
r=m.recognize_image(im,[[40,40],[552,40],[552,616],[40,616]])
assert r['board'][2][8]=='b',r
assert [2,8] in r['uncertain'] and [2,8,'b'] in r['recovered'],r
"""
    r=subprocess.run([str(root/".recognition-venv/Scripts/python.exe"),"-X","utf8","-c",code],cwd=root,capture_output=True,timeout=15)
    assert r.returncode==0,r.stderr.decode("utf-8")


def test_secondary_classifier_requires_visual_occupancy_and_keeps_review():
    import subprocess
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    code="""
from PIL import Image,ImageDraw
from screen_follow.tchess import TChessRecognizer
m=TChessRecognizer.__new__(TChessRecognizer)
m._detect=lambda rgb,threshold=.5:[]
m._classify_recheck=lambda rgb,x,y:('b',.999)
im=Image.new('RGB',(592,656),(20,40,70));d=ImageDraw.Draw(im)
d.ellipse((527,143,577,193),fill='white',outline='black',width=3)
r=m.recognize_image(im,[[40,40],[552,40],[552,616],[40,616]])
assert r['board'][2][8]=='b' and [2,8] in r['uncertain']
assert r['confidence'][2][8] is None
assert r['local_checks'][0]['classifier_confidence']==.999
m._classify_recheck=lambda *args:(_ for _ in ()).throw(AssertionError('No occupancy evidence'))
try:m.recognize_image(Image.new('RGB',(592,656),(20,40,70)),[[40,40],[552,40],[552,616],[40,616]])
except ValueError as e:assert '未检测到棋子' in str(e)
else:raise AssertionError('Blank board must not be accepted')
"""
    result=subprocess.run([str(root/".recognition-venv/Scripts/python.exe"),"-X","utf8","-c",code],cwd=root,capture_output=True,timeout=15)
    assert result.returncode==0,result.stderr.decode("utf-8")


def test_suspected_missing_piece_cannot_be_bulk_confirmed(gui):
    gui.open_follow();w=gui.follow_setup
    board=[[""]*9 for _ in range(10)]
    w.set_result(dict(board=board,uncertain=[[2,8]]))
    w.confirm_uncertain()
    assert (2,8) in w.draft.uncertain and "疑似漏子" in w.status_var.get()
    # An explicit cell edit, including selecting clear for a real empty cell,
    # is required before the blanket confirmation is allowed.
    w.draft.set_piece(2,8,"")
    w.confirm_uncertain()
    assert not w.draft.uncertain
    w.close()
