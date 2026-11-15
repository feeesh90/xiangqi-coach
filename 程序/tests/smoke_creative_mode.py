"""Real GUI, mouse events and real engines; screenshots/reports are opt-in artifacts."""
import argparse
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
from rules import Move, Position, legal_moves


def wait(app, predicate, timeout=60):
    end = time.monotonic()+timeout
    while time.monotonic() < end:
        app.update()
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError(str(app.status.cget("text")))


def click(app, r, c):
    flipped = {"red_bottom": False, "black_bottom": True}.get(
        app.board_view_mode, app.mode in ("ai", "lan") and app.human_red)
    vr, vc = (9-r, 8-c) if flipped else (r, c)
    ox, oy, dx, dy = app._geo()
    app.canvas.event_generate("<Button-1>", x=round(ox+vc*dx), y=round(oy+vr*dy))


def capture(app, path):
    from PIL import ImageGrab
    app.deiconify()
    app.attributes("-topmost", True)
    app.lift()
    end = time.monotonic()+.35
    while time.monotonic() < end:
        app.update()
        time.sleep(.01)
    x, y = app.winfo_rootx(), app.winfo_rooty()
    ImageGrab.grab(bbox=(x, y, x+app.winfo_width(), y+app.winfo_height())).save(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gpu", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT/"data/performance/20261017/gui_smoke")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    ui_preferences_directory = tempfile.TemporaryDirectory(prefix="xiangqi-ui-")
    app = App(preferences_path=Path(ui_preferences_directory.name)/"ui_preferences.json",engine_config_path=(Path(ui_preferences_directory.name)/"ui_preferences.json").with_suffix(".engine.json"))
    app.geometry("1080x720+10+10")
    app.api.settings["enabled"] = False
    app.api.configured = lambda: False
    result = {"cases": [], "errors": []}
    profiles = [("pikafish", {"threads": 8, "hash_mb": 512}),
                ("px0", {"enginePath": "engine/px0-gpu/px0.exe" if args.gpu else "engine/px0/px0.exe",
                         "networkPath": "engine/px0/network.pb.gz", "backend": "onnx-cuda" if args.gpu else "onnx-cpu",
                         "backendOptions": "fp16=false" if args.gpu else "threads=8",
                         "threads": 1})]
    try:
        with tempfile.TemporaryDirectory() as temp:
            wait(app, lambda: app.engine.ready and not app.busy)
            for key, profile in profiles:
                for human_red in (True, False):
                    app._set_mode("pvp")
                    app._switch_engine(key, profile, human_red=human_red)
                    wait(app, lambda: not app.busy)
                    assert app.engine.selected == key, app.coach_text.get("1.0", "end")
                    app.new_game()
                    # Each player deliberately chooses to have their own pieces at TOP.
                    desired_flip = human_red
                    if app._view_flipped() != desired_flip:
                        app.flip_board_button.invoke()
                    app.creative_button.invoke()
                    assert app._editing()
                    # Both sides can be moved during a single editor session.
                    for r, c in ((9, 1), (7, 2), (0, 1), (2, 2)):
                        click(app, r, c)
                    app.editor_turn_var.set("红方" if human_red else "黑方")
                    app.editor_turn_selector.event_generate("<<ComboboxSelected>>")
                    root = app.editor.position().fen()
                    capture(app, args.output/f"{key}_{human_red}_editing.png")
                    app.editor_apply_button.invoke()
                    assert app.game_start_fen == root and not app.move_history
                    app._set_mode("ai")
                    app.level.set("中级")
                    # Exercise a real human mouse move followed by a real engine response.
                    move = next(m for m in legal_moves(app.official)
                                if app.official.b[m.fr][m.fc].upper() == "N")
                    click(app, move.fr, move.fc)
                    started = time.perf_counter()
                    click(app, move.tr, move.tc)
                    wait(app, lambda: len(app.move_history) == 2 and not app.busy)
                    elapsed = (time.perf_counter()-started)*1000
                    probe = Position(root)
                    for u in app.move_history:
                        assert probe.apply(Move.from_ucci(u))
                    assert probe.fen() == app.official.fen()
                    assert app.human_red == human_red and app._view_flipped() == desired_flip
                    capture(app, args.output/f"{key}_{human_red}_continued.png")
                    app.undo()
                    assert app.official.fen() == root and not app.history
                    # Enter during a genuine long search, cancel, then resume.
                    app.level.set("大师")
                    click(app, move.fr, move.fc)
                    click(app, move.tr, move.tc)
                    wait(app, lambda: app.busy)
                    snapshot = app.official.fen(), tuple(app.move_history)
                    app.creative_button.invoke()
                    assert app._editing() and not app.busy
                    app.editor.tool = "delete"
                    click(app, 0, 4)  # Invalid draft is safe to cancel.
                    app.flip_board_button.invoke()
                    app.editor_cancel_button.invoke()
                    assert (app.official.fen(), tuple(app.move_history)) == snapshot
                    wait(app, lambda: len(app.move_history) == 2 and not app.busy)
                    result["cases"].append({"engine": key, "backend": profile.get("backend"),
                        "human_red": human_red, "root": root, "medium_response_ms": round(elapsed, 3),
                        "mouse_move": move.ucci(), "search_cancel_resume": True, "legal": True})
                    print(key, human_red, "CREATIVE_MOUSE_RESUME_OK", round(elapsed, 1), flush=True)
            # A broken GPU selection must leave the current working engine available.
            app._set_mode("pvp")
            app._switch_engine("pikafish", {"threads": 8}, human_red=True)
            wait(app, lambda: not app.busy)
            app._switch_engine("px0", {"enginePath": "missing/px0.exe", "backend": "onnx-cuda"})
            wait(app, lambda: not app.busy)
            assert app.engine.name == "Pikafish"
            app.level.set("中级")
            app.new_game()
            app._set_mode("ai")
            app.make_move(legal_moves(app.official)[0])
            wait(app, lambda: len(app.move_history) == 2 and not app.busy)
            result["failed_switch_retains_working_engine"] = True
            result["status"] = "passed"
    except Exception as exc:
        result["status"] = "failed"
        result["errors"].append(repr(exc))
        raise
    finally:
        app._close()
        app.engine.close()
        result["all_owned_engine_processes_closed"] = app.engine._engine.p is None
        (args.output/"result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        app.__dict__.clear()
        gc.collect()


if __name__ == "__main__":
    main()
