"""BoardState —— 19x19 围棋棋盘逻辑层。

职责（与 UI、KataGo 完全解耦）：
  * 棋子表示与轮次
  * 落子：提子（俘虏无气棋串）、自杀检测、简单打劫检测
  * 不可变快照：try_play 返回一个【新的】BoardState，供 MoveTree 每个节点存储

坐标约定（与 UI 一致）：
  grid[y][x]，y=0 为棋盘顶部，x=0 为左侧。
  与 GTP/ KataGo 的 A1=左下 对应转换在 movetree.py 完成。
"""
from __future__ import annotations

EMPTY, BLACK, WHITE = 0, 1, 2


def opponent(color: int) -> int:
    return WHITE if color == BLACK else BLACK


def color_letter(color: int) -> str:
    return "B" if color == BLACK else "W"


class IllegalMove(Exception):
    """落子非法（占位 / 自杀 / 打劫）。"""


def _group_and_liberties(grid, x, y, size):
    """返回 (棋串坐标集合, 气集合) for 落在 grid[y][x] 的棋串。"""
    color = grid[y][x]
    stones = set()
    libs = set()
    if color == EMPTY:
        return stones, libs
    seen = {(x, y)}
    stack = [(x, y)]
    while stack:
        cx, cy = stack.pop()
        stones.add((cx, cy))
        # 四邻
        for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
            if 0 <= nx < size and 0 <= ny < size:
                v = grid[ny][nx]
                if v == EMPTY:
                    libs.add((nx, ny))
                elif v == color and (nx, ny) not in seen:
                    seen.add((nx, ny))
                    stack.append((nx, ny))
    return stones, libs


class BoardState:
    __slots__ = ("size", "grid", "to_move", "prev_grid", "last_move", "captures")

    def __init__(self, size: int = 19):
        self.size = size
        self.grid = [[EMPTY] * size for _ in range(size)]
        self.to_move = BLACK
        self.prev_grid = None          # 上一手落子前的盘面（用于简单打劫判定）
        self.last_move = None          # 最近落子坐标 (x, y) 或 None
        self.captures = {BLACK: 0, WHITE: 0}   # 各方累计提子数

    def clone(self) -> "BoardState":
        b = BoardState.__new__(BoardState)
        b.size = self.size
        b.grid = [row[:] for row in self.grid]
        b.to_move = self.to_move
        b.prev_grid = None if self.prev_grid is None else [r[:] for r in self.prev_grid]
        b.last_move = self.last_move
        b.captures = dict(self.captures)
        return b

    def stone_at(self, x, y) -> int:
        return self.grid[y][x]

    def try_play(self, x: int, y: int) -> "BoardState":
        """在 (x,y) 落当前轮 to_move 的子；合法则返回新盘面，否则 raise IllegalMove。"""
        if not (0 <= x < self.size and 0 <= y < self.size):
            raise IllegalMove("坐标越界")
        if self.grid[y][x] != EMPTY:
            raise IllegalMove("此处已有棋子")

        color = self.to_move
        opp = opponent(color)

        # 在拷贝上模拟
        trial = [row[:] for row in self.grid]
        trial[y][x] = color

        # 1) 提对方无气棋串
        captured_count = 0
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= nx < self.size and 0 <= ny < self.size and trial[ny][nx] == opp:
                stones, libs = _group_and_liberties(trial, nx, ny, self.size)
                if not libs:
                    for (sx, sy) in stones:
                        trial[sy][sx] = EMPTY
                    captured_count += len(stones)

        # 2) 自杀检测（提子之后本串仍无气 = 非法）
        _, libs = _group_and_liberties(trial, x, y, self.size)
        if not libs:
            raise IllegalMove("禁着点（自杀）")

        # 3) 简单打劫：若新盘面与上一手落子前的盘面完全相同 => 非法
        if self.prev_grid is not None and trial == self.prev_grid:
            raise IllegalMove("打劫（禁同型再现）")

        # 构造新状态
        nb = self.clone()
        nb.grid = trial
        nb.prev_grid = [r[:] for r in self.grid]   # 下一手判劫用
        nb.to_move = opp
        nb.last_move = (x, y)
        nb.captures = dict(self.captures)
        nb.captures[color] += captured_count
        return nb

    def pass_move(self) -> "BoardState":
        """虚着（pass）：盘面不变、轮次翻转、last_move=None。返回【新】BoardState。

        与 try_play 语义一致：prev_grid 记录“此手之前”的盘面（pass 不改棋子，
        故等于当前盘面），供下一手简单打劫判定连续。
        """
        nb = self.clone()
        nb.to_move = opponent(self.to_move)
        nb.last_move = None
        nb.prev_grid = [r[:] for r in self.grid]
        return nb

    def with_setup(self, stones) -> "BoardState":
        """放置 setup 子（让子棋 AB/AW）：不触发提子/自杀/轮次逻辑，返回新 BoardState。

        stones: iterable of (color, x, y)。
        """
        nb = self.clone()
        for color, x, y in stones:
            if 0 <= x < nb.size and 0 <= y < nb.size:
                nb.grid[y][x] = color
        nb.last_move = None
        nb.prev_grid = None
        return nb
