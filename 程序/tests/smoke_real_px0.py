"""Real Tk + bundled Px0/model acceptance. No fake engine or fallback allowed."""
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import App, LEVELS
from rules import Position, legal_moves

ui_preferences_directory = tempfile.TemporaryDirectory(prefix="xiangqi-ui-")
app = App(preferences_path=Path(ui_preferences_directory.name)/"ui_preferences.json",engine_config_path=(Path(ui_preferences_directory.name)/"ui_preferences.json").with_suffix(".engine.json"))
app.withdraw()
pids = set()


def wait_until(predicate, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.update()
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("UI timed out: " + str(app.status.cget("text")))


try:
    with tempfile.TemporaryDirectory() as temp:
        app.level.set(min(LEVELS, key=LEVELS.get))
        wait_until(lambda: app.engine.ready and not app.busy)
        for human_red in (True, False):
            app._switch_engine("px0", {"backend": "", "threads": 2}, human_red=human_red)
            wait_until(lambda: not app.busy)
            assert app.engine.name == "Px0", app.coach_text.get("1.0", "end")
            pids.add(app.engine._engine.p.pid)
            if not human_red:
                wait_until(lambda: len(app.move_history) == 1 and not app.busy)
            before = len(app.move_history)
            app.make_move(legal_moves(app.official)[0])
            wait_until(lambda: len(app.move_history) == before + 2 and not app.busy)
            assert app.official.red == human_red
            app.undo()
            assert len(app.move_history) == before
            app.recommend()
            wait_until(lambda: not app.busy)
            assert app.reco is not None
            app.recommend()
            app.new_game()
            app.new_game()  # Stale search callbacks must not move the new board.
            wait_until(lambda: not app.busy)
            if human_red:
                assert app.official.fen() == Position().fen()
            else:
                wait_until(lambda: len(app.move_history) == 1 and not app.busy)
            print("REAL_PX0_HUMAN_" + ("RED" if human_red else "BLACK") + "_OK", flush=True)
        app._switch_engine("pikafish", {"threads": 1, "hash_mb": 16}, human_red=True)
        wait_until(lambda: not app.busy)
        assert app.engine.name == "Pikafish"
        app.make_move(legal_moves(app.official)[0])
        wait_until(lambda: len(app.move_history) == 2 and not app.busy)
        print("REAL_PX0_GUI_SWITCH_ANALYSIS_UNDO_RESTART_OK", flush=True)
finally:
    app._close()
    app.engine.close()
    assert app.engine._engine.p is None or app.engine._engine.p.poll() is not None
