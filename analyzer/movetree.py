"""MoveTree —— 棋局状态树。

每个节点 = 一步棋之后的局面快照（BoardState）。
  * 撤回（undo）= 回到父节点
  * 快进（redo）= 进入主线的第一个子节点
  * 分支 = 同一局面下的不同落子，各自成一个子节点（首个子节点视为主线）
  * 分析缓存：每个节点缓存一次 KataGo 分析结果，回到旧局面无需重算

坐标与 GTP/KataGo 字符串互转也在此处。
GTP 约定：列 A..T（跳过 I），A=左侧第 1 列；行 1..19，1=底部。
本模块约定：grid[y][x]，y=0 顶部，x=0 左侧；故 GTP 行 = size - y。
"""
from __future__ import annotations

from board import BoardState, BLACK, WHITE, IllegalMove, color_letter

# 列字母（A..T，跳过 I）
COLS = "ABCDEFGHJKLMNOPQRST"


def xy_to_point(x: int, y: int, size: int = 19) -> str:
    """(x,y) -> GTP 点字符串，如 (15,3)->'Q16'。"""
    return COLS[x] + str(size - y)


def point_to_xy(point: str, size: int = 19):
    """'Q16' -> (15,3)。"""
    letter = point[0].upper()
    row = int(point[1:])
    x = COLS.index(letter)
    y = size - row
    return x, y


_NODE_COUNTER = [0]


class MoveNode:
    __slots__ = ("board", "move", "parent", "children", "analysis", "comment", "nid")

    def __init__(self, board: BoardState, move=None, parent: "MoveNode" = None):
        _NODE_COUNTER[0] += 1
        self.nid = _NODE_COUNTER[0]     # 稳定唯一 id（供分析请求守卫追踪）
        self.board = board          # 本节点落子后的盘面快照
        self.move = move            # (color_letter, (x,y)|None) 或 None（根节点）；None 坐标=pass
        self.parent = parent
        self.children = []          # 第一个子节点 = 主线
        self.analysis = None        # 缓存的 KataGo 分析响应 dict
        self.comment = None         # SGF C[] 注释（用户注释；导出时追加 AI 摘要）

    @property
    def depth(self) -> int:
        d, n = 0, self
        while n.parent is not None:
            d += 1
            n = n.parent
        return d

    def path_to_root(self):
        path, n = [], self
        while n is not None:
            path.append(n)
            n = n.parent
        path.reverse()
        return path

    def moves_list(self):
        """从根到本节点（不含根）的落子序列，KataGo 查询用：[["B","Q16"], ...]；pass 为 ["B","pass"]。"""
        moves = []
        for n in self.path_to_root():
            if n.move is not None:
                cletter, coord = n.move
                if coord is None:
                    moves.append([cletter, "pass"])
                else:
                    moves.append([cletter, xy_to_point(coord[0], coord[1], n.board.size)])
        return moves


class MoveTree:
    def __init__(self, size: int = 19):
        self.size = size
        self.root = MoveNode(BoardState(size))
        self.current = self.root
        self.initial_stones = []    # 让子 setup：(color_letter, (x,y)) 列表，置于根盘面
        self.score_result = None    # 终局点目确认结果（ScoreResult）；导出 SGF 时写 RE[] + 点目摘要

    def set_initial_stones(self, stones, to_move=None):
        """stones: list of (color_int, x, y)。重建根盘面放置 setup 子；清空已有子节点。

        to_move：显式指定首手方；None 时按惯例（有黑 setup 子则白先，否则黑先）。
        """
        self.initial_stones = [(color_letter(c), (x, y)) for c, x, y in stones]
        self.root.board = BoardState(self.size).with_setup(stones)
        if to_move is None:
            to_move = WHITE if any(c == BLACK for c, x, y in stones) else BLACK
        self.root.board.to_move = to_move
        self.root.children = []
        self.current = self.root

    def initial_stones_list(self):
        """KataGo 查询用 initialStones：[['B','D4'], ...]（GTP 坐标）。"""
        return [[cl, xy_to_point(x, y, self.size)] for cl, (x, y) in self.initial_stones]

    # ---- 落子 / 导航 ----
    def play(self, x: int, y: int):
        """在当前节点落子。命中已有分支则进入；否则新建分支。
        返回 (ok:bool, reason:str)。"""
        for ch in self.current.children:
            if ch.move and ch.move[1] == (x, y):
                self.current = ch
                return True, "existing"
        color = self.current.board.to_move
        try:
            nb = self.current.board.try_play(x, y)
        except IllegalMove as e:
            return False, str(e)
        node = MoveNode(nb, move=(color_letter(color), (x, y)), parent=self.current)
        self.current.children.append(node)
        self.current = node
        return True, "ok"

    def play_pass(self):
        """当前方虚着（pass）：复用既有 pass 子节点，否则新建。返回 (ok, reason)。"""
        cl = color_letter(self.current.board.to_move)
        for ch in self.current.children:
            if ch.move and ch.move[0] == cl and ch.move[1] is None:
                self.current = ch
                return True, "existing"
        nb = self.current.board.pass_move()
        node = MoveNode(nb, move=(cl, None), parent=self.current)
        self.current.children.append(node)
        self.current = node
        return True, "ok"

    def undo(self) -> bool:
        if self.current.parent is not None:
            self.current = self.current.parent
            return True
        return False

    def redo(self) -> bool:
        if self.current.children:
            self.current = self.current.children[0]
            return True
        return False

    def siblings(self):
        """当前节点的兄弟（含自身）= 父节点的全部子节点；根节点返回 [根]。"""
        if self.current.parent is not None:
            return self.current.parent.children
        return [self.root]

    def sibling_index(self) -> int:
        for i, s in enumerate(self.siblings()):
            if s is self.current:
                return i
        return 0

    def goto_sibling(self, delta: int) -> bool:
        """在兄弟间循环切换（-1 上一个分支，+1 下一个）；无兄弟返回 False。"""
        sibs = self.siblings()
        if len(sibs) <= 1:
            return False
        self.current = sibs[(self.sibling_index() + delta) % len(sibs)]
        return True

    def reset(self):
        self.current = self.root

    # ---- 便捷 ----
    def can_undo(self) -> bool:
        return self.current.parent is not None

    def can_redo(self) -> bool:
        return bool(self.current.children)

    @property
    def double_pass(self) -> bool:
        """当前节点与【上一手】均为 pass（连续两手虚着）。

        用于终局点目提示。pass 节点的 move=(color_letter, None)；根节点 move=None。
        """
        cur = self.current
        if cur.move is None or cur.move[1] is not None:   # 当前手非 pass（或为根）
            return False
        parent = cur.parent
        if parent is None or parent.move is None or parent.move[1] is not None:
            return False                                    # 上一手非 pass
        return True
