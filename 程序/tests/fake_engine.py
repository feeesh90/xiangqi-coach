import sys,time
for line in sys.stdin:
    s=line.strip()
    if s=="uci":
        print("id name FakePika");print("uciok");sys.stdout.flush()
    elif s=="isready":
        print("readyok");sys.stdout.flush()
    elif s.startswith("go "):
        print("info depth 16 multipv 1 score cp 20 pv h2e2 h9g7 h0g2 i9h9")
        print("info depth 16 multipv 2 score cp 10 pv c3c4 h9g7")
        print("bestmove h2e2");sys.stdout.flush()
    elif s=="quit":
        break
