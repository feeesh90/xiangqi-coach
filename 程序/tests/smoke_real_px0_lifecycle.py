"""Destructive fault injection limited to engines created by this test."""
from pathlib import Path
import argparse
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine_manager import EngineManager
from px0_engine import Px0, SearchCancelled, NoLegalMoves
from rules import Position, legal_moves

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--gpu", action="store_true")
args = parser.parse_args()

with tempfile.TemporaryDirectory() as temp:
    manager = EngineManager(ROOT, Path(temp) / "settings.json")
    if args.gpu:
        manager.settings["px0"].update(
            enginePath="engine/px0-gpu/px0.exe", networkPath="engine/px0/network.pb.gz",
            backend="onnx-cuda", backendOptions="fp16=false", threads=1)
    processes = []
    try:
        manager.switch("px0")
        engine = manager._engine
        process = engine.p
        processes.append(process)
        engine.start()
        assert engine.p is process
        outcome = []
        def search():
            try:
                manager.analyze(Position().fen(), 30000, 1)
            except Exception as exc:
                outcome.append(exc)
        worker = threading.Thread(target=search)
        worker.start()
        time.sleep(.3)
        manager.stop()
        worker.join(5)
        assert not worker.is_alive() and isinstance(outcome[0], SearchCancelled)
        assert manager.analyze(Position().fen(), 100, 1).best in legal_moves(Position())
        processes.append(engine.p)
        engine.p.kill()  # Simulate a real unexpected child exit.
        engine.p.wait(5)
        assert manager.analyze(Position().fen(), 100, 1).best in legal_moves(Position())
        processes.append(engine.p)
        manager.switch("pikafish", {"threads": 1, "hash_mb": 16})
        assert all(p.poll() is not None for p in processes)
        bad = Path(temp) / "bad.pb.gz"
        bad.write_bytes(b"invalid neural network")
        try:
            manager.switch("px0", {"networkPath": str(bad)})
        except Exception:
            pass
        else:
            raise AssertionError("Corrupt model accepted")
        assert manager.name == "Pikafish"
        assert manager.analyze(Position().fen(), 100, 1).best in legal_moves(Position())
        manager.switch("px0")
        processes.append(manager._engine.p)
        manager.new_game()
        assert manager.analyze(Position().fen(), 100, 1).best in legal_moves(Position())
        for fen in (
            "3k5/9/9/9/9/9/9/9/3rrr3/4K4 w - - 0 1",
            "3k5/9/9/9/9/9/9/3r1r3/r8/4K4 w - - 0 1",
        ):
            try:
                manager.analyze(fen, 100, 1)
            except NoLegalMoves:
                pass
            else:
                raise AssertionError("Terminal game must not make a move")
        print(("REAL_PX0_GPU" if args.gpu else "REAL_PX0_CPU")+
              "_CANCEL_CRASH_RECOVERY_BAD_MODEL_ROLLBACK_RESTART_OK")
    finally:
        manager.close()
        assert all(p.poll() is not None for p in processes)
