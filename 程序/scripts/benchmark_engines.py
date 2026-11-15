"""Reproducible real-engine benchmark; never writes the user's engine settings.

Every sample clears engine search state, warms the runtime, disables the GUI
result cache, then measures a real search. Raw UCI counters retain their native
meaning and are comparable only within the same engine/model.
"""
import argparse
import cProfile
import csv
import importlib.util
import json
import platform
from pathlib import Path
import queue
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine import Pikafish
from px0_engine import Px0
from rules import Move, Position, legal_moves


class ObservedQueue(queue.Queue):
    def reset_metrics(self):
        self.metrics = {}
        self.bestmove_at = None
        self.info_count = 0

    def put(self, item, *args, **kwargs):
        _, line = item
        if line:
            if line.startswith("bestmove "):
                self.bestmove_at = time.perf_counter()
            elif line.startswith("info ") and not line.startswith("info string "):
                self.info_count += 1
                for key, val in re.findall(r"\b(nodes|nps|time|depth|seldepth) (\d+)", line):
                    self.metrics[key] = int(val)
        return super().put(item, *args, **kwargs)


def create_engine(profile):
    if profile["engine"] == "pikafish":
        engine = Pikafish(ROOT)
        engine.configure(profile["threads"], profile.get("hash_mb", 512))
    else:
        cls = Px0
        if profile.get("original_adapter"):
            path = ROOT / "data/backups/20261017_flip_creative_speed/px0_engine.py"
            spec = importlib.util.spec_from_file_location("original_px0", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            cls = module.Px0
        engine = cls(ROOT, profile)
    q = ObservedQueue()
    q.reset_metrics()
    engine._stdout_queue = q
    original_send = engine._send
    def send(command):
        if command.startswith("go "):
            q.go_at = time.perf_counter()
        return original_send(command)
    engine._send = send
    return engine, q


def one_sample(engine, observed, case, budget, mode="time", profile=False):
    engine.new_game()
    # A real one-node search warms model/provider initialization and the same
    # FEN's tensor shapes. Clear search/hash state again before measurement.
    engine.analyze_limit(case["fen"], "nodes", 1, 1)
    engine.new_game()
    engine.cache.clear()
    observed.reset_metrics()
    observed.go_at = None
    profiler = cProfile.Profile() if profile else None
    started = time.perf_counter()
    if profiler:
        profiler.enable()
    result = engine.analyze_limit(case["fen"], mode, budget, 1)
    if profiler:
        profiler.disable()
    finished = time.perf_counter()
    assert result.best in legal_moves(Position(case["fen"]))
    selected = result.candidates[0] if result.candidates else None
    data = {
        "case": case["id"], "category": case["category"], "fen": case["fen"],
        "budget": budget, "mode": mode,
        "elapsed_ms": round((finished-started)*1000, 3),
        "setup_ms": round((observed.go_at-started)*1000, 3) if observed.go_at else None,
        "engine_wall_ms": round((observed.bestmove_at-observed.go_at)*1000, 3)
            if observed.bestmove_at and observed.go_at else None,
        "after_bestmove_ms": round((finished-observed.bestmove_at)*1000, 3)
            if observed.bestmove_at else None,
        "info_count": observed.info_count, "counters": dict(observed.metrics),
        "bestmove": result.best.ucci(), "depth": result.depth,
        "score_cp": selected.score_cp if selected else None,
        "mate": selected.mate if selected else None,
        "pv": selected.pv if selected else [], "legal": True,
        "acceptable": case.get("acceptable"),
        "tactic_pass": result.best.ucci() in case["acceptable"] if case.get("acceptable") else None,
    }
    if profiler:
        profiler.create_stats()
        data["profile"] = sorted([
            {"function": f"{key[0]}:{key[1]}:{key[2]}", "calls": val[1],
             "self_seconds": val[2], "cumulative_seconds": val[3]}
            for key, val in profiler.stats.items()
        ], key=lambda x: x["cumulative_seconds"], reverse=True)[:30]
    return data


def save(path, output):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    with path.with_suffix(".csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=[
            "profile", "case", "repeat", "budget", "elapsed_ms", "engine_wall_ms",
            "after_bestmove_ms", "nodes", "nps", "depth", "bestmove", "score_cp", "tactic_pass"])
        writer.writeheader()
        for sample in output["samples"]:
            writer.writerow({key: sample.get(key, sample.get("counters", {}).get(key))
                             for key in writer.fieldnames})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", required=True, type=Path)
    parser.add_argument("--cases", type=Path, default=ROOT/"tests/fixtures/performance_positions.json")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--times", default="7000")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args()
    profiles = json.loads(args.profiles.read_text(encoding="utf-8"))
    cases = json.loads(args.cases.read_text(encoding="utf-8"))[:args.limit]
    for case in cases:
        assert legal_moves(Position(case["fen"])), case
    output = {"python": sys.version, "platform": platform.platform(), "profiles": profiles,
              "method": "warm runtime; new_game between trials; one-node same-FEN warmup; no GUI cache",
              "started": time.strftime("%Y-%m-%d %H:%M:%S"), "samples": [], "cold_start_ms": {}}
    for profile in profiles:
        engine, observed = create_engine(profile)
        try:
            cold = time.perf_counter()
            engine.start()
            engine.analyze_limit(cases[0]["fen"], "nodes", 1, 1)
            output["cold_start_ms"][profile["id"]] = round((time.perf_counter()-cold)*1000, 3)
            for budget in map(int, args.times.split(",")):
                for trial in range(args.repeat):
                    for case in cases:
                        sample = one_sample(engine, observed, case, budget, profile=args.profile)
                        sample.update(profile=profile["id"], repeat=trial+1)
                        output["samples"].append(sample)
                        save(args.output, output)
                        print(f'{profile["id"]} {case["id"]} r{trial+1} {budget}ms: '
                              f'{sample["elapsed_ms"]}ms nodes={sample["counters"].get("nodes")} '
                              f'best={sample["bestmove"]}', flush=True)
        finally:
            engine.close()
    output["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save(args.output, output)


if __name__ == "__main__":
    main()
