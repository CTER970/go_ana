"""ui.timeline —— 学习时间轴（V6 §38-43，Phase 6；合并进度条 v2）。

单条画布同时承担（此前时间轴与进度条分两行、视觉不同步的问题就此消除）：

- **手数导航**：可拖动手柄 + 点击/拖动轨道跳到主线第 N 手
  （drag 过程走 on_change 实时跟随，松手走 on_commit，程序侧
  set_position 只改视觉不回调，避免 导航↔同步 反馈循环）；
- **颜色 = 目损**：<1 目无标记，1-3 黄，3-6 橙，>6 红（V6 §41）；
- **紫色外圈 = 学习价值**：本盘学习节点加 learning_priority 专用紫圈；
- 悬停显示"第N手 · 损失X目 · 重点学习"（不暴露内部算法数字）。

兼容 MoveScrubber 的对外接口（set_range / set_position / is_dragging /
redraw），app 侧旧引用可无缝切换。
"""
from __future__ import annotations

import tkinter as tk

from ui import theme as th

# 目损 → 颜色档（V6 §41）
LOSS_TIERS = (
    (6.0, "red", 5),       # >6 目：红
    (3.0, "amber", 4),     # 3-6 目：橙档
    (1.0, "amber", 3),     # 1-3 目：黄档
)


class LearningTimeline(tk.Canvas):
    def __init__(self, master, on_change, on_commit=None, height=52,
                 colors=None, fonts=None):
        super().__init__(master, height=height,
                         bg=(colors or {}).get("card", th.t("card")),
                         highlightthickness=1, bd=0,
                         highlightbackground=(colors or {}).get(
                             "muted", th.t("muted")))
        self._on_change = on_change
        self._on_commit = on_change if on_commit is None else on_commit
        self._colors = colors or {}
        self._fonts = fonts or {}
        self._max = 1
        self._pos = 0
        self._points = []          # [{move, loss, priority}]
        self._dragging = False
        self._hover_idx = None
        self._pad = 14
        self._track_h = 6
        self._thumb_r = 11
        for seq, handler in (("<Button-1>", self._on_press),
                             ("<B1-Motion>", self._on_motion),
                             ("<ButtonRelease-1>", self._on_release),
                             ("<Enter>", lambda _e: self._set_hover_cursor(True)),
                             ("<Leave>", lambda _e: self._set_hover_cursor(False)),
                             ("<Configure>", lambda _e: self.redraw())):
            self.bind(seq, handler)
        self.configure(width=240)

    # ---- MoveScrubber 兼容接口 ----
    def set_range(self, max_n):
        self._max = max(1, int(max_n))
        self.redraw()

    def set_position(self, n):
        """程序侧同步：只更新视觉，不触发回调。"""
        self._pos = max(0, min(int(n), self._max))
        self.redraw()

    @property
    def is_dragging(self):
        return self._dragging

    # ---- 时间轴数据 ----
    def set_data(self, total, points, current=0):
        self._max = max(1, int(total))
        self._points = sorted(
            ({"move": int(p.get("move") or 0),
              "loss": float(p.get("loss") or 0.0),
              "priority": p.get("priority")} for p in points),
            key=lambda p: p["move"])
        self._pos = max(0, min(int(current), self._max))
        self.redraw()

    # ---- 几何 ----
    def _track_geom(self):
        w = max(60, self.winfo_width())
        h = max(30, self.winfo_height())
        return self._pad, w - self._pad, h * 0.66

    def _x_to_move(self, x):
        x0, x1, _cy = self._track_geom()
        span = x1 - x0
        if span <= 0:
            return self._pos
        return max(0, min(self._max, round((x - x0) * self._max / span)))

    def _move_to_x(self, n):
        x0, x1, _cy = self._track_geom()
        return x0 + (x1 - x0) * (n / self._max)

    # ---- 拖动/点击（MoveScrubber 语义） ----
    def _on_press(self, event):
        self._dragging = True
        self._jump_to(event.x, commit=False, force=True)

    def _on_motion(self, event):
        if self._dragging:
            self._jump_to(event.x, commit=False)
            return
        move = self._x_to_move(event.x)
        idx = None
        for i, p in enumerate(self._points):
            if abs(p["move"] - move) <= max(1, self._max // 120):
                idx = i
                break
        if idx != self._hover_idx:
            self._hover_idx = idx
            self.redraw()

    def _on_release(self, _event):
        if not self._dragging:
            return
        self._dragging = False
        n = self._pos
        self.redraw()
        self._on_commit(n)

    def _jump_to(self, x, commit=False, force=False):
        n = self._x_to_move(x)
        if n == self._pos and not commit and not force:
            return
        self._pos = n
        self.redraw()
        self._on_change(n)

    def _set_hover_cursor(self, on):
        self.configure(cursor="hand2" if on else "")

    # ---- 绘制 ----
    def _col(self, key):
        return self._colors.get(key) or th.t(key)

    def _font(self, key, fallback):
        return self._fonts.get(key) or fallback

    def redraw(self):
        c = self
        c.delete("all")
        w = max(60, self.winfo_width())
        h = max(30, self.winfo_height())
        if w < 40:
            return
        x0, x1, cy = self._track_geom()
        # 轨道 + 已播放填充
        c.create_rectangle(x0, cy - self._track_h / 2, x1, cy + self._track_h / 2,
                           fill=self._col("muted"), outline="", tags=("track",))
        px = self._move_to_x(self._pos)
        c.create_rectangle(x0, cy - self._track_h / 2, max(x0 + 1, px),
                           cy + self._track_h / 2,
                           fill=self._col("accent"), outline="", tags=("fill",))
        # 目损色杆（画在轨道上方，颜色档见 LOSS_TIERS）
        for p in self._points:
            color, bar_h = self._tier_style(p["loss"])
            if color is None:
                continue
            x = self._move_to_x(p["move"])
            c.create_line(x, cy, x, cy - bar_h, fill=self._col(color),
                          width=3, tags=("tick", str(p["move"])))
            if p.get("priority"):
                r = 7 if p["priority"] >= 0.6 else 5
                c.create_oval(x - r, cy - bar_h - r - 2, x + r,
                              cy - bar_h + r - 2,
                              outline=self._col("learning_priority"),
                              width=2, tags=("ring", str(p["move"])))
        # 悬停信息（V6 §42-43：说人话，不暴露内部数字）
        if self._hover_idx is not None and self._points:
            p = self._points[self._hover_idx]
            c.create_text(x0 + 2, 4, anchor="nw",
                          text="第%d手 · 损失%.1f目%s" % (
                              p["move"], p["loss"],
                              " · 重点学习" if p.get("priority") else ""),
                          fill=self._col("text"), tags=("hover",),
                          font=self._font("small", th.f("small")))
        # 手柄最后画：始终在色杆/填充之上，明确可抓
        r = self._thumb_r + (2 if self._dragging else 0)
        c.create_oval(px - r, cy - r, px + r, cy + r,
                      fill=self._col("accent"), outline="#ffffff", width=2,
                      tags=("thumb",))

    @staticmethod
    def _tier_style(loss):
        """目损 → (颜色令牌, 杆高px)；<1 目无标记（V6 §41）。"""
        for threshold, color, bar_h in LOSS_TIERS:
            if loss >= threshold:
                return color, bar_h * 3   # 3/4/5 档 → 9/12/15px 杆高
        return None, 0
