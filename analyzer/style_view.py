"""“我的棋风与成长路线”独立窗口，避免继续扩大 app.py 的 UI 构建代码。"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

try:
    import customtkinter as ctk
    _HAS_CTK = True
except ImportError:
    ctk = None
    _HAS_CTK = False

from style_cost import StyleCostResult


def _mk_button(parent, text, command, colors, fonts, accent=False):
    """按钮工厂（与 app._make_button 同语义）：CTk 圆角，降级 ttk。"""
    if _HAS_CTK:
        return ctk.CTkButton(
            parent, text=text, command=command,
            fg_color=colors["accent"] if accent else colors["card"],
            hover_color=colors["accent_h"] if accent else colors["accent_s"],
            text_color="#ffffff" if accent else colors["text"],
            corner_radius=8 if accent else 6,
            border_width=0 if accent else 1,
            border_color=colors["muted"],
            font=(fonts["ui"][0], fonts["ui"][1]))
    return ttk.Button(
        parent, text=text, command=command,
        style="Accent.TButton" if accent else "TButton")


def _mk_card(parent, title, colors):
    """卡片容器工厂（与 app._make_card_frame 同语义）：CTk 圆角，降级 ttk.LabelFrame。"""
    if _HAS_CTK:
        return ctk.CTkFrame(parent, fg_color=colors["card"], corner_radius=10)
    return ttk.LabelFrame(parent, text=" %s " % title, style="Section.TLabelframe")


def _label(value, kind):
    maps = {
        "tendency": {
            "high": "高", "medium": "中", "low": "低", "unknown": "未知"},
        "cost": {
            "low_cost": "低", "medium_cost": "中",
            "high_cost": "高", "unknown": "未知"},
        "conclusion": {
            "keep": "可保留", "observe": "继续观察",
            "fix": "需要修正", "insufficient": "样本不足"},
        "trend": {
            "improving": "改善", "stable": "稳定",
            "worsening": "反复", "insufficient": "样本不足"},
    }
    return maps[kind].get(value, value or "—")


class StyleProfileWindow(tk.Toplevel):
    def __init__(self, parent, style_profile, growth_path, tasks,
                 colors, fonts, on_export, on_generate, on_verify):
        super().__init__(parent)
        self.title("我的棋风与成长路线")
        self.geometry("940x780")
        self.minsize(780, 650)
        self.configure(bg=colors["bg"])
        self.style_profile = style_profile
        self.growth_path = growth_path
        self._tasks = []
        self._task_map = {}
        self._on_verify = on_verify

        header = tk.Frame(
            self, bg=colors["card"], highlightthickness=1,
            highlightbackground=colors["muted"], padx=14, pady=11)
        header.pack(fill="x", padx=14, pady=(12, 8))
        title_row = tk.Frame(header, bg=colors["card"])
        title_row.pack(fill="x")
        tk.Label(
            title_row, text="我的棋风与成长路线", font=fonts["h1"],
            bg=colors["card"], fg=colors["text"]).pack(side=tk.LEFT)
        tk.Label(
            title_row, text="统计倾向，不是固定标签", font=fonts["small"],
            bg=colors["accent_s"], fg=colors["accent"],
            padx=9, pady=4).pack(side=tk.RIGHT)

        metrics = tk.Frame(header, bg=colors["card"])
        metrics.pack(fill="x", pady=(10, 0))
        metric_items = [
            ("纳入棋局", "%d 盘" % style_profile.games_count),
            ("有效样本", "%d 手" % style_profile.evaluated_moves_count),
            ("整体置信", style_profile.confidence),
        ]
        for index, (label, value) in enumerate(metric_items):
            card = tk.Frame(
                metrics, bg=colors["card2"], padx=10, pady=6)
            card.pack(
                side=tk.LEFT, fill="x", expand=True,
                padx=(0, 6) if index < len(metric_items) - 1 else 0)
            tk.Label(
                card, text=label, bg=colors["card2"], fg=colors["subtext"],
                font=fonts["small"]).pack(anchor="w")
            tk.Label(
                card, text=value, bg=colors["card2"], fg=colors["text"],
                font=fonts["title"]).pack(anchor="w")

        summary = _mk_card(self, "棋风摘要", colors)
        summary.pack(fill="x", padx=14, pady=(0, 8))
        tk.Label(
            summary, text=style_profile.style_summary,
            bg=colors["card"], fg=colors["text"], font=fonts["ui"],
            justify=tk.LEFT, wraplength=860).pack(anchor="w", fill="x")

        views = ttk.Notebook(self)
        views.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        dimension_tab = tk.Frame(views, bg=colors["card"], padx=8, pady=8)
        growth_tab = tk.Frame(views, bg=colors["card"], padx=8, pady=8)
        views.add(dimension_tab, text="棋风维度")
        views.add(growth_tab, text="成长与复核")

        dimensions = _mk_card(dimension_tab, "八个可追溯维度", colors)
        dimensions.pack(fill="both", expand=True)
        tv = ttk.Treeview(
            dimensions,
            columns=("dimension", "sample", "tendency", "cost", "loss",
                     "trend", "conclusion", "confidence"),
            show="headings", height=8)
        for key, text, width, anchor in [
                ("dimension", "维度", 140, "w"), ("sample", "样本", 55, "e"),
                ("tendency", "倾向", 55, "center"), ("cost", "代价", 55, "center"),
                ("loss", "平均目损", 75, "e"), ("trend", "趋势", 70, "center"),
                ("conclusion", "结论", 80, "center"),
                ("confidence", "置信", 55, "center")]:
            tv.heading(key, text=text)
            tv.column(key, width=width, anchor=anchor)
        costs = {
            item.dimension_key: item for item in style_profile.cost_results}
        for dimension in style_profile.dimensions:
            cost = costs.get(dimension.key, StyleCostResult(
                dimension.key, dimension.label))
            tv.insert("", "end", values=(
                dimension.label, dimension.sample_count,
                _label(cost.tendency_level, "tendency"),
                _label(cost.cost_level, "cost"),
                "—" if dimension.avg_score_loss is None
                else "%.2f" % dimension.avg_score_loss,
                _label(dimension.recent_trend, "trend"),
                _label(cost.conclusion, "conclusion"),
                dimension.confidence))
        tv.pack(fill="both", expand=True)
        self.dimension_tree = tv

        route = _mk_card(growth_tab, "下一阶段成长路线", colors)
        route.pack(fill="x", pady=(0, 8))
        route_lines = ["主线目标：%s" % growth_path.main_goal]
        if growth_path.keep_styles:
            route_lines.append("建议保留：%s" % "、".join(
                item.get("label", "") for item in growth_path.keep_styles))
        if growth_path.fix_habits:
            route_lines.append("建议修正：%s" % "、".join(
                item.get("label", "") for item in growth_path.fix_habits))
        route_lines.append("复盘优先看：%s" % "、".join(
            growth_path.next_review_focus))
        tk.Label(
            route, text="\n".join(route_lines), bg=colors["card"],
            fg=colors["text"], font=fonts["ui"], justify=tk.LEFT,
            wraplength=860).pack(anchor="w")

        verify = _mk_card(growth_tab, "高强度复核队列", colors)
        verify.pack(fill="both", expand=True)
        task_tv = ttk.Treeview(
            verify,
            columns=("finding", "game", "move", "quality", "loss",
                     "visits", "status"),
            show="headings", height=5)
        for key, text, width, anchor in [
                ("finding", "结论", 130, "w"), ("game", "棋局", 220, "w"),
                ("move", "手数", 48, "e"), ("quality", "原评价", 65, "center"),
                ("loss", "原目损", 60, "e"), ("visits", "目标", 60, "e"),
                ("status", "状态", 70, "center")]:
            task_tv.heading(key, text=text)
            task_tv.column(key, width=width, anchor=anchor)
        task_tv.pack(fill="both", expand=True)
        self.task_tree = task_tv

        buttons = tk.Frame(self, bg=colors["bg"])
        buttons.pack(fill="x", padx=14, pady=(0, 12))
        _mk_button(buttons, "导出报告", on_export, colors, fonts).pack(side=tk.LEFT)
        _mk_button(buttons, "生成复核队列", on_generate, colors, fonts).pack(
            side=tk.LEFT, padx=(8, 0))
        _mk_button(buttons, "开始复核选中项", self._verify_selected,
                   colors, fonts, accent=True).pack(side=tk.LEFT, padx=(8, 0))
        _mk_button(buttons, "开始全部待复核", self._verify_all,
                   colors, fonts).pack(side=tk.LEFT, padx=(8, 0))
        _mk_button(buttons, "关闭", self.destroy, colors, fonts).pack(side=tk.RIGHT)
        self.refresh_tasks(tasks)

    def refresh_tasks(self, tasks):
        self._tasks = list(tasks or [])
        self._task_map = {}
        self.task_tree.delete(*self.task_tree.get_children())
        status_map = {
            "pending": "待复核", "running": "复核中", "done": "已完成",
            "failed": "失败", "skipped": "已跳过"}
        for task in self._tasks:
            raw = task.to_dict() if hasattr(task, "to_dict") else dict(task)
            iid = self.task_tree.insert("", "end", values=(
                raw.get("conclusion_label", ""),
                raw.get("game_name") or raw.get("game_id", ""),
                raw.get("move_no", ""),
                raw.get("original_quality", ""),
                "—" if raw.get("original_score_loss") is None
                else "%.1f" % float(raw["original_score_loss"]),
                raw.get("target_visits", ""),
                status_map.get(raw.get("status"), raw.get("status", "")),
            ))
            self._task_map[iid] = raw

    def _verify_selected(self):
        selected = self.task_tree.selection()
        if not selected:
            return
        task = self._task_map.get(selected[0])
        if task:
            self._on_verify([task])

    def _verify_all(self):
        tasks = [
            item.to_dict() if hasattr(item, "to_dict") else dict(item)
            for item in self._tasks
            if (item.status if hasattr(item, "status") else item.get("status"))
            in ("pending", "failed")]
        self._on_verify(tasks)
