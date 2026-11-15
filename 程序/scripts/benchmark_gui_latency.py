"""Measure real GUI scheduling -> engine -> validated Canvas update, offscreen.

The AI position is supplied directly (as after a completed human move).
Mouse-input continuation is independently verified by smoke_creative_mode.py.
The experiment's time budget lives only in this process, never in user settings.
"""
import argparse
import gc
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import App, LEVELS
from rules import Move, Position, legal_moves
from benchmark_engines import ObservedQueue


def wait(app, predicate, timeout=90):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        app.update()
        if predicate():
            return
        time.sleep(.001)
    raise TimeoutError(app.status.cget("text"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--budget", type=int, default=2500)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--limit", type=int, default=12)
    args = parser.parse_args()
    profiles = json.loads(args.profiles.read_text(encoding="utf-8"))
    cases = json.loads((ROOT/"tests/fixtures/performance_positions.json").read_text(encoding="utf-8"))[:args.limit]
    LEVELS["基准专用"] = args.budget
    ui_preferences_directory = tempfile.TemporaryDirectory(prefix="xiangqi-ui-")
    app = App(preferences_path=Path(ui_preferences_directory.name)/"ui_preferences.json",engine_config_path=(Path(ui_preferences_directory.name)/"ui_preferences.json").with_suffix(".engine.json"))
    app.update()
    app.withdraw()
    app.api.settings["enabled"] = False
    app.api.configured = lambda: False
    output = {"method": __doc__, "budget_ms": args.budget, "samples": [], "profiles": profiles}
    try:
        with tempfile.TemporaryDirectory() as temp:
            wait(app, lambda: app.engine.ready and not app.busy)
            for profile in profiles:
                app.mode = "pvp"
                app._switch_engine(profile["engine"], profile)
                wait(app, lambda: not app.busy)
                assert app.engine.selected == profile["engine"], app.coach_text.get("1.0", "end")
                engine = app.engine._engine
                observed = ObservedQueue()
                observed.reset_metrics()
                engine._stdout_queue = observed
                old_send = engine._send
                def send(command, original=old_send, monitor=observed):
                    if command.startswith("go "):
                        monitor.go_at = time.perf_counter()
                    return original(command)
                engine._send = send
                for repeat in range(args.repeat):
                    for case in cases:
                        app.mode = "pvp"
                        app._cancel_opponent_job()
                        fen = case["fen"]
                        def reset():
                            app.engine.new_game()
                            app.engine.analyze_limit(fen, "nodes", 1, 1)
                            app.engine.new_game()
                        app._run_task("基准预热", reset, None)
                        wait(app, lambda: not app.busy)
                        engine.cache.clear()
                        observed.reset_metrics()
                        observed.go_at = None
                        app.official = Position(fen)
                        app.history = []
                        app.move_history = []
                        app.sim = None
                        app.human_red = not app.official.red
                        app.mode = "ai"
                        app.manual_takeover = False
                        app.level.set("基准专用")
                        app.draw()
                        app._update_turn()
                        app.update_idletasks()
                        started = time.perf_counter()
                        app._schedule_opponent()
                        wait(app, lambda: len(app.move_history) == 1 and not app.busy)
                        ended = time.perf_counter()
                        move = Move.from_ucci(app.move_history[0])
                        assert move in legal_moves(Position(fen))
                        row = {
                            "profile": profile["id"], "case": case["id"],
                            "category": case["category"], "repeat": repeat+1, "fen": fen,
                            "budget_ms": args.budget, "total_ms": round((ended-started)*1000, 3),
                            "request_delay_ms": round((observed.go_at-started)*1000, 3),
                            "search_wall_ms": round((observed.bestmove_at-observed.go_at)*1000, 3),
                            "receive_to_gui_ms": round((ended-observed.bestmove_at)*1000, 3),
                            "bestmove": move.ucci(), "counters": observed.metrics,
                            "legal": True, "within_3_seconds": ended-started <= 3,
                            "tactic_pass": move.ucci() in case["acceptable"] if case.get("acceptable") else None}
                        output["samples"].append(row)
                        args.output.parent.mkdir(parents=True, exist_ok=True)
                        args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
                        print(profile["id"], case["id"], repeat+1, row["total_ms"], flush=True)
    finally:
        app._close()
        app.engine.close()
        app.__dict__.clear()
        gc.collect()
        del LEVELS["基准专用"]


if __name__ == "__main__":
    main()
