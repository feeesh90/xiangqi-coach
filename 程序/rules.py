from dataclasses import dataclass
from copy import deepcopy

START_FEN = "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1"

@dataclass(frozen=True)
class Move:
    fr: int
    fc: int
    tr: int
    tc: int

    def ucci(self):
        return f"{chr(97+self.fc)}{9-self.fr}{chr(97+self.tc)}{9-self.tr}"

    @staticmethod
    def from_ucci(s):
        if not s or not __import__("re").fullmatch(r"[a-i][0-9][a-i][0-9]", s.lower()):
            return None
        s=s.lower()
        return Move(9-int(s[1]), ord(s[0])-97, 9-int(s[3]), ord(s[2])-97)

class Position:
    def __init__(self, fen=START_FEN):
        self.b=[[""]*9 for _ in range(10)]
        self.red=True
        if not self.load_fen(fen):
            raise ValueError("Bad FEN")

    def copy(self):
        q=Position.__new__(Position)
        q.b=[row[:] for row in self.b]
        q.red=self.red
        return q

    @staticmethod
    def is_red(p):
        return bool(p) and p.isupper()

    def load_fen(self, fen):
        try:
            parts=fen.strip().split()
            rows=parts[0].split("/")
            if len(rows)!=10: return False
            board=[]
            for s in rows:
                row=[]
                for ch in s:
                    if ch.isdigit(): row += [""]*int(ch)
                    else: row.append(ch)
                if len(row)!=9:return False
                board.append(row)
            self.b=board
            self.red=(parts[1]=="w")
            return True
        except Exception:
            return False

    def fen(self):
        rows=[]
        for row in self.b:
            s=""; n=0
            for p in row:
                if not p:n+=1
                else:
                    if n:s+=str(n);n=0
                    s+=p
            if n:s+=str(n)
            rows.append(s)
        return "/".join(rows)+(" w - - 0 1" if self.red else " b - - 0 1")

    def apply_unchecked(self,m):
        self.b[m.tr][m.tc]=self.b[m.fr][m.fc]
        self.b[m.fr][m.fc]=""
        self.red=not self.red

    def apply(self,m):
        if m not in legal_moves(self): return False
        self.apply_unchecked(m); return True

def inside(r,c): return 0<=r<10 and 0<=c<9

def palace(red,r,c):
    return 3<=c<=5 and ((7<=r<=9) if red else (0<=r<=2))

def add_target(pos,out,red,fr,fc,tr,tc):
    if not inside(tr,tc): return
    t=pos.b[tr][tc]
    if not t or Position.is_red(t)!=red:
        out.append(Move(fr,fc,tr,tc))

def pseudo_moves(pos,r,c,king_face=True):
    p=pos.b[r][c]
    if not p:return []
    red=Position.is_red(p); t=p.upper(); out=[]

    if t in ("R","C"):
        for dr,dc in ((1,0),(-1,0),(0,1),(0,-1)):
            rr,cc=r+dr,c+dc; screen=False
            while inside(rr,cc):
                q=pos.b[rr][cc]
                if t=="R":
                    if not q: out.append(Move(r,c,rr,cc))
                    else:
                        if Position.is_red(q)!=red:out.append(Move(r,c,rr,cc))
                        break
                else:
                    if not screen:
                        if not q:out.append(Move(r,c,rr,cc))
                        else:screen=True
                    elif q:
                        if Position.is_red(q)!=red:out.append(Move(r,c,rr,cc))
                        break
                rr+=dr;cc+=dc

    elif t=="N":
        for dr,dc,lr,lc in (
            (-2,-1,-1,0),(-2,1,-1,0),(2,-1,1,0),(2,1,1,0),
            (-1,-2,0,-1),(1,-2,0,-1),(-1,2,0,1),(1,2,0,1)):
            rr,cc=r+lr,c+lc
            if inside(rr,cc) and not pos.b[rr][cc]:
                add_target(pos,out,red,r,c,r+dr,c+dc)

    elif t=="B":
        for dr,dc in ((-2,-2),(-2,2),(2,-2),(2,2)):
            tr,tc=r+dr,c+dc
            er,ec=r+dr//2,c+dc//2
            if not inside(tr,tc) or pos.b[er][ec]:continue
            if red and tr<5:continue
            if not red and tr>4:continue
            add_target(pos,out,red,r,c,tr,tc)

    elif t=="A":
        for dr,dc in ((-1,-1),(-1,1),(1,-1),(1,1)):
            tr,tc=r+dr,c+dc
            if palace(red,tr,tc):add_target(pos,out,red,r,c,tr,tc)

    elif t=="K":
        for dr,dc in ((-1,0),(1,0),(0,-1),(0,1)):
            tr,tc=r+dr,c+dc
            if palace(red,tr,tc):add_target(pos,out,red,r,c,tr,tc)
        if king_face:
            step=-1 if red else 1
            rr=r+step
            while inside(rr,c):
                q=pos.b[rr][c]
                if q:
                    if q.upper()=="K" and Position.is_red(q)!=red:
                        out.append(Move(r,c,rr,c))
                    break
                rr+=step

    elif t=="P":
        f=-1 if red else 1
        add_target(pos,out,red,r,c,r+f,c)
        crossed=(r<=4 if red else r>=5)
        if crossed:
            add_target(pos,out,red,r,c,r,c-1)
            add_target(pos,out,red,r,c,r,c+1)
    return out

def in_check(pos,red):
    kr=kc=-1
    for r in range(10):
        for c in range(9):
            q=pos.b[r][c]
            if q and q.upper()=="K" and Position.is_red(q)==red:
                kr,kc=r,c
    if kr<0:return True

    # kings face
    step=-1 if red else 1
    rr=kr+step
    while inside(rr,kc):
        q=pos.b[rr][kc]
        if q:
            if q.upper()=="K" and Position.is_red(q)!=red:return True
            break
        rr+=step

    # enemy attacks
    for r in range(10):
        for c in range(9):
            q=pos.b[r][c]
            if not q or Position.is_red(q)==red:continue
            for m in pseudo_moves(pos,r,c,king_face=False):
                if m.tr==kr and m.tc==kc:return True
    return False

def legal_moves(pos):
    out=[]; red=pos.red
    for r in range(10):
        for c in range(9):
            p=pos.b[r][c]
            if p and Position.is_red(p)==red:
                for m in pseudo_moves(pos,r,c):
                    n=pos.copy();n.apply_unchecked(m)
                    if not in_check(n,red):out.append(m)
    return out

def legal_from(pos,r,c):
    return [m for m in legal_moves(pos) if m.fr==r and m.fc==c]

PIECE_CN={
    "R":"车","N":"马","B":"相","A":"仕","K":"帅","C":"炮","P":"兵",
    "r":"车","n":"马","b":"象","a":"士","k":"将","c":"炮","p":"卒"
}

def piece_name(p): return PIECE_CN.get(p,"棋子")


def explain_move(pos,m):
    p=pos.b[m.fr][m.fc]; target=pos.b[m.tr][m.tc]
    parts=[]
    if target:
        parts.append(f"这步直接处理对方的{piece_name(target)}。")
    t=p.upper()
    if t=="N":
        parts.append("马落到新的控制点，通常是在发展子力、争夺中心或配合车炮。")
    elif t=="R":
        parts.append("车进入更开放的横线或纵线，扩大活动范围和压制力。")
    elif t=="C":
        parts.append("炮调整炮架与线路，为牵制、打击或中路控制做准备。")
    elif t=="P":
        parts.append("兵卒推进是在抢空间，同时限制对方子力的活动。")
    elif t in ("A","B"):
        parts.append("这一步主要改善阵型和王将安全，同时保持子力协调。")
    elif t=="K":
        parts.append("这是王将安全相关的处理；如果开局阶段出现，应该结合主变化确认是否有具体战术原因。")
    else:
        parts.append("这是引擎在当前局面搜索后的主选择。")
    return "".join(parts)


CN_RED = {1:"一",2:"二",3:"三",4:"四",5:"五",6:"六",7:"七",8:"八",9:"九"}
CN_BLACK = {1:"1",2:"2",3:"3",4:"4",5:"5",6:"6",7:"7",8:"8",9:"9"}

def _file_num(red,c):
    return 9-c if red else c+1

def _num_text(red,n):
    return CN_RED.get(n,str(n)) if red else CN_BLACK.get(n,str(n))

def chinese_notation(pos,m):
    """
    Convert a legal UCCI move to common Xiangqi notation.
    Examples:
      炮二平五
      马8进7
    For same-file duplicate pieces, use 前/后/中 when possible.
    """
    p=pos.b[m.fr][m.fc]
    if not p:return m.ucci()
    red=p.isupper()
    typ=p.upper()
    name=piece_name(p)

    # same type, same file: try 前/后/中
    same=[]
    for r in range(10):
        q=pos.b[r][m.fc]
        if q and q.upper()==typ and q.isupper()==red:
            same.append((r,m.fc))
    prefix=None
    if len(same)>=2:
        if red:
            ordered=sorted(same,key=lambda x:x[0])   # smaller row = front
        else:
            ordered=sorted(same,key=lambda x:-x[0]) # larger row = front for black
        try:
            idx=ordered.index((m.fr,m.fc))
            if idx==0:prefix="前"
            elif idx==len(ordered)-1:prefix="后"
            elif len(ordered)%2==1 and idx==len(ordered)//2:prefix="中"
        except ValueError:
            pass

    if prefix:
        head=prefix+name
    else:
        head=name+_num_text(red,_file_num(red,m.fc))

    if m.tr==m.fr:
        action="平"
    else:
        forward=(m.tr<m.fr) if red else (m.tr>m.fr)
        action="进" if forward else "退"

    if typ in ("N","B","A") or action=="平":
        value=_file_num(red,m.tc)
    else:
        value=abs(m.tr-m.fr)

    return head+action+_num_text(red,value)

# override richer move description used by the UI/coaching layer
def move_desc(pos,m):
    p=pos.b[m.fr][m.fc]
    return f"{chinese_notation(pos,m)}（{m.ucci()[:2]} → {m.ucci()[2:]}）"
