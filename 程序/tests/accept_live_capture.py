"""Manual five-capture acceptance against a real, user-opened ToDesk window.

No remote clicks, game import or engine search. Use the visible capture buttons;
the observer only records outcomes. Explicit finish saves one GUI screenshot.
"""
import gc
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
from tkinter import ttk
from unittest.mock import patch
from datetime import datetime,timezone,timedelta

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from PIL import ImageGrab
from app import App
from screen_follow.capture import api,geometry,physical_pixels


def fingerprints():
    folder=Path.home()/'.xiangqi-coach'
    return {name:hashlib.sha256((folder/name).read_bytes()).hexdigest() if (folder/name).exists() else None
        for name in ('engine_settings.json','ui_preferences.json','api_settings.json')}


def main():
    stamp=datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d_%H%M%S')
    out=ROOT/'data/screen_follow_verification/20261028_capture_readiness'/('live_'+stamp)
    out.mkdir(parents=True,exist_ok=True)
    report=dict(source='real user-opened ToDesk window; actual capture buttons and actual local recognition',
        started_at=datetime.now(timezone.utc).isoformat(),attempts=[],callback_errors=[])
    before=fingerprints();pending=None;seen=0;finished=False
    with tempfile.TemporaryDirectory(prefix='xiangqi-live-accept-') as temp, \
            patch.object(App,'_engine_check',lambda self:None), \
            patch.object(App,'_schedule_opponent',lambda *a,**kw:None):
        app=App(preferences_path=Path(temp)/'ui.json',engine_config_path=Path(temp)/'engine.json')
        app.title('真实投屏验收 · 临时配置 · 不会落子');app.geometry('1080x720+500+10')
        app.api.configured=lambda:False
        app.engine_badge.configure(text='采集验收 · 引擎未启动')
        initial=app.official.fen(),app.move_history[:]
        app.report_callback_exception=lambda kind,value,tb:report['callback_errors'].append(str(value))

        def save():
            report['formal_game_unchanged']=initial==(app.official.fen(),app.move_history)
            report['user_configs_unchanged']=before==fingerprints()
            (out/'live_capture.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')

        def finish():
            nonlocal finished
            if finished:return
            finished=True
            window=app.follow_setup
            if window and window.alive:
                report['diagnostics']=list(window.capture_diagnostics)
                app.update_idletasks()
                with physical_pixels():
                    x,y,width,height,dpi=geometry(api().GetAncestor(window.winfo_id(),2))
                    report['window_dpi']=dpi
                    with ImageGrab.grab(bbox=(x,y,x+width,y+height),all_screens=True) as shot:
                        shot.save(out/'live_capture.png')
            save()
            print('LIVE_ACCEPTANCE_FINISHED '+str(out),flush=True)
            app._close()

        def open_setup():
            app.open_follow();window=app.follow_setup
            window.title('真实 ToDesk 采集验收 · 仅识别，不开始跟随')
            window.geometry('1000x800+430+0')
            ttk.Button(window.retry_button.master,text='保存结果并结束验收',command=lambda:app.after(200,finish)).pack(side='left',padx=6)

        def watch():
            nonlocal pending,seen
            if finished:return
            window=app.follow_setup
            if window and window.alive:
                events=list(window.capture_diagnostics)
                for event in events[seen:]:
                    if event['stage']=='preparing':
                        pending=dict(index=len(report['attempts'])+1,operation=event['operation'],
                            started=time.monotonic(),window=window.selected_hwnd)
                    elif pending and event['stage'] in ('ready','capture_failed','recognition_failed'):
                        item={k:v for k,v in pending.items() if k!='started'}
                        item.update(outcome=event['stage'],seconds=round(time.monotonic()-pending['started'],3),
                            status=window.status_var.get())
                        if event['stage']=='ready':
                            result=window.last_result
                            item.update(board=result['board'],uncertain=result['uncertain'],
                                pieces=sum(bool(p) for row in result['board'] for p in row),
                                recognition_seconds=result.get('recognition_seconds'),model=window.model_var.get())
                        report['attempts'].append(item);pending=None;save()
                        print(json.dumps(item,ensure_ascii=False),flush=True)
                seen=len(events)
            app.after(50,watch)

        app.after(100,open_setup);app.after(200,watch);app.after(360000,finish)
        print('LIVE_ACCEPTANCE_OUTPUT '+str(out),flush=True)
        try:app.mainloop()
        finally:
            save();app.engine.close();app.__dict__.clear();gc.collect()


if __name__=='__main__':main()
