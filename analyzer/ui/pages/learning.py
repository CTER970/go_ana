"""ui.pages.learning —— 我的学习一级页面（V6 Phase 8）。

替代"我的学习"路由弹个人画像 Toplevel 的过渡态：页面内嵌长期画像的
核心结论（学习指标 / 阶段表现 / 优势弱点 / 重复类别）。只读缓存
（learning_store / learning_profile / profile_store），完整明细仍走
app.open_player_profile / open_style_profile 窗口。
"""
from __future__ import annotations

import tkinter as tk

from ui import components as C
from ui import theme as th


def _summary():
    try:
        from learning_profile import summarize_learning
        from learning_store import get_events
        return summarize_learning(get_events()) or {}
    except Exception:
        return {}


def _profile():
    try:
        from profile_store import get_or_rebuild
        return get_or_rebuild()
    except Exception:
        return None


class LearningPage(tk.Frame):
    name = "learning"

    def __init__(self, parent, app):
        super().__init__(parent, bg=th.t("surface0"))
        self.app = app
        self._build()

    def _build(self):
        head = tk.Frame(self, bg=th.t("surface0"))
        head.pack(fill="x", padx=th.sp("xl"), pady=(th.sp("xl"), th.sp("md")))
        tk.Label(head, text="我的学习", font=th.f("display"),
                 bg=th.t("surface0"), fg=th.t("text")).pack(side=tk.LEFT)
        self.lbl_head = tk.Label(head, text="", font=th.f("ui"),
                                 bg=th.t("surface0"), fg=th.t("subtext"))
        self.lbl_head.pack(side=tk.RIGHT)
        self.zone_metrics = tk.Frame(self, bg=th.t("surface0"))
        self.zone_metrics.pack(fill="x", padx=th.sp("xl"))
        self.zone_mid = tk.Frame(self, bg=th.t("surface0"))
        self.zone_mid.pack(fill="x", padx=th.sp("xl"), pady=(th.sp("lg"), 0))
        self.zone_mid.columnconfigure(0, weight=1)
        self.zone_mid.columnconfigure(1, weight=1)
        self.zone_bottom = tk.Frame(self, bg=th.t("surface0"))
        self.zone_bottom.pack(fill="x", padx=th.sp("xl"),
                              pady=(th.sp("lg"), th.sp("xl")))

    def refresh(self):
        for zone in (self.zone_metrics, self.zone_mid, self.zone_bottom):
            for child in zone.winfo_children():
                child.destroy()
        summary = _summary()
        profile = _profile()
        games = int(summary.get("recent_games", 0) or 0)
        direction = getattr(getattr(profile, "recent_trend", None), "direction", "")
        trend_label = {"improving": "改善中", "stable": "稳定",
                       "declining": "需关注"}.get(direction, "样本不足")
        self.lbl_head.config(text="近 %d 盘 · %s" % (games, trend_label))
        self._card_metrics(summary)
        self._card_stages(profile)
        self._card_weakness(profile)
        self._card_categories(summary)

    # ---- 卡片：学习指标（与首页趋势同源，值+空态） ----
    def _card_metrics(self, summary):
        outer, body = C.card(self.zone_metrics, "学习指标")
        outer.pack(fill="x")
        row = tk.Frame(body, bg=th.t("card"))
        row.pack(fill="x")
        for label, key in (("重复类别率", "repeat_category_rate"),
                           ("主动纠正率", "correction_rate"),
                           ("多次复习保持", "multi_review_retention"),
                           ("7日保持", "retention_7d")):
            value = summary.get(key)
            C.metric(row, label, "%.0f%%" % value if value is not None else "—")
        if not summary:
            tk.Label(body, text="完成至少 5 盘分析以后，这里会逐渐形成你的个人学习画像。",
                     font=th.f("ui"), bg=th.t("card"),
                     fg=th.t("subtext")).pack(anchor="w", pady=th.sp("sm"))

    # ---- 卡片：三阶段平均目损（画像缓存） ----
    def _card_stages(self, profile):
        outer, body = C.card(self.zone_mid, "阶段表现（平均目损）")
        outer.grid(row=0, column=0, sticky="nsew", padx=(0, th.sp("md")))
        stages = [("布局", getattr(profile, "opening", None)),
                  ("中盘", getattr(profile, "middle", None)),
                  ("官子", getattr(profile, "endgame", None))]
        values = [(label, getattr(stats, "avg_score_loss", None))
                  for label, stats in stages]
        known = [v for _l, v in values if v is not None]
        worst = max(known) if known else None
        if not known:
            tk.Label(body, text="尚无阶段统计。", font=th.f("ui"),
                     bg=th.t("card"), fg=th.t("subtext")).pack(anchor="w")
            return
        row = tk.Frame(body, bg=th.t("card"))
        row.pack(fill="x")
        for label, value in values:
            C.metric(row, label, "%.1f" % value if value is not None else "—",
                     value_color=th.t("red") if (value is not None
                                                 and value == worst) else None)

    # ---- 卡片：优势 / 弱点 / 建议（画像缓存） ----
    def _card_weakness(self, profile):
        outer, body = C.card(self.zone_mid, "优势与弱点")
        outer.grid(row=0, column=1, sticky="nsew", padx=(th.sp("md"), 0))
        strengths = list(getattr(profile, "strengths", None) or [])[:3]
        weaknesses = list(getattr(profile, "weaknesses", None) or [])[:3]
        recommendations = list(getattr(profile, "recommendations", None) or [])[:2]
        if not (strengths or weaknesses):
            tk.Label(body, text="画像样本还不足以给出优势/弱点结论。",
                     font=th.f("ui"), bg=th.t("card"),
                     fg=th.t("subtext")).pack(anchor="w")
        for text in strengths:
            C.status_badge(body, "success", text)
        for text in weaknesses:
            C.status_badge(body, "warning", text)
        for text in recommendations:
            tk.Label(body, text="· %s" % text, font=th.f("small"),
                     bg=th.t("card"), fg=th.t("subtext"),
                     wraplength=340, justify=tk.LEFT).pack(anchor="w")
        actions = tk.Frame(body, bg=th.t("card"))
        actions.pack(anchor="w", pady=(th.sp("md"), 0))
        C.button(actions, "完整画像", self.app.open_player_profile).pack(
            side=tk.LEFT, padx=(0, th.sp("sm")))
        if hasattr(self.app, "open_style_profile"):
            C.button(actions, "棋风画像", self.app.open_style_profile).pack(
                side=tk.LEFT)

    # ---- 卡片：重复错误类别（频率+出现盘占比） ----
    def _card_categories(self, summary):
        outer, body = C.card(self.zone_bottom, "重复错误类别（近窗口出现盘数）")
        outer.pack(fill="x")
        dist = summary.get("category_distribution") or {}
        if not dist:
            tk.Label(body, text="暂无类别分布。", font=th.f("ui"),
                     bg=th.t("card"), fg=th.t("subtext")).pack(anchor="w")
            return
        try:
            from taxonomy import category_label
            cat_label = lambda c: category_label(c) if c != "unclassified" else "未分类"
        except Exception:
            cat_label = lambda c: c
        for cat, info in list(dist.items())[:6]:
            row = tk.Frame(body, bg=th.t("card"))
            row.pack(anchor="w", fill="x", pady=1)
            tk.Label(row, text=cat_label(cat), font=th.f("ui"),
                     bg=th.t("card"), fg=th.t("text")).pack(side=tk.LEFT)
            tk.Label(row, text="%d 盘 / %d 次 · %s%%" % (
                int(info.get("games", 0)), int(info.get("count", 0)),
                info.get("pct", 0)), font=th.f("small"),
                bg=th.t("card"), fg=th.t("subtext")).pack(side=tk.RIGHT)
