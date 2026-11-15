from pathlib import Path
from chinese_pgn import parse_pgn_text
text=Path("tests/sample_cn.pgn").read_text(encoding="utf-8")
h,m,bad=parse_pgn_text(text)
assert not bad,bad
assert len(m)==8,m
assert m[0]=="h2e2",m
assert m[1]=="b9c7",m
assert m[2]=="h0g2",m
print("CHINESE_PGN_OK",m)
