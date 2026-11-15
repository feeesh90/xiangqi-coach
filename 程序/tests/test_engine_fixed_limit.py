import subprocess
import sys
import threading

from engine import Pikafish


def test_fixed_depth_parses_score_and_keeps_bestmove_first():
    process = subprocess.Popen(
        [sys.executable, "tests/fake_engine.py"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    engine = Pikafish.__new__(Pikafish)
    engine.p = process
    engine.ready = True
    engine.lock = threading.RLock()
    engine.write_lock = threading.Lock()
    engine.current_multipv = None
    engine.cancel_generation = 0

    try:
        result = engine._analyze_go("fen", "depth", 8, multipv=2)
        assert result.best.ucci() == "h2e2"
        assert result.candidates[0].move.ucci() == "h2e2"
        assert result.candidates[0].score_cp == 20
        assert result.candidates[1].score_cp == 10
    finally:
        process.stdin.write("quit\n")
        process.stdin.flush()
        process.wait(timeout=2)
