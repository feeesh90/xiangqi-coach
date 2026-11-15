"""Scripted real GUI and two real engines; isolated configs, no external API."""
import ctypes
import gc
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
if sys.platform=="win32":
    ctypes.windll.shcore.SetProcessDpiAwareness(2)

from app import App
from api_client import CONFIG_DIR
from repetition import advance, blocked_cycle_move, playable_moves, MESSAGE, FORCED_MESSAGE
from rules import Move, Position, START_FEN, legal_moves
from smoke_player_settings import capture, click_move, wait

CYCLE=["b0c2","b9c7","c2b0","c7b9"]
FORCED_FEN="4k4/9/5R3/9/4P4/9/9/9/9/3RK4 w - - 0 1"
FORCED_CYCLE=["f7e7","e9f9","e7f7","f9e9"]


def fingerprint():
    return {name:hashlib.sha256((CONFIG_DIR/name).read_bytes()).hexdigest()
            if (CONFIG_DIR/name).exists() else None
            for name in ("engine_settings.json","api_settings.json","ui_preferences.json")}


def verify(app):
    position=Position(app.game_start_fen);context=()
    for value in app.move_history:
        move=Move.from_ucci(value)
        assert move in playable_moves(position,context)
        context=advance(context,position,move);position.apply_unchecked(move)
    assert position.fen()==app.official.fen()
    assert context==app._cycle_context()
    assert len(app.canvas.find_withtag("last_move_ring"))==bool(app.move_history)


def main():
    out=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/"data/repetition_verification/20261019"
    out.mkdir(parents=True,exist_ok=True)
    report={"verification":"Scripted Tk/Canvas events and real engines; not human mouse or two physical computers",
            "cases":[]}
    initial=fingerprint()
    with tempfile.TemporaryDirectory(prefix="xiangqi-cycle-smoke-") as temp:
        app=App(preferences_path=Path(temp)/"ui.json",engine_config_path=(Path(temp)/"ui.json").with_suffix(".engine.json"))
        try:
            app.geometry("1180x820+15+15")
            app.api.configured=lambda:False
            wait(app,lambda:app.engine.ready and not app.busy)
            app.level.set("入门");app.coach.set("极速")
            profile_name="engine_settings.gpu.example.json" if (ROOT/"engine/px0-gpu/px0.exe").is_file() else "engine_settings.cpu.example.json"
            profiles=json.loads((ROOT/profile_name).read_text(encoding="utf-8"))
            calls=[]
            analyze=app.engine.analyze
            def recorded(fen,ms=10000,multipv=3,searchmoves=None):
                calls.append(dict(fen=fen,milliseconds=ms,multipv=multipv,searchmoves=searchmoves))
                return analyze(fen,ms,multipv,searchmoves)
            app.engine.analyze=recorded
            for key in ("pikafish","px0"):
                app._set_mode("pvp")
                app._switch_engine(key,profiles[key],human_red=True)
                wait(app,lambda:not app.busy)
                assert app.engine.selected==key, app.coach_text.get("1.0","end")
                app.new_game();app._set_board_view_mode("red_bottom")
                for value in (CYCLE*2)[:7]:
                    click_move(app,Move.from_ucci(value))
                assert len(app.move_history)==7
                before=(app.cur().fen(),app.move_history[:],app.last)
                click_move(app,Move.from_ucci("c7b9"))
                assert app.status.cget("text")==MESSAGE
                assert (app.cur().fen(),app.move_history,app.last)==before
                capture(app,out/(key+"_cycle_blocked.png"))
                app.recommend()
                wait(app,lambda:not app.busy)
                assert app.analysis_result and app.analysis_result.best!=Move.from_ucci("c7b9")
                assert all(c.move!=Move.from_ucci("c7b9") for c in app.analysis_result.candidates)
                assert "c7b9" not in calls[-1]["searchmoves"]
                # Seed the same legal seven-ply position in normal AI mode.
                app._set_mode("ai")
                for value in (CYCLE*2)[:7]:
                    assert app.make_move(Move.from_ucci(value),auto_ai=False)
                assert app._is_ai_turn()
                app._schedule_opponent()
                wait(app,lambda:not app.busy and len(app.move_history)==8)
                assert app.last!=Move.from_ucci("c7b9")
                verify(app)
                report["cases"].append(dict(engine=key,scenario="black_opponent_changes_route",
                                            moves=app.move_history[:],search=calls[-1],legal=True))
                capture(app,out/(key+"_alternative_played.png"))
                app._set_mode("pvp")
                app.import_position(Position(FORCED_FEN),True,"black_bottom")
                for value in (FORCED_CYCLE*2)[:7]:
                    click_move(app,Move.from_ucci(value))
                assert len(legal_moves(app.cur()))==1
                click_move(app,Move.from_ucci("f9e9"))
                assert app.status.cget("text")==FORCED_MESSAGE
                assert len(app.move_history)==8
                assert blocked_cycle_move(app.cur(),app._cycle_context())==Move.from_ucci("f7e7")
                app.ai_take()
                wait(app,lambda:not app.busy and len(app.move_history)==9)
                assert app.last!=Move.from_ucci("f7e7")
                verify(app)
                report["cases"].append(dict(engine=key,scenario="forced_black_evasion_then_red_changes",
                                            root=FORCED_FEN,moves=app.move_history[:],search=calls[-1],legal=True))
                print(key,"REAL_GUI_CYCLE_AND_FORCED_EXCEPTION_OK",flush=True)
        finally:
            app._close();app.engine.close()
            report["owned_engine_closed"]=app.engine._engine.p is None
            app.__dict__.clear();gc.collect()
    report["user_configs_unchanged"]=fingerprint()==initial
    assert report["user_configs_unchanged"]
    report["status"]="passed"
    (out/"real_gui_result.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
