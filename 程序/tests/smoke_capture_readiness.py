"""Native Windows capture + real model, a local window displaying a JJ still.

Not a live ToDesk test. Can run on an isolated Windows desktop without switching
the user's input desktop. Screenshots here are explicit verification artifacts.
"""
import gc
import hashlib
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import time
import tkinter as tk
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from PIL import Image,ImageGrab,ImageTk
from app import App
from screen_follow.capture import api,geometry,physical_pixels


def wait(app,predicate,timeout=12):
    end=time.monotonic()+timeout
    while not predicate() and time.monotonic()<end:
        app.update();time.sleep(.005)
    assert predicate(),app.follow_setup.status_var.get()


def fingerprints():
    folder=Path.home()/'.xiangqi-coach'
    return {name:hashlib.sha256((folder/name).read_bytes()).hexdigest() if (folder/name).exists() else None
        for name in ('engine_settings.json','ui_preferences.json','api_settings.json')}


def source_window():
    phone=tk.Tk();phone.title('JJ静态图片测试源（非ToDesk）');phone.geometry('400x850+5+5')
    canvas=tk.Canvas(phone,highlightthickness=0,bg='black');canvas.pack(fill='both',expand=True)
    with Image.open(ROOT/'data/screen_follow_verification/20261028/jj_original.jpg') as image:
        image.thumbnail((400,850));phone.photo=ImageTk.PhotoImage(image,master=phone)
    canvas.create_image(0,0,image=phone.photo,anchor='nw');phone.update()
    print(int(api().GetAncestor(phone.winfo_id(),2)),flush=True)
    phone.mainloop()


def main():
    out=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'data/screen_follow_verification/20261028_capture_readiness/native'
    out.mkdir(parents=True,exist_ok=True)
    report=dict(source='native Windows local test window displaying provided JJ still; real recognition process; NOT live ToDesk',captures=[])
    before=fingerprints()
    with tempfile.TemporaryDirectory(prefix='xiangqi-native-capture-') as temp, \
            patch.object(App,'_engine_check',lambda self:None),patch.object(App,'_schedule_opponent',lambda *a,**kw:None):
        app=App(preferences_path=Path(temp)/'ui.json',engine_config_path=Path(temp)/'engine.json')
        app.geometry('1080x720+530+10');errors=[]
        app.report_callback_exception=lambda kind,value,tb:errors.append(str(value))
        original=app.official.fen(),app.move_history[:]
        phone=subprocess.Popen([sys.executable,'-X','utf8',str(Path(__file__).resolve()),'--source'],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            hwnd=int(phone.stdout.readline().strip())
            report['source_in_separate_process']=True
            app.open_follow();w=app.follow_setup
            w.geometry('1000x800+350+5');app.update()
            # A real topmost non-tool window must remain a blocker.
            blocker=tk.Toplevel(app);blocker.title('采集测试遮挡窗口');blocker.geometry('180x160+50+100')
            blocker.attributes('-topmost',True);tk.Label(blocker,text='真实遮挡测试').pack();app.update()
            with patch('screen_follow.setup.phone_window_choices',lambda:[(hwnd,'JJ静态图片测试源（非ToDesk）')]):
                w.choose_image();w.capture_button.invoke()
                wait(app,lambda:w.capture_stage=='capture_failed',timeout=4)
                assert '遮挡' in w.progress_var.get(),w.progress_var.get()
                assert w.apply_button.instate(['disabled'])
                report['blocked_status']=w.progress_var.get()
                report['blocker_details']=[e for e in w.capture_diagnostics if e.get('code')=='occluded']
                assert report['blocker_details']
                blocker.destroy();app.update()
                for index in range(5):
                    started=time.monotonic();w.retry_button.invoke()
                    wait(app,lambda:w.draft is not None and w.request is None and w.capture_stage in ('ready','capture_failed','recognition_failed'))
                    assert w.capture_stage=='ready',w.progress_var.get()
                    result=w.last_result
                    assert sum(bool(p) for row in result['board'] for p in row)==19,result
                    assert result['board'][9][7]=='N',result  # known JJ stylized red horse
                    report['captures'].append(dict(index=index+1,seconds=round(time.monotonic()-started,3),
                        recognition_seconds=result['recognition_seconds'],uncertain=len(result['uncertain'])))
                for size in ('960x640','1000x800'):
                    w.geometry(size+'+100+5');app.update()
                    assert w.apply_button.winfo_ismapped()
                    assert w.apply_button.winfo_rooty()-w.winfo_rooty()+w.apply_button.winfo_height()<=w.winfo_height()
                    assert w.retry_button.winfo_rootx()+w.retry_button.winfo_width()<=w.winfo_rootx()+w.winfo_width()
                report['layout_sizes']=['960x640','1000x800']
                # Only for the explicit verification screenshot, after all capture
                # assertions: keep the separate source process from covering it.
                w.attributes('-topmost',True);w.lift();app.update()
                with physical_pixels():
                    x,y,width,height,dpi=geometry(api().GetAncestor(w.winfo_id(),2))
                    report['window_dpi']=dpi
                    report['physical_pixels_per_tk_pixel']=round(width/w.winfo_width(),3)
                    with ImageGrab.grab(bbox=(x,y,x+width,y+height),all_screens=True) as shot:
                        shot.save(out/'capture_recognized.png')
                report['formal_game_unchanged']=original==(app.official.fen(),app.move_history)
                assert report['formal_game_unchanged'] and app.follow_session is None and not errors
                report['diagnostics']=list(w.capture_diagnostics)
        finally:
            app._close();app.engine.close();app.__dict__.clear();gc.collect()
            if phone.poll() is None:phone.kill()
            phone.wait(timeout=4);phone.stdout.close();phone.stderr.close()
    report['callback_errors']=errors
    report['user_configs_unchanged']=fingerprints()==before
    assert report['user_configs_unchanged']
    (out/'native_capture.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('diagnostics','blocker_details')},ensure_ascii=False))


if __name__=='__main__':
    if '--source' in sys.argv:source_window()
    else:main()
