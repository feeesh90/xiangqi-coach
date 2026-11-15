from rules import Position,Move,legal_moves

p=Position()
moves=legal_moves(p)
assert len(moves)==44, len(moves)
assert Move.from_ucci("h2e2") in moves
assert Move.from_ucci("h0g2") in moves
assert Move.from_ucci("e0e1") in moves
assert Move.from_ucci("e0d0") not in moves
m=Move.from_ucci("h2e2")
assert m.ucci()=="h2e2"
p.apply(m)
assert " b " in p.fen()
print("RULES_OK",len(moves))
