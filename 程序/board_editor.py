"""A free-editing draft in canonical board coordinates, never a live Position."""
from dataclasses import dataclass

from position_import import PIECES, checked_board, validate_position


@dataclass
class BoardEditor:
    board: list
    red_to_move: bool
    tool: str = "move"
    piece: str = "R"
    selected: tuple | None = None

    @classmethod
    def from_position(cls, position):
        return cls(checked_board(position.b), position.red)

    def click(self, r, c):
        if not (0 <= r < 10 and 0 <= c < 9):
            return
        if self.tool == "delete":
            self.board[r][c] = ""
            self.selected = None
        elif self.tool == "add":
            if self.piece not in PIECES:
                raise ValueError("请选择红方或黑方棋子")
            self.board[r][c] = self.piece
            self.selected = None
        elif self.tool == "move":
            if self.selected is None:
                self.selected = (r, c) if self.board[r][c] else None
            else:
                fr, fc = self.selected
                self.board[r][c], self.board[fr][fc] = self.board[fr][fc], self.board[r][c]
                self.selected = None
        else:
            raise ValueError("未知编辑工具")

    def position(self):
        return validate_position(self.board, self.red_to_move)
