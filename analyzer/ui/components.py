"""ui.components —— V6 基础组件库（Phase 1，V6 §93）。

只依赖 ui.theme 令牌，不依赖 app.py；旧 UI 的 _make_card_frame 等
保持不动，新页面一律用这里的组件，逐步统一视觉。
组件工厂返回 tk 原生控件（CTk 存在时 button 自动升级圆角）。
"""
from __future__ import annotations

import tkinter as tk

from ui import theme as th

try:
    import customtkinter as ctk
    _HAS_CTK = True
except Exception:  # pragma: no cover
    _HAS_CTK = False


# ---------- 按钮（V6 §65：Primary / Secondary / Ghost / Danger 四种） ----------
def button(parent, text, command, variant="secondary", **kw):
    accent = th.t("accent")
    if _HAS_CTK:
        styles = {
            "primary": dict(fg_color=accent, hover_color=th.t("accent_h"),
                            text_color="#ffffff"),
            "secondary": dict(fg_color=th.t("card2"),
                              hover_color=th.t("muted"),
                              border_width=1, border_color=th.t("accent"),
                              text_color=th.t("text")),
            "ghost": dict(fg_color="transparent", hover_color=th.t("card2"),
                          text_color=th.t("subtext")),
            "danger": dict(fg_color=th.t("red"), hover_color=th.t("amber"),
                           text_color="#ffffff"),
        }
        btn = ctk.CTkButton(parent, text=text, command=command,
                            corner_radius=th.radius("button"),
                            font=th.f("ui"), height=30,
                            **styles.get(variant, styles["secondary"]), **kw)
        return btn
    styles = {
        "primary": dict(bg=accent, fg="#ffffff", activebackground=th.t("accent_h")),
        "secondary": dict(bg=th.t("card2"), fg=th.t("text"),
                          activebackground=th.t("muted")),
        "ghost": dict(bg=th.t("bg"), fg=th.t("subtext"),
                      activebackground=th.t("card2")),
        "danger": dict(bg=th.t("red"), fg="#ffffff",
                       activebackground=th.t("amber")),
    }
    return tk.Button(parent, text=text, command=command, relief=tk.FLAT,
                     bd=0, padx=th.sp("lg"), pady=th.sp("sm"),
                     font=th.f("ui"), cursor="hand2",
                     **styles.get(variant, styles["secondary"]), **kw)


# ---------- 卡片（V6 §63：圆角 10 / 边界分层，不做重阴影） ----------
def card(parent, title=None, accent_title=True):
    outer = tk.Frame(parent, bg=th.t("card"),
                     highlightthickness=1, highlightbackground=th.t("muted"))
    outer.pack_propagate(True)
    if title:
        tk.Label(outer, text=title, font=th.f("section"),
                 bg=th.t("card"),
                 fg=th.t("accent") if accent_title else th.t("text")
                 ).pack(anchor="w", padx=th.sp("lg"),
                        pady=(th.sp("md"), th.sp("xs")))
    body = tk.Frame(outer, bg=th.t("card"))
    body.pack(fill="both", expand=True, padx=th.sp("lg"),
              pady=(0, th.sp("lg")))
    return outer, body


# ---------- 指标（V6 §61：数值一律等宽字体） ----------
def metric(parent, label, value, trend=None, value_color=None):
    """label + 大数值 + 可选趋势箭头（↓改善用绿，↑恶化用红）。"""
    row = tk.Frame(parent, bg=th.t("card"))
    row.pack(side=tk.LEFT, fill="y", expand=True, padx=th.sp("sm"))
    tk.Label(row, text=label, font=th.f("small"), bg=th.t("card"),
             fg=th.t("subtext")).pack(anchor="w")
    val_row = tk.Frame(row, bg=th.t("card"))
    val_row.pack(anchor="w")
    color = value_color or th.t("text")
    tk.Label(val_row, text=str(value), font=th.f("data"),
             bg=th.t("card"), fg=color).pack(side=tk.LEFT)
    if trend:
        good = trend.startswith("↓") or trend.startswith("改善")
        tk.Label(val_row, text=" %s" % trend, font=th.f("small"),
                 bg=th.t("card"),
                 fg=th.t("green") if good else th.t("red")).pack(side=tk.LEFT)
    return row


# ---------- 分段控件（V6 §23：学习 | 研究） ----------
def segmented(parent, options, command, initial=0):
    """一组互斥切换钮；初始只渲染选中态不触发回调。返回 (frame, state)。"""
    frame = tk.Frame(parent, bg=th.t("card2"),
                     highlightthickness=1, highlightbackground=th.t("muted"))
    state = {"index": initial}
    buttons = []

    def _apply(index, fire=True):
        state["index"] = index
        for i, btn in enumerate(buttons):
            is_on = i == index
            try:
                btn.configure(
                    bg=th.t("accent") if is_on else th.t("card2"),
                    fg="#ffffff" if is_on else th.t("subtext"))
            except tk.TclError:
                pass
        if fire:
            command(index)

    for i, text in enumerate(options):
        btn = tk.Button(frame, text=text, relief=tk.FLAT, bd=0,
                        padx=th.sp("lg"), pady=th.sp("xs"),
                        font=th.f("ui"), cursor="hand2",
                        command=lambda idx=i: _apply(idx))
        btn.pack(side=tk.LEFT, padx=(th.sp("xs") if i else th.sp("sm"),
                                     th.sp("sm") if i < len(options) - 1 else th.sp("xs")),
                 pady=th.sp("xs"))
        buttons.append(btn)
    _apply(initial, fire=False)
    return frame, state


# ---------- 空态（V6 §69：每个页面必须有明确空态） ----------
def empty_state(parent, title, hint="", action_text=None, action=None):
    box = tk.Frame(parent, bg=th.t("card"),
                   highlightthickness=1, highlightbackground=th.t("muted"))
    box.pack(fill="x")
    tk.Label(box, text=title, font=th.f("h2"), bg=th.t("card"),
             fg=th.t("text")).pack(pady=(th.sp("xl"), th.sp("sm")))
    if hint:
        tk.Label(box, text=hint, font=th.f("ui"), bg=th.t("card"),
                 fg=th.t("subtext"), wraplength=420,
                 justify=tk.LEFT).pack(padx=th.sp("xl"))
    if action_text and action:
        button(box, action_text, action, variant="primary").pack(
            pady=(th.sp("lg"), th.sp("xl")))
    else:
        tk.Frame(box, bg=th.t("card"), height=th.sp("xl")).pack()
    return box


# ---------- 状态徽标（V6 §67 + §87：不只靠颜色，必须带文字） ----------
def status_badge(parent, level, text):
    """level: running/success/warning/error → 图符+文字+语义色。"""
    styles = {
        "running": (th.t("accent"), "●"),
        "success": (th.t("green"), "✓"),
        "warning": (th.t("amber"), "!"),
        "error":   (th.t("red"), "×"),
    }
    color, icon = styles.get(level, (th.t("subtext"), "·"))
    row = tk.Frame(parent, bg=th.t("card"))
    row.pack(anchor="w")
    tk.Label(row, text="%s %s" % (icon, text), font=th.f("ui"),
             bg=th.t("card"), fg=color).pack(anchor="w")
    return row
