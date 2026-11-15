"""Actual Tk GUI + real Pikafish; Px0 missing-resource rollback (no mock UI)."""
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import App
from rules import Move, Position, legal_moves


screenshots = Path(sys.argv[1]) if len(sys.argv) > 1 else None
if screenshots is not None and sys.platform == "win32":
    # Keep Tk geometry and screenshot pixels in the same DPI coordinate space.
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
ui_preferences_directory = tempfile.TemporaryDirectory(prefix="xiangqi-ui-")
app = App(preferences_path=Path(ui_preferences_directory.name)/"ui_preferences.json",engine_config_path=(Path(ui_preferences_directory.name)/"ui_preferences.json").with_suffix(".engine.json"))
app.withdraw()


def capture(name):
    if screenshots is None:
        return
    from PIL import ImageGrab
    screenshots.mkdir(parents=True, exist_ok=True)
    app.geometry("1080x720")
    app.deiconify()
    app.attributes("-topmost", True)
    app.update()
    app.draw()
    app.update_idletasks()
    x, y = app.winfo_rootx(), app.winfo_rooty()
    ImageGrab.grab(bbox=(x, y, x+app.winfo_width(), y+app.winfo_height())).save(screenshots / name)


def click_move(move):
    ox, oy, dx, dy = app._geo()
    flipped = {"red_bottom": False, "black_bottom": True}.get(app.board_view_mode, app.human_red)
    for r, c in ((move.fr, move.fc), (move.tr, move.tc)):
        vr, vc = (9-r, 8-c) if flipped else (r, c)
        app.canvas.event_generate("<Button-1>", x=round(ox+vc*dx), y=round(oy+vr*dy))


def choose(view):
    label = {"auto": "自动（己方在上）", "red_bottom": "红方在下", "black_bottom": "黑方在下"}[view]
    before = (app.official.fen(), tuple(app.move_history), app.human_red, app.task_token, app.api_request_token)
    if view == "auto":
        app._set_board_view_mode("auto")
    elif app._view_flipped() != (view == "black_bottom"):
        app.flip_board_button.invoke()
    elif app.board_view_mode == "auto":
        app.flip_board_button.invoke()
        app.flip_board_button.invoke()
    assert app.board_view_mode == view
    assert (app.official.fen(), tuple(app.move_history), app.human_red, app.task_token, app.api_request_token) == before


def wait_until(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.update()
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("UI timed out: " + str(app.status.cget("text")))


try:
    with tempfile.TemporaryDirectory() as temp:
        wait_until(lambda: app.engine.ready and not app.busy)
        capture("red_view.png")
        assert app.board_view_mode == "auto"
        choose("black_bottom")
        assert app.human_red and app._view_flipped()
        capture("red_player_at_top.png")
        click_move(Move.from_ucci("b0c2"))
        wait_until(lambda: app.busy)
        choose("red_bottom")
        assert app.busy
        choose("black_bottom")
        wait_until(lambda: len(app.move_history) == 2 and not app.busy)
        assert app.move_history[0] == "b0c2"
        assert app.board_view_mode == "black_bottom"
        capture("red_at_top_after_mouse_move.png")
        app.new_game()
        assert app.board_view_mode == "black_bottom"
        choose("auto")
        app.engine_settings()
        app.update()
        dialogs = [w for w in app.winfo_children() if w.winfo_class() == "Toplevel"]
        assert dialogs
        dialogs[0].destroy()
        app._switch_engine("px0", {"enginePath": "missing/px0.exe"})
        wait_until(lambda: not app.busy)
        assert app.engine.name == "Pikafish"
        assert app.human_red and app._view_flipped()
        assert "Px0 executable not found" in app.coach_text.get("1.0", "end")
        app._switch_engine("pikafish", {"threads": 1, "hash_mb": 16}, human_red=False)
        wait_until(lambda: len(app.move_history) == 1 and not app.busy)
        assert app.official.red is False  # AI opened as red, human now black.
        assert not app._view_flipped()
        assert "红方在下" in app.sim_label.cget("text")
        capture("black_view.png")
        choose("red_bottom")
        assert not app.human_red and not app._view_flipped()
        capture("black_player_at_top.png")
        move = legal_moves(app.official)[0]
        click_move(move)
        wait_until(lambda: len(app.move_history) == 3 and not app.busy)
        assert app.move_history[1] == move.ucci()
        capture("black_at_top_after_mouse_move.png")
        app.undo()
        assert len(app.move_history) == 1 and not app.official.red
        app.undo()  # Undo the AI opening: it must be scheduled again for human black.
        wait_until(lambda: len(app.move_history) == 1 and not app.busy)
        assert app.board_view_mode == "red_bottom" and not app._view_flipped()
        app.recommend()
        app.undo()  # Cancels pending work before it can touch the restored position.
        app.new_game()
        wait_until(lambda: len(app.move_history) == 1 and not app.busy)
        app._switch_engine("pikafish", {"threads": 1, "hash_mb": 16}, human_red=True)
        wait_until(lambda: app.human_red and not app.busy)
        assert app.official.fen() == Position().fen()
        assert not app._view_flipped()
        assert app.board_view_mode == "red_bottom"
        click_move(legal_moves(app.official)[0])
        wait_until(lambda: len(app.move_history) == 2 and not app.busy)
        print("GUI_MANUAL_VIEW_RED_BLACK_TOP_SEARCH_UNDO_RESTART_SWITCH_OK")
finally:
    app._close()
