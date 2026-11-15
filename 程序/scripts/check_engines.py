"""Real-binary acceptance, never substitutes a mock when resources are missing."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine import Pikafish
from engine_manager import EngineManager
from px0_engine import Px0
from rules import Position, Move, legal_moves, in_check


def digest(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=("pikafish", "px0"), default="px0")
    parser.add_argument("--engine-path")
    parser.add_argument("--network-path")
    parser.add_argument("--backend")
    parser.add_argument("--backend-options")
    parser.add_argument("--threads", type=int)
    parser.add_argument("--plies", type=int, default=60)
    parser.add_argument("--movetime", type=int, default=100)
    parser.add_argument("--report", default="data/engine_acceptance.json")
    args = parser.parse_args()
    manager = EngineManager(ROOT)
    settings = dict(manager.settings[args.engine])
    for arg, key in (("engine_path", "enginePath"), ("network_path", "networkPath"),
                     ("backend", "backend"), ("backend_options", "backendOptions"), ("threads", "threads")):
        value = getattr(args, arg)
        if value is not None:
            settings[key] = value
    engine = Px0(ROOT, settings) if args.engine == "px0" else Pikafish(ROOT)
    if args.engine == "pikafish" and args.threads is not None:
        engine.configure(threads=args.threads)
    report = {"engine": engine.name, "status": "failed", "tests": [], "plies": 0}
    try:
        engine.start()
        report["runtime_config"] = {"threads": engine.threads,
            "backend": settings.get("backend") if args.engine == "px0" else None,
            "backendOptions": settings.get("backendOptions") if args.engine == "px0" else None}
        report["executable_sha256"] = digest(engine.exe)
        report["network_sha256"] = digest(engine.network if args.engine == "px0" else engine.nnue)
        if args.engine == "px0":
            report["id"] = engine.engine_id
            report["uci"] = engine.handshake
            report["options"] = engine.options
            report["centipawn_enabled"] = engine.cp_enabled
        cases = [
            ("opening_red", Position().fen()),
            ("opening_black", Position().fen().replace(" w ", " b ")),
            ("check_escape", "3kr4/9/9/9/9/9/9/9/R8/4K4 w - - 0 1"),
            ("endgame_red", "3k5/9/9/9/4p4/9/9/9/9/4K4 w - - 0 1"),
            ("endgame_black", "3k5/9/9/9/4p4/9/9/9/9/4K4 b - - 0 1"),
        ]
        for name, fen in cases:
            position = Position(fen)
            side = position.red
            result = engine.analyze(fen, args.movetime, 3)
            assert result.best in legal_moves(position), (name, result.best)
            position.apply_unchecked(result.best)
            assert not in_check(position, side), name
            report["tests"].append({"case": name, "bestmove": result.best.ucci(),
                                    "pv_count": len(result.candidates), "depth": result.depth,
                                    "candidates": [{"move": c.move.ucci(), "score_cp": c.score_cp,
                                                    "mate": c.mate, "pv": c.pv}
                                                   for c in result.candidates]})
        for mode in ("depth", "nodes"):
            result = engine.analyze_limit(Position().fen(), mode, 2 if mode == "depth" else 32, 1)
            assert result.best in legal_moves(Position())
            report["tests"].append({"case": mode, "bestmove": result.best.ucci()})
        forced = engine.analyze(Position().fen(), args.movetime, 1, ["h2e2"])
        assert forced.best == Move.from_ucci("h2e2")
        report["tests"].append({"case": "searchmoves", "bestmove": forced.best.ucci()})
        engine.new_game()
        position = Position()
        history = []
        report["moves"] = []
        for ply in range(args.plies):
            if not legal_moves(position):
                report["terminal_at_ply"] = ply
                break
            history.append(position.copy())
            result = engine.analyze(position.fen(), args.movetime, 1)
            report["moves"].append({"fen": position.fen(), "bestmove": result.best.ucci()})
            assert position.apply(result.best), (ply, result.best)
            report["plies"] += 1
            if ply == 29:
                report["tests"].append({"case": "midgame", "fen": history[-1].fen(),
                                        "bestmove": result.best.ucci()})
            if ply % 10 == 9:
                print(f"{engine.name}: {ply+1} legal plies", flush=True)
        if history:
            position = history[-1]  # Undo restores a previous FEN, then search again.
            assert engine.analyze(position.fen(), args.movetime, 1).best in legal_moves(position)
        engine.new_game()
        assert engine.analyze(Position().fen(), args.movetime, 1).best in legal_moves(Position())
        report["status"] = "passed"
    except Exception as exc:
        report["error"] = str(exc)
    finally:
        engine.close()
        manager.close()
        report["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
        path = Path(args.report)
        if not path.is_absolute():
            path = ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
