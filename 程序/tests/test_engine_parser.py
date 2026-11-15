import sys,subprocess,re
from rules import Move

p=subprocess.Popen([sys.executable,"tests/fake_engine.py"],
                   stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                   text=True,bufsize=1)

def send(s):
    p.stdin.write(s+"\n");p.stdin.flush()

send("uci")
assert p.stdout.readline().strip()=="id name FakePika"
assert p.stdout.readline().strip()=="uciok"
send("isready")
assert p.stdout.readline().strip()=="readyok"
send("go movetime 100 searchmoves h2e2")
lines=[p.stdout.readline().strip() for _ in range(3)]
assert "multipv 1" in lines[0]
assert "score cp 20" in lines[0]
assert Move.from_ucci(lines[2].split()[1]).ucci()=="h2e2"
send("quit");p.wait(timeout=2)
print("ENGINE_PROTOCOL_SCORE_SEARCHMOVES_OK")
