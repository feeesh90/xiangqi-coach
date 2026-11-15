from engine import Candidate,Analysis
from rules import Position,Move
from coach import candidate_report
p=Position()
c=Candidate(1,Move.from_ucci("h2e2"),["h2e2","h9g7","h0g2"],20,10,None)
s=candidate_report(p,c,1,True)
assert "首选方案" in s
assert "炮二平五" in s
assert "对方最强回应" in s
print("CANDIDATE_REPORT_OK")
