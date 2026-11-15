"""Vision regressions: synthetic scenes, including sprites cropped from one JJ image.

These are not a real JJ/ToDesk continuous game recording.
"""
import json,sys,time,random
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from PIL import Image,ImageDraw,ImageEnhance
from rules import Position,Move,legal_moves
from screen_follow.tracker import MoveTracker,warp
from verify_follow_tracking import render,CORNERS,applied


def observe_move(t,image,move,now):
    results=[t.observe(image,now+dt) for dt in (0,.11,.23)]
    assert results[-1]['kind']=='move',(move,results)
    assert results[-1]['move']==move.ucci(),results
    t.accept(move)


def lights(image):
    return ImageEnhance.Brightness(image).enhance(.82)


def transient():
    p=Position();t=MoveTracker(p,render(p),CORNERS,True);before=t.reference.copy()
    # Unknown popup fragment, then correct animation endpoint, within grace.
    bad=render(p);ImageDraw.Draw(bad).rectangle((0,0,300,250),fill='white')
    assert t.observe(bad,0)['kind']=='checking'
    assert t.observe(bad,.15)['kind']=='checking'
    m=Move.from_ucci('b0c2');p.apply(m)
    image=lights(render(p));observe_move(t,image,m,.3)
    assert t.position.fen()==p.fen()
    assert not np.array_equal(t.reference,before)
    return dict(moves=1,false_commits=0)


def selection():
    for view in (True,False):
        p=Position();t=MoveTracker(p,render(p,view),CORNERS,view);before=t.reference.copy()
        image=lights(render(p,view,(9,1)));d=ImageDraw.Draw(image)
        for r,c in ((6,1),(5,2),(4,4)):
            vr,vc=(r,c) if view else (9-r,8-c);x,y=40+vc*64,40+vr*64
            d.ellipse((x-2,y-2,x+2,y+2),fill=(30,235,60))
        # Selection may be held indefinitely; it is not a new position.
        for now in (0,.12,.3,1,2,5):
            result=t.observe(image,now)
            assert result['kind']=='unchanged',result
        assert np.array_equal(t.reference,before) and t.position.fen()==p.fen()
        m=Move.from_ucci('b0c2');p.apply(m)
        observe_move(t,lights(render(p,view)),m,5.2)
    return dict(views=2,false_commits=0,selection_seconds=5)


def safety():
    for mode in ('multi','popup','wrong_colour'):
        p=Position();t=MoveTracker(p,render(p),CORNERS,True);before=t.position.fen()
        if mode=='multi':
            p.apply(Move.from_ucci('b0c2'));p.apply(Move.from_ucci('b9c7'));bad=render(p)
        elif mode=='popup':bad=Image.new('RGB',(592,656),'white')
        else:
            p.apply(Move.from_ucci('b0c2'));p.b[7][2]='n';bad=render(p)
        results=[t.observe(bad,now) for now in (0,.12,.3,1,1.7,7)]
        assert all(r['kind']!='move' for r in results),(mode,results)
        assert results[-1]['kind']==('resync_required' if mode=='multi' else 'waiting_clear'),(mode,results)
        assert t.position.fen()==before and t.pending is None
    # Genuine changing animation should not consume the stable-image timeout.
    p=Position();t=MoveTracker(p,render(p),CORNERS,True)
    for i in range(20):
        bad=render(p);ImageDraw.Draw(bad).rectangle((0,0,150+(i%2)*150,250),fill='white')
        assert t.observe(bad,i*.2)['kind']!='uncertain'
    assert t.observe(render(p),4.1)['kind']=='unchanged'
    return dict(rejected_cases=3,false_commits=0)


def two_frames():
    p=Position();t=MoveTracker(p,render(p),CORNERS,True)
    untouched=t.reference[0].copy();untouched_grid=t.reference_grid[0].copy()
    m=Move.from_ucci('b0c2');p.apply(m);image=render(p)
    assert t.observe(image,0)['kind']=='checking'
    assert t.observe(image,.05)['kind']=='checking'
    assert t.position.fen()==Position().fen()
    assert t.observe(image,.11)['kind']=='move'
    # Before acknowledgement, observing never mutates confirmed truth.
    assert t.position.fen()==Position().fen()
    t.accept(m);assert t.observe(image,.23)['kind']=='unchanged'
    assert np.array_equal(t.reference[0],untouched) and np.array_equal(t.reference_grid[0],untouched_grid)
    t=MoveTracker(Position(),render(Position()),CORNERS,True)
    assert t.observe(image,1)['kind']=='checking'
    obstructed=image.copy();ImageDraw.Draw(obstructed).rectangle((0,0,250,200),fill='white')
    assert t.observe(obstructed,1.06)['kind']=='checking'
    # Two matching observations can bracket a transient effect; the effect
    # cannot itself serve as either confirmation or a new reference.
    assert t.observe(image,1.23)['kind']=='move'
    return dict(confirmations=1,false_commits=0)


def verifier():
    import screen_follow.tracker as module
    original=module._body_error
    module._body_error=lambda *args:1.0  # Force the independent verification path.
    try:
        p=Position();m=Move.from_ucci('b0c2');p.apply(m)
        for verdict,accepted in ((('N',.999),True),(('n',.999),False),(('N',.94),False),(None,False)):
            calls=[]
            def classify(image,x,y):calls.append((x,y));return verdict
            t=MoveTracker(Position(),render(Position()),CORNERS,True,piece_verifier=classify)
            assert t.round_pieces
            image=render(p);results=[t.observe(image,now) for now in (0,.11,.23)]
            assert (results[-1]['kind']=='move')==accepted,(verdict,results)
            assert len(calls)>=2 and t.position.fen()==Position().fen()
    finally:module._body_error=original
    p=Position();t=MoveTracker(p,render(p),CORNERS,True)
    for i in range(40):
        bad=render(p);ImageDraw.Draw(bad).rectangle((0,0,150+(i%2)*150,250),fill='white')
        result=t.observe(bad,i*.2)
        assert result['kind'] not in ('uncertain','resync_required','move'),result
    assert result['kind']=='waiting_clear' and not t.requires_resync,result
    return dict(verifier_cases=4,continued_animation_seconds=i*.2)


def photo_sequence():
    root=Path(__file__).resolve().parents[1]
    source=root/'data/screen_follow_verification/20261028'
    image=Image.open(source/'jj_original.jpg')
    from board_recognizer import find_grid
    aligned=Image.fromarray(warp(image,find_grid(np.asarray(image))))
    board=json.loads((source/'jj_manual_label.json').read_text(encoding='utf-8'))['board']
    sprites={}
    for r,row in enumerate(board):
        for c,p in enumerate(row):
            if p and p not in sprites:
                x,y=40+c*64,40+r*64;sprites[p]=aligned.crop((x-27,y-27,x+28,y+28))
    mask=Image.new('L',(55,55));ImageDraw.Draw(mask).ellipse((0,0,54,54),fill=255)
    def scene(p,view,ply):
        im=Image.new('RGB',(592,656),(120,142,118));d=ImageDraw.Draw(im)
        for r in range(10):d.line((40,40+r*64,552,40+r*64),fill=(75,85,70))
        for c in range(9):d.line((40+c*64,40,40+c*64,616),fill=(75,85,70))
        for r,row in enumerate(p.b):
            for c,piece in enumerate(row):
                if piece:
                    vr,vc=(r,c) if view else (9-r,8-c)
                    im.paste(sprites[piece],(40+vc*64-27,40+vr*64-27),mask)
        return ImageEnhance.Brightness(im).enhance(.90+(ply%5)*.025)
    cases=[]
    for view in (True,False):
        p=Position();t=MoveTracker(p,scene(p,view,0),CORNERS,view);rng=random.Random(147);moves=[];costs=[]
        assert t.round_pieces
        decorated=scene(p,view,0);draw=ImageDraw.Draw(decorated)
        # Empty intersections can contain board labels, hints and pointer
        # effects. They must not be treated as newly appeared pieces.
        for r,c in ((4,2),(4,3)):
            vr,vc=(r,c) if view else (9-r,8-c)
            draw.text((40+vc*64,40+vr*64),'3',anchor='mm',fill=(240,230,200))
        for now in (-3,-2.8,-1):assert t.observe(decorated,now)['kind']=='unchanged'
        for ply in range(60):
            choices=[m for m in legal_moves(p) if p.b[m.tr][m.tc].upper()!='K'];rng.shuffle(choices)
            m=next(m for m in choices if legal_moves(applied(p,m)))
            p.apply(m);image=scene(p,view,ply+1);started=time.perf_counter()
            observe_move(t,image,m,ply*.4);costs.append((time.perf_counter()-started)*1000);moves.append(m.ucci())
            assert t.position.fen()==p.fen()
        cases.append(dict(red_bottom=view,plies=60,moves=moves,final_fen=p.fen(),false_commits=0,
            median_three_observations_ms=round(float(np.median(costs)),2),p95_three_observations_ms=round(float(np.percentile(costs,95)),2)))
    return dict(source='synthetic replay using crops from one existing JJ photo; not live ToDesk',cases=cases)


CASES=dict(transient=transient,selection=selection,safety=safety,two_frames=two_frames,verifier=verifier,photo_sequence=photo_sequence)
if __name__=='__main__':
    selected=sys.argv[1:] or list(CASES)
    print(json.dumps({name:CASES[name]() for name in selected},ensure_ascii=False))
