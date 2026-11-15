"""Project rule: forbid two consecutive, capture-free, two-point round trips.

This is deliberately narrower than general position repetition or a tournament
perpetual-check/chase adjudicator. Contexts are immutable and contain seven plies.
All supplied histories must already have passed the ordinary move rules.
"""
from dataclasses import dataclass

from rules import Move, legal_moves

MESSAGE = "双方已连续重复往返，请换一种走法。"
FORCED_MESSAGE = "当前仅有这一着合法走法，允许重复。"


@dataclass(frozen=True)
class Ply:
    move: Move
    captured: str = ""


def advance(context, position, move):
    """Record a known-legal move before the caller changes the position."""
    return (*tuple(context)[-6:], Ply(move, position.b[move.tr][move.tc]))


def from_history(history, moves, prefix=()):
    """Reuse the App's (before-position, before-moves, last) undo snapshots."""
    context = tuple(prefix)
    for before, before_moves, _ in history[-7:]:
        move = Move.from_ucci(moves[len(before_moves)])
        context = advance(context, before, move)
    return context[-7:]


def reverse(move):
    return Move(move.tr, move.tc, move.fr, move.fc)


def cycle_move(position, context):
    """Return the move completing eight alternating plies, even if forced."""
    if len(context) < 7 or any(p.captured for p in context[-7:]):
        return None
    a, b, c, d, e, f, g = (p.move for p in context[-7:])
    if c != reverse(a) or d != reverse(b) or (e, f, g) != (a, b, c):
        return None
    if position.b[d.tr][d.tc]:
        return None
    return d


def blocked_cycle_move(position, context=(), basic=None):
    basic = legal_moves(position) if basic is None else basic
    if len(basic) <= 1:
        return None
    move = cycle_move(position, context)
    return move if move in basic else None


def playable_moves(position, context=(), basic=None):
    basic = legal_moves(position) if basic is None else basic
    blocked = blocked_cycle_move(position, context, basic)
    return [m for m in basic if m != blocked]


def trim_pv(position, context, pv):
    """Return a fresh executable PV and whether our cycle rule truncated it."""
    probe = position.copy()
    context = tuple(context)
    out = []
    for value in pv:
        move = Move.from_ucci(value)
        basic = legal_moves(probe)
        if move not in basic:
            break
        if move == blocked_cycle_move(probe, context, basic):
            return out, True
        context = advance(context, probe, move)
        probe.apply_unchecked(move)
        out.append(value)
    return out, False
