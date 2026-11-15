"""Injected frame source for testing the real worker loop and JSON protocol."""
import sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import ImageDraw
from rules import Position,Move
from verify_follow_tracking import render,CORNERS
import board_recognizer
import screen_follow.worker as worker

class Recognizer:
    def recognize_image(self,image,corners):return dict(board=Position().b,corners=CORNERS,uncertain=[])

class Capture:
    baseline=(592,656,96)
    def __init__(self,*args):self.n=0
    def set_grid(self,corners):pass
    def grab(self):
        self.n+=1;p=Position()
        if '--scene' in sys.argv:
            mode=Path(sys.argv[sys.argv.index('--scene')+1]).read_text(encoding='utf-8')
            if mode=='obscured':
                image=render(p);ImageDraw.Draw(image).rectangle((0,0,300,250),fill='white');return image
            if mode in ('one','two','three'):p.apply(Move.from_ucci('b0c2'))
            if mode in ('two','three'):p.apply(Move.from_ucci('b9c7'))
            if mode=='three':p.apply(Move.from_ucci('h0g2'))
            return render(p)
        if self.n<=4:
            image=render(p);ImageDraw.Draw(image).rectangle((0,0,300,250),fill='white');return image
        p.apply(Move.from_ucci('b0c2'));return render(p)

board_recognizer.BoardRecognizer=Recognizer
worker.WindowCapture=Capture
if __name__=='__main__':worker.main()
