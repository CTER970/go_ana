"""scrubber —— 可拖动的棋局进度条（Canvas 滑块）。

替代细瘦的 ttk.Scale：明显的滑块手柄 + 已播放填充 + 点击/拖动跳到手数。
程序侧 set_position() 只更新视觉、不触发回调（避免 导航↔同步 反馈循环）；
用户拖动或点击轨道才通过 on_change(n) 跳转主线第 N 手。
"""
from __future__ import annotations

import tkinter as tk


class MoveScrubber(tk.Canvas):
    def __init__(self, master, on_change, on_commit=None, height=46,
                 colors=None, fonts=None):
        super().__init__(master, height=height,
                         bg=(colors or {}).get("card", "#f5f6f8"),
                         highlightthickness=0, bd=0)
        self._on_change = on_change
        self._on_commit = on_change if on_commit is None else on_commit
        self._colors = colors or {}
        self._fonts = fonts or {}
        self._max = 1
        self._pos = 0
        self._dragging = False
        self._hover = False
        self._track_pad = 10
        self._track_h = 12
        self._thumb_r = 13
        for seq, handler in (("<Button-1>", self._on_press),
                             ("<B1-Motion>", self._on_motion),
                             ("<ButtonRelease-1>", self._on_release),
                             ("<Enter>", self._on_enter),
                             ("<Leave>", self._on_leave),
                             ("<Configure>", lambda e: self.redraw())):
            self.bind(seq, handler)
        # 兜底：未布局前给个宽度，redraw 不至于拿到 1
        self.configure(width=200)

    # ---- 对外接口 ----
    def set_range(self, max_n):
        self._max = max(1, int(max_n))
        self.redraw()

    def set_position(self, n):
        """程序侧同步：只更新视觉，不触发 on_change。"""
        self._pos = max(0, min(int(n), self._max))
        self.redraw()

    @property
    def is_dragging(self):
        return self._dragging

    # ---- 几何 ----
    def _track_geom(self):
        w = max(40, self.winfo_width())
        h = max(20, self.winfo_height())
        return self._track_pad, w - self._track_pad, h / 2.0

    def _x_to_move(self, x):
        x0, x1, _cy = self._track_geom()
        if x1 <= x0:
            return 0
        frac = max(0.0, min(1.0, (x - x0) / (x1 - x0)))
        return int(round(frac * self._max))

    def _move_to_x(self, n):
        x0, x1, _cy = self._track_geom()
        if self._max <= 0:
            return x0
        return x0 + (n / self._max) * (x1 - x0)

    # ---- 交互 ----
    def _on_press(self, event):
        self._dragging = True
        # force=True：按下即视为明确跳转意图，即使落在当前手位置也触发回调，
        # 避免 max 未就绪时点击中间被 n==_pos 吞掉（app 侧借此次回调校准范围）。
        self._jump_to(event.x, commit=False, force=True)

    def _on_motion(self, event):
        if not self._dragging:
            return
        self._jump_to(event.x, commit=False)

    def _on_release(self, event):
        if not self._dragging:
            return
        self._dragging = False
        self._jump_to(event.x, commit=True)
        self.redraw()

    def _jump_to(self, x, commit, force=False):
        n = self._x_to_move(x)
        if n == self._pos and not commit and not force:
            return
        self._pos = n
        self.redraw()
        (self._on_commit if commit else self._on_change)(n)

    def _on_enter(self, _event):
        self._hover = True
        self.configure(cursor="hand2")
        self.redraw()

    def _on_leave(self, _event):
        self._hover = False
        self.configure(cursor="")
        self.redraw()

    # ---- 绘制 ----
    def redraw(self):
        c = self
        c.delete("all")
        w = max(40, self.winfo_width())
        h = max(20, self.winfo_height())
        if w < 30:
            return
        x0, x1, cy = self._track_geom()
        th = self._track_h
        col_track = self._colors.get("muted", "#d0d4da")
        col_fill = self._colors.get("accent", "#1a5fd0")
        col_thumb = self._colors.get("accent", "#1a5fd0")
        r = self._thumb_r + (2 if (self._hover or self._dragging) else 0)
        # 轨道（圆角感用 outline 描边近似）
        c.create_rectangle(x0, cy - th / 2, x1, cy + th / 2,
                           fill=col_track, outline="", tags=("track",))
        # 已播放部分
        px = self._move_to_x(self._pos)
        c.create_rectangle(x0, cy - th / 2, max(x0, px), cy + th / 2,
                           fill=col_fill, outline="", tags=("fill",))
        # 滑块手柄（明显可抓）
        c.create_oval(px - r, cy - r, px + r, cy + r,
                      fill=col_thumb, outline="#ffffff", width=2, tags=("thumb",))
