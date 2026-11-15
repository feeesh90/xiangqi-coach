"""Real engines + native Windows capture of a synthetic phone window, NOT ToDesk."""
import sys,json,tempfile,queue,gc,time,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]));sys.path.insert(0,str(Path(__file__).resolve().parent))
import tkinter as tk
from PIL import Image,ImageDraw,ImageFont,ImageTk,ImageGrab
from app import App
from rules import Position,Move,legal_moves
from screen_follow.capture import WindowCapture,api,windows,geometry,physical_pixels
from screen_follow.process import FollowProcess
from smoke_player_settings import wait,verify_history
ROOT=Path(__file__).resolve().parents[1]
CORNERS=[[40,40],[552,40],[552,616],[40,616]]
font=ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf",28)
def render(p):
    image=Image.new("RGB",(592,656),(190,180,150));d=ImageDraw.Draw(image)
    for r in range(10):d.line((40,40+r*64,552,40+r*64),fill=(95,85,65))
    for c in range(9):d.line((40+c*64,40,40+c*64,616),fill=(95,85,65))
    for r,row in enumerate(p.b):
        for c,piece in enumerate(row):
            if piece:
                x,y=40+c*64,40+r*64
                d.ellipse((x-25,y-25,x+25,y+25),fill=(245,220,170),outline=(100,60,30),width=2)
                d.text((x,y),piece.upper(),font=font,anchor="mm",fill=(185,20,20) if piece.isupper() else (15,15,15))
    return image

def snapshot(app,path):
    with physical_pixels():
        x,y,w,h,_=geometry(api().GetAncestor(app.winfo_id(),2))
        ImageGrab.grab(bbox=(x,y,x+w,y+h),all_screens=True).save(path)

def main():
    out=Path(sys.argv[sys.argv.index("--out")+1]) if "--out" in sys.argv else ROOT/"data/screen_follow_verification/20261028"
    out.mkdir(parents=True,exist_ok=True)
    report={"source":"native Windows capture of synthetic Tk phone; real engines; not JJ or ToDesk","cases":[],"todesk_visible":any("todesk" in title.lower() for _,title in windows())}
    configdir=Path.home()/".xiangqi-coach"
    def hashes():return {n:hashlib.sha256((configdir/n).read_bytes()).hexdigest() if (configdir/n).exists() else None for n in ("engine_settings.json","ui_preferences.json","api_settings.json")}
    before=hashes()
    with tempfile.TemporaryDirectory(prefix="xiangqi-follow-") as temp:
        temp=Path(temp);profiles=json.loads((ROOT/"engine_settings.gpu.example.json").read_text(encoding="utf-8"))
        for engine in ("pikafish","px0"):
            for own in (True,False):
                config=temp/f"{engine}_{own}.json";config.write_text(json.dumps({**profiles,"selected":engine}),encoding="utf-8")
                app=App(preferences_path=temp/"ui.json",engine_config_path=config);app.geometry("1080x720+1460+20" if app.winfo_screenwidth()>=2500 else "1080x720+650+20")
                phone=tk.Toplevel(app);phone.title("Synthetic phone test (not ToDesk)");phone.geometry("592x656+850+0" if app.winfo_screenwidth()>=2500 else "592x656+0+0")
                # Keep this controlled source visible despite existing desktop apps.
                phone.attributes("-topmost",True)
                canvas=tk.Canvas(phone,highlightthickness=0);canvas.pack(fill="both",expand=True)
                def show(p,uncertain=False):
                    image=render(p)
                    if uncertain:ImageDraw.Draw(image).rectangle((0,0,300,250),fill='white')
                    phone.photo=ImageTk.PhotoImage(image,master=phone)
                    canvas.delete("all");canvas.create_image(0,0,image=phone.photo,anchor="nw");phone.lift();app.update()
                process=None
                try:
                    wait(app,lambda:not app.busy and app.engine.ready)
                    app.level.set("入门");app.api.configured=lambda:False
                    pos=Position();show(pos)
                    hwnd=int(api().GetAncestor(phone.winfo_id(),2));capture=WindowCapture(hwnd)
                    frame=capture.grab()
                    result_queue=queue.Queue();process=FollowProcess(result_queue.put)
                    process.recognize(frame,CORNERS,"tchess")
                    wait(app,lambda:not result_queue.empty())
                    result=result_queue.get();assert result['kind']=='recognized',result
                    app._follow_begin(pos,own,worker=process,capture=capture,corners=CORNERS)
                    show(pos,uncertain=True)
                    wait(app,lambda:app.follow_session.tracking=='checking')
                    assert app.follow_session.phase=='following' and not app.move_history
                    if engine=='pikafish' and own:snapshot(app,out/'checking.png')
                    if engine=='pikafish' and own:
                        wait(app,lambda:app.follow_session.tracking=='waiting_clear',timeout=12)
                        assert app.follow_session.phase=='following' and not app.follow_session.requires_resync
                        assert not app.move_history and app.follow_session.proposal is not None
                        snapshot(app,out/'waiting_clear.png')
                    show(pos)
                    wait(app,lambda:app.follow_session.tracking=='unchanged')
                    assert app.follow_session.phase=='following' and not app.move_history
                    if own:
                        wait(app,lambda:app.follow_session.proposal is not None)
                        first=next(m for m in legal_moves(pos) if m!=app.follow_session.proposal.move)
                    else:first=Move.from_ucci("b0c2")
                    pos.apply(first);started=time.monotonic();show(pos)
                    wait(app,lambda:len(app.move_history)==1,timeout=15)
                    recognition=time.monotonic()-started
                    assert app.official.fen()==pos.fen()
                    recommendation_start=started
                    if own:
                        assert app.canvas.itemcget(app.canvas.find_withtag("last_move_ring")[0],"outline")=="#000000"
                        second=legal_moves(pos)[0];pos.apply(second);recommendation_start=time.monotonic();show(pos)
                        wait(app,lambda:len(app.move_history)==2)
                    wait(app,lambda:app.follow_session.proposal is not None and not app.busy)
                    recommendation_seconds=time.monotonic()-recommendation_start
                    assert app.official.fen()==pos.fen()
                    expected=app.move_history[:]+[app.follow_session.proposal.move.ucci()]
                    app.draw();app.update()
                    if engine=="pikafish" and own:
                        snapshot(app,out/"follow_recommendation.png")
                    app.stop_follow();assert app.move_history==expected
                    assert process.p.poll() is not None
                    # Manual opponent Canvas clicks, independent expected orientation.
                    app.update()
                    manual=legal_moves(app.official)[0]
                    ox,oy,dx,dy=app._geo()
                    for r,c in ((manual.fr,manual.fc),(manual.tr,manual.tc)):
                        if not own:r,c=9-r,8-c
                        app.canvas.event_generate("<Button-1>",x=round(ox+c*dx),y=round(oy+r*dy));app.update()
                    wait(app,lambda:len(app.move_history)==len(expected)+2 and not app.busy,timeout=15)
                    verify_history(app)
                    if engine=="pikafish" and own:
                        app.update();snapshot(app,out/"manual_continuation.png")
                    report["cases"].append(dict(engine=engine,own_red=own,moves=app.move_history,checking_recovered=True,
                        long_check_tested=(engine=='pikafish' and own),long_check_recovered=True if engine=='pikafish' and own else None,
                        observed_move_seconds=round(recognition,3),recommendation_end_to_end_seconds=round(recommendation_seconds,3),actual_fen=app.official.fen(),worker_reaped=True))
                finally:
                    if process:process.close()
                    app._close();app.engine.close();app.__dict__.clear();gc.collect()
    assert before==hashes();report["real_user_configs_unchanged"]=True
    (out/"real_engine_capture_smoke.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False))
if __name__=="__main__":main()
