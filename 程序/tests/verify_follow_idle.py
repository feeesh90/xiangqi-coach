"""Virtual-time image tests for indefinite waiting and reliable gap evidence."""
import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from PIL import ImageDraw
from rules import Position,Move
from screen_follow.tracker import MoveTracker
from verify_follow_tracking import render,CORNERS
from verify_follow_resilience import observe_move


def idle():
    cases=[]
    for view in (True,False):
        for red in (True,False):
            p=Position();p.red=red;image=render(p,view);t=MoveTracker(p,image,CORNERS,view)
            before=(t.reference.copy(),t.reference_grid.copy(),t.reference_image.copy())
            for now in [0,.12]+list(range(30,601,30)):
                result=t.observe(image,now);assert result['kind']=='unchanged',result
                assert not t.requires_resync and t.position.fen()==p.fen() and t.pending is None
            assert all(np.array_equal(a,b) for a,b in zip(before,(t.reference,t.reference_grid,t.reference_image)))
            move=Move.from_ucci('b0c2' if red else 'b9c7');p.apply(move)
            observe_move(t,render(p,view),move,600.2)
            assert t.position.fen()==p.fen()
            cases.append(dict(red_bottom=view,red_to_move=red,virtual_idle_seconds=600,next_move=move.ucci()))
    return cases


def obscured():
    for recovery in ('same','move'):
        p=Position();image=render(p);t=MoveTracker(p,image,CORNERS,True);reference=t.reference.copy()
        overlay=image.copy();ImageDraw.Draw(overlay).rectangle((0,0,300,250),fill='white')
        for now in (0,.12,2,6.2,10,60,600):
            result=t.observe(overlay,now)
            assert result['kind']==('checking' if now<6 else 'waiting_clear'),result
            assert not t.requires_resync and t.pending is None and np.array_equal(reference,t.reference)
        if recovery=='same':assert t.observe(image,600.2)['kind']=='unchanged'
        else:
            m=Move.from_ucci('b0c2');p.apply(m);observe_move(t,render(p),m,600.2)
        assert t.position.fen()==p.fen()
    return dict(recoveries=['same','move'],virtual_obscured_seconds=600)


def gap():
    p=Position();t=MoveTracker(p,render(p),CORNERS,True);before=t.position.fen()
    p.apply(Move.from_ucci('b0c2'));p.apply(Move.from_ucci('b9c7'))
    assert t.observe(render(p),0)['kind']=='checking'
    assert t.observe(render(p),.12)['kind']=='resync_required'
    single=Position();single.apply(Move.from_ucci('b0c2'))
    for image,now in ((render(Position()),9),(render(single),10),(render(single),600)):
        assert t.observe(image,now)['kind']=='resync_required'
        assert t.position.fen()==before and t.pending is None
    try:t.accept(Move.from_ucci('b0c2'))
    except ValueError:pass
    else:raise AssertionError('Confirmed gap must block acceptance')
    fresh=MoveTracker(single,render(single),CORNERS,True)
    m=Move.from_ucci('b9c7');single.apply(m);observe_move(fresh,render(single),m,601)
    assert fresh.position.fen()==single.fen()
    return dict(latched=True,late_single_rejected=True,new_session_can_resume=True)


def ambiguous():
    p=Position();image=render(p);t=MoveTracker(p,image,CORNERS,True)
    unknown=image.copy();draw=ImageDraw.Draw(unknown)
    for r,c in ((4,2),(4,4),(4,6)):
        x,y=40+c*64,40+r*64;draw.ellipse((x-25,y-25,x+25,y+25),fill=(190,100,200),outline='black',width=2)
        draw.text((x,y),'?',anchor='mm',fill='white')
    for now in (0,.12,2,7,20):
        result=t.observe(unknown,now)
        assert result['kind'] not in ('move','resync_required','uncertain'),result
    assert t.observe(image,21)['kind']=='unchanged'
    # A flashy label that a classifier calls 'king' is still not a plausible
    # board. High classification confidence must not manufacture a history gap.
    t=MoveTracker(p,image,CORNERS,True,piece_verifier=lambda *args:('K',.999))
    for now in (0,.12,2,7,20):
        result=t.observe(unknown,now)
        assert result['kind'] not in ('move','resync_required','uncertain'),result
    assert t.observe(image,21)['kind']=='unchanged'
    return dict(uncertain_objects_do_not_prove_history_gap=True)


CASES=dict(idle=idle,obscured=obscured,gap=gap,ambiguous=ambiguous)
if __name__=='__main__':print(json.dumps({k:CASES[k]() for k in sys.argv[1:] or CASES},ensure_ascii=False))
