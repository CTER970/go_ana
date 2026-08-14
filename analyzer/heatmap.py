"""heatmap —— ownership / policy 与棋盘坐标的纯映射逻辑（与 tkinter Canvas 解耦，可无头测试）。

KataGo analysis 约定（见 KataGo/docs/Analysis_Engine.md）：
  * ownership：长度 boardYSize*boardXSize，值域 [-1,1]，+1=黑属地、-1=白属地；
    行序，从左上 A19 到右下 T1，索引 = y*size+x（y=0 顶部）。
  * policy：长度 boardYSize*boardXSize + 1，正价值和为 1，-1=非法；末位 = pass。
本项目内部坐标 grid[y][x]、y=0 顶部，故索引与内部坐标【直接对应，无需翻转】。
"""
from __future__ import annotations


def ownership_index(x: int, y: int, size: int = 19) -> int:
    """(x,y) -> ownership 数组索引（行序，y=0 顶部）。"""
    return y * size + x


def ownership_at(ownership, x: int, y: int, size: int = 19):
    return ownership[y * size + x]


def ownership_is_black(v) -> bool:
    """KataGo 约定：+1=黑属地，-1=白属地；>0 即黑方属地。"""
    return v is not None and v > 0


def policy_board_entries(policy, size: int = 19):
    """返回 [(x, y, value), ...]：仅棋盘点（去掉末位 pass）、去掉非法(-1)。"""
    out = []
    for i, v in enumerate(policy[:size * size]):
        if v is None or v < 0:
            continue
        out.append((i % size, i // size, v))
    return out


def policy_pass_value(policy, size: int = 19):
    """policy 末位 = pass 的权重（不画到棋盘）。"""
    return policy[size * size] if len(policy) > size * size else None
