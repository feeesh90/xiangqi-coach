from engine import Pikafish,Candidate,Analysis
from rules import Move
p=Pikafish.__new__(Pikafish)
calls=[]
def fake(fen,movetime,multipv,searchmoves=None):
    calls.append((movetime,multipv))
    if multipv==1:
        a=Move.from_ucci('h2e2')
        return Analysis(a,[Candidate(1,a,['h2e2','h9g7'],25,30,None)],25,1.0)
    a=Move.from_ucci('c3c4'); b=Move.from_ucci('h2e2'); c=Move.from_ucci('h0g2')
    return Analysis(a,[Candidate(1,a,['c3c4'],16,20,None),Candidate(2,b,['h2e2'],16,15,None),Candidate(3,c,['h0g2'],16,10,None)],16,.5)
p.analyze=fake
r=Pikafish.analyze_coach(p,'fen',9000,True)
assert r.best.ucci()=='c3c4'
assert r.candidates[0].move.ucci()=='c3c4'
assert r.candidates[1].move.ucci()=='h2e2'
assert calls==[(9000,3)]
print('STRONG_MERGE_OK', [x.move.ucci() for x in r.candidates])
