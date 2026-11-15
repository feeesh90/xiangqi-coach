"""
将 UCCI/ICCS 风格的象棋 PGN 批量转换为 data/master_book.json。

支持正文里出现类似：
1. H2-E2 H9-G7
2. H0-G2 I9-H9

运行：
    py -3 scripts/import_master_pgn.py my_games.pgn
也可以一次传多个 PGN。
"""
import re,sys,json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"data"/"master_book.json"
MOVE_RE=re.compile(r"\b([A-Ia-i][0-9])[- ]?([A-Ia-i][0-9])\b")

def parse(path):
    text=Path(path).read_text(encoding="utf-8",errors="ignore")
    chunks=re.split(r"(?=\[Event\s)",text)
    games=[]
    for block in chunks:
        moves=[]
        for a,b in MOVE_RE.findall(block):
            moves.append((a+b).lower())
        if len(moves)<2:continue
        event=re.search(r'\[Event\s+"([^"]+)"\]',block)
        red=re.search(r'\[Red\s+"([^"]+)"\]',block) or re.search(r'\[White\s+"([^"]+)"\]',block)
        black=re.search(r'\[Black\s+"([^"]+)"\]',block)
        name=" vs ".join([x.group(1) for x in (red,black) if x]) or "大师实战"
        source=event.group(1) if event else Path(path).name
        games.append({"name":name,"source":source,"moves":moves})
    return games

existing=[]
if OUT.exists():
    try: existing=json.loads(OUT.read_text(encoding="utf-8"))
    except: existing=[]

for p in sys.argv[1:]:
    existing.extend(parse(p))

# 去重
seen=set();out=[]
for g in existing:
    k=tuple(g.get("moves",[]))
    if not k or k in seen:continue
    seen.add(k);out.append(g)

OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
print("MASTER_BOOK_GAMES",len(out))
