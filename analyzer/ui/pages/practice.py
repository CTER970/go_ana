"""ui.pages.practice —— 复习一级页面（V6 Phase 7）。

替代"复习"导航弹出错题本 Toplevel 的过渡态：页面内嵌到期队列、
掌握状态分布与最近到期错题。只读缓存（LearningEvent / 错题本），
复习交互（隐藏答案落子判分）仍走 app._start_next_due_mistake_review；
官子收束训练（GAP-3）入口在本页，训练窗口与判分逻辑都在 app 侧
（app.open_endgame_drill），V6 / 非 V6 路径共用同一入口。
"""
from __future__ import annotations

import tkinter as tk

from ui import components as C
from ui import theme as th


def _due_events():
    """到期复习题（单一事实源：LearningEvent，与首页/训练排序同源）。"""
    try:
        from learning_store import get_due_reviews
        return list(get_due_reviews() or [])
    except Exception:
        return []


def _book_stats():
    try:
        from mistake_book import book_stats
        return book_stats()
    except Exception:
        return {"total": 0, "due": 0, "mastered": 0, "reviewed": 0,
                "by_mastery": {}, "attempts": 0}


def _tree_has_analysis(app):
    """当前棋局主线是否挂有分析缓存（官子训练可用性提示，只走主线 O(n)）。"""
    node = getattr(getattr(app, "tree", None), "root", None)
    try:
        while node is not None:
            if getattr(node, "analysis", None):
                return True
            node = node.children[0] if node.children else None
    except Exception:
        return False
    return False


class PracticePage(tk.Frame):
    name = "practice"

    def __init__(self, parent, app):
        super().__init__(parent, bg=th.t("surface0"))
        self.app = app
        self._build()

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        head = tk.Frame(self, bg=th.t("surface0"))
        head.grid(row=0, column=0, columnspan=2, sticky="ew",
                  padx=th.sp("xl"), pady=(th.sp("xl"), th.sp("md")))
        tk.Label(head, text="复习", font=th.f("display"),
                 bg=th.t("surface0"), fg=th.t("text")).pack(side=tk.LEFT)
        self.lbl_head = tk.Label(head, text="", font=th.f("ui"),
                                 bg=th.t("surface0"), fg=th.t("subtext"))
        self.lbl_head.pack(side=tk.RIGHT)
        self.zone_top = tk.Frame(self, bg=th.t("surface0"))
        self.zone_top.grid(row=1, column=0, columnspan=2, sticky="nsew",
                           padx=th.sp("xl"))
        self.rowconfigure(1, weight=1)
        self.zone_top.columnconfigure(0, weight=1)
        self.zone_top.columnconfigure(1, weight=1)
        self.zone_list = tk.Frame(self, bg=th.t("surface0"))
        self.zone_list.grid(row=2, column=0, columnspan=2, sticky="ew",
                            padx=th.sp("xl"), pady=(th.sp("lg"), th.sp("xl")))

    def refresh(self):
        for zone in (self.zone_top, self.zone_list):
            for child in zone.winfo_children():
                child.destroy()
        due = _due_events()
        stats = _book_stats()
        due_count = len(due) or int(stats.get("due") or 0)
        self.lbl_head.config(text="错题总量 %d · 已复习 %d" % (
            int(stats.get("total") or 0), int(stats.get("reviewed") or 0)))
        self._card_start(due_count, due)
        self._card_mastery(stats)
        self._card_endgame()
        self._card_due_list(due)

    # ---- 卡片：开始复习（第一主操作） ----
    def _card_start(self, due_count, due):
        outer, body = C.card(self.zone_top, "今日到期")
        outer.grid(row=0, column=0, sticky="nsew", padx=(0, th.sp("md")))
        if due_count <= 0:
            C.empty_state(body, "今天没有到期复习",
                          "去下一盘棋，或者继续完成尚未复盘的棋局；新的问题手会自动进入复习队列。")
            return
        tk.Label(body, text="%d 题" % due_count, font=th.f("data"),
                 bg=th.t("card"), fg=th.t("red")).pack(anchor="w")
        cats = {}
        for evt in due:
            cat = evt.primary_category or "unclassified"
            cats[cat] = cats.get(cat, 0) + 1
        if cats:
            try:
                from taxonomy import category_label
                ordered = sorted(cats.items(), key=lambda kv: -kv[1])[:4]
                for cat, n in ordered:
                    label = category_label(cat) if cat != "unclassified" else "未分类"
                    tk.Label(body, text="%s　%s" % (label, n), font=th.f("ui"),
                             bg=th.t("card"), fg=th.t("text")).pack(anchor="w")
            except Exception:
                pass
        C.button(body, "开始复习", self.app._start_next_due_mistake_review,
                 variant="primary").pack(anchor="w", pady=(th.sp("md"), 0))

    # ---- 卡片：掌握状态分布 ----
    def _card_mastery(self, stats):
        outer, body = C.card(self.zone_top, "掌握状态")
        outer.grid(row=0, column=1, sticky="nsew", padx=(th.sp("md"), 0))
        by_mastery = stats.get("by_mastery") or {}
        labels = [
            ("new", "未学", th.t("subtext")),
            ("understanding", "理解中", th.t("accent")),
            ("retained", "已巩固", th.t("green")),
            ("transferred", "已迁移", th.t("green")),
            ("unstable", "实战不稳", th.t("red")),
        ]
        rows = [(label, by_mastery.get(key, 0), color)
                for key, label, color in labels]
        if not any(n for _l, n, _c in rows):
            tk.Label(body, text="还没有错题记录。完成整盘分析后，高价值问题手会自动进入复习队列。",
                     font=th.f("ui"), bg=th.t("card"), fg=th.t("subtext"),
                     wraplength=360, justify=tk.LEFT).pack(anchor="w")
            return
        for label, n, color in rows:
            row = tk.Frame(body, bg=th.t("card"))
            row.pack(anchor="w", fill="x")
            tk.Label(row, text="%s　%d" % (label, n), font=th.f("ui"),
                     bg=th.t("card"), fg=color).pack(side=tk.LEFT)

    # ---- 卡片：官子收束训练（GAP-3，本局终局段自动出题） ----
    def _card_endgame(self):
        outer, body = C.card(self.zone_top, "官子收束训练")
        outer.grid(row=1, column=0, columnspan=2, sticky="ew",
                   pady=(th.sp("lg"), 0))
        tk.Label(body, text="从当前已分析棋局的终局段自动生成收束题"
                            "（目损收束 / 先后手转换），题面即实战局面，"
                            "棋盘落子作答、AI 判分并对比最佳收束序列。",
                 font=th.f("ui"), bg=th.t("card"), fg=th.t("subtext"),
                 wraplength=560, justify=tk.LEFT).pack(anchor="w")
        if not _tree_has_analysis(self.app):
            C.status_badge(body, "warning",
                           "当前棋局还没有分析缓存——先在复盘页「补全分析」再开始")
        C.button(body, "开始官子训练", self.app.open_endgame_drill,
                 variant="primary").pack(anchor="w", pady=(th.sp("md"), 0))

    # ---- 卡片：最近到期错题明细 ----
    def _card_due_list(self, due):
        outer, body = C.card(self.zone_list, "到期明细（最多 8 条）")
        outer.pack(fill="x")
        if not due:
            tk.Label(body, text="无到期明细。", font=th.f("ui"),
                     bg=th.t("card"), fg=th.t("subtext")).pack(anchor="w")
            return
        try:
            from taxonomy import category_label
            cat_label = lambda c: category_label(c) if c else "未分类"
        except Exception:
            cat_label = lambda c: c or "未分类"
        for evt in due[:8]:
            row = tk.Frame(body, bg=th.t("card"))
            row.pack(anchor="w", fill="x", pady=1)
            tk.Label(row, text="%s · 第%d手 · %s" % (
                evt.game_name or evt.game_id[:8], int(evt.move_no or 0),
                cat_label(evt.primary_category)), font=th.f("ui"),
                bg=th.t("card"), fg=th.t("text")).pack(side=tk.LEFT)
            tk.Label(row, text="-%.1f目" % float(evt.score_loss or 0.0),
                     font=th.f("small"), bg=th.t("card"),
                     fg=th.t("red") if float(evt.score_loss or 0) >= 3
                     else th.t("subtext")).pack(side=tk.RIGHT)
