"""Track only visually verified single moves from a user-confirmed reference."""
import time
import numpy as np
import cv2
from rules import legal_moves


def warp(image,corners):
    rgb=np.asarray(image.convert("RGB"))
    pts=np.asarray(corners,dtype=np.float32)
    matrix=cv2.getPerspectiveTransform(pts,np.array([[40,40],[552,40],[552,616],[40,616]],np.float32))
    return cv2.warpPerspective(rgb,matrix,(592,656))


def patches(image,red_bottom,centers=None):
    out=[]
    for r in range(10):
        for c in range(9):
            vr,vc=(r,c) if red_bottom else (9-r,8-c)
            x,y=40+vc*64,40+vr*64
            if centers and r*9+c in centers:x,y=(round(v) for v in centers[r*9+c])
            roi=image[y-17:y+18,x-17:x+18]
            out.append(cv2.resize(roi,(20,20)).astype(np.float32)/255)
    return np.asarray(out)


def _rgb_error(a,b):
    # Compensate modest lighting changes, not arbitrary colour/texture changes.
    delta=a-b
    shift=np.clip(np.mean(delta,axis=(-3,-2),keepdims=True),-.08,.08)
    return np.mean(abs(delta-shift),axis=(-3,-2,-1))


def _glyph(patch):
    gray=cv2.cvtColor(patch,cv2.COLOR_RGB2GRAY)
    return gray-cv2.GaussianBlur(gray,(0,0),1.6)


def _body_error(current,reference):
    # The inner glyph excludes the external selection/move rings. Normalised
    # local correlation tolerates lighting and a few pixels of grid-fit error.
    ref=_glyph(reference)[3:-3,3:-3]
    if float(np.std(ref))<.008:
        return float(_rgb_error(current,reference))
    shape=(1-float(cv2.matchTemplate(_glyph(current),ref,cv2.TM_CCOEFF_NORMED).max()))/2
    # Keep red/black evidence separate from the luminance-only glyph.
    def ink_colour(p):
        core=p[3:-3,3:-3]
        gray=cv2.cvtColor(core,cv2.COLOR_RGB2GRAY)
        ink=core[gray<=np.percentile(gray,35)]
        return np.mean(ink[:,0]-ink[:,1])
    colour=abs(float(ink_colour(current)-ink_colour(reference)))
    return max(shape,colour*.8)


def _round_objects(image,red_bottom):
    gray=cv2.GaussianBlur(cv2.cvtColor(image,cv2.COLOR_RGB2GRAY),(5,5),0)
    circles=cv2.HoughCircles(gray,cv2.HOUGH_GRADIENT,1,40,param1=100,param2=22,minRadius=14,maxRadius=32)
    cells={}
    if circles is not None:
        for x,y,_ in circles[0]:
            c,r=round((x-40)/64),round((y-40)/64)
            if 0<=r<10 and 0<=c<9 and ((x-40-c*64)**2+(y-40-r*64)**2)**.5<13:
                if not red_bottom:r,c=9-r,8-c
                cells[r*9+c]=(float(x),float(y))
    return cells


class MoveTracker:
    def __init__(self,position,image,corners,red_bottom,piece_verifier=None):
        self.position=position.copy();self.corners=corners;self.red_bottom=red_bottom
        aligned=warp(image,corners)
        pieces={r*9+c for r,row in enumerate(position.b) for c,p in enumerate(row) if p}
        objects=_round_objects(aligned,red_bottom)
        self.round_pieces=bool(pieces) and len(pieces & objects.keys())>=.8*len(pieces)
        self.reference=patches(aligned,red_bottom,objects if self.round_pieces else None)
        self.reference_grid=patches(aligned,red_bottom)
        self.reference_image=aligned;self.reference_centers=objects
        self.piece_verifier=piece_verifier;self.verified_reference={}
        self.previous=None
        self.pending=None
        self.candidate=None;self.uncertain_since=None;self.checking_since=None
        self.requires_resync=False;self.gap_candidate=None;self.gap_diagnostics={}

    def _compare(self,current,circles=None,grid=None,image=None,objects=None):
        if grid is None:grid=current
        board=[piece for row in self.position.b for piece in row]
        empty=self.reference[[i for i,p in enumerate(board) if not p]]
        raw=np.minimum(_rgb_error(current,self.reference),_rgb_error(grid,self.reference_grid))
        same=raw<.025
        known=self.reference[[i for i,p in enumerate(board) if p]]
        background={}
        measured={}
        def center(i,locations):
            r,c=divmod(i,9)
            if not self.red_bottom:r,c=9-r,8-c
            return tuple(round(v) for v in (locations or {}).get(i,(40+c*64,40+r*64)))
        def verified(a,b,piece):
            # Independent classification is a fallback, not a relaxed template
            # threshold. Both the confirmed source and the observed destination
            # must agree, with a real round body at the destination.
            if self.piece_verifier is None or image is None or circles is None or b not in circles:return False
            key=(a,piece)
            try:
                if key not in self.verified_reference:
                    self.verified_reference[key]=self.piece_verifier(self.reference_image,*center(a,self.reference_centers))
                if b not in measured:measured[b]=self.piece_verifier(image,*center(b,objects))
                src,dst=self.verified_reference[key],measured[b]
                return bool(src and dst and src[0]==dst[0]==piece and min(src[1],dst[1])>=.95)
            except Exception:
                # Missing/unavailable verifier leaves strict visual matching in
                # place; it must never crash or make a guessed move.
                return False
        def observed_piece(i):
            if circles is None or i not in circles:return None
            if self.piece_verifier is not None and image is not None:
                try:
                    if i not in measured:measured[i]=self.piece_verifier(image,*center(i,objects))
                    value=measured[i]
                    if value and value[0] in 'KABNRCPkabnrcp' and value[1]>=.95:return value[0]
                except Exception:pass
            # A unique trusted glyph identity is also positive visual evidence.
            labels={piece for j,piece in enumerate(board) if piece and
                min(_body_error(current[i],self.reference[j]),_body_error(grid[i],self.reference_grid[j]))<.075}
            return next(iter(labels)) if len(labels)==1 else None
        def empty_at(i):
            if i not in background:
                error=float(_rgb_error(current[i],empty).min()) if len(empty) else 1
                # Circle absence alone is insufficient: retain any matching
                # trusted glyph. Used only after the reference confirms that
                # this skin has round bodies, never for arbitrary screenshots.
                no_body=(circles is not None and i not in circles
                    and not any(_body_error(current[i],ref)<.12 for ref in known))
                background[i]=(error,error<.085 or no_body)
            return background[i]
        for i,piece in enumerate(board):
            if piece:
                if not same[i]:same[i]=(min(_body_error(current[i],self.reference[i]),_body_error(grid[i],self.reference_grid[i]))<.12 or verified(i,i,piece))
            elif not same[i]:
                error,is_empty=empty_at(i)
                same[i]=error<.065 or (is_empty and circles is not None and i not in circles)
        matches=[];errors=[]
        for m in legal_moves(self.position):
            a,b=m.fr*9+m.fc,m.tr*9+m.tc
            if same[a] or same[b]:continue
            rest=same.copy();rest[a]=rest[b]=True
            if not rest.all():continue
            dest=min(_body_error(current[b],self.reference[a]),_body_error(grid[b],self.reference_grid[a]))
            vacated,is_empty=empty_at(a)
            errors.append((dest,vacated))
            if is_empty and (dest<.12 or verified(a,b,board[a])):matches.append((m,dest,vacated))
        evidence=None
        if not matches and not same.all():
            observed=board[:];vacated=0;complete=True
            for i in np.flatnonzero(~same):
                if circles is not None and i in circles:
                    piece=observed_piece(i)
                    if piece is None:complete=False;break
                    observed[i]=piece
                else:
                    error,_=empty_at(i)
                    # Neither elapsed time nor a missing detection proves an
                    # empty square. Require an actual trusted-background match.
                    if error>=.085:complete=False;break
                    observed[i]='';vacated+=bool(board[i])
            if complete and (int((~same).sum())>=3 or vacated>=2):evidence=tuple(observed)
        return same,raw,matches,errors,evidence

    def _single_successor(self,board):
        for move in legal_moves(self.position):
            p=self.position.copy();p.apply_unchecked(move)
            if tuple(piece for row in p.b for piece in row)==board:return True
        return False

    def _plausible_gap(self,board):
        # Even confident glyphs in an effect (e.g. the word '将') do not prove
        # a new chess position. Reuse the existing static position validator.
        from position_import import validate_position
        rows=[list(board[r*9:(r+1)*9]) for r in range(10)]
        for turn in (self.position.red,not self.position.red):
            try:validate_position(rows,turn);return True
            except ValueError:pass
        return False

    def observe(self,image,now=None):
        now=time.monotonic() if now is None else now
        if self.requires_resync:
            return dict(kind="resync_required",reason="已确认局面无法单步衔接，需要重新同步；仍在采集",**self.gap_diagnostics)
        aligned=warp(image,self.corners)
        objects=_round_objects(aligned,self.red_bottom) if self.round_pieces else None
        current=patches(aligned,self.red_bottom,objects)
        grid=patches(aligned,self.red_bottom)
        stable=self.previous is not None
        if stable:
            varying=np.flatnonzero(_rgb_error(grid,self.previous)>=.035)
            stable=all(_body_error(grid[i],self.previous[i])<.08 for i in varying)
        self.previous=grid
        circles=set(objects) if objects is not None else None
        same,change,matches,errors,evidence=self._compare(current,circles,grid,aligned,objects)
        diagnostics=dict(candidate_count=len(matches),changed_cells=int((~same).sum()),
            mismatch_cells=[[int(i//9),int(i%9),self.position.b[i//9][i%9],round(float(change[i]),4)] for i in np.flatnonzero(~same)[:12]],
            max_error=round(float(change.max()),4),
            visual_error=round(min((v[0] for v in errors),default=1),4),
            empty_error=round(min((v[1] for v in errors),default=1),4))
        if evidence is not None and not self._single_successor(evidence) and self._plausible_gap(evidence):
            old=self.gap_candidate
            if old and old[0]==evidence and .1<=now-old[1]<=1.5:
                self.requires_resync=True;self.gap_diagnostics=diagnostics
                self.candidate=None;self.pending=None
                return dict(kind="resync_required",reason="已确认局面无法单步衔接，需要重新同步；仍在采集",**diagnostics)
            if not old or old[0]!=evidence or now-old[1]>1.5:self.gap_candidate=(evidence,now)
        else:self.gap_candidate=None
        if same.all():
            self.candidate=None;self.uncertain_since=None;self.checking_since=None
            return dict(kind="unchanged",**diagnostics)
        if self.checking_since is None:self.checking_since=now
        if len(matches)==1:
            m,dest,vacated=matches[0]
            old=self.candidate
            self.uncertain_since=None
            # Both observations have independently passed the complete board,
            # identity, colour and vacated-origin checks above. Comparing their
            # raw pixels again would reintroduce the very check-glow failure.
            consistent=(old is not None and old[0]==m)
            if consistent and .1<=now-old[1]<=1.5:
                self.pending=(m,current,grid,aligned,objects)
                return dict(kind="move",move=m.ucci(),**{**diagnostics,"visual_error":dest,"empty_error":vacated})
            if not consistent or now-old[1]>1.5:self.candidate=(m,now,current,grid)
            return dict(kind="waiting_clear" if now-self.checking_since>=6 else "checking",
                reason="等待画面清晰，仍在监听" if now-self.checking_since>=6 else "正在核对手机画面",**diagnostics)
        # A check/selection effect elsewhere may obscure an intermediate frame.
        # Keep bounded evidence, but never commit until a second complete,
        # uniquely matching observation supports the very same move.
        if self.candidate and now-self.candidate[1]>1.5:self.candidate=None
        if not stable:self.uncertain_since=None
        elif self.uncertain_since is None:self.uncertain_since=now
        return dict(kind="waiting_clear" if now-self.checking_since>=6 else "checking",
            reason="等待画面清晰，仍在监听" if now-self.checking_since>=6 else "正在核对手机画面",**diagnostics)

    def accept(self,move):
        if self.requires_resync:raise ValueError("历史缺口未重新同步，不能提交走法")
        if not self.pending or self.pending[0]!=move:raise ValueError("没有对应的已验证画面")
        _,current,grid,image,objects=self.pending
        # Only the two changed cells may refresh trusted appearance. Replacing
        # the whole frame would teach a check overlay on an unrelated cannon
        # as that cannon's permanent identity and break the very next move.
        for r,c in ((move.fr,move.fc),(move.tr,move.tc)):
            i=r*9+c;self.reference[i]=current[i];self.reference_grid[i]=grid[i]
            vr,vc=(r,c) if self.red_bottom else (9-r,8-c)
            x,y=40+vc*64,40+vr*64
            self.reference_image[y-32:y+33,x-32:x+33]=image[y-32:y+33,x-32:x+33]
            self.reference_centers.pop(i,None)
            if objects and i in objects:self.reference_centers[i]=objects[i]
        self.position.apply_unchecked(move)
        self.pending=None
        self.verified_reference={}
        self.candidate=None;self.uncertain_since=None;self.checking_since=None

