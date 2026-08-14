"""test_scrubber —— 可拖动棋局进度条的几何与交互测试（无头 Tk）。

注意：全程复用单个隐藏 root，避免在同一进程里多次 create/destroy tk.Tk()
（会污染 Tk 状态，导致后续 root 报 "Can't find a usable init.tcl"）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import tkinter as tk
from scrubber import MoveScrubber

_ROOT = None


def _root():
    global _ROOT
    if _ROOT is None:
        _ROOT = tk.Tk()
        _ROOT.withdraw()
    return _ROOT


class FakeEvent:
    def __init__(self, x):
        self.x = x


def check(name, cond, extra=""):
    print(("[CHECK] %-34s %s %s" % (name, "OK" if cond else "FAIL", extra)))
    if not cond:
        raise AssertionError(name)


def make():
    root = _root()
    changes = []
    s = MoveScrubber(root, on_change=lambda n: changes.append(n),
                     on_commit=lambda n: changes.append(("commit", n)),
                     colors={}, fonts={})
    s.pack(fill="x")
    root.update_idletasks()
    s.configure(width=240)
    root.update_idletasks()
    return root, s, changes


def test_range_position():
    _r, s, _c = make()
    s.set_range(10)
    s.set_position(3)
    check("set_range→_max=10", s._max == 10, str(s._max))
    check("set_position→_pos=3", s._pos == 3, str(s._pos))
    check("is_dragging 初值 False", s.is_dragging is False)


def test_x_to_move_edges():
    _r, s, _c = make()
    s.set_range(100)
    x0, x1, _cy = s._track_geom()
    check("左端→0", s._x_to_move(x0) == 0, str(s._x_to_move(x0)))
    check("右端→100", s._x_to_move(x1) == 100, str(s._x_to_move(x1)))
    mid = s._x_to_move((x0 + x1) / 2)
    check("中点≈50", 48 <= mid <= 52, str(mid))
    check("越界左 clamp 0", s._x_to_move(-50) == 0)
    check("越界右 clamp 100", s._x_to_move(10 ** 6) == 100)


def test_drag_fires_change_and_moves_thumb():
    _r, s, changes = make()
    s.set_range(50)
    x0, x1, _cy = s._track_geom()
    s._on_press(FakeEvent(x0))            # 起点（pos 已是 0，不触发回调）
    check("按下 is_dragging=True", s.is_dragging is True)
    s._on_motion(FakeEvent(x1))           # 拖到末端 → 50
    check("拖到末端 _pos=50", s._pos == 50, str(s._pos))
    check("on_change 收到 50", 50 in changes, str(changes))
    s._on_release(FakeEvent((x0 + x1) // 2))   # 松手到中段 → on_commit(~25)
    check("松手 is_dragging=False", s.is_dragging is False)
    check("on_commit 被调用", any(isinstance(c, tuple) and c[0] == "commit" for c in changes),
          str(changes))


def test_set_position_does_not_fire_callback():
    """程序侧同步只更新视觉，不触发导航（避免 导航↔同步 反馈循环）。"""
    _r, s, changes = make()
    s.set_range(20)
    s.set_position(7)
    s.set_position(15)
    check("set_position 不触发 on_change", changes == [], str(changes))
    check("_pos 同步到 15", s._pos == 15, str(s._pos))


def test_press_force_fires_even_at_pos():
    """修复回归：按下 force=True，即使落在当前手位置也触发回调，
    让 app 侧借首次交互校准范围（修复初始 max 未就绪时拖不动）。"""
    _r, s, changes = make()
    s.set_range(1)            # 模拟加载后范围未就绪
    s.set_position(0)
    x0, _x1, _cy = s._track_geom()
    changes.clear()
    s._on_press(FakeEvent(x0))   # _x_to_move(x0)=0==pos；force 应触发回调
    check("按下 force 触发回调（n==pos）", len(changes) >= 1, str(changes))
    check("按下设 is_dragging=True", s.is_dragging is True)


if __name__ == "__main__":
    print("=" * 60)
    print(" 可拖动进度条（MoveScrubber）测试")
    print("=" * 60)
    test_range_position(); print()
    test_x_to_move_edges(); print()
    test_drag_fires_change_and_moves_thumb(); print()
    test_set_position_does_not_fire_callback(); print()
    test_press_force_fires_even_at_pos(); print()
    print("test_scrubber 全部通过 ✅")
