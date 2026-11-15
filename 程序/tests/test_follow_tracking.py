"""Tracking grace, visual evidence and persistent UI diagnostics."""
import json,subprocess,queue,threading,time,io,base64
from pathlib import Path
from types import SimpleNamespace
import pytest
from rules import Position,Move
from test_board_orientation import gui
from test_screen_follow import prepare,event


@pytest.mark.parametrize('case',['transient','selection','safety','two_frames','verifier','photo_sequence'])
def test_visual_resilience(case):
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([str(root/'.recognition-venv/Scripts/python.exe'),'-X','utf8',
        str(root/'tests/verify_follow_resilience.py'),case],cwd=root,capture_output=True,timeout=40)
    assert result.returncode==0,result.stderr.decode('utf-8')
    assert case in json.loads(result.stdout)


def test_checking_is_not_pause_and_cannot_mutate_confirmed_game(gui,monkeypatch):
    a=gui;s=prepare(a,monkeypatch);a.follow_process=SimpleNamespace(send=lambda *args,**kw:None,close=lambda:None)
    s.offer(Move.from_ucci('b0c2'),s.stamp);before=(a.official.fen(),a.move_history[:],s.proposal)
    a._follow_event(dict(kind='tracking',state='checking',epoch=s.epoch,version=s.version,candidate_count=0))
    assert s.phase=='following' and '正在核对手机画面' in a.sim_label.cget('text')
    assert before==(a.official.fen(),a.move_history,s.proposal)
    a._follow_event(dict(kind='tracking',state='unchanged',epoch=s.epoch,version=s.version))
    assert '正在核对' not in a.sim_label.cget('text')
    event(a,'h0g2');assert a.move_history==['h0g2'] and s.last_observed_own


def test_pause_reason_survives_generic_status_and_diagnostics_are_bounded(gui,monkeypatch):
    a=gui;s=prepare(a,monkeypatch);before=a.official.fen()
    for _ in range(220):a._follow_event(dict(kind='tracking',state='checking',epoch=s.epoch,version=0,candidate_count=0))
    assert len(s.diagnostics)==200 and all('image' not in d for d in s.diagnostics)
    reason='稳定画面无法唯一确认一步走法'
    a._follow_event(dict(kind='error',reason=reason,epoch=s.epoch,version=0))
    a.status.configure(text='分析完成');a._follow_refresh()
    assert s.phase=='paused' and reason in a.sim_label.cget('text')
    assert a.official.fen()==before and not a.move_history
    old_count=len(s.diagnostics)
    a._follow_event(dict(kind='move',move='b0c2',epoch=s.epoch,version=0))
    assert not a.move_history and len(s.diagnostics)==old_count


def test_real_worker_keeps_capturing_checks_then_move_and_acknowledgement():
    from smoke_screen_follow import render
    root=Path(__file__).resolve().parents[1]
    p=subprocess.Popen([str(root/'.recognition-venv/Scripts/python.exe'),'-X','utf8',
        str(root/'tests/worker_follow_fixture.py')],cwd=root,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    events=queue.Queue()
    def read():
        for line in p.stdout:events.put(json.loads(line))
    thread=threading.Thread(target=read,daemon=True);thread.start()
    def send(op,**data):p.stdin.write((json.dumps(dict(op=op,**data))+'\n').encode());p.stdin.flush()
    def until(predicate,timeout=8):
        deadline=time.monotonic()+timeout;seen=[]
        while time.monotonic()<deadline:
            e=events.get(timeout=max(.01,deadline-time.monotonic()));seen.append(e)
            assert e['kind'] not in ('uncertain','error'),e
            if predicate(e):return seen
        raise AssertionError(seen)
    try:
        data=io.BytesIO();render(Position()).save(data,format='PNG')
        send('recognize',id='initial',image=base64.b64encode(data.getvalue()).decode(),model='compatible')
        until(lambda e:e['kind']=='recognized')
        send('start',epoch='fixture',version=0,hwnd=1,roi=[0,0,592,656],geometry=[592,656,96],
            fen=Position().fen(),corners=[[40,40],[552,40],[552,616],[40,616]],red_bottom=True)
        seen=until(lambda e:e['kind']=='move')
        assert any(e['kind']=='tracking' and e['state']=='checking' for e in seen)
        assert seen[-1]['move']=='b0c2' and seen[-1]['version']==0
        send('accept',epoch='fixture',version=1,move='b0c2')
        seen=until(lambda e:e['kind']=='heartbeat' and e['version']==1)
        assert not any(e['kind']=='move' for e in seen)
        send('pause');send('stop');p.wait(timeout=4)
        assert p.returncode==0,p.stderr.read().decode('utf-8')
    finally:
        if p.poll() is None:p.kill();p.wait(timeout=4)
        thread.join(timeout=2)
        for stream in (p.stdin,p.stdout,p.stderr):stream.close()
