"""Opt-in capture of THIS application, with independently declared FEN labels.

Alternate font samples are controlled GUI variations, not third-party screenshots.
Normal image import never invokes this script or writes these screenshots.
"""
import tempfile
from pathlib import Path
import sys
import json
import time
import ctypes

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
if sys.platform=="win32":ctypes.windll.shcore.SetProcessDpiAwareness(2)
from app import App
from rules import Position, START_FEN, legal_moves
from PIL import ImageGrab

out=Path(sys.argv[1]) if len(sys.argv)>1 else Path("data/endgame_verification/samples")
out.mkdir(parents=True,exist_ok=True)
App._engine_check=lambda self:None
App._schedule_opponent=lambda self:None
ui_preferences_directory = tempfile.TemporaryDirectory(prefix="xiangqi-ui-")
app=App(preferences_path=Path(ui_preferences_directory.name)/"ui_preferences.json",engine_config_path=(Path(ui_preferences_directory.name)/"ui_preferences.json").with_suffix(".engine.json"))
app.attributes("-topmost",True)
app.geometry("1500x920+30+30")
app.mode="pvp"
base="1r2k4/9/9/9/4p4/9/2N1P4/9/4C4/4K1R2 w - - 0 1"
labels=[]
try:
    for i in range(10):
        pos=Position(START_FEN if i<2 else base)
        if i>=6:
            for turn in range(i-5):
                moves=legal_moves(pos)
                pos.apply(moves[(i+turn)%len(moves)])
        app.official=pos
        app.sim=None
        app.game_source="image" if i>=2 else "standard"
        app.board_view_mode="black_bottom" if i%2 else "red_bottom"
        app.update();app.draw();app.update()
        style="SimSun" if i<6 else "KaiTi"
        if style=="KaiTi":
            for item in app.canvas.find_withtag("piece"):
                app.canvas.itemconfigure(item,font=("KaiTi",18,"bold"))
        app.update();time.sleep(.08)
        x,y=app.winfo_rootx(),app.winfo_rooty()
        name=f"sample_{i:02}.png"
        ImageGrab.grab(bbox=(x,y,x+app.winfo_width(),y+app.winfo_height())).save(out/name)
        ox,oy,dx,dy=app._geo()
        gx,gy=app.canvas.winfo_rootx()-x,app.canvas.winfo_rooty()-y
        labels.append({"file":name,"fen":pos.fen(),"red_bottom":i%2==0,"font":style,
            "source":"Actual Tk application capture; controlled font variant" if style=="KaiTi" else "Actual Tk application capture",
            "corners":[[gx+ox,gy+oy],[gx+ox+8*dx,gy+oy],[gx+ox+8*dx,gy+oy+9*dy],[gx+ox,gy+oy+9*dy]]})
    (out/"labels.json").write_text(json.dumps(labels,ensure_ascii=False,indent=2),encoding="utf-8")
finally:
    app._close()
