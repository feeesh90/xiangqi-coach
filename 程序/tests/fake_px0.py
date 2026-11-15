"""Protocol fixture, NOT a Px0 binary or neural engine. Test-only legal moves."""
import os
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rules import Position, Move, legal_moves

pos = Position()
multi = 1
mode = os.environ.get("FAKE_PX0_MODE", "normal")
stopped = threading.Event()
workers = []
trace = os.environ.get("FAKE_PX0_TRACE")


def search(position, command):
    if mode == "crash":
        os._exit(7)
    if mode == "hang":
        time.sleep(60)
        return
    if mode == "slow":
        stopped.wait(5)
    moves = legal_moves(position)
    if " searchmoves " in command:
        allowed = command.split(" searchmoves ")[1].split()
        moves = [m for m in moves if m.ucci() in allowed]
    if mode == "illegal":
        print("bestmove a0a9", flush=True)
        return
    for rank, move in enumerate(moves[:multi], 1):
        probe = position.copy()
        probe.apply_unchecked(move)
        reply = legal_moves(probe)
        pv = move.ucci() + (" " + reply[0].ucci() if reply else "")
        if mode != "noscore":
            score = "mate -3" if mode == "mate" else f"cp {20-rank}"
            print(f"info depth 7 multipv {rank} score {score} pv {pv}", flush=True)
    print("bestmove " + (moves[0].ucci() if moves else "0000"), flush=True)


for raw in sys.stdin:
    command = raw.strip()
    if trace:
        with open(trace, "a", encoding="utf-8") as out:
            out.write(command + "\n")
    if command == "uci":
        print("id name " + ("Lc0" if mode == "wrongengine" else "Px0 protocol fixture"))
        print("option name WeightsFile type string default <autodiscover>")
        print("option name Backend type combo default cpu var cpu var cuda")
        print("option name BackendOptions type string default")
        print("option name Threads type spin default 2 min 1 max 8")
        if mode != "single":
            print("option name MultiPV type spin default 1 min 1 max 3")
        if mode != "noscore":
            print("option name ScoreType type combo default WDL_mu var WDL_mu var centipawn")
        print("uciok", flush=True)
    elif command.startswith("setoption name MultiPV value "):
        multi = int(command.split()[-1])
    elif command.startswith("position fen "):
        if mode == "badnet":
            print("error Network weights invalid", flush=True)
        else:
            pos = Position(command[len("position fen "):])
    elif command == "isready":
        print("readyok", flush=True)
    elif command.startswith("go "):
        stopped.clear()
        worker = threading.Thread(target=search, args=(pos.copy(), command), daemon=True)
        workers.append(worker)
        worker.start()
    elif command == "stop":
        stopped.set()
    elif command == "quit":
        break
