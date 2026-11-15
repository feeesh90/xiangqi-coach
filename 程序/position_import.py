"""Independent image drafts and static checks for custom starting positions."""
from collections import Counter
from dataclasses import dataclass
import math

from rules import Position, in_check, palace, piece_name

PIECES = frozenset("RNBAKCP rnbakcp".replace(" ", ""))


class ImportErrorDetail(ValueError):
    pass


def checked_board(board):
    if not isinstance(board, list) or len(board) != 10:
        raise ImportErrorDetail("棋盘必须有 10 行。")
    result = []
    for row in board:
        if not isinstance(row, list) or len(row) != 9:
            raise ImportErrorDetail("每行必须有 9 个交叉点。")
        if any(not isinstance(p, str) or p not in PIECES | {""} for p in row):
            raise ImportErrorDetail("棋盘包含未知棋子，请先修正。")
        result.append(row[:])
    return result


def normalize_board(board, red_bottom):
    result = checked_board(board)
    if not isinstance(red_bottom, bool):
        raise ImportErrorDetail("请选择图片中哪方在下。")
    return result if red_bottom else [row[::-1] for row in result[::-1]]


def guess_red_bottom(board):
    kings = {p: r for r, row in enumerate(board) for p in row if p in ("K", "k")}
    if "K" in kings and "k" in kings and kings["K"] != kings["k"]:
        return kings["K"] > kings["k"]
    return None


def validate_position(board, red_to_move):
    board = checked_board(board)
    if not isinstance(red_to_move, bool):
        raise ImportErrorDetail("请选择轮到红方还是黑方走。")
    counts = Counter(p for row in board for p in row if p)
    errors = []
    for side in (True, False):
        color = "红方" if side else "黑方"
        for kind, maximum in (("K", 1), ("R", 2), ("N", 2), ("C", 2), ("A", 2), ("B", 2), ("P", 5)):
            p = kind if side else kind.lower()
            if kind == "K" and counts[p] != 1:
                errors.append(f"{color}必须恰好有一个{piece_name(p)}。")
            elif counts[p] > maximum:
                errors.append(f"{color}{piece_name(p)}超过 {maximum} 个。")
    advisors = {(7, 3), (7, 5), (8, 4), (9, 3), (9, 5)}
    bishops = {(9, 2), (9, 6), (7, 0), (7, 4), (7, 8), (5, 2), (5, 6)}
    for r, row in enumerate(board):
        for c, p in enumerate(row):
            if not p:
                continue
            red = p.isupper()
            rr = r if red else 9-r
            bad = (p.upper() == "K" and not palace(red, r, c)
                   or p.upper() == "A" and (rr, c) not in advisors
                   or p.upper() == "B" and (rr, c) not in bishops
                   or p.upper() == "P" and (rr > 6 or (rr >= 5 and c % 2 != 0)))
            if bad:
                errors.append(f"第 {r+1} 行第 {c+1} 列的{piece_name(p)}位置不符合规则。")
    if errors:
        raise ImportErrorDetail("\n".join(errors))
    pos = Position.__new__(Position)
    pos.b, pos.red = board, red_to_move
    kr, kc = next((r, c) for r, row in enumerate(board) for c, p in enumerate(row) if p == "K")
    br, bc = next((r, c) for r, row in enumerate(board) for c, p in enumerate(row) if p == "k")
    if kc == bc and not any(board[r][kc] for r in range(br+1, kr)):
        raise ImportErrorDetail("将帅照面：请修正棋子位置或补上漏识别的棋子。")
    if in_check(pos, not red_to_move):
        raise ImportErrorDetail("非行棋方正在被将军，请检查棋子位置和“轮到谁走”。")
    canonical = Position(pos.fen())
    if canonical.b != board or canonical.red != red_to_move:
        raise ImportErrorDetail("局面转换不一致，请重新确认。")
    return canonical


@dataclass
class BoardDraft:
    # Cells are in IMAGE orientation, independent of the game's view.
    board: list
    uncertain: set
    red_bottom: object = None

    @classmethod
    def from_result(cls, result):
        if not isinstance(result, dict):
            raise ImportErrorDetail("识别程序返回的结果格式错误。")
        board = checked_board(result.get("board"))
        corners=result.get("corners")
        if corners is not None:
            size=result.get("image_size")
            if (not isinstance(size,list) or len(size)!=2 or
                    any(type(x) is not int or x<1 or x>16000000 for x in size) or
                    not isinstance(corners,list) or len(corners)!=4):
                raise ImportErrorDetail("图片尺寸或网格数据错误。")
            for point in corners:
                if (not isinstance(point,list) or len(point)!=2 or
                        any(type(x) not in (int,float) or not math.isfinite(x) for x in point) or
                        not (0<=point[0]<size[0] and 0<=point[1]<size[1])):
                    raise ImportErrorDetail("网格坐标越界。")
        unknown = result.get("uncertain", [])
        if not isinstance(unknown, list) or len(unknown) > 90:
            raise ImportErrorDetail("不确定格子列表错误。")
        cells = set()
        for pair in unknown:
            if (not isinstance(pair, list) or len(pair) != 2 or
                    any(type(x) is not int for x in pair) or
                    not (0 <= pair[0] < 10 and 0 <= pair[1] < 9)):
                raise ImportErrorDetail("识别坐标越界。")
            cells.add(tuple(pair))
        return cls(board, cells, guess_red_bottom(board))

    def set_piece(self, r, c, piece):
        if not (0 <= r < 10 and 0 <= c < 9) or piece not in PIECES | {""}:
            raise ImportErrorDetail("未知棋子或坐标越界。")
        self.board[r][c] = piece
        self.uncertain.discard((r, c))

    def position(self, red_to_move):
        if self.uncertain:
            raise ImportErrorDetail("黄色格子尚未确认；请逐格修正，或检查后点击“确认标记格”。")
        return validate_position(normalize_board(self.board, self.red_bottom), red_to_move)
