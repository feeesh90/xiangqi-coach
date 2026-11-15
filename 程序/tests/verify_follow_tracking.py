"""Synthetic image sequence, not a real JJ or ToDesk recording."""
import sys,json,random,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image,ImageDraw,ImageFont
from rules import Position,legal_moves
from screen_follow.tracker import MoveTracker

font=ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf",28)
CORNERS=[[40,40],[552,40],[552,616],[40,616]]
def render(position,red_bottom=True,highlight=None):
    image=Image.new("RGB",(592,656),(190,180,150));d=ImageDraw.Draw(image)
    for r in range(10):d.line((40,40+r*64,552,40+r*64),fill=(95,85,65),width=1)
    for c in range(9):d.line((40+c*64,40,40+c*64,616),fill=(95,85,65),width=1)
    for r,row in enumerate(position.b):
        for c,p in enumerate(row):
            if not p:continue
            vr,vc=(r,c) if red_bottom else (9-r,8-c)
            x,y=40+vc*64,40+vr*64
            d.ellipse((x-25,y-25,x+25,y+25),fill=(245,220,170),outline=(100,60,30),width=2)
            d.text((x,y),p.upper(),font=font,anchor="mm",fill=(185,20,20) if p.isupper() else (15,15,15))
            if highlight==(r,c):d.ellipse((x-29,y-29,x+29,y+29),outline="white",width=3)
    return image

def main():
    report={"source":"synthetic: programmatically rendered pieces, not JJ footage","cases":[]}
    for red_bottom in (True,False):
        pos=Position();tracker=MoveTracker(pos,render(pos,red_bottom),CORNERS,red_bottom)
        rng=random.Random(147);moves=[];times=[];now=0
        for ply in range(60):
            possible=[m for m in legal_moves(pos) if pos.b[m.tr][m.tc].upper()!="K"]
            rng.shuffle(possible)
            move=next((m for m in possible if (lambda p:bool(legal_moves(p)))(applied(pos,m))),None)
            assert move is not None
            pos.apply_unchecked(move);frame=render(pos,red_bottom,(move.tr,move.tc))
            started=time.perf_counter()
            results=[]
            for dt in (.0,.105,.21):results.append(tracker.observe(frame,now+dt))
            result=results[-1];times.append(time.perf_counter()-started)
            assert result["kind"]=="move",(ply,move,result)
            assert result["move"]==move.ucci()
            tracker.accept(move);moves.append(move.ucci());now+=.4
            assert tracker.position.fen()==pos.fen()
            assert tracker.observe(frame,now)["kind"]=="unchanged"
        # Opaque popup cannot silently become a legal move.
        popup=Image.new("RGB",(592,656),"white")
        tracker.observe(popup,now+1)
        assert tracker.observe(popup,now+1.2)["kind"]=="checking"
        assert tracker.observe(popup,now+2.8)["kind"]=="checking"
        assert tracker.observe(popup,now+8)["kind"]=="waiting_clear"
        assert not tracker.requires_resync and tracker.position.fen()==pos.fen()
        report["cases"].append(dict(red_bottom=red_bottom,plies=60,moves=moves,final_fen=pos.fen(),false_commits=0,pauses=0,median_tracking_ms=round(sorted(times)[30]*1000,2),p95_tracking_ms=round(sorted(times)[56]*1000,2)))
    print(json.dumps(report,ensure_ascii=False))

def applied(p,m):
    p=p.copy();p.apply_unchecked(m);return p

if __name__=="__main__":main()
