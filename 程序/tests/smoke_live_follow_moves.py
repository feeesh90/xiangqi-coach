"""User-operated real phone game; actual GUI/worker/engine; isolated settings.

Does not select, move, or click anything on the phone. Close this test window
at any point. Results describe observations, not independently labelled truth.
"""
import sys,time,tempfile,json,hashlib,gc,shutil
import queue
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import App
ROOT=Path(__file__).resolve().parents[1]
OUT=Path(sys.argv[sys.argv.index('--out')+1]) if '--out' in sys.argv else ROOT/'data/screen_follow_verification/20261028_tracking_fix'

def fingerprints():
    folder=Path.home()/'.xiangqi-coach'
    return {n:hashlib.sha256((folder/n).read_bytes()).hexdigest() if (folder/n).exists() else None
        for n in ('engine_settings.json','ui_preferences.json','api_settings.json')}

def main():
    OUT.mkdir(parents=True,exist_ok=True);before=fingerprints()
    idle_required=float(sys.argv[sys.argv.index('--idle-seconds')+1]) if '--idle-seconds' in sys.argv else 0
    finish_plies=int(sys.argv[sys.argv.index('--finish-plies')+1]) if '--finish-plies' in sys.argv else 6
    report=dict(source='real ToDesk, user operates phone, actual App and engine; no phone automation',events=[],moves=[],
        idle_required_seconds=idle_required,heartbeat_count=0,tracking_count=0)
    capture=None;worker=None;initial=None;corners=None
    if '--verified-puzzle' in sys.argv or '--verified-board' in sys.argv:
        # This exact board was independently checked on the live phone at step 2.
        # Never use this convenience path for an arbitrary unconfirmed board.
        from screen_follow.capture import WindowCapture,phone_window_choices
        from screen_follow.process import FollowProcess
        from position_import import validate_position
        board=[['']*9 for _ in range(10)]
        for r,c,p in [(0,4,'C'),(1,4,'a'),(2,0,'b'),(2,3,'a'),(2,4,'n'),(2,5,'k'),(2,8,'b'),
            (5,1,'R'),(5,2,'N'),(5,5,'c'),(5,6,'r'),(5,7,'C'),(6,3,'n'),(6,5,'p'),(7,3,'K'),(7,5,'A'),(8,4,'A')]:board[r][c]=p
        if '--verified-board' in sys.argv:
            board=json.loads(Path(sys.argv[sys.argv.index('--verified-board')+1]).read_text(encoding='utf-8'))['board']
        hwnd=next(hwnd for hwnd,title in phone_window_choices() if title.startswith('Xiaomi'))
        capture=WindowCapture(hwnd);frame=capture.grab(initial=True);events=queue.Queue();worker=FollowProcess(events.put)
        try:
            worker.recognize(frame,model='tchess');result=events.get(timeout=15)
            if result['kind']!='recognized' or result['result']['board']!=board:
                raise RuntimeError('Live board differs from independently checked reference: '+str(result))
            corners=result['result']['corners'];initial=validate_position(board,True)
        except Exception:worker.close();raise
        finally:frame.close()
    with tempfile.TemporaryDirectory(prefix='xiangqi-live-tracking-') as temp:
        temp=Path(temp)
        for name in ('engine_settings.json','ui_preferences.json'):
            source=Path.home()/'.xiangqi-coach'/name
            if source.exists():shutil.copyfile(source,temp/name)
        a=App(preferences_path=temp/'ui_preferences.json',engine_config_path=temp/'engine_settings.json')
        a.title('投屏长等待验证 · 请先保持手机不动' if idle_required else '投屏连续验证 · 请在手机走棋')
        a.geometry('1080x720+650+20');a.api.configured=lambda:False
        started=time.monotonic();last=None;ready=False;closing=False
        idle_started=None;idle_snapshot=None;idle_done=not idle_required;last_progress=0;last_beat=None
        original_event=a._follow_event
        def observe_event(event):
            nonlocal last_beat
            s=a.follow_session
            if s and event.get('epoch')==s.epoch:
                if event.get('kind')=='heartbeat':
                    now=time.monotonic();report['heartbeat_count']+=1
                    if last_beat is not None:report['max_heartbeat_gap_seconds']=max(report.get('max_heartbeat_gap_seconds',0),now-last_beat)
                    last_beat=now
                elif event.get('kind')=='tracking':report['tracking_count']+=1
            original_event(event)
        a._follow_event=observe_event
        def finish(reason):
            nonlocal closing
            if closing:return
            closing=True;s=a.follow_session
            report.update(result=reason,seconds=round(time.monotonic()-started,3),user_configs_unchanged=before==fingerprints())
            if s:
                report.update(final_fen=s.position.fen(),phase=s.phase,notice=s.notice,diagnostics=list(s.diagnostics))
            (OUT/'live_continuous.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print('LIVE_FINISH',json.dumps({k:v for k,v in report.items() if k not in ('diagnostics','events')},ensure_ascii=False),flush=True)
            a._close()
        a.protocol('WM_DELETE_WINDOW',lambda:finish('closed_by_user'))
        def watch():
            nonlocal last,ready,idle_started,idle_snapshot,idle_done,last_progress
            if closing:return
            s=a.follow_session
            if s and not ready:
                ready=True;report['initial_fen']=s.position.fen();report['engine']=a.engine.name
                print('LIVE_READY',s.phase,a.engine.name,flush=True)
            if s and s.version!=last:
                last=s.version;report['moves']=a.move_history[:]
                report['events'].append(dict(seconds=round(time.monotonic()-started,3),version=s.version,
                    move=s.last.ucci() if s.last else None,phase=s.phase,fen=s.position.fen()))
                print('LIVE_VERSION',s.version,report['moves'],flush=True)
            if s and s.phase=='paused':
                print('LIVE_PAUSED',s.notice,flush=True);a.after(3000,lambda:finish('paused'));return
            if s and s.requires_resync:
                print('LIVE_REQUIRES_RESYNC',s.notice,flush=True);a.after(3000,lambda:finish('resync_required'));return
            if s and not idle_done:
                if s.version:finish('phone_moved_before_idle_finished');return
                if idle_started is None and not a.busy and (not s.own_turn or s.proposal):
                    idle_started=time.monotonic();idle_snapshot=(s.stamp,a.official.fen(),a.move_history[:],s.proposal,a.task_token)
                    print('IDLE_WAIT_STARTED',idle_required,flush=True)
                if idle_started is not None:
                    elapsed=time.monotonic()-idle_started
                    if idle_snapshot!=(s.stamp,a.official.fen(),a.move_history,s.proposal,a.task_token):
                        finish('confirmed_state_changed_during_idle');return
                    report['idle_elapsed_seconds']=round(elapsed,3)
                    if elapsed>=idle_required:
                        idle_done=True;report['idle_passed']=True
                        print('IDLE_WAIT_PASSED',report['idle_elapsed_seconds'],'PHONE_CAN_MOVE_NOW',flush=True)
                    if idle_done or time.monotonic()-last_progress>=5:
                        last_progress=time.monotonic()
                        (OUT/'live_idle_progress.json').write_text(json.dumps(dict(elapsed_seconds=round(elapsed,2),
                            phase=s.phase,tracking=s.tracking,requires_resync=s.requires_resync,
                            heartbeat_count=report['heartbeat_count'],tracking_count=report['tracking_count'],
                            confirmed_state_unchanged=True,idle_passed=idle_done),ensure_ascii=False,indent=2),encoding='utf-8')
            if s and idle_done and s.version>=finish_plies:
                finish(f'{finish_plies} confirmed half-moves; final phone board still requires visual check');return
            if time.monotonic()-started>1200:finish('timed_out_waiting_for_user');return
            a.after(50,watch)
        def begin():
            if initial is None:a.open_follow();return
            if a.busy or not a.engine.ready:a.after(100,begin);return
            a._follow_begin(initial,True,worker=worker,capture=capture,corners=corners)
        a.after(1000,begin);a.after(1100,watch);a.mainloop()
        a.engine.close();a.__dict__.clear();gc.collect()

if __name__=='__main__':main()
