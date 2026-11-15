import json,sqlite3
from pathlib import Path

ROOT=Path(__file__).resolve().parent
DB=ROOT/"data"/"master_book.sqlite"
BUILDING_DB=ROOT/"data"/"master_book.sqlite.building"
JSON_BOOK=ROOT/"data"/"master_book.json"

def _json_games():
    try:
        if JSON_BOOK.exists():
            rows=json.loads(JSON_BOOK.read_text(encoding="utf-8"))
            if isinstance(rows,list):return rows
    except:pass
    return []

def stats():
    importing=BUILDING_DB.exists()
    if importing:
        try:
            con=sqlite3.connect(BUILDING_DB,timeout=1)
            g=con.execute("select count(*) from games").fetchone()[0]
            p=con.execute("select count(*) from prefix_moves").fetchone()[0]
            con.close()
            return {"games":g,"prefix_rows":p,"source":"SQLite大师库","importing":True}
        except:pass
    if DB.exists():
        try:
            con=sqlite3.connect(DB)
            g=con.execute("select count(*) from games").fetchone()[0]
            p=con.execute("select count(*) from prefix_moves").fetchone()[0]
            con.close()
            return {"games":g,"prefix_rows":p,"source":"SQLite大师库","importing":False}
        except:pass
    rows=_json_games()
    return {"games":len(rows),"prefix_rows":0,"source":"内置小型参考","importing":importing}

def lookup(history,limit=4):
    key=" ".join(history)
    if DB.exists():
        try:
            con=sqlite3.connect(DB);con.row_factory=sqlite3.Row
            rows=con.execute("""
                select next_move,count(*) as n,
                       min(player_red) as red,min(player_black) as black,
                       min(event) as event
                from prefix_moves where prefix=?
                group by next_move order by n desc limit ?
            """,(key,limit)).fetchall()
            con.close()
            total=sum(r["n"] for r in rows)
            return [{
                "name":f"{r['red'] or '?'} vs {r['black'] or '?'}",
                "source":r["event"] or "大师实战库",
                "next":r["next_move"],"count":r["n"],"total":total,
                "matched":len(history)
            } for r in rows]
        except:pass
    out=[]
    for g in _json_games():
        ms=g.get("moves",[])
        if len(ms)>=len(history) and ms[:len(history)]==history:
            out.append({
                "name":g.get("name","大师实战"),
                "source":g.get("source","本地棋谱库"),
                "next":ms[len(history)] if len(ms)>len(history) else None,
                "count":1,"total":1,"matched":len(history)
            })
    return out[:limit]
