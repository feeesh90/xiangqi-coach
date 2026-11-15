"""Visible real ToDesk calibration test. No phone input, no game import, temp config."""
import sys,time,tempfile,json,gc,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from unittest.mock import patch
from PIL import ImageGrab
from app import App
from screen_follow.capture import api,geometry,physical_pixels

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"data/screen_follow_verification/20261028_capture_fix"
OUT.mkdir(parents=True,exist_ok=True)
def fingerprint():
    d=Path.home()/".xiangqi-coach"
    return {n:hashlib.sha256((d/n).read_bytes()).hexdigest() if (d/n).exists() else None for n in ("engine_settings.json","ui_preferences.json","api_settings.json")}

def main():
    report={"kind":"actual ToDesk window and actual calibration UI; no phone input or game import"}
    before=fingerprint()
    with tempfile.TemporaryDirectory(prefix="xiangqi-capture-fix-") as folder,patch.object(App,"_engine_check",lambda self:None):
        folder=Path(folder);a=App(preferences_path=folder/"ui.json",engine_config_path=folder/"engines.json")
        a.title("采集修复验证 · 不会落子");a.geometry("1080x720+600+20")
        a.engine_badge.configure(text="采集测试 · 引擎未启动")
        initial=a.official.fen();errors=[];started=time.monotonic();completed=False
        a.report_callback_exception=lambda kind,value,tb:errors.append(str(value))
        def open_setup():
            a.open_follow();a.follow_setup.title("投屏采集验证 · 请选择手机窗口")
            a.follow_setup.geometry("1000x700+20+30")
            if "--auto" in sys.argv:
                def capture():
                    w=a.follow_setup;w.choose_image()
                    report["selected_window"]=w.window_selector.get()
                    w.capture_button.invoke()
                a.after(15000,capture)
        def finish():
            report["callback_errors"]=errors;report["formal_game_unchanged"]=a.official.fen()==initial and not a.move_history
            report["user_configs_unchanged"]=before==fingerprint()
            (OUT/"live_calibration.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
            print(json.dumps(report,ensure_ascii=False),flush=True)
            a._close()
        def watch():
            nonlocal completed
            w=a.follow_setup
            if not completed and w and w.last_result:
                completed=True;r=w.last_result
                report.update(pieces=sum(bool(p) for row in r["board"] for p in row),board=r["board"],uncertain=r["uncertain"],
                    recovered=r.get("recovered",[]),recognition_seconds=r["recognition_seconds"],model=w.model_var.get(),status=w.status_var.get())
                a.update_idletasks()
                hwnd=api().GetAncestor(w.winfo_id(),2)
                with physical_pixels():
                    x,y,width,height,dpi=geometry(hwnd)
                    ImageGrab.grab(bbox=(x,y,x+width,y+height),all_screens=True).save(OUT/"live_calibration.png")
                print("REAL_CAPTURE_RECOGNIZED",report["pieces"],report["uncertain"],flush=True)
                a.after(15000,finish);return
            if time.monotonic()-started>300:
                report["error"]="UI validation timed out";finish();return
            if errors:
                report["error"]="UI callback error";finish();return
            a.after(100,watch)
        a.after(100,open_setup);a.after(200,watch);a.mainloop()
        a.engine.close();a.__dict__.clear();gc.collect()
    return 1 if report.get("error") else 0
if __name__=="__main__":raise SystemExit(main())
