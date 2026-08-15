"""ui.timeline —— 学习时间轴（V6 §38-43，Phase 6）。

底部常驻的单盘时间轴，把两个 previously 混淆的概念在视觉上拆开：

- **颜色 = AI 错误严重程度（目损）**：<1 目无标记，1-3 目黄(amber)，
  3-6 目橙(amber 加深/加粗)，>6 目红——只用 amber/red 两令牌分三档；
- **紫色外圈 = 学习价值（LearningPriority）**：本盘被选为学习节点
  （learning store 优先级 Top）的手加紫圈（learning_priority 专用紫，
  V6 §59：绝不挪作他用）。

点击/拖动跳到对应手；悬停显示 手数/目损/学习价值（V6 §42-43：
普通文案说"重点学习"，不暴露 0.92 这类内部数字）。
"""
from __future__ import annotations

import tkinter as tk

from ui import theme as th

# 目损 → 颜色档（V6 §41）
LOSS_TIERS = (
    (6.0, "red", 5),       # >6 目：红，高杆
    (3.0, "amber", 4),     # 3-6 目：橙档，中杆
    (1.0, "amber", 3),     # 1-3 目：黄档，矮杆
)


class LearningTimeline(tk.Canvas):
    def __init__(self, master, on_jump, height=44):
        super().__init__(master, height=height, bg=th.t("card"),
                         highlightthickness=1,
                         highlightbackground=th.t("muted"), bd=0)
        self._on_jump = on_jump
        self._points = []          # [{move, loss, priority}]
        self._total = 1
        self._current = 0
        self._hover_idx = None
        self._pad = 14
        for seq, handler in (("<Button-1>", self._on_click),
                             ("<Motion>", self._on_motion),
                             ("<Leave>", lambda _e: self._set_hover(None)),
                             ("<Configure>", lambda _e: self._draw())):
            self.bind(seq, handler)

    # ---- 数据 ----
    def set_data(self, total, points, current=0):
        """points: [{'move': int, 'loss': float, 'priority': float|None}]"""
        self._total = max(1, int(total))
        self._points = sorted(
            ({'move': int(p.get("move") or 0),
              'loss': float(p.get("loss") or 0.0),
              'priority': p.get("priority")} for p in points),
            key=lambda p: p["move"])
        self._current = int(current)
        self._draw()

    def set_current(self, n):
        if int(n) != self._current:
            self._current = int(n)
            self._draw()

    # ---- 几何 ----
    def _x_of(self, move):
        w = max(60, self.winfo_width())
        span = w - self._pad * 2
        return self._pad + span * (move / self._total)

    def _move_at(self, x):
        w = max(60, self.winfo_width())
        span = w - self._pad * 2
        if span <= 0:
            return 0
        return max(0, min(self._total, round((x - self._pad) * self._total / span)))

    # ---- 交互 ----
    def _on_click(self, event):
        self._on_jump(self._move_at(event.x))

    def _on_motion(self, event):
        move = self._move_at(event.x)
        idx = None
        for i, p in enumerate(self._points):
            if abs(p["move"] - move) <= max(1, self._total // 120):
                idx = i
                break
        self._set_hover(idx)

    def _set_hover(self, idx):
        if idx != self._hover_idx:
            self._hover_idx = idx
            self._draw()

    # ---- 绘制 ----
    def _draw(self):
        self.delete("all")
        w = max(60, self.winfo_width())
        h = max(30, self.winfo_height())
        base_y = h * 0.62
        self.create_line(self._pad, base_y, w - self._pad, base_y,
                         fill=th.t("muted"), width=2)
        # 当前位置游标
        cx = self._x_of(self._current)
        self.create_line(cx, 4, cx, h - 4, fill=th.t("accent"), width=2,
                         tags=("cursor",))
        for p in self._points:
            x = self._x_of(p["move"])
            color, bar_h = self._tier_style(p["loss"])
            if color is None:
                continue
            self.create_line(x, base_y, x, base_y - bar_h,
                             fill=th.t(color), width=3,
                             tags=("tick", str(p["move"])))
            # 学习价值紫圈（V6 §41：外圈表达 LearningPriority）
            if p.get("priority"):
                r = 7 if p["priority"] >= 0.6 else 5
                self.create_oval(x - r, base_y - bar_h - r - 2,
                                 x + r, base_y - bar_h + r - 2,
                                 outline=th.t("learning_priority"), width=2,
                                 tags=("ring", str(p["move"])))
        # 悬停信息（V6 §42：手数/目损/重点学习，不暴露内部数字）
        if self._hover_idx is not None and self._points:
            p = self._points[self._hover_idx]
            label = "第%d手 · 损失%.1f目%s" % (
                p["move"], p["loss"],
                " · 重点学习" if p.get("priority") else "")
            self.create_text(self._pad + 2, 3, anchor="nw", text=label,
                             font=th.f("small"), fill=th.t("text"),
                             tags=("hover",))

    @staticmethod
    def _tier_style(loss):
        """目损 → (颜色, 杆高px)；<1 目无标记（V6 §41）。"""
        for threshold, color, bar_h in LOSS_TIERS:
            if loss >= threshold:
                return color, bar_h * 3   # 3/4/5 档 → 9/12/15px 杆高
        return None, 0
