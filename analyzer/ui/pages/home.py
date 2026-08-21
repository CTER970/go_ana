"""ui.pages.home —— 今日学习首页（V6 §10-14，Phase 3）。

只回答一个问题：**我现在应该做什么？**
- 继续复盘（第一主操作）：最近一盘未完成复盘的棋
- 今日复习（第二主操作）：到期错题 + 分类分布
- 当前训练主题：一次只给一个（learning_profile.top_training_theme）
- 最近趋势：≤4 个指标，当前值 + 方向

数据全部来自既有模块（game_library / mistake_book / learning_store /
learning_profile / player_profile），不新增业务逻辑；打开首页只读缓存，
不触发任何重新分析（V6 §106）。
"""
from __future__ import annotations

import tkinter as tk

from ui import components as C
from ui import theme as th
from ui import tokens


class HomePage(tk.Frame):
    name = "home"

    def __init__(self, parent, app):
        super().__init__(parent, bg=th.t("surface0"))
        self.app = app
        self._build()
        self._due_cache = []
        # 懒刷新：构造只搭骨架，首次 router.go("home") 才读数据（启动提速）

    # ---- 数据（全部轻量读） ----
    @staticmethod
    def _data(app):
        data = {"due": 0, "due_by_category": [], "summary": {},
                "theme": None, "resume": None, "trend_label": "样本不足"}
        try:
            # 单一事实源：到期数读 LearningEvent（与训练排序/画像同源，
            # 审查 #6）；事件库为空（全新环境）才回退错题本旧数据
            from learning_store import get_due_reviews
            due_events = get_due_reviews()
            if due_events:
                data["due"] = len(due_events)
                data["_due_events"] = due_events
            else:
                from mistake_book import book_stats
                data["due"] = int(book_stats().get("due") or 0)
        except Exception:
            pass
        try:
            from learning_profile import summarize_learning
            from learning_store import get_events
            summary = summarize_learning(get_events())
            data["summary"] = summary
            data["theme"] = summary.get("top_training_theme")
        except Exception:
            pass
        try:
            from game_library import search_records
            records = search_records("") or []
            for rec in records:                      # 最近打开且能复盘的一盘
                if rec.get("projectPath"):
                    data["resume"] = rec
                    break
        except Exception:
            pass
        try:
            from profile_store import get_or_rebuild
            profile = get_or_rebuild()
            data["trend_label"] = {
                "improving": "改善中", "stable": "稳定",
                "declining": "需关注"}.get(
                    profile.recent_trend.direction, "样本不足")
        except Exception:
            pass
        return data

    def _due_categories(self):
        """到期错题按学习类别聚合（读 LearningEvent，最多 4 类 + N）。"""
        try:
            from taxonomy import category_label
            counts = {}
            for evt in (self._due_cache or []):
                cat = evt.primary_category or "unclassified"
                counts[cat] = counts.get(cat, 0) + 1
            ordered = sorted(counts.items(), key=lambda kv: -kv[1])
            top = [(category_label(c), n) for c, n in ordered[:4]
                   if c != "unclassified"]
            rest = sum(n for c, n in ordered[4:]) + counts.get("unclassified", 0)
            return top, rest
        except Exception:
            return [], 0

    # ---- 布局 ----
    def _build(self):
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        head = tk.Frame(self, bg=th.t("surface0"))
        head.grid(row=0, column=0, columnspan=2, sticky="ew",
                  padx=th.sp("xl"), pady=(th.sp("xl"), th.sp("md")))
        tk.Label(head, text="今天该做什么", font=th.f("display"),
                 bg=th.t("surface0"), fg=th.t("text")).pack(side=tk.LEFT)
        self.lbl_trend_head = tk.Label(
            head, text="", font=th.f("ui"), bg=th.t("surface0"),
            fg=th.t("subtext"))
        self.lbl_trend_head.pack(side=tk.RIGHT)

        self.zone_top = tk.Frame(self, bg=th.t("surface0"))
        self.zone_top.grid(row=1, column=0, columnspan=2, sticky="nsew",
                           padx=th.sp("xl"))
        self.rowconfigure(1, weight=1)
        self.zone_top.columnconfigure(0, weight=1)
        self.zone_top.columnconfigure(1, weight=1)

        self.zone_theme = tk.Frame(self, bg=th.t("surface0"))
        self.zone_theme.grid(row=2, column=0, columnspan=2, sticky="ew",
                             padx=th.sp("xl"), pady=(th.sp("lg"), 0))
        self.zone_trend = tk.Frame(self, bg=th.t("surface0"))
        self.zone_trend.grid(row=3, column=0, columnspan=2, sticky="ew",
                             padx=th.sp("xl"), pady=(th.sp("lg"), 0))
        # 弱入口（减法 R2）：一级导航不再有"我的学习"，详情从首页这里进入
        link = tk.Label(self, text="查看学习详情 ›", font=th.f("small"),
                        bg=th.t("surface0"), fg=th.t("subtext"),
                        cursor="hand2")
        link.grid(row=4, column=0, columnspan=2, sticky="e",
                  padx=th.sp("xl"), pady=(th.sp("sm"), th.sp("lg")))
        link.bind("<Button-1>", lambda _e: self.app.router.go("learning"))

    def refresh(self):
        for zone in (self.zone_top, self.zone_theme, self.zone_trend):
            for child in zone.winfo_children():
                child.destroy()
        data = self._data(self.app)
        self._due_cache = data.pop("_due_events", [])
        self.lbl_trend_head.config(
            text="最近%d盘：%s" % (data["summary"].get("recent_games", 0)
                                   or 0, data["trend_label"]))

        self._card_resume(data)
        self._card_review(data)
        self._card_theme(data)
        self._card_trend(data)

    # ---- 卡片：继续复盘（V6 §12，第一主操作） ----
    def _card_resume(self, data):
        outer, body = C.card(self.zone_top, "继续复盘")
        outer.grid(row=0, column=0, sticky="nsew", padx=(0, th.sp("md")))
        rec = data.get("resume")
        if not rec:
            C.empty_state(body, "还没有棋谱",
                          "导入一盘自己的实战棋，系统会自动找出值得学习的位置。",
                          "导入 SGF", self.app.do_import_sgf)
            return
        meta = " · ".join(str(x) for x in (
            rec.get("name") or "未命名",
            "%d 手" % int(rec.get("moves") or 0),
            "分析 %s/%s" % (rec.get("analyzed", 0), rec.get("totalNodes", 0)),
        ) if x)
        tk.Label(body, text=meta, font=th.f("ui"), bg=th.t("card"),
                 fg=th.t("text"), wraplength=380,
                 justify=tk.LEFT).pack(anchor="w")
        learned = self._resume_progress(rec)
        if learned:
            tk.Label(body, text=learned, font=th.f("small"),
                     bg=th.t("card"), fg=th.t("subtext")).pack(anchor="w",
                                                                pady=(th.sp("xs"), 0))
        C.button(body, "继续复盘", lambda: self._open_record(rec),
                 variant="primary").pack(anchor="w", pady=(th.sp("md"), 0))

    def _resume_progress(self, rec):
        try:
            from learning_store import get_events_by_game
            events = get_events_by_game(rec.get("id"))
            if not events:
                return None
            done = sum(1 for e in events if e.attempts or e.retry_status)
            return "学习点 %d/%d 已处理" % (done, len(events))
        except Exception:
            return None

    def _open_record(self, rec):
        import os
        path = rec.get("projectPath")
        if path and os.path.exists(path):
            self.app.router.go("review")
            self.app._load_project_from_path(
                path, rec.get("name") or os.path.basename(path),
                library_record_id=rec.get("id"))
            self.app.open_problem_drill()

    # ---- 卡片：今日复习（V6 §13，第二主操作） ----
    def _card_review(self, data):
        outer, body = C.card(self.zone_top, "今日复习")
        outer.grid(row=0, column=1, sticky="nsew", padx=(th.sp("md"), 0))
        due = data.get("due", 0)
        if due <= 0:
            C.empty_state(body, "今天没有到期复习",
                          "去下一盘棋，或者继续完成尚未复盘的棋局。")
            return
        tk.Label(body, text="%d 题" % due, font=th.f("data"),
                 bg=th.t("card"), fg=th.t("red")).pack(anchor="w")
        cats, rest = self._due_categories()
        for label, n in cats:
            tk.Label(body, text="%s　%s" % (label, n), font=th.f("ui"),
                     bg=th.t("card"), fg=th.t("text")).pack(anchor="w")
        if rest:
            tk.Label(body, text="其他 +%d" % rest, font=th.f("small"),
                     bg=th.t("card"), fg=th.t("subtext")).pack(anchor="w")
        C.button(body, "开始复习",
                 self.app._start_next_due_mistake_review,
                 variant="primary").pack(anchor="w", pady=(th.sp("md"), 0))

    # ---- 卡片：当前训练主题（V6 §14，一次只给一个） ----
    def _card_theme(self, data):
        theme = data.get("theme")
        outer, body = C.card(self.zone_theme, "当前训练主题")
        outer.pack(fill="x")
        if not theme:
            C.empty_state(body, "还没有形成训练主题",
                          "完成至少 5 盘分析以后，系统会从你的重复错误里"
                          "挑出当前最值得改的一件事。")
            return
        from taxonomy import category_label
        rec = (data.get("summary", {}).get("recurrence_by_category")
               or {}).get(theme["category"], {})
        tk.Label(body, text=category_label(theme["category"]),
                 font=th.f("h1"), bg=th.t("card"),
                 fg=th.t("learning_priority")).pack(anchor="w")
        tk.Label(body, text="最近 %d 盘出现 %d 盘 · 平均损失 %.1f 目" % (
            data["summary"].get("recent_games", 0),
            theme.get("count", 0), theme.get("avg_loss", 0.0)),
            font=th.f("ui"), bg=th.t("card"),
            fg=th.t("text")).pack(anchor="w", pady=(th.sp("xs"), 0))
        if rec.get("earlier"):
            current, earlier = theme.get("count", 0), rec.get("earlier", 0)
            if earlier and current < earlier:
                tk.Label(body, text="相比之前 %d 盘 ↓ 改善中" % earlier,
                         font=th.f("small"), bg=th.t("card"),
                         fg=th.t("green")).pack(anchor="w")

    # ---- 卡片：最近趋势（V6 §52，≤4 指标，当前值+方向） ----
    def _card_trend(self, data):
        outer, body = C.card(self.zone_trend, "最近趋势")
        outer.pack(fill="x")
        s = data.get("summary") or {}
        row = tk.Frame(body, bg=th.t("card"))
        row.pack(fill="x")
        def _item(label, value, trend=None):
            if value is not None:
                C.metric(row, label, value, trend)
        _item("重复类别率", s.get("repeat_category_rate"))
        _item("主动纠正率", s.get("correction_rate"))
        _item("多次复习保持", s.get("multi_review_retention"))
        _item("7日保持", s.get("retention_7d"))
        if not s:
            tk.Label(body, text="完成至少 5 盘分析以后，这里会逐渐形成你的个人学习画像。",
                     font=th.f("ui"), bg=th.t("card"),
                     fg=th.t("subtext")).pack(anchor="w", pady=th.sp("sm"))
