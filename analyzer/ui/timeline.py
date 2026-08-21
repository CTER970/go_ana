"""ui.timeline —— 学习时间轴（V6 §38-43，Phase 6；合并进度条 v2）。

单条画布同时承担（此前时间轴与进度条分两行、视觉不同步的问题就此消除）：

- **手数导航**：可拖动手柄 + 点击/拖动轨道跳到主线第 N 手
  （drag 过程走 on_change 实时跟随，松手走 on_commit，程序侧
  set_position 只改视觉不回调，避免 导航↔同步 反馈循环）；
- **颜色 = 目损**：<1 目无标记，1-3 黄，3-6 橙，>6 红（V6 §41）；
- **紫色外圈 = 学习价值**：本盘学习节点加 learning_priority 专用紫圈；
- 悬停显示"第N手 · 损失X目 · 重点学习"（不暴露内部算法数字）。

渲染（Phase 6 锐利化）：PIL 可用时轨道/色杆/紫圈/手柄全部走 3x 超采样
+ Lanczos 预渲染图（ui/render.py），静态层（轨道+色杆+紫圈）按
宽度/数据/配色缓存，拖动时仅重切填充条与手柄位图；无 PIL 环境保持
原生 canvas 绘制为降级路径（视觉与 Phase 6 之前一致）。

兼容 MoveScrubber 的对外接口（set_range / set_position / is_dragging /
redraw），app 侧旧引用可无缝切换。
"""
from __future__ import annotations

import tkinter as tk

from ui import render as v6render
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
        # PIL 预渲染缓存（PhotoImage 必须挂在 self 上防 GC）
        self._base_photo = None     # 轨道 + 色杆 + 紫圈（静态层）
        self._base_key = None
        self._fill_pil = None       # 全宽填充条（左端圆角），按需裁切
        self._fill_photo = None
        self._thumb_photos = {}     # (半径, 配色) → 位图
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
        self._base_key = None       # 数据变了，静态层缓存作废
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
        px = self._move_to_x(self._pos)
        # PIL 预渲染优先（真抗锯齿）；任何失败回到原生 canvas 降级
        if v6render._HAS_PIL and self._redraw_pil(w, h, x0, x1, cy, px):
            return
        self._redraw_canvas(x0, x1, cy, px)

    # ---- PIL 路径：静态层缓存 + 动态层裁切 ----
    def _tick_specs(self):
        """[(x 比例处无需，此处返回画点说明)] —— 统一供两种路径描述色杆/紫圈。"""
        specs = []
        for p in self._points:
            color, bar_h = self._tier_style(p["loss"])
            if color is None:
                continue
            specs.append((p["move"], self._col(color), bar_h, p.get("priority")))
        return specs

    def _base_signature(self, w, h, x0, x1, cy):
        """静态层缓存键：几何 + 数据 + 配色任一变化才重渲染。"""
        return (w, h, x0, x1, cy, tuple(self._tick_specs()),
                self._col("muted"), self._col("learning_priority"))

    def _redraw_pil(self, w, h, x0, x1, cy, px):
        try:
            self._draw_base(w, h, x0, x1, cy)
            self._draw_fill(x0, x1, cy, px)
            # 悬停信息（V6 §42-43：说人话，不暴露内部数字）
            self._draw_hover_text(x0)
            self._draw_thumb(px, cy)
            return True
        except Exception:
            return False

    def _draw_base(self, w, h, x0, x1, cy):
        key = self._base_signature(w, h, x0, x1, cy)
        if self._base_key == key and self._base_photo is not None:
            photo = self._base_photo
        else:
            ss = v6render.SUPER_SAMPLE
            muted = v6render.color_or(self._col("muted"), (69, 79, 74))
            ring_color = self._col("learning_priority")
            track_h = self._track_h

            def _paint(draw, _w, _h, ss):
                half = track_h / 2
                draw.rounded_rectangle(
                    [x0 * ss, (cy - half) * ss, x1 * ss, (cy + half) * ss],
                    radius=half * ss, fill=muted)
                for move, color, bar_h, priority in self._tick_specs():
                    x = self._move_to_x(move)
                    rgb = v6render.color_or(color, (224, 160, 67))
                    draw.line([(x * ss, (cy - bar_h) * ss), (x * ss, cy * ss)],
                              fill=rgb, width=3 * ss)
                    if priority:
                        r = 7 if priority >= 0.6 else 5
                        ring_y = cy - bar_h - r - 2
                        draw.ellipse(
                            [(x - r) * ss, (ring_y - r) * ss,
                             (x + r) * ss, (ring_y + r) * ss],
                            outline=v6render.color_or(ring_color, (155, 138, 251)),
                            width=2 * ss)

            photo = v6render.photo(w, h, _paint, master=self)
            if photo is None:
                raise RuntimeError("timeline base render unavailable")
            self._base_photo = photo
            self._base_key = key
        self.create_image(0, 0, anchor="nw", image=photo)

    def _draw_fill(self, x0, x1, cy, px):
        """已播放填充：全宽条缓存 + 按当前位置裁切（拖动零重渲染）。"""
        track_w = int(x1 - x0)
        fill_w = int(px - x0)
        if track_w <= 0 or fill_w < 2:
            return
        if self._fill_pil is None or self._fill_pil.size != (
                track_w * v6render.SUPER_SAMPLE, self._track_h * v6render.SUPER_SAMPLE):
            if not v6render._HAS_PIL:
                raise RuntimeError("PIL unavailable")
            ss = v6render.SUPER_SAMPLE
            half = self._track_h / 2
            img = v6render.Image.new(
                "RGBA", (track_w * ss, self._track_h * ss), (0, 0, 0, 0))
            draw = v6render.ImageDraw.Draw(img)
            draw.rounded_rectangle(
                [0, 0, track_w * ss - 1, self._track_h * ss - 1],
                radius=half * ss,
                fill=v6render.color_or(self._col("accent"), (61, 184, 160)))
            self._fill_pil = img
        crop = self._fill_pil.crop((0, 0, fill_w * v6render.SUPER_SAMPLE,
                                    self._track_h * v6render.SUPER_SAMPLE))
        self._fill_photo = v6render.ImageTk.PhotoImage(crop, master=self)
        if self._fill_photo is None:
            raise RuntimeError("timeline fill render unavailable")
        self.create_image(x0, cy - self._track_h / 2, anchor="nw",
                          image=self._fill_photo)

    def _draw_hover_text(self, x0):
        if self._hover_idx is None or not self._points:
            return
        p = self._points[self._hover_idx]
        self.create_text(x0 + 2, 4, anchor="nw",
                         text="第%d手 · 损失%.1f目%s" % (
                             p["move"], p["loss"],
                             " · 重点学习" if p.get("priority") else ""),
                         fill=self._col("text"), tags=("hover",),
                         font=self._font("small", th.f("small")))

    def _draw_thumb(self, px, cy):
        """手柄最后画：始终在色杆/填充之上，明确可抓。"""
        r = self._thumb_r + (2 if self._dragging else 0)
        accent = self._col("accent")
        outline = self._col("white")
        key = (r, accent, outline)
        photo = self._thumb_photos.get(key)
        if photo is None:
            size = (r + 2) * 2

            def _paint(draw, _w, _h, ss):
                draw.ellipse(
                    [2 * ss, 2 * ss, (size - 2) * ss, (size - 2) * ss],
                    fill=v6render.color_or(accent, (61, 184, 160)),
                    outline=v6render.color_or(outline, (248, 248, 240)),
                    width=2 * ss)

            photo = v6render.photo(size, size, _paint, master=self)
            if photo is None:
                raise RuntimeError("timeline thumb render unavailable")
            self._thumb_photos[key] = photo
        self.create_image(px, cy, anchor="center", image=photo)

    # ---- 降级路径：原生 canvas 绘制（无 PIL 环境保持原视觉） ----
    def _redraw_canvas(self, x0, x1, cy, px):
        c = self
        # 轨道 + 已播放填充
        c.create_rectangle(x0, cy - self._track_h / 2, x1, cy + self._track_h / 2,
                           fill=self._col("muted"), outline="", tags=("track",))
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
                      fill=self._col("accent"), outline=self._col("white"),
                      width=2, tags=("thumb",))

    @staticmethod
    def _tier_style(loss):
        """目损 → (颜色令牌, 杆高px)；<1 目无标记（V6 §41）。"""
        for threshold, color, bar_h in LOSS_TIERS:
            if loss >= threshold:
                return color, bar_h * 3   # 3/4/5 档 → 9/12/15px 杆高
        return None, 0
