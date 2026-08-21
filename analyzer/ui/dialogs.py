"""ui.dialogs —— 从 app.py 外迁的对话框（减法重构：app.py 瘦身第一步）。

模式约定：
- 每个对话框一个 ``xxx(app)`` 函数，app 为 GoAnalyzer 实例；
- 依赖（COLORS/FONTS/业务函数）在函数体内惰性 import，避免与 app.py
  循环依赖——对话框只在用户打开时才执行；
- 状态仍写在 app 属性上（如 ``app._training_report_tv``），测试与
  双击跳转等既有接线不变。

已外迁：在线导入（open_online_import）、训练报告（show_training_report）。
后续新对话框直接放这里，不再长进 app.py。
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk


def open_online_import(app):
    """在线导入棋谱：URL 直链 / OGS 对局批量下载，后台线程不卡界面。

    线程分工：下载在 worker 线程（网络 IO 慢），入库在 UI 线程
    （game_library 的 index.json 读改写只在主线程做，避免并发写竞争）；
    两者经 events 队列 + after 轮询衔接。
    """
    from app import COLORS, FONTS
    from game_library import import_sgf_text
    from online_import import (OnlineImportError, download_from_url,
                               download_ogs_games, ogs_list_games)

    win = app._make_centered_toplevel(
        "在线导入棋谱", 780, 600, minsize=(660, 480))
    events = queue.Queue()

    status_var = tk.StringVar(value="输入链接或 OGS 用户名开始")

    # ---- ① URL 直链 ----
    sec1 = tk.Frame(win, bg=COLORS["bg"])
    sec1.pack(fill="x", padx=12, pady=(12, 0))
    tk.Label(sec1, text="① 从链接导入", font=FONTS["title"],
             bg=COLORS["bg"], fg=COLORS["text"]).pack(anchor="w")
    tk.Label(sec1, text="支持 .sgf 直链和 OGS 对局页链接（online-go.com/game/编号）",
             font=FONTS["small"], bg=COLORS["bg"],
             fg=COLORS["subtext"]).pack(anchor="w")
    row1 = tk.Frame(sec1, bg=COLORS["bg"])
    row1.pack(fill="x", pady=(4, 0))
    url_var = tk.StringVar()
    ent_url = ttk.Entry(row1, textvariable=url_var)
    ent_url.pack(side=tk.LEFT, fill="x", expand=True, padx=(0, 6))
    btn_url = app._make_button(row1, "下载并入库", lambda: None,
                               variant="accent")
    btn_url.pack(side=tk.LEFT)

    # ---- ② OGS 用户对局 ----
    sec2 = tk.Frame(win, bg=COLORS["bg"])
    sec2.pack(fill="x", padx=12, pady=(10, 0))
    tk.Label(sec2, text="② 从 OGS 导入（输入用户名，查询后选择对局批量下载）",
             font=FONTS["title"], bg=COLORS["bg"],
             fg=COLORS["text"]).pack(anchor="w")
    row2 = tk.Frame(sec2, bg=COLORS["bg"])
    row2.pack(fill="x", pady=(4, 0))
    user_var = tk.StringVar()
    ent_user = ttk.Entry(row2, textvariable=user_var, width=24)
    ent_user.pack(side=tk.LEFT, padx=(0, 6))
    btn_query = app._make_button(row2, "查询对局", lambda: None,
                                 variant="default")
    btn_query.pack(side=tk.LEFT)

    # ---- 对局列表 ----
    list_wrap = tk.Frame(win, bg=COLORS["card"], highlightthickness=1,
                         highlightbackground=COLORS["muted"])
    list_wrap.pack(fill="both", expand=True, padx=12, pady=(8, 0))
    tv = ttk.Treeview(list_wrap, columns=("date", "black", "white", "result", "size"),
                      show="headings", height=9, selectmode="extended")
    for col, txt, w, anch in [
        ("date", "日期", 90, "w"), ("black", "黑方", 160, "w"),
        ("white", "白方", 160, "w"), ("result", "结果", 70, "center"),
        ("size", "棋盘", 60, "center"),
    ]:
        tv.heading(col, text=txt)
        tv.column(col, width=w, anchor=anch)
    tv.pack(fill="both", expand=True, padx=6, pady=6)
    games_by_iid = {}

    act_bar = tk.Frame(win, bg=COLORS["bg"])
    act_bar.pack(fill="x", padx=12, pady=(6, 0))
    app._make_button(act_bar, "全选", lambda: tv.selection_set(tv.get_children()),
                     variant="default").pack(side=tk.LEFT)
    app._make_button(act_bar, "清空选择", lambda: tv.selection_set(),
                     variant="default").pack(side=tk.LEFT, padx=(6, 0))
    btn_dl = app._make_button(act_bar, "下载所选", lambda: None,
                              variant="accent")
    btn_dl.pack(side=tk.LEFT, padx=(12, 0))
    tk.Label(act_bar,
             text="列表可按住 Ctrl / 拖动多选",
             font=FONTS["small"], bg=COLORS["bg"],
             fg=COLORS["subtext"]).pack(side=tk.LEFT, padx=(10, 0))

    tk.Label(
        win,
        text="星阵 / 涨棋网等暂无公开接口：请在官网导出 SGF，用「粘贴 SGF」或收件箱导入。",
        font=FONTS["small"], bg=COLORS["bg"],
        fg=COLORS["subtext"]).pack(anchor="w", padx=12, pady=(8, 0))

    bar = app._dialog_button_bar(win)
    tk.Label(bar, textvariable=status_var, bg=COLORS["card"],
             fg=COLORS["subtext"], font=FONTS["small"]).pack(side=tk.LEFT)
    app._make_button(bar, "关闭", win.destroy,
                     variant="default").pack(side=tk.RIGHT)

    def _set_busy(busy):
        state = tk.DISABLED if busy else tk.NORMAL
        for b in (btn_url, btn_query, btn_dl):
            try:
                b.configure(state=state)
            except tk.TclError:
                pass

    def _start_worker(fn):
        _set_busy(True)

        def _work():
            try:
                fn()
            except OnlineImportError as e:
                events.put(("error", str(e)))
            except Exception as e:              # 网络栈外的意外错误也不挂死界面
                events.put(("error", "在线导入失败：%s" % e))
        threading.Thread(target=_work, daemon=True).start()

    def _apply_downloaded(items, failed, source_kind):
        """UI 线程入库 + 排队分析；items 为 [{name, text}]。"""
        imported, duplicates = [], []
        for item in items:
            try:
                rec, created = import_sgf_text(
                    item["text"], rules=app.rules, komi=app.komi,
                    name=item["name"], source_kind=source_kind)
                (imported if created else duplicates).append(rec)
            except Exception as e:
                failed.append({"game": item.get("name", "?"), "error": str(e)})
        records = imported + duplicates
        if records:
            app._enqueue_records_for_analysis(records)
            app._refresh_library_window()
            app.after(20, app._kick_analysis_queue)
        msg = "在线导入：新增 %d，重复 %d，失败 %d" % (
            len(imported), len(duplicates), len(failed))
        app._log_usage("online_import_used",
                       added=len(imported), failed=len(failed))
        status_var.set(msg)
        app._set_msg(msg)
        if failed:
            messagebox.showwarning(
                "部分棋谱下载失败",
                "\n".join("%s：%s" % (f.get("game", "?"), f.get("error", ""))
                          for f in failed[:10]), parent=win)

    def do_url_import():
        url = url_var.get().strip()
        if not url:
            status_var.set("请先输入棋谱链接")
            return
        status_var.set("下载中：%s" % url)

        def fn():
            text, name = download_from_url(url)
            events.put(("url_done", {"name": name, "text": text}))
        _start_worker(fn)

    def do_query():
        name = user_var.get().strip()
        if not name:
            status_var.set("请先输入 OGS 用户名")
            return
        status_var.set("正在查询 %s 的最近对局…" % name)

        def fn():
            player, games = ogs_list_games(name, limit=30)
            events.put(("ogs_list", (player, games)))
        _start_worker(fn)

    def _fill_games(player, games):
        tv.delete(*tv.get_children())
        games_by_iid.clear()
        for g in games:
            iid = tv.insert("", "end", values=(
                g.get("ended", ""), g.get("black", "?"), g.get("white", "?"),
                g.get("result", ""), g.get("size", "")))
            games_by_iid[iid] = g
        status_var.set("玩家 %s（%s）：最近 %d 盘，选择后点「下载所选」" % (
            player.get("username", "?"), player.get("rank") or "?", len(games)))

    def do_download_selected():
        chosen = [games_by_iid[i] for i in tv.selection() if i in games_by_iid]
        if not chosen:
            status_var.set("请先在列表中选择对局（可按住 Ctrl 多选）")
            return

        def progress(done, total, name):
            events.put(("progress", "下载中 %d/%d：%s" % (done, total, name)))

        def fn():
            result = download_ogs_games(chosen, progress=progress)
            events.put(("ogs_done", (result["items"], result["failed"])))
        _start_worker(fn)

    btn_url.configure(command=do_url_import)
    btn_query.configure(command=do_query)
    btn_dl.configure(command=do_download_selected)
    ent_url.bind("<Return>", lambda _e: do_url_import())
    ent_user.bind("<Return>", lambda _e: do_query())

    def _poll():
        try:
            while True:
                kind, payload = events.get_nowait()
                if kind == "progress":
                    status_var.set(payload)
                elif kind == "error":
                    status_var.set("✗ %s" % payload)
                    _set_busy(False)
                elif kind == "url_done":
                    _apply_downloaded([payload], [], "online-url")
                    _set_busy(False)
                elif kind == "ogs_list":
                    _fill_games(*payload)
                    _set_busy(False)
                elif kind == "ogs_done":
                    items, failed = payload
                    _apply_downloaded(items, failed, "online-ogs")
                    _set_busy(False)
        except queue.Empty:
            pass
        if win.winfo_exists():
            win.after(100, _poll)

    _poll()
    ent_url.focus_set()


def show_training_report(app, report):
    """可交互训练报告：摘要 + 问题类型变化 + 逐手对比表（双击跳转）+ 建议。

    把 training_analysis 已算好并落盘的富字段
    (comparisons / recommended_review_positions / problem_tag_changes) 渲染出来。
    """
    from app import COLORS, FONTS
    from move_quality import PROBLEM_TAGS

    detailed = report.get("trainingAnalysis") or report.get("training_analysis") or {}

    def _num(v):
        return "—" if v is None else "%.2f" % v

    win = tk.Toplevel(app)
    app._prepare_child_window(win, "训练分析", 760, 640, minsize=(620, 480))
    frame = tk.Frame(win, bg=COLORS["card"])
    frame.pack(fill="both", expand=True, padx=12, pady=12)

    # ---- 顶部摘要 ----
    score = detailed.get("training_score", 0)
    label = detailed.get("training_label", "样本不足")
    score_col = {"优秀": COLORS.get("green"), "明显改善": COLORS.get("green"),
                 "基本合格": COLORS.get("accent"),
                 "仍需复习": COLORS.get("amber"), "建议重练": COLORS.get("red"),
                 "样本不足": COLORS["subtext"]}.get(label, COLORS["text"])
    summary = tk.Frame(frame, bg=COLORS["card"])
    summary.pack(fill="x", pady=(0, 6))
    tk.Label(summary, text="本次训练  %d 分 · %s" % (score, label),
             font=FONTS["title"], fg=score_col,
             bg=COLORS["card"]).pack(anchor="w")
    tk.Label(
        summary,
        text=("平均目损  原实战 %s → 本次 %s   改善 %s        "
              "恶手 %s→%s  不佳 %s→%s        建议复习 %s 天") % (
                  _num(detailed.get("original_avg_score_loss")),
                  _num(detailed.get("training_avg_score_loss")),
                  _num(detailed.get("improvement_score_loss")),
                  detailed.get("original_blunder_count", 0),
                  detailed.get("training_blunder_count", 0),
                  detailed.get("original_inaccuracy_count", 0),
                  detailed.get("training_inaccuracy_count", 0),
                  detailed.get("suggested_review_after_days", "—")),
        font=FONTS["ui"], fg=COLORS["text"], bg=COLORS["card"],
        justify=tk.LEFT).pack(anchor="w", pady=(2, 0))

    # ---- 问题类型变化 ----
    changes = detailed.get("problem_tag_changes") or {}
    if changes:
        chips = tk.Frame(frame, bg=COLORS["card"])
        chips.pack(fill="x", pady=(0, 6))
        tk.Label(chips, text="问题类型变化：", font=FONTS["ui"],
                 fg=COLORS["subtext"], bg=COLORS["card"]).pack(side=tk.LEFT)
        for tag, vals in sorted(changes.items(), key=lambda kv: kv[1][2]):
            orig_n, train_n, delta = vals
            if delta == 0:
                continue
            name = PROBLEM_TAGS.get(tag, tag)
            col = COLORS.get("green") if delta < 0 else COLORS.get("red")
            sign = "+" if delta > 0 else ""
            tk.Label(chips, text="%s %s→%s (%s%s)" % (name, orig_n, train_n, sign, delta),
                     font=FONTS["small"], fg=col,
                     bg=COLORS["card"]).pack(side=tk.LEFT, padx=(0, 10))

    # ---- 逐手对比表 ----
    table_wrap = tk.Frame(frame, bg=COLORS["card"])
    table_wrap.pack(fill="both", expand=True)
    tk.Label(table_wrap, text="逐手对比（双击跳到该训练手；标 ★ 为重点复盘位）",
             font=FONTS["ui"], fg=COLORS["subtext"],
             bg=COLORS["card"]).pack(anchor="w", pady=(0, 2))
    cols = ("move", "side", "played", "orig_q", "train_q", "loss", "improve", "cat")
    tv = ttk.Treeview(table_wrap, columns=cols, show="headings", height=14)
    headers = {"move": "手", "side": "方", "played": "实战下法",
               "orig_q": "原评级", "train_q": "本次", "loss": "本次目损",
               "improve": "改善", "cat": "分类"}
    for c in cols:
        tv.heading(c, text=headers[c])
        tv.column(c, width=72, anchor=tk.CENTER)
    tv.column("played", width=92, anchor=tk.W)
    tv.column("cat", width=78)
    review_move_nos = {
        int(c.get("move_no") or 0)
        for c in (detailed.get("recommended_review_positions") or [])}
    comparisons = sorted(
        detailed.get("comparisons") or [],
        key=lambda c: (c.get("training_score_loss") is None,
                       -(c.get("training_score_loss") or 0)))
    cat_cn = {"improved": "已改善", "repeated_error": "重复错误",
              "new_error": "新错误", "neutral": "—"}
    app._training_report_tv_map = {}
    for comp in comparisons:
        mn = int(comp.get("move_no") or 0)
        side = "黑" if comp.get("color") == "B" else "白"
        star = "★ " if mn in review_move_nos else ""
        cat = comp.get("category") or "neutral"
        cat_tag = cat if cat in ("improved", "repeated_error", "new_error") else ""
        iid = tv.insert("", "end", values=(
            "%s%d" % (star, mn), side, comp.get("played_move") or "?",
            app._quality_cn(comp.get("original_quality")),
            app._quality_cn(comp.get("training_quality")),
            _num(comp.get("training_score_loss")),
            _num(comp.get("score_loss_improvement")),
            cat_cn.get(cat, cat)), tags=(cat_tag,) if cat_tag else ())
        app._training_report_tv_map[iid] = mn
    tv.tag_configure("improved", foreground=COLORS.get("green"))
    tv.tag_configure("repeated_error", foreground=COLORS.get("red"))
    tv.tag_configure("new_error", foreground=COLORS.get("amber"))
    vsb = ttk.Scrollbar(table_wrap, orient="vertical", command=tv.yview)
    tv.configure(yscrollcommand=vsb.set)
    tv.pack(side=tk.LEFT, fill="both", expand=True)
    vsb.pack(side=tk.RIGHT, fill="y")
    app._training_report_tv = tv
    tv.bind("<Double-Button-1>", app._on_training_report_double_click)

    # ---- 建议 ----
    recs = detailed.get("review_recommendations") or []
    if recs:
        tk.Label(frame, text="建议：" + "  ".join(recs), font=FONTS["small"],
                 fg=COLORS["subtext"], bg=COLORS["card"], wraplength=720,
                 justify=tk.LEFT).pack(anchor="w", pady=(6, 0))
    btns = app._dialog_button_bar(win)
    app._make_button(btns, "关闭", win.destroy, variant="default").pack(side=tk.RIGHT, padx=8)
    app._set_msg(report.get("summary", "训练完成。"))



# ===================== 错题本窗口（自 app.py 外迁） =====================
def open_mistake_book(app):
    from app import COLORS, FONTS
    """打开跨棋局错题队列；双击题目即可进入隐藏答案测验。"""
    if app._mistake_book_win is not None and app._mistake_book_win.winfo_exists():
        app._mistake_book_win.lift()
        app._refresh_mistake_book_window()
        return
    app._sync_mistake_book_library()
    win = tk.Toplevel(app)
    app._prepare_child_window(
        win, "错题本 · 间隔复习", 940, 500, minsize=(820, 420))

    top = tk.Frame(win, bg=COLORS["bg"])
    top.pack(fill="x", padx=10, pady=(10, 4))
    tk.Label(top, text="错题本", font=FONTS["title"], bg=COLORS["bg"],
             fg=COLORS["text"]).pack(side=tk.LEFT)
    app._mistake_book_stats_label = tk.Label(
        top, text="", bg=COLORS["bg"], fg=COLORS["subtext"], font=FONTS["ui"])
    app._mistake_book_stats_label.pack(side=tk.LEFT, padx=(12, 0))
    app._mistake_due_only_var = tk.BooleanVar(value=True)
    tk.Checkbutton(
        top, text="只看今日到期", variable=app._mistake_due_only_var,
        command=app._refresh_mistake_book_window,
        bg=COLORS["bg"], fg=COLORS["text"], activebackground=COLORS["bg"],
        selectcolor=COLORS["card"]).pack(side=tk.RIGHT)

    content = tk.Frame(win, bg=COLORS["card"])
    content.pack(fill="both", expand=True, padx=10, pady=4)
    tv = ttk.Treeview(
        content,
        columns=("due", "game", "move", "side", "played", "best",
                 "quality", "loss", "tags", "progress"),
        show="headings", height=15)
    for col, text, width, anchor in [
            ("due", "下次复习", 88, "center"), ("game", "棋局", 210, "w"),
            ("move", "手数", 48, "e"), ("side", "方", 32, "center"),
            ("played", "实战", 50, "center"), ("best", "AI首选", 56, "center"),
            ("quality", "评价", 58, "center"), ("loss", "目损", 52, "e"),
            ("tags", "弱点标签", 120, "w"), ("progress", "复习进度", 92, "center")]:
        tv.heading(col, text=text)
        tv.column(col, width=width, anchor=anchor)
    tv.pack(fill="both", expand=True)
    tv.bind("<Double-1>", lambda _e: app._start_selected_mistake_review())
    tv.tag_configure("due", foreground=COLORS["red"])
    tv.tag_configure("future", foreground=COLORS["text"])
    app._mistake_book_empty = app._empty_card(
        content, "错题本暂无内容",
        "请先在棋谱库为棋局设置「我方」身份并完成整盘分析，"
        "问题手会自动进入错题本用于间隔复习。")

    btns = app._dialog_button_bar(win)
    tk.Label(
        btns,
        text="按实际目损判分（与主动复盘同一条链）；判定未达标回到题面重试，榜外选点自动送 AI 强制分析。",
        bg=COLORS["card"], fg=COLORS["subtext"], font=FONTS["small"]).pack(side=tk.LEFT)
    app._make_button(btns, "暂不复习",
                      app._master_selected_mistake, variant="default").pack(side=tk.RIGHT, padx=(6, 0))
    app._make_button(btns, "明天再练",
                      lambda: app._postpone_selected_mistake(1), variant="default").pack(side=tk.RIGHT, padx=8)
    app._make_button(btns, "开始复习",
                      app._start_selected_mistake_review, variant="accent").pack(side=tk.RIGHT, padx=8)

    app._mistake_book_win = win
    app._mistake_book_tv = tv
    app._mistake_book_map = {}
    win.protocol("WM_DELETE_WINDOW", app._close_mistake_book)
    app._refresh_mistake_book_window()

def _close_mistake_book(app):
    if app._mistake_book_win is not None:
        try:
            app._mistake_book_win.destroy()
        except tk.TclError:
            pass
    app._mistake_book_win = None
    app._mistake_book_tv = None
    app._mistake_book_map = {}
    # 关闭错题本窗口时终止进行中的复习，避免 _mistake_review.active 残留：
    # 否则后续任意落子会触发 _mistake_review_after_user_move 对失效题目做"回题面"，
    # 其引用的 parent 节点可能已属于切换后的旧棋局，导致跨树跳转或崩溃。
    if app._mistake_review and app._mistake_review.get("active"):
        app._mistake_review = None
        app._set_msg("已关闭错题本，进行中的复习已终止")

def _refresh_mistake_book_window(app):
    from move_quality import PROBLEM_TAGS, QUALITY_LABELS
    from mistake_book import book_stats, list_items
    tv = app._mistake_book_tv
    if tv is None or not (
            app._mistake_book_win and app._mistake_book_win.winfo_exists()):
        return
    tv.delete(*tv.get_children())
    app._mistake_book_map = {}
    due_only = bool(
        app._mistake_due_only_var and app._mistake_due_only_var.get())
    items = list_items(due_only=due_only)
    for item in items:
        tags = "、".join(
            PROBLEM_TAGS.get(tag, tag) for tag in item.get("problemTags") or [])
        progress = "%d次 · 错%d" % (
            int(item.get("repetitions") or 0), int(item.get("lapses") or 0))
        iid = tv.insert("", "end", values=(
            item.get("dueDate") or "—",
            item.get("gameName") or item.get("gameId") or "",
            item.get("moveNo") or "",
            "黑" if item.get("color") == "B" else "白",
            item.get("playedMove") or "—",
            item.get("bestMove") or "—",
            QUALITY_LABELS.get(item.get("qualityKey"), item.get("qualityKey") or "—"),
            "—" if item.get("scoreLoss") is None
            else "%.1f" % float(item.get("scoreLoss")),
            tags or "—", progress),
            tags=("due" if item.get("isDue") else "future",))
        app._mistake_book_map[iid] = item
    stats = book_stats()
    if app._mistake_book_stats_label is not None:
        app._mistake_book_stats_label.config(
            text="共 %d 题 · 今日到期 %d · 已掌握 %d" % (
                stats["total"], stats["due"], stats["mastered"]))
    empty = getattr(app, "_mistake_book_empty", None)
    if not items:
        tv.pack_forget()
        if empty is not None:
            empty.pack(fill="both", expand=True)
        if not due_only:
            app._set_msg("错题本为空：请先在棋谱库设置画像身份并完成整盘分析")
    else:
        if empty is not None and empty.winfo_ismapped():
            empty.pack_forget()
        if not tv.winfo_ismapped():
            tv.pack(fill="both", expand=True)

def _selected_mistake_item(app):
    if app._mistake_book_tv is None:
        return None
    selected = app._mistake_book_tv.selection()
    if not selected:
        rows = app._mistake_book_tv.get_children()
        if not rows:
            app._set_msg("当前没有可复习的错题")
            return None
        selected = (rows[0],)
        app._mistake_book_tv.selection_set(selected[0])
    return app._mistake_book_map.get(selected[0])

def _postpone_selected_mistake(app, days):
    from mistake_book import postpone_mistake_item
    item = app._selected_mistake_item()
    if not item:
        return
    postpone_mistake_item(item.get("id"), days)
    app._refresh_mistake_book_window()
    app._set_msg("已将第 %s 手错题推迟 %d 天" % (item.get("moveNo"), days))

def _master_selected_mistake(app):
    from mistake_book import set_mistake_mastered
    item = app._selected_mistake_item()
    if not item:
        return
    set_mistake_mastered(item.get("id"), True)  # 暂不复习：仅推迟调度，不改掌握状态
    app._refresh_mistake_book_window()
    app._set_msg("已暂不复习（一年内不再排队）：%s 第 %s 手" % (
        item.get("gameName") or "", item.get("moveNo")))


# ===================== 系统设置窗口（自 app.py 外迁） =====================
def open_settings(app):
    from app import (COLORS, FONTS, MAX_CANDIDATES, TRAINING_SPEED_MODES,
                     UI_STYLE_LABELS)
    from config_manager import list_engine_paths, list_model_paths
    if app._settings_win is not None and app._settings_win.winfo_exists():
        app._settings_win.lift()
        app._settings_win.focus_set()
        return
    win = tk.Toplevel(app)
    app._settings_win = win
    app._prepare_child_window(
        win, "系统设置", 840, 690, minsize=(760, 600))
    win.protocol("WM_DELETE_WINDOW", app._close_settings_window)
    win.columnconfigure(0, weight=1)
    win.rowconfigure(0, weight=1)
    exe_var = tk.StringVar(value=app.katago_exe)
    model_var = tk.StringVar(value=app.model_file)
    rules_var = tk.StringVar(value=str(app.rules))
    komi_var = tk.StringVar(value=str(app.komi))
    visits_var = tk.StringVar(value=str(app.cfg.get("max_visits", 200)))
    candidate_count_var = tk.StringVar(value=str(app._candidate_count))
    pv_length_var = tk.StringVar(value=str(app._pv_length))
    style_labels = [UI_STYLE_LABELS["simple"]]
    style_label_to_key = {label: key for key, label in UI_STYLE_LABELS.items()}
    ui_style_label_var = tk.StringVar(
        value=UI_STYLE_LABELS.get(app._ui_style, UI_STYLE_LABELS["simple"]))
    training_mode_var = tk.StringVar(value=str(app.cfg.get("training_speed_mode", "fast")))
    library_visits_var = tk.StringVar(value=str(app.cfg.get("library_training_visits", 120)))
    profile_cfg = app.cfg.get("profile", {}) or {}
    profile_names_var = tk.StringVar(
        value="，".join(profile_cfg.get("my_player_names") or []))
    profile_side_var = tk.StringVar(
        value=str(profile_cfg.get("default_profile_side", "unknown")))
    profile_window_var = tk.StringVar(
        value=str(profile_cfg.get("profile_window_games", 30)))
    engines = list_engine_paths(app.cfg.runtime_dir)
    models = list_model_paths(app.cfg.runtime_dir)

    content = tk.Frame(win, bg=COLORS["bg"], padx=12, pady=10)
    content.grid(row=0, column=0, sticky="nsew")
    content.columnconfigure(0, weight=1)
    content.columnconfigure(1, weight=1)

    def section(title, row, column=0, columnspan=1, hint=""):
        box = app._make_card_frame(content, title)
        box.grid(row=row, column=column, columnspan=columnspan, sticky="nsew",
                 padx=(0, 8) if column == 0 and columnspan == 1 else 0,
                 pady=(0, 9))
        try:
            box.columnconfigure(1, weight=1)
        except Exception:
            pass
        start_row = 0
        if hint:
            tk.Label(
                box, text=hint, bg=COLORS["card"], fg=COLORS["subtext"],
                font=FONTS["small"], justify=tk.LEFT, wraplength=700
            ).grid(row=0, column=0, columnspan=3, sticky="ew", padx=8, pady=(2, 7))
            start_row = 1
        return box, start_row

    def field(parent, row, label, widget, extra=None):
        ttk.Label(parent, text=label).grid(
            row=row, column=0, sticky="w", padx=8, pady=5)
        widget.grid(row=row, column=1, sticky="ew", padx=6, pady=5)
        if extra is not None:
            extra.grid(row=row, column=2, sticky="w", padx=(0, 8), pady=5)

    appearance, ar = section(
        "外观", 0, 0, 2,
        "统一深色主题：各区域亮度平滑过渡，对比度合理，适合长时间复盘。")
    appearance.columnconfigure(1, weight=0)
    appearance.columnconfigure(2, weight=1)
    ttk.Label(appearance, text="界面风格：").grid(
        row=ar, column=0, sticky="w", padx=8, pady=6)
    ttk.OptionMenu(
        appearance, ui_style_label_var, ui_style_label_var.get(),
        *style_labels).grid(row=ar, column=1, sticky="w", padx=6, pady=6)
    preview = tk.Canvas(
        appearance, width=270, height=92, bg=COLORS["bg"],
        highlightthickness=1, highlightbackground=COLORS["muted"])
    preview.grid(row=ar, column=2, rowspan=2, sticky="e", padx=8, pady=3)
    app._draw_style_preview(
        preview, style_label_to_key.get(ui_style_label_var.get(), "simple"))
    ui_style_label_var.trace_add(
        "write",
        lambda *_: app._draw_style_preview(
            preview, style_label_to_key.get(ui_style_label_var.get(), "simple")))
    tk.Label(
        appearance,
        text="提示：当前为统一深色主题，Ctrl+T 可刷新视觉。",
        bg=COLORS["card"], fg=COLORS["subtext"], font=FONTS["small"],
        justify=tk.LEFT, wraplength=430
    ).grid(row=ar + 1, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 5))

    engine, er = section(
        "引擎与规则", 1, 0, 2,
        "这里决定 KataGo 分析进程、模型、规则和贴目；修改引擎或模型后会自动重启分析进程。")
    field(
        engine, er, "引擎 (.exe)：",
        ttk.Combobox(engine, textvariable=exe_var, values=engines, width=62),
        ttk.Button(engine, text="…", width=3,
                   command=lambda: app._pick_file(
                       exe_var, [("可执行文件", "*.exe")], app.katago_exe)))
    er += 1
    field(
        engine, er, "模型 (.bin.gz)：",
        ttk.Combobox(engine, textvariable=model_var, values=models, width=62),
        ttk.Button(engine, text="…", width=3,
                   command=lambda: app._pick_file(
                       model_var,
                       [("KataGo 模型", "*.bin.gz"), ("所有文件", "*.*")],
                       app.model_file)))
    er += 1
    field(
        engine, er, "规则：",
        ttk.OptionMenu(
            engine, rules_var, app.rules, "chinese", "japanese", "korean",
            "tromp-taylor", "aga", "new-zealand"))
    er += 1
    field(engine, er, "贴目 komi：", ttk.Entry(engine, textvariable=komi_var, width=10))
    # Human SL 可用性显式提示（治理遗留：此前模型缺失时整条链静默失效）
    try:
        sl_status = app.cfg.human_sl_status()
    except Exception:
        sl_status = {"available": False, "message": ""}
    tk.Label(
        engine,
        text="Human SL 模型：%s" % (sl_status.get("message") or "未安装"),
        bg=COLORS["card"],
        fg=COLORS["green"] if sl_status.get("available") else COLORS["subtext"],
        font=FONTS["small"], justify=tk.LEFT, wraplength=700
    ).grid(row=er + 1, column=0, columnspan=3, sticky="ew", padx=8, pady=(2, 6))

    analysis, rr = section("分析参数", 2, 0, 1)
    analysis.columnconfigure(1, weight=1)
    field(analysis, rr, "复盘 maxVisits：",
          ttk.Entry(analysis, textvariable=visits_var, width=10))
    rr += 1
    ttk.Label(analysis, text="复盘预设：").grid(
        row=rr, column=0, sticky="w", padx=8, pady=5)
    preset = tk.Frame(analysis, bg=COLORS["card"])
    preset.grid(row=rr, column=1, sticky="w", padx=6, pady=5)
    for text, value in (("快 80", "80"), ("标准 200", "200"), ("深入 800", "800")):
        app._make_button(
            preset, text,
            lambda v=value: visits_var.set(v), variant="default"
        ).pack(side=tk.LEFT, padx=(0, 6))
    rr += 1
    field(analysis, rr, "推荐点数量：",
          ttk.Spinbox(analysis, from_=1, to=MAX_CANDIDATES,
                      textvariable=candidate_count_var, width=8))
    rr += 1
    field(analysis, rr, "主变显示长度：",
          ttk.Spinbox(analysis, from_=1, to=30,
                      textvariable=pv_length_var, width=8))
    rr += 1
    ttk.Label(analysis, text="训练速度：").grid(
        row=rr, column=0, sticky="w", padx=8, pady=5)
    mode_labels = ["%s（%d visits）" % (label, visits) for _key, (label, visits) in TRAINING_SPEED_MODES.items()]
    label_to_mode = {
        "%s（%d visits）" % (label, visits): key
        for key, (label, visits) in TRAINING_SPEED_MODES.items()
    }
    current_mode = training_mode_var.get()
    current_label = "%s（%d visits）" % TRAINING_SPEED_MODES.get(current_mode, TRAINING_SPEED_MODES["fast"])
    training_mode_label_var = tk.StringVar(value=current_label)
    ttk.OptionMenu(
        analysis, training_mode_label_var, current_label, *mode_labels
    ).grid(row=rr, column=1, sticky="w", padx=6, pady=5)
    rr += 1
    field(analysis, rr, "棋局库后台 visits：",
          ttk.Entry(analysis, textvariable=library_visits_var, width=10))
    rr += 1
    ttk.Label(analysis, text="训练揭示首选：").grid(
        row=rr, column=0, sticky="w", padx=8, pady=5)
    auto_hint_training_var = tk.BooleanVar(
        value=bool(app.cfg.get("auto_hint_training", False)))
    ttk.Checkbutton(
        analysis, text="训练中也自动揭示 AI 首选（默认关闭，保留盲下训练）",
        variable=auto_hint_training_var
    ).grid(row=rr, column=1, sticky="w", padx=6, pady=5)

    profile, pr = section("个人画像", 2, 1, 1)
    profile.columnconfigure(1, weight=1)
    field(profile, pr, "我的棋手名：",
          ttk.Entry(profile, textvariable=profile_names_var, width=32))
    pr += 1
    field(
        profile, pr, "默认画像方：",
        ttk.OptionMenu(
            profile, profile_side_var, profile_side_var.get(),
            "unknown", "B", "W", "both"))
    pr += 1
    field(profile, pr, "画像最近棋局数：",
          ttk.Entry(profile, textvariable=profile_window_var, width=10))
    pr += 1
    tk.Label(
        profile,
        text="棋手名可用中文逗号或英文逗号分隔；如果不确定执棋方，可保持 unknown，再在棋谱库中逐盘标记。",
        bg=COLORS["card"], fg=COLORS["subtext"], font=FONTS["small"],
        justify=tk.LEFT, wraplength=330
    ).grid(row=pr, column=0, columnspan=2, sticky="ew", padx=8, pady=(7, 2))

    def apply_and_close():
        training_mode_var.set(label_to_mode.get(training_mode_label_var.get(), "fast"))
        app._apply_settings(exe_var.get().strip(), model_var.get().strip(),
                             rules_var.get().strip(), komi_var.get().strip(),
                             visits_var.get().strip(), training_mode_var.get().strip(),
                             library_visits_var.get().strip(),
                             profile_names_var.get().strip(),
                             profile_side_var.get().strip(),
                             profile_window_var.get().strip(),
                             candidate_count_var.get().strip(),
                             pv_length_var.get().strip(),
                             style_label_to_key.get(
                                 ui_style_label_var.get(), "simple"),
                             bool(auto_hint_training_var.get()))
        app._close_settings_window()
    btns = tk.Frame(win, bg=COLORS["card"], highlightthickness=1,
                    highlightbackground=COLORS["muted"])
    btns.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))
    inner = tk.Frame(btns, bg=COLORS["card"])
    inner.pack(fill="x", padx=12, pady=9)
    tk.Label(
        inner, text="设置保存到 user_settings.json",
        bg=COLORS["card"], fg=COLORS["subtext"], font=FONTS["small"]
    ).pack(side=tk.LEFT)
    app._make_button(inner, "检测配置",
               lambda: app._check_settings(exe_var.get().strip(), model_var.get().strip(),
                                                    rules_var.get().strip(), komi_var.get().strip(),
                                                    visits_var.get().strip(),
                                                    label_to_mode.get(training_mode_label_var.get(), "fast"),
                                                    library_visits_var.get().strip(),
                                                    profile_names_var.get().strip(),
                                                    profile_side_var.get().strip(),
                                                    profile_window_var.get().strip(),
                                                    candidate_count_var.get().strip(),
                                                    pv_length_var.get().strip()),
               variant="default"
               ).pack(side=tk.RIGHT, padx=8)
    app._make_button(inner, "应用（持久化；引擎/模型变更时自动重启）",
               apply_and_close, variant="accent"
               ).pack(side=tk.RIGHT, padx=8)

def _close_settings_window(app):
    if app._settings_win is not None:
        try:
            app._settings_win.destroy()
        except tk.TclError:
            pass
    app._settings_win = None

