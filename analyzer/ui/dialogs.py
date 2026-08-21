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
