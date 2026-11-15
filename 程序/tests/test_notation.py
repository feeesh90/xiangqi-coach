from rules import Position,Move,chinese_notation,move_desc
p=Position()
assert chinese_notation(p,Move.from_ucci("h2e2"))=="炮二平五",chinese_notation(p,Move.from_ucci("h2e2"))
p.apply(Move.from_ucci("h2e2"))
assert chinese_notation(p,Move.from_ucci("h9g7"))=="马8进7",chinese_notation(p,Move.from_ucci("h9g7"))
print("NOTATION_OK",move_desc(Position(),Move.from_ucci("h2e2")))
