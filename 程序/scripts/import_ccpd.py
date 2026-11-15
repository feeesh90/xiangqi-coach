import os, sys, time, sqlite3, multiprocessing as mp
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from chinese_pgn import parse_pgn_text


def _read_text(p):
    raw = p.read_bytes()
    for enc in ("big5", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def parse_one(path):
    """Worker: 返回 (status, path, headers_dict, moves_str)。status 1=ok 0=bad。"""
    try:
        text = _read_text(Path(path))
        h, moves, errors = parse_pgn_text(text)
        if len(moves) < 4:
            return (0, path, None, None)
        return (1, path, h, " ".join(moves))
    except Exception:
        return (0, path, None, None)


def publish_database(building, out):
    """Publish a complete database without leaving an old WAL beside it."""
    if out.exists():
        old = sqlite3.connect(out, timeout=10)
        try:
            old.execute("pragma wal_checkpoint(truncate)")
        finally:
            old.close()
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(out) + suffix)
        try:
            sidecar.unlink()
        except FileNotFoundError:
            pass
    for attempt in range(20):
        try:
            os.replace(building, out)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.5)


def main():
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python import_ccpd.py <dataset_repo_path>")
    repo = Path(sys.argv[1])
    base = repo / "Dataset" / "對局" / "大師對局"
    if not base.exists():
        base = repo / "Dataset" / "对局" / "大师对局"
    if not base.exists():
        raise SystemExit("Cannot find Dataset/對局/大師對局")

    files = [str(p) for p in base.rglob("*.pgn")]
    print("total pgn files:", len(files), flush=True)

    out = ROOT / "data" / "master_book.sqlite"
    building = ROOT / "data" / "master_book.sqlite.building"
    if building.exists():
        building.unlink()
    # Build beside the live database and replace it only after a complete
    # import.  An interrupted run therefore never destroys the usable book.
    con = sqlite3.connect(building)
    con.executescript("""
    pragma journal_mode=WAL;
    pragma synchronous=NORMAL;
    pragma temp_store=MEMORY;
    create table games(
     id integer primary key,path text,event text,date text,
     player_red text,player_black text,result text,ecco text,moves text
    );
    create table prefix_moves(
     prefix text,next_move text,game_id integer,
     player_red text,player_black text,event text
    );
    create index idx_prefix on prefix_moves(prefix);
    """)

    seen = set()
    ok = bad = dup = processed = 0
    ncpu = max(1, min(12, (mp.cpu_count() or 4) - 1))
    print("worker processes:", ncpu, flush=True)
    started = time.monotonic()
    with mp.Pool(ncpu) as pool:
        for status, path, h, moves_str in pool.imap_unordered(parse_one, files, chunksize=100):
            processed += 1
            if status == 0:
                bad += 1
            elif moves_str in seen:
                dup += 1
            else:
                seen.add(moves_str)
                h = h or {}
                moves = moves_str.split(" ")
                cur = con.execute(
                    """insert into games(path,event,date,player_red,player_black,result,ecco,moves)
                       values(?,?,?,?,?,?,?,?)""",
                    (path, h.get("Event", ""), h.get("Date", ""),
                     h.get("Red", ""), h.get("Black", ""), h.get("Result", ""),
                     h.get("ECCO", ""), moves_str),
                )
                gid = cur.lastrowid
                prefix_rows = []
                for ply in range(min(40, len(moves))):
                    prefix_rows.append(
                        (" ".join(moves[:ply]), moves[ply], gid,
                         h.get("Red", ""), h.get("Black", ""), h.get("Event", ""))
                    )
                con.executemany(
                    """insert into prefix_moves(prefix,next_move,game_id,player_red,player_black,event)
                       values(?,?,?,?,?,?)""",
                    prefix_rows,
                )
                ok += 1
            if processed % 500 == 0:
                con.commit()
                elapsed = max(0.001, time.monotonic() - started)
                rate = processed / elapsed
                eta = int((len(files) - processed) / rate) if rate else 0
                print(
                    "processed", processed, "/", len(files),
                    f"({processed/len(files):.1%})", "games", ok,
                    "bad", bad, "dup", dup, "eta_seconds", eta,
                    flush=True,
                )
    con.commit()
    con.execute("vacuum")
    con.execute("analyze")
    con.commit()
    con.execute("pragma wal_checkpoint(truncate)")
    con.close()
    publish_database(building, out)
    print("MASTER_DB_READY", out)
    print("games", ok, "bad", bad, "duplicates_skipped", dup)


if __name__ == "__main__":
    main()
