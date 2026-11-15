"""Opt-in real worker -> preview -> import -> Canvas click -> real engines.

Only this verification script writes screenshots/reports. The user feature does
not write images. Includes measured one-shot worker memory and 10 reaped jobs.
"""
import ctypes
import gc
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
if sys.platform=="win32":ctypes.windll.shcore.SetProcessDpiAwareness(2)
from app import App
from recognition_process import RecognitionJob
from position_import import normalize_board
from rules import Position,legal_moves
from PIL import ImageGrab
from recognition_worker import memory_mb

root=Path(__file__).resolve().parents[1]
out=root/"data/endgame_verification"
out.mkdir(exist_ok=True)
samples=out/"samples"
labels=json.loads((samples/"labels.json").read_text(encoding="utf-8"))
results={"model_samples":[],"gui":[],"worker_runs":[],"gui_import_memory":[]}


def capture(widget,name):
    widget.update();widget.attributes("-topmost",True);widget.lift()
    # Let Windows finish its focus/resize animation before ImageGrab reads it.
    deadline=time.monotonic()+.5
    while time.monotonic()<deadline:widget.update();time.sleep(.01)
    x,y=widget.winfo_rootx(),widget.winfo_rooty()
    ImageGrab.grab(bbox=(x,y,x+widget.winfo_width(),y+widget.winfo_height())).save(out/name)


def wait(app,predicate,timeout=40):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        app.update()
        if predicate():return
        time.sleep(.01)
    raise AssertionError("GUI timed out: "+str(app.status.cget("text")))


for sample in labels:
    job=RecognitionJob()
    started=time.monotonic()
    r=job.run(samples/sample["file"])
    before=Position(sample["fen"]).b
    board=normalize_board(r["board"],sample["red_bottom"])
    errors=[(i,j) for i,row in enumerate(before) for j,p in enumerate(row) if p!=board[i][j]]
    results["model_samples"].append({"file":sample["file"],"font":sample["font"],"source":sample["source"],
        "different_cells":errors,"correct_cells":90-len(errors),"uncertain":r["uncertain"],
        "elapsed_seconds":round(time.monotonic()-started,3),"memory":r["memory"],
        "process_reaped":job.process is None and job.finished.is_set()})
    results["worker_runs"].append({"elapsed_seconds":round(time.monotonic()-started,3),
                                  "memory":r["memory"],"process_reaped":job.process is None})

ui_preferences_directory = tempfile.TemporaryDirectory(prefix="xiangqi-ui-")
app=App(preferences_path=Path(ui_preferences_directory.name)/"ui_preferences.json",engine_config_path=(Path(ui_preferences_directory.name)/"ui_preferences.json").with_suffix(".engine.json"))
app.geometry("1500x920+20+20")
app.level.set("休闲")
app.api.settings["enabled"]=False
app.api.configured=lambda:False  # no API use in verification
try:
    with tempfile.TemporaryDirectory() as settings:
        wait(app,lambda:app.engine.ready and not app.busy)
        app._set_mode("pvp")
        gc.collect();results["main_memory_before_imports"]=memory_mb()
        # Six real select/recognize/confirm/close cycles, followed by the four
        # real engine cases below, make ten actual GUI imports in this process.
        for i in range(6):
            app.open_endgame();win=app.endgame_window
            if i==0:
                external=out/"external"
                label=json.loads((external/"verification.json").read_text(encoding="utf-8"))
                win.load_image(external/"page_11_image_00_Im6.png")
                win.update()
                win.mark_corners()
                l,t,w,h=win.image_rect
                for x,y in label["corners"]:
                    win.image_canvas.event_generate("<Button-1>",x=round(l+x*w/954),y=round(t+y*h/608))
                assert len(win.corners)==4 and not win.marking
                expected=Position(label["fen"])
            else:
                win.load_image(samples/labels[8]["file"])
                expected=Position(labels[8]["fen"])
                expected.red=True
            win.recognize();job=win.job
            wait(app,lambda:win.job is None)
            assert win.draft is not None,win.status_var.get()
            assert normalize_board(win.draft.board,win.draft.red_bottom)==expected.b
            if win.draft.uncertain:win.confirm_uncertain()
            win.turn_var.set("红方");win.apply()
            assert app.official.fen()==expected.fen()
            assert not win.alive and win.photo is None and win.preview is None
            assert job.finished.is_set() and job.process is None
            del win,job;gc.collect()
            results["gui_import_memory"].append({"import":i+1,"main":memory_mb(),"child_reaped":True})
        app._set_mode("ai")
        for key in ("pikafish","px0"):
            profile=dict(app.engine.settings[key])
            if key=="pikafish":profile.update(threads=1,hash_mb=16)
            app._switch_engine(key,profile)
            wait(app,lambda:not app.busy)
            assert app.engine.selected==key,app.coach_text.get("1.0","end")
            for human_red in (True,False):
                # Same image, independently chosen side-to-move and identity.
                sample=labels[8]
                app.open_endgame();win=app.endgame_window
                win.load_image(samples/sample["file"])
                old_root=app.game_start_fen
                win.recognize()
                job=win.job
                wait(app,lambda:win.draft is not None or (win.job is None))
                assert win.draft is not None,win.status_var.get()
                # An already-running opponent may legally move during preview;
                # recognition must not replace the official starting point.
                assert app.game_start_fen==old_root
                expected=Position(sample["fen"]);expected.red=human_red
                assert normalize_board(win.draft.board,win.draft.red_bottom)==expected.b
                if win.draft.uncertain:win.confirm_uncertain()
                win.turn_var.set("红方" if human_red else "黑方")
                win.human_var.set("红方" if human_red else "黑方")
                # Put the human at the top for both identities.
                win.view_var.set("黑方在下" if human_red else "红方在下")
                if key=="pikafish" and human_red:capture(win,"preview.png")
                win.apply()
                assert not win.alive and win.preview is None and win.photo is None
                assert job.finished.is_set() and job.process is None
                gc.collect()
                results["gui_import_memory"].append({"import":len(results["gui_import_memory"])+1,
                                                    "main":memory_mb(),"child_reaped":True})
                assert app.official.fen()==expected.fen()
                assert app.game_start_fen==expected.fen()
                move=legal_moves(expected)[0]
                ox,oy,dx,dy=app._geo()
                for r,c in ((move.fr,move.fc),(move.tr,move.tc)):
                    vr,vc=(9-r,8-c) if human_red else (r,c)
                    app.canvas.event_generate("<Button-1>",x=round(ox+vc*dx),y=round(oy+vr*dy))
                wait(app,lambda:len(app.move_history)==2 and not app.busy)
                assert app.move_history[0]==move.ucci()
                rebuilt=expected.copy()
                from rules import Move
                for encoded in app.move_history:
                    m=Move.from_ucci(encoded);assert m in legal_moves(rebuilt);rebuilt.apply(m)
                assert rebuilt.fen()==app.official.fen()
                app.recommend();wait(app,lambda:not app.busy)
                assert app.analysis_result is not None
                snapshot=app.official.fen()
                app.toggle_sim()
                assert app.sim.fen()==snapshot
                app.toggle_sim();assert app.official.fen()==snapshot
                capture(app,f"{key}_{'red' if human_red else 'black'}_at_top.png")
                app.undo();assert app.official.fen()==expected.fen()
                app.restart_current_game();assert app.official.fen()==expected.fen()
                assert app.board_view_mode==("black_bottom" if human_red else "red_bottom")
                app.new_game();assert app.game_source=="standard"
                app._cancel_opponent_job()
                results["gui"].append({"engine":key,"human_red":human_red,"player_at_top":True,
                    "actual_worker_preview_import":True,"mouse_move_and_legal_ai_reply":True,
                    "recommendation_and_simulation":True,"undo_restart_standard":True})
        # Small-window controls must remain visible; no image is imported here.
        app.geometry("1080x720");app.update();app.open_endgame();app.update()
        capture(app.endgame_window,"import_window.png")
        app.endgame_window.close()
        capture(app,"main_1080.png")
finally:
    app._close()
    (out/"verification_results.json").write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding="utf-8")
print("REAL_IMAGE_WORKER_PREVIEW_IMPORT_PIKAFISH_PX0_RED_BLACK_TOP_OK")
