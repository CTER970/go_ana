"""score_estimator —— 终局点目（中国规则 / 面积数法）。

纯逻辑（不依赖 tkinter / KataGo），可被 test_score_estimator.py 无头测试。
复用 movetree 的 GTP 坐标（xy_to_point / point_to_xy，跳过 I）与 board 的 EMPTY/BLACK/WHITE。

面积数法：
  黑方 = 黑活子数 + 黑属地空点数
  白方 = 白活子数 + 白属地空点数 + 贴目
  margin = 黑方面积 − 白方面积（正=黑胜，负=白胜）
死子在计分前从【临时】scoring_grid 移除（不改原 BoardState / MoveTree）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from board import EMPTY, BLACK, WHITE
from movetree import xy_to_point, point_to_xy


@dataclass
class ScoreResult:
    black_stones: int
    white_stones: int
    black_territory: int
    white_territory: int
    neutral_points: int
    komi: float
    black_area: float
    white_area: float
    margin: float
    winner: str            # "B" / "W" / "Draw"
    result_text: str       # SGF RE[]，如 "B+3.5" / "W+7.5" / "0"
    dead_black: list = field(default_factory=list)
    dead_white: list = field(default_factory=list)
    black_territory_points: list = field(default_factory=list)
    white_territory_points: list = field(default_factory=list)
    neutral_points_list: list = field(default_factory=list)


def ownership_territory_split(ownership, size: int = 19,
                              strong: float = 0.6, weak: float = 0.05):
    """把 KataGo ownership 拆成「实地 / 倾向」四方点数，用于中盘形势判断。

    ownership 行序 y*size+x，+黑/−白（与 heatmap.ownership_at 一致）。
      实地（strong）：|v| >= strong —— AI 高置信归属，近似已成地。
      倾向（lean） ：weak <= |v| < strong —— 有影响但未定型。
    长度不足/残缺响应的点跳过，不越界。
    返回 dict：{b_strong, w_strong, b_lean, w_lean}（均为 int 点数）。
    """
    b_strong = w_strong = b_lean = w_lean = 0
    n = len(ownership) if ownership is not None else 0
    limit = min(n, size * size)
    for idx in range(limit):
        v = ownership[idx]
        if not v:
            continue
        if v >= strong:
            b_strong += 1
        elif v <= -strong:
            w_strong += 1
        elif v >= weak:
            b_lean += 1
        elif v <= -weak:
            w_lean += 1
    return {"b_strong": b_strong, "w_strong": w_strong,
            "b_lean": b_lean, "w_lean": w_lean}


class ScoreEstimator:
    def __init__(self, board, komi: float = 7.5):
        self.board = board
        self.size = board.size
        self.komi = komi
        self._dead = set()      # GTP 点串集合（大写）

    # ---- 死子管理 ----
    def set_dead_stones(self, dead_points) -> None:
        self._dead = {p.upper() for p in dead_points}

    def toggle_dead_stone(self, point: str) -> None:
        point = point.upper()
        if point in self._dead:
            self._dead.discard(point)
        else:
            self._dead.add(point)

    def get_dead_stones(self):
        return set(self._dead)

    # ---- 计分 ----
    def _scoring_grid(self):
        """复制盘面并移除死子（不改原 board）。"""
        grid = [row[:] for row in self.board.grid]
        for pt in self._dead:
            try:
                x, y = point_to_xy(pt, self.size)
            except Exception:
                continue
            if 0 <= x < self.size and 0 <= y < self.size:
                grid[y][x] = EMPTY
        return grid

    def compute_chinese_area_score(self) -> ScoreResult:
        size = self.size
        grid = self._scoring_grid()
        # 活子计数
        black_stones = white_stones = 0
        for y in range(size):
            for x in range(size):
                if grid[y][x] == BLACK:
                    black_stones += 1
                elif grid[y][x] == WHITE:
                    white_stones += 1
        # flood fill 空区域 → 归属
        black_territory = white_territory = neutral = 0
        black_tp, white_tp, neutral_tp = [], [], []
        seen = [[False] * size for _ in range(size)]
        for y in range(size):
            for x in range(size):
                if grid[y][x] != EMPTY or seen[y][x]:
                    continue
                region, borders, stack = [], set(), [(x, y)]
                seen[y][x] = True
                while stack:
                    cx, cy = stack.pop()
                    region.append((cx, cy))
                    for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
                        if 0 <= nx < size and 0 <= ny < size:
                            v = grid[ny][nx]
                            if v == EMPTY:
                                if not seen[ny][nx]:
                                    seen[ny][nx] = True
                                    stack.append((nx, ny))
                            elif v == BLACK:
                                borders.add(BLACK)
                            elif v == WHITE:
                                borders.add(WHITE)
                pts = [xy_to_point(cx, cy, size) for cx, cy in region]
                if borders == {BLACK}:
                    black_territory += len(region); black_tp.extend(pts)
                elif borders == {WHITE}:
                    white_territory += len(region); white_tp.extend(pts)
                else:
                    neutral += len(region); neutral_tp.extend(pts)
        # 死子按原色分类（按原盘面颜色；越界/非法点串跳过）
        dead_black, dead_white = [], []
        for pt in self._dead:
            try:
                x, y = point_to_xy(pt, size)
            except Exception:
                continue
            if not (0 <= x < size and 0 <= y < size):   # 防止负行/越界（如 'A0'/'A20'）
                continue
            orig = self.board.grid[y][x]
            if orig == BLACK:
                dead_black.append(pt)
            elif orig == WHITE:
                dead_white.append(pt)
        black_area = black_stones + black_territory
        white_area = white_stones + white_territory + self.komi
        margin = black_area - white_area
        if margin > 0:
            winner, result_text = "B", "B+%.1f" % margin
        elif margin < 0:
            winner, result_text = "W", "W+%.1f" % abs(margin)
        else:
            winner, result_text = "Draw", "0"
        return ScoreResult(
            black_stones=black_stones, white_stones=white_stones,
            black_territory=black_territory, white_territory=white_territory,
            neutral_points=neutral, komi=self.komi,
            black_area=black_area, white_area=white_area,
            margin=margin, winner=winner, result_text=result_text,
            dead_black=sorted(dead_black), dead_white=sorted(dead_white),
            black_territory_points=sorted(black_tp),
            white_territory_points=sorted(white_tp),
            neutral_points_list=sorted(neutral_tp),
        )

    def suggest_dead_stones_from_ownership(self, ownership, threshold: float = 0.75):
        """ownership（行序 y*size+x，+黑/−白）：黑子点强偏白(<−thr)、白子点强偏黑(>thr) → 建议死子。

        ownership 长度不足（异常/截断响应）的点跳过，不越界。
        """
        dead = set()
        size = self.size
        n = len(ownership) if ownership is not None else 0
        for y in range(size):
            for x in range(size):
                stone = self.board.grid[y][x]
                if stone == EMPTY:
                    continue
                idx = y * size + x
                if idx >= n:                     # 短/残缺 ownership，跳过避免 IndexError
                    continue
                v = ownership[idx]
                if stone == BLACK and v < -threshold:
                    dead.add(xy_to_point(x, y, size))
                elif stone == WHITE and v > threshold:
                    dead.add(xy_to_point(x, y, size))
        return dead
