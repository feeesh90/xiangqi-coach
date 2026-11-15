"""Pure state: confirmed truth and tentative engine display never share history."""
from dataclasses import dataclass
from uuid import uuid4
from collections import deque
from rules import legal_moves
from repetition import advance, playable_moves

@dataclass(frozen=True)
class Proposal:
    move: object
    stamp: tuple

class FollowSession:
    def __init__(self, position, own_red):
        self.position=position.copy()
        self.own_red=bool(own_red)
        self.epoch=uuid4().hex
        self.version=0
        self.phase="following"
        self.context=()
        self.proposal=None
        self.last=None
        self.last_observed_own=False
        self.notice=""
        self.tracking="unchanged"
        self.requires_resync=False
        self.diagnostics=deque(maxlen=200)

    @property
    def stamp(self):
        return self.epoch,self.version,self.position.fen(),self.context

    @property
    def own_turn(self):
        return self.position.red==self.own_red

    def offer(self,move,stamp):
        if stamp!=self.stamp or not self.own_turn or move not in playable_moves(self.position,self.context):
            return False
        self.proposal=Proposal(move,stamp)
        return True

    def commit(self,move,observed=False):
        if move not in legal_moves(self.position):return False
        own=self.own_turn
        self.context=advance(self.context,self.position,move)
        self.position.apply_unchecked(move)
        self.version+=1;self.last=move;self.proposal=None
        self.tracking="unchanged"
        self.last_observed_own=observed and own
        return True

    def displayed(self):
        p=self.position.copy()
        if self.proposal and self.proposal.stamp==self.stamp:p.apply_unchecked(self.proposal.move)
        return p

    def match_board(self,board):
        if board==self.position.b:return "unchanged",None
        matches=[]
        for move in legal_moves(self.position):
            p=self.position.copy();p.apply_unchecked(move)
            if p.b==board:matches.append(move)
        return ("move",matches[0]) if len(matches)==1 else ("uncertain",None)
