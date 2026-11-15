"""Exact opening defaults for a confirmed image orientation; no vision or AI."""
from dataclasses import dataclass

from position_import import ImportErrorDetail, normalize_board
from rules import Position, legal_moves


@dataclass(frozen=True)
class OpeningDefaults:
    own_red: bool
    red_to_move: bool
    kind: str  # initial / red_first_move


def infer_opening(board, red_bottom):
    """Match all 90 cells, allowing only a non-capturing legal red first move.

    The input is in image coordinates. Unknown direction, malformed data and
    every other position return None; this never edits the image draft.
    """
    try:
        board = normalize_board(board, red_bottom)
    except ImportErrorDetail:
        return None
    start = Position()
    if board == start.b:
        return OpeningDefaults(red_bottom, True, "initial")
    for move in legal_moves(start):
        if start.b[move.tr][move.tc]:
            continue
        after = start.copy()
        after.apply_unchecked(move)
        if board == after.b:
            return OpeningDefaults(red_bottom, False, "red_first_move")
    return None
