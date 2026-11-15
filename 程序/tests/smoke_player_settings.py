"""Real engines and Tk events; isolated preferences, no external API or firewall changes."""
import ctypes
import gc
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if sys.platform == "win32":
    ctypes.windll.shcore.SetProcessDpiAwareness(2)

from app import App
from rules import Move, Position, START_FEN, legal_moves


def wait(app, predicate, timeout=60):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        app.update()
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError(str(app.status.cget("text")))


def click_move(app, move):
    assert move in legal_moves(app.cur())
    flipped = {"red_bottom": False, "black_bottom": True}.get(
        app.board_view_mode, app.mode in ("ai", "lan") and app.human_red)
    ox, oy, dx, dy = app._geo()
    for r, c in ((move.fr, move.fc), (move.tr, move.tc)):
        vr, vc = (9-r, 8-c) if flipped else (r, c)
        app.canvas.event_generate("<Button-1>", x=round(ox+vc*dx), y=round(oy+vr*dy))


def verify_history(app):
    position = Position(app.game_start_fen)
    for encoded in app.move_history:
        assert position.apply(Move.from_ucci(encoded))
    assert position.fen() == app.official.fen()
    assert len(app.canvas.find_withtag("last_move_ring")) == bool(app.move_history)


def capture(app, path):
    from PIL import ImageGrab
    app.deiconify();app.attributes("-topmost", True);app.lift()
    end = time.monotonic()+.3
    while time.monotonic() < end:
        app.update();time.sleep(.01)
    x, y = app.winfo_rootx(), app.winfo_rooty()
    ImageGrab.grab(bbox=(x, y, x+app.winfo_width(), y+app.winfo_height())).save(path)


def main():
    out = Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/"data/player_settings_verification/20261018"
    out.mkdir(parents=True, exist_ok=True)
    result = {"verification": "Scripted real Tk widget/Canvas events, real engines; not human mouse or two physical computers", "cases": []}
    with tempfile.TemporaryDirectory(prefix="xiangqi-side-smoke-") as directory:
        preferences = Path(directory)/"ui_preferences.json"
        app = App(preferences_path=preferences,engine_config_path=(preferences).with_suffix(".engine.json"))
        try:
            app.geometry("1080x720+15+15")
            app.api.configured = lambda: False
            wait(app, lambda: app.engine.ready and not app.busy)
            for selector, value in ((app.level_selector, "入门"), (app.coach_selector, "极速")):
                selector.set(value)
                selector.event_generate("<<ComboboxSelected>>")
            assert json.loads(preferences.read_text(encoding="utf-8")) == {"level": "入门", "coach": "极速"}
            profile_file = "engine_settings.gpu.example.json" if (ROOT/"engine/px0-gpu/px0.exe").is_file() else "engine_settings.cpu.example.json"
            profiles = json.loads((ROOT/profile_file).read_text(encoding="utf-8"))
            for key in ("pikafish", "px0"):
                app._set_mode("pvp")
                app._switch_engine(key, profiles[key], human_red=True)
                wait(app, lambda: not app.busy)
                assert app.engine.selected == key, app.coach_text.get("1.0", "end")
                app.new_game();app._set_mode("ai")
                app._set_board_view_mode("auto")
                click_move(app, Move.from_ucci("b0c2"))
                wait(app, lambda: not app.busy and len(app.move_history)==2)
                verify_history(app)
                adapter = app.engine._engine
                app.player_side_button.invoke()
                assert not app.human_red and app.official.fen()==START_FEN and not app.history
                assert app.player_side_button.cget("text")=="我执黑方" and not app._view_flipped()
                assert app.last is None and not app.canvas.find_withtag("last_move_ring")
                wait(app, lambda: not app.busy and len(app.move_history)==1)
                verify_history(app)
                assert app.engine._engine is adapter
                result["cases"].append({"engine": key, "root": "standard", "moves": app.move_history[:], "legal": True})
                capture(app, out/(key+"_black_player_1080.png"))
                # Use the button again; hand-selected orientation survives changing identity.
                app.flip_board_button.invoke()
                manual_view = app.board_view_mode
                app.player_side_button.invoke()
                assert app.human_red and app.board_view_mode==manual_view
                assert app.official.fen()==START_FEN and not app.move_history
                custom = Position("1r2k4/9/9/9/4p4/9/2N1P4/9/4C4/4K1R2 w - - 0 1")
                app.import_position(custom, True, manual_view)
                click_move(app, legal_moves(app.official)[0])
                wait(app, lambda: not app.busy and len(app.move_history)==2)
                verify_history(app)
                app.player_side_button.invoke()
                assert not app.human_red and app.game_start_fen==custom.fen()
                assert app.game_source=="image" and app.official.fen()==custom.fen() and not app.move_history
                wait(app, lambda: not app.busy and len(app.move_history)==1)
                verify_history(app)
                assert app.board_view_mode==manual_view
                result["cases"].append({"engine": key, "root": "custom", "fen": custom.fen(), "moves": app.move_history[:], "legal": True})
                app.geometry("1280x860+15+15");app.update()
                capture(app, out/(key+"_custom_black_player.png"))
                print(key, "STANDARD_AND_CUSTOM_SIDE_SWITCH_OK", flush=True)
        finally:
            app._close();app.engine.close()
            result["owned_engine_closed"] = app.engine._engine.p is None
            app.__dict__.clear();gc.collect()
        reopened = App(preferences_path=preferences,engine_config_path=(preferences).with_suffix(".engine.json"))
        try:
            reopened.api.configured = lambda: False
            reopened.geometry("1080x720+15+15")
            wait(reopened, lambda: reopened.engine.ready and not reopened.busy)
            assert (reopened.level.get(), reopened.coach.get()) == ("入门", "极速")
            assert reopened.human_red and reopened.board_view_mode=="auto"
            capture(reopened, out/"preferences_restored.png")
            result["recreated_gui_restores_preferences"] = True
        finally:
            reopened._close();reopened.engine.close()
            result["reopened_owned_engine_closed"] = reopened.engine._engine.p is None
            reopened.__dict__.clear();gc.collect()
    result["status"] = "passed"
    (out/"real_gui_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
