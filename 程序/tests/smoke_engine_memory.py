"""Actual Tk reopen + real Pikafish/Px0; configs and subprocess ownership isolated."""
import copy
import gc
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from smoke_player_settings import capture, click_move, wait, verify_history
from app import App
from api_client import CONFIG_DIR
from rules import Move, legal_moves


def fingerprint():
    return {name:hashlib.sha256((CONFIG_DIR/name).read_bytes()).hexdigest()
            if (CONFIG_DIR/name).exists() else None
            for name in ("engine_settings.json","api_settings.json","ui_preferences.json")}


def player_is_on_top(app):
    row=9 if app.human_red else 0
    item,=app.canvas.find_withtag(f"piece:{row}:4")
    assert app.canvas.coords(item)[1]<app.canvas.winfo_height()/2
    assert app._view_flipped()==app.human_red


def close(app):
    manager=app.engine
    app._close();manager.close()
    assert manager._engine.p is None
    app.__dict__.clear();gc.collect()


def main():
    out=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/"data/engine_memory_verification/20261027"
    out.mkdir(parents=True,exist_ok=True)
    report={"verification":"Scripted real Tk/Canvas events, separate App instances and real engines; not human mouse or two physical computers",
            "cases":[]}
    before=fingerprint()
    with tempfile.TemporaryDirectory(prefix="xiangqi-engine-memory-") as directory:
        directory=Path(directory)
        preferences=directory/"ui.json";engines=directory/"engines.json"
        profile_file="engine_settings.gpu.example.json" if (ROOT/"engine/px0-gpu/px0.exe").is_file() else "engine_settings.cpu.example.json"
        profiles=json.loads((ROOT/profile_file).read_text(encoding="utf-8"))
        engines.write_text(json.dumps({**profiles,"selected":"pikafish"}),encoding="utf-8")
        def open_app(config=engines):
            app=App(preferences_path=preferences,engine_config_path=config)
            app.api.configured=lambda:False
            app.geometry("1080x720+15+15")
            wait(app,lambda:not app.busy and app.engine.ready)
            return app

        app=open_app()
        try:
            player_is_on_top(app)
            for selector,value in ((app.level_selector,"入门"),(app.coach_selector,"极速")):
                selector.set(value);selector.event_generate("<<ComboboxSelected>>")
            app._switch_engine("px0",profiles["px0"])
            wait(app,lambda:not app.busy)
            assert app.engine.selected=="px0" and app.engine.ready
            assert json.loads(engines.read_text(encoding="utf-8"))["selected"]=="px0"
            report["cases"].append(dict(case="px0_activated_and_saved",engine=app.engine.name))
        finally:close(app)

        spawned=[];processes=[]
        popen=subprocess.Popen
        def observed(command,*args,**kwargs):
            process=popen(command,*args,**kwargs);processes.append(process)
            spawned.append(Path(command[0]).name if isinstance(command,(tuple,list)) else str(command))
            return process
        with patch("subprocess.Popen",observed):
            app=open_app()
            try:
                assert app.engine.selected=="px0"
                assert app.engine.settings["px0"]["backend"]==profiles["px0"]["backend"]
                assert app.engine.settings["px0"]["networkPath"]==profiles["px0"]["networkPath"]
                assert (app.level.get(),app.coach.get())==("入门","极速")
                player_is_on_top(app)
                capture(app,out/"px0_restored_red_player_top.png")
                click_move(app,Move.from_ucci("b0c2"))
                wait(app,lambda:not app.busy and len(app.move_history)==2)
                verify_history(app)
                assert spawned and all("pikafish" not in name.lower() for name in spawned)
                report["cases"].append(dict(case="px0_restored_without_starting_pikafish",
                                            processes=spawned[:],moves=app.move_history[:]))
                app.player_side_button.invoke()
                wait(app,lambda:not app.busy and len(app.move_history)==1)
                assert not app.human_red and not app._view_flipped()
                player_is_on_top(app);verify_history(app)
                capture(app,out/"px0_black_player_top.png")
                report["cases"].append(dict(case="black_player_auto_top",moves=app.move_history[:]))
                app.flip_board_button.invoke()
                fixed=app.board_view_mode
                app.player_side_button.invoke()
                assert app.human_red and app.board_view_mode==fixed
                app.flip_board_button.invoke()
                assert app.board_view_mode=="red_bottom" and not app._view_flipped()
                app.restart_current_game()
                assert app.board_view_mode=="red_bottom"
                app._switch_engine("pikafish",profiles["pikafish"])
                wait(app,lambda:not app.busy)
                assert app.engine.selected=="pikafish" and app.engine.ready
                assert json.loads(engines.read_text(encoding="utf-8"))["selected"]=="pikafish"
            finally:close(app)
        assert all(process.poll() is not None for process in processes)

        app=open_app()
        try:
            assert app.engine.selected=="pikafish" and app.engine.ready
            assert app.human_red and app.board_view_mode=="auto"
            player_is_on_top(app)
            click_move(app,Move.from_ucci("b0c2"))
            wait(app,lambda:not app.busy and len(app.move_history)==2)
            verify_history(app)
            report["cases"].append(dict(case="pikafish_restored_and_manual_view_reset",moves=app.move_history[:]))
            capture(app,out/"pikafish_restored_red_player_top.png")
        finally:close(app)

        broken=copy.deepcopy(profiles)
        broken["px0"]["networkPath"]=str(directory/"missing-network.pb.gz")
        fallback=directory/"fallback.json"
        fallback.write_text(json.dumps({**broken,"selected":"px0"}),encoding="utf-8")
        app=open_app(fallback)
        try:
            assert app.engine.selected=="pikafish" and app.engine.ready
            assert "恢复 Px0 失败" in app.engine.startup_notice
            assert "已回退 Pikafish" in app.coach_text.get("1.0","end")
            assert json.loads(fallback.read_text(encoding="utf-8"))["selected"]=="pikafish"
            report["cases"].append(dict(case="missing_px0_model_falls_back_to_real_pikafish",
                                        notice=app.engine.startup_notice))
        finally:close(app)
    report["user_configs_unchanged"]=fingerprint()==before
    assert report["user_configs_unchanged"]
    report["owned_engines_closed"]=True
    report["status"]="passed"
    (out/"real_gui_result.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
