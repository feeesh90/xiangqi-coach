import re
from rules import Position, Move, in_check, pseudo_moves

NUMS={
    "一":1,"二":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9,
    "１":1,"２":2,"３":3,"４":4,"５":5,"６":6,"７":7,"８":8,"９":9,
    "1":1,"2":2,"3":3,"4":4,"5":5,"6":6,"7":7,"8":8,"9":9
}
PIECES={
    "車":"R","车":"R","俥":"R",
    "馬":"N","马":"N","傌":"N",
    "炮":"C","砲":"C",
    "相":"B","象":"B",
    "仕":"A","士":"A",
    "帥":"K","帅":"K","將":"K","将":"K",
    "兵":"P","卒":"P",
}
ACTION={"進":"进","进":"进","退":"退","平":"平"}

def file_to_col(red,n):
    return 9-n if red else n-1

def normalize_token(s):
    return (s.strip().replace("　","").replace(" ","")
            .replace("進","进").replace("帥","帅").replace("將","将"))

def _direction_ok(red,m,action):
    dr=m.tr-m.fr
    if action=="平": return dr==0
    if action=="进": return dr<0 if red else dr>0
    if action=="退": return dr>0 if red else dr<0
    return False

def _legal_candidates(pos,sources,typ):
    """Generate only moves for the pieces named by one notation token.

    CCPD contains millions of plies.  Building every legal move for the whole
    board for each token made an import take hours.  This keeps the same
    self-check test while limiting pseudo-move generation to matching pieces.
    """
    red=pos.red
    out=[]
    for r,c in sources:
        p=pos.b[r][c]
        if not p or p.upper()!=typ or p.isupper()!=red:
            continue
        for m in pseudo_moves(pos,r,c):
            after=pos.copy()
            after.apply_unchecked(m)
            if not in_check(after,red):
                out.append(m)
    return out

def parse_chinese_move(pos,raw):
    s=normalize_token(raw)
    if len(s)<4:return None
    red=pos.red

    if s[0] in PIECES and s[1] in NUMS and s[2] in ACTION and s[3] in NUMS:
        typ=PIECES[s[0]]
        src_col=file_to_col(red,NUMS[s[1]])
        action=ACTION[s[2]]
        dest=NUMS[s[3]]
        sources=[(r,src_col) for r in range(10)]
        cand=[]
        for m in _legal_candidates(pos,sources,typ):
            if not _direction_ok(red,m,action):continue
            if typ in ("N","B","A"):
                if m.tc!=file_to_col(red,dest):continue
            elif action=="平":
                if m.tc!=file_to_col(red,dest):continue
            else:
                if abs(m.tr-m.fr)!=dest:continue
            cand.append(m)
        return cand[0] if len(cand)==1 else None

    if s[0] in ("前","后","中") and s[1] in PIECES and s[2] in ACTION and s[3] in NUMS:
        prefix=s[0];typ=PIECES[s[1]];action=ACTION[s[2]];dest=NUMS[s[3]]
        groups={}
        for r in range(10):
            for c in range(9):
                p=pos.b[r][c]
                if p and p.upper()==typ and p.isupper()==red:
                    groups.setdefault(c,[]).append((r,c))
        sources=[]
        for c,arr in groups.items():
            if len(arr)<2:continue
            arr=sorted(arr,key=(lambda rc:rc[0]) if red else (lambda rc:-rc[0]))
            if prefix=="前":sources.append(arr[0])
            elif prefix=="后":sources.append(arr[-1])
            elif prefix=="中" and len(arr)%2==1:sources.append(arr[len(arr)//2])

        cand=[]
        for m in _legal_candidates(pos,sources,typ):
            if not _direction_ok(red,m,action):continue
            if typ in ("N","B","A"):
                if m.tc!=file_to_col(red,dest):continue
            elif action=="平":
                if m.tc!=file_to_col(red,dest):continue
            else:
                if abs(m.tr-m.fr)!=dest:continue
            cand.append(m)
        return cand[0] if len(cand)==1 else None
    return None

TOKEN_RE=re.compile(
    r"(?:[前后中]?[車车俥馬马傌炮砲相象仕士帥帅將将兵卒]"
    r"[一二三四五六七八九１２３４５６７８９1-9]?"
    r"[進进退平][一二三四五六七八九１２３４５６７８９1-9])"
)

def parse_pgn_text(text):
    headers={}
    for k,v in re.findall(r'\[([A-Za-z]+)\s+"([^"]*)"\]',text):
        headers[k]=v
    fen=headers.get("FEN") or Position().fen()
    pos=Position(fen)
    body=re.sub(r"\[[^\]]*\]"," ",text)
    tokens=TOKEN_RE.findall(body)
    moves=[];bad=[]
    for tok in tokens:
        m=parse_chinese_move(pos,tok)
        if not m:
            bad.append((len(moves)+1,tok,pos.fen()))
            break
        moves.append(m.ucci())
        pos.apply_unchecked(m)
    return headers,moves,bad
