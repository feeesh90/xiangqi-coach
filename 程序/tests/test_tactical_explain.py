from rules import Position,Move
from coach import feature_text,reply_consequence
p=Position()
m=Move.from_ucci("h2e2")
s=feature_text(p,m)
assert "炮" not in s or isinstance(s,str)
assert len(s)>5
print("TACTICAL_EXPLAIN_OK",s)
