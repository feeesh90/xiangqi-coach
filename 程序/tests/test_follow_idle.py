"""Indefinite wait, automatic visual recovery and session-scoped health."""
import base64,io,json,queue,subprocess,threading,time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import pytest
from rules import Position,Move
from test_board_orientation import gui
from test_screen_follow import event,prepare

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('case',['idle','obscured','gap','ambiguous'])
def test_image_idle_and_recovery(case):
    result=subprocess.run([str(ROOT/'.recognition-venv/Scripts/python.exe'),'-X','utf8',
        str(ROOT/'tests/verify_follow_idle.py'),case],cwd=ROOT,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr.decode('utf-8')
    assert case in json.loads(result.stdout)


@pytest.mark.parametrize('own',[True,False])
@pytest.mark.parametrize('red_to_move',[True,False])
def test_gui_ten_minutes_wait_does_not_repeat_search_or_change_game(gui,monkeypatch,own,red_to_move):
    import screen_follow.ui as ui
    a=gui;clock=[0.0];monkeypatch.setattr(ui,'monotonic',lambda:clock[0]);calls=[]
    monkeypatch.setattr(a,'_follow_request',lambda:calls.append('search'))
    p=Position();p.red=red_to_move;a._follow_begin(p,own);s=a.follow_session
    a.after_cancel(a.follow_watch_after) if a.__dict__.get('follow_watch_after') else None
    commands=[];a.follow_process=SimpleNamespace(send=lambda op,**kw:commands.append(op),close=lambda:commands.append('close'))
    if s.own_turn:s.offer(Move.from_ucci('b0c2' if red_to_move else 'b9c7'),s.stamp)
    before=(a.official.fen(),a.move_history[:],s.proposal,a.task_token)
    for seconds in range(0,601,30):
        clock[0]=float(seconds)
        # Version mismatch across an acknowledgement cannot invalidate health.
        a._follow_event(dict(kind='heartbeat',epoch=s.epoch,version=99))
        a._follow_event(dict(kind='tracking',state='unchanged',epoch=s.epoch,version=0))
        a._follow_watch(s,a.follow_process)
        if a.follow_watch_after:a.after_cancel(a.follow_watch_after);a.follow_watch_after=None
        assert s.phase=='following' and not s.requires_resync
        assert before==(a.official.fen(),a.move_history,s.proposal,a.task_token)
    assert calls==['search'] and not commands
    assert ('等待手机落子' if s.own_turn else '等待对手落子') in a.sim_label.cget('text')
    m='b0c2' if red_to_move else 'b9c7';event(a,m)
    assert a.move_history==[m] and calls==['search','search']


def test_soft_visual_uncertainty_recovers_automatically(gui,monkeypatch):
    a=gui;s=prepare(a,monkeypatch);commands=[]
    a.follow_process=SimpleNamespace(send=lambda op,**kw:commands.append(op),close=lambda:None)
    s.offer(Move.from_ucci('b0c2'),s.stamp);proposal=s.proposal;before=a.official.fen()
    for state in ('checking','waiting_clear'):
        a._follow_event(dict(kind='tracking',state=state,epoch=s.epoch,version=0))
        assert s.phase=='following' and not s.requires_resync and s.proposal is proposal
    assert '仍在监听' in a.sim_label.cget('text')
    # Legacy visual uncertainty is also not a capture error.
    a._follow_event(dict(kind='uncertain',epoch=s.epoch,version=0))
    assert a.official.fen()==before and not commands
    a._follow_event(dict(kind='tracking',state='unchanged',epoch=s.epoch,version=0))
    assert '仍在监听' not in a.sim_label.cget('text') and s.phase=='following'
    event(a,'h0g2');assert a.move_history==['h0g2']


def test_confirmed_gap_freezes_only_sync_and_never_clears_itself(gui,monkeypatch):
    a=gui;s=prepare(a,monkeypatch);commands=[]
    a.follow_process=SimpleNamespace(send=lambda op,**kw:commands.append(op),close=lambda:None)
    s.offer(Move.from_ucci('b0c2'),s.stamp);before=(a.official.fen(),s.proposal)
    a._follow_event(dict(kind='resync_required',reason='已确认漏步',epoch=s.epoch,version=0))
    assert s.phase=='following' and s.requires_resync and commands==['freeze_sync']
    assert '仍在采集' in a.sim_label.cget('text')
    a._follow_event(dict(kind='tracking',state='unchanged',epoch=s.epoch,version=0));event(a,'h0g2')
    assert (a.official.fen(),s.proposal)==before and not a.move_history and s.requires_resync
    # Even a matching board needs explicit acknowledgement of lost history.
    prompts=[]
    monkeypatch.setattr('screen_follow.ui.messagebox.askyesno',lambda *args,**kw:prompts.append(args) or False)
    assert not a._follow_begin(a.official.copy(),True) and s.requires_resync
    monkeypatch.setattr('screen_follow.ui.messagebox.askyesno',lambda *args,**kw:True)
    assert a._follow_begin(a.official.copy(),True)
    assert prompts and a.follow_session is not s and not a.follow_session.requires_resync


def test_watchdog_uses_current_reader_health_not_board_activity(gui,monkeypatch):
    import screen_follow.ui as ui
    a=gui;s=prepare(a,monkeypatch);clock=[20.0];monkeypatch.setattr(ui,'monotonic',lambda:clock[0]);closed=[]
    if a.__dict__.get('follow_watch_after'):a.after_cancel(a.follow_watch_after);a.follow_watch_after=None
    a.follow_last_heartbeat=1
    worker=a.follow_process=SimpleNamespace(send=lambda *args,**kw:None,close=lambda:closed.append(True),
        last_heartbeat_at=lambda epoch:19 if epoch==s.epoch else None)
    a._follow_watch(s,worker)
    assert not closed and s.phase=='following'
    if a.follow_watch_after:a.after_cancel(a.follow_watch_after);a.follow_watch_after=None
    a._follow_event(dict(kind='heartbeat',epoch='old session',version=0))
    assert a.follow_last_heartbeat==1
    clock[0]=25.1;a._follow_watch(s,worker)
    assert closed and s.phase=='paused' and '超时' in s.notice


def test_reader_records_epoch_health_before_gui_dispatch(monkeypatch):
    import screen_follow.process as module
    worker=module.FollowProcess.__new__(module.FollowProcess)
    worker.context=dict(epoch='new',version=3);worker.heartbeat=None;worker.closed=False;received=[]
    worker.callback=received.append
    records=[dict(kind='heartbeat',epoch='old',version=0),dict(kind='heartbeat',epoch='new',version=2),
        dict(kind='heartbeat',epoch='old',version=100)]
    worker.p=SimpleNamespace(stdout=io.BytesIO(b''.join((json.dumps(e)+'\n').encode() for e in records)))
    monkeypatch.setattr(module,'monotonic',lambda:50.0);worker._read()
    assert worker.last_heartbeat_at('new')==50 and worker.last_heartbeat_at('old') is None
    assert any(e['kind']=='heartbeat' for e in received)


@contextmanager
def worker_scene(tmp_path):
    from smoke_screen_follow import render
    path=tmp_path/'scene.txt';path.write_text('initial',encoding='utf-8')
    p=subprocess.Popen([str(ROOT/'.recognition-venv/Scripts/python.exe'),'-X','utf8',
        str(ROOT/'tests/worker_follow_fixture.py'),'--scene',str(path)],cwd=ROOT,
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    events=queue.Queue();seen=[]
    def reader():
        for line in p.stdout:events.put(json.loads(line))
    thread=threading.Thread(target=reader,daemon=True);thread.start()
    def send(op,**data):p.stdin.write((json.dumps(dict(op=op,**data))+'\n').encode());p.stdin.flush()
    def get(timeout=5):
        try:e=events.get(timeout=timeout)
        except queue.Empty:raise AssertionError(('No worker response',p.poll(),seen[-5:]))
        seen.append(e);assert e['kind']!='error',e;return e
    def until(predicate,timeout=8):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            e=get(max(.01,end-time.monotonic()))
            if predicate(e):return e
        raise AssertionError(seen[-5:])
    def scene(value):
        replacement=path.with_suffix('.next');replacement.write_text(value,encoding='utf-8');replacement.replace(path)
    try:
        data=io.BytesIO();render(Position()).save(data,format='PNG')
        send('recognize',id='initial',model='compatible',image=base64.b64encode(data.getvalue()).decode())
        until(lambda e:e['kind']=='recognized')
        send('start',epoch='idle-test',version=0,hwnd=1,roi=[0,0,592,656],geometry=[592,656,96],
            fen=Position().fen(),corners=[[40,40],[552,40],[552,616],[40,616]],red_bottom=True)
        until(lambda e:e['kind']=='started')
        yield scene,send,get,until,seen
        send('stop');p.wait(timeout=4);assert p.returncode==0,p.stderr.read().decode('utf-8')
    finally:
        if p.poll() is None:p.kill();p.wait(timeout=4)
        thread.join(timeout=2)
        for stream in (p.stdin,p.stdout,p.stderr):stream.close()


def test_real_worker_twenty_seconds_idle_and_long_obscuration(tmp_path):
    with worker_scene(tmp_path) as (scene,send,get,until,seen):
        started=time.monotonic();beats=[]
        while time.monotonic()-started<20.2:
            e=get();assert e['kind'] not in ('move','resync_required')
            if e['kind']=='heartbeat':beats.append(time.monotonic())
        idle_seconds=time.monotonic()-started
        assert idle_seconds>=20 and len(beats)>=15
        assert all(b-a<5 for a,b in zip(beats,beats[1:]))
        scene('obscured');until(lambda e:e['kind']=='tracking' and e['state']=='checking')
        end=time.monotonic()+7.2
        while time.monotonic()<end:
            e=get();assert e['kind'] not in ('move','resync_required')
        assert any(e.get('state')=='waiting_clear' for e in seen)
        scene('initial');until(lambda e:e.get('state')=='unchanged')
        scene('one');e=until(lambda e:e['kind']=='move');assert e['move']=='b0c2'
        send('accept',epoch='idle-test',version=1,move=e['move'])
        until(lambda e:e['kind']=='heartbeat' and e['version']==1)
        scene('three');until(lambda e:e['kind']=='resync_required')
        scene('two');end=time.monotonic()+2.2
        while time.monotonic()<end:
            e=get();assert e['kind']!='move'
        assert any(e.get('state')=='resync_required' for e in seen)
        assert seen[-1]['version']==1
        (tmp_path/'worker_idle_result.json').write_text(json.dumps(dict(real_idle_seconds=idle_seconds,
            heartbeats=len(beats),soft_recovery=True,gap_remained_latched=True)),encoding='utf-8')
