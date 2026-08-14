"""test_ui_smoke —— 全部 UI 功能无头冒烟（单 app 顺序段，不启动 KataGo）。

单 app 跑所有 UI 断言（点目 / 复盘 / 易用性），避免一个 pytest 进程内连续创建
多个 Tk root 引发 Windows Tcl「invalid command name / tcl_findLibrary」不稳。
段间用 _clean() 换全新空树，彻底清除上一段的子节点 / analysis 缓存。
"""
import os
import sys
import tempfile
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import tkinter.messagebox as _mb
import tkinter.filedialog as _fd
import tkinter as tk
from tkinter import ttk
from app import GoAnalyzer, COLORS
from movetree import MoveTree
from player_profile import GameBenchmark, GameTrendPoint, PlayerProfile, ProfileStats
from review import ReviewReport
from sgf import export_sgf

# 防弹窗阻塞无头测试
_mb.askyesno = lambda *a, **k: False
_mb.showinfo = lambda *a, **k: None
_mb.showerror = lambda *a, **k: None


def check(name, cond, extra=""):
    print(("[CHECK] %-34s %s %s" % (name, "OK" if cond else "FAIL", extra)))
    if not cond:
        raise AssertionError(name)


def analysis(sl, wr, mis):
    return {"rootInfo": {"scoreLead": sl, "winrate": wr}, "moveInfos": mis}


def mi(move, sl, wr, order=0):
    return {"move": move, "scoreLead": sl, "winrate": wr, "order": order}


def _clean(app):
    """彻底重置：全新空树（清掉上一段子节点/analysis 缓存，避免段间状态污染）。"""
    if app.scoring_mode:
        app.exit_scoring()
    app._stop_auto_play()
    if app._graph_win is not None:
        app._close_graph()
    app._auto_start_attempted = True
    app.tree = MoveTree(app.size)
    app._reset_batch_state()
    app._current_loss_val = None
    app._hover_point = None
    app._hint_point = None
    app._hint_pending_nid = None
    app._candidate_actions = []
    app._clear_candidate_module()
    app._clear_analysis()
    app._refresh_treeview()
    app.redraw()
    app.update_idletasks()


# ===================== 段：自动启动 =====================
def _section_autostart(app):
    orig_preflight = app.cfg.preflight
    errs = []
    _mb.showerror = lambda *a, **k: errs.append(a)
    app.cfg.preflight = lambda: {"ok": False, "errors": ["模拟未配置"], "warnings": []}
    app._auto_start_attempted = False
    app._maybe_autostart()
    check("autostart 尝试过", app._auto_start_attempted is True)
    check("quiet 不弹 showerror", len(errs) == 0, str(len(errs)))
    check("状态栏提示未配置", "未配置" in app.lbl_status.cget("text"), app.lbl_status.cget("text"))
    app._maybe_autostart()                  # 第二次直接返回（只尝试一次）
    app.cfg.preflight = orig_preflight
    _mb.showerror = lambda *a, **k: None


# ===================== 段：合法落点悬停预览 =====================
def _section_hover_preview(app):
    _clean(app)
    x, y = 3, 3
    event = SimpleNamespace(
        x=app.MARGIN + x * app.CELL,
        y=app.MARGIN + y * app.CELL)
    app._on_board_motion(event)
    check("空点显示悬停棋子", app._hover_point == (x, y), str(app._hover_point))
    check("悬停棋子使用独立绘制标签",
          len(app.canvas.find_withtag("hover-stone")) >= 1,
          str(len(app.canvas.find_withtag("hover-stone"))))
    app.play(x, y)
    app._on_board_motion(event)
    check("已有棋子不显示悬停", app._hover_point is None, str(app._hover_point))
    check("已有棋子无悬停绘制", not app.canvas.find_withtag("hover-stone"))
    event2 = SimpleNamespace(
        x=app.MARGIN + 15 * app.CELL,
        y=app.MARGIN + 15 * app.CELL)
    app._on_board_motion(event2)
    check("下一手显示白方悬停", app._hover_point == (15, 15), str(app._hover_point))
    app._on_board_leave()
    check("移出棋盘清除悬停", app._hover_point is None)


def _section_global_hint_takeback(app):
    _clean(app)
    app.tree.current.analysis = {
        "rootInfo": {"currentPlayer": "B", "winrate": 0.55},
        "moveInfos": [{"move": "Q16", "order": 0, "winrate": 0.55}],
    }
    app.show_hint()
    check("全局提示标出首选", app._hint_point == (15, 3), str(app._hint_point))
    check("棋盘提示标记存在", len(app.canvas.find_withtag("hint-marker")) == 3)
    app.play(15, 3)
    check("落子后清除提示", app._hint_point is None)
    app.do_takeback()
    check("普通悔棋退一手", app.tree.current.depth == 0)


# ===================== 段：快捷键 =====================
def _section_hotkeys(app):
    _clean(app)
    app.play(15, 3); app.play(3, 15); app.play(15, 15)
    check("初始 depth=3", app.tree.current.depth == 3)
    app.do_goto_root()
    check("Home 跳根 depth=0", app.tree.current.depth == 0)
    app.do_goto_mainline_end()
    check("End 跳末尾 depth=3", app.tree.current.depth == 3)
    app.do_goto_root(); app.do_step(10)
    check("PgDn 翻到末尾 depth=3", app.tree.current.depth == 3)
    app.do_goto_root(); app.do_step(2)
    check("PgDn 翻 2 步 depth=2", app.tree.current.depth == 2)
    app.do_step(-10)
    check("PgUp 翻到根 depth=0", app.tree.current.depth == 0)


# ===================== 段：自动播放 =====================
def _section_autoplay(app):
    _clean(app)
    app.play(15, 3); app.play(3, 15); app.play(15, 15)
    app.do_goto_root()
    check("回根 depth=0", app.tree.current.depth == 0)
    app._start_auto_play()
    check("播放启动", app._auto_play is True)
    check("按钮变暂停", app.btn_play.cget("text") == "⏸ 暂停")
    check("启动即走第一步 depth=1", app.tree.current.depth == 1)
    app._auto_play_step()
    check("播放步进 depth=2", app.tree.current.depth == 2)
    app._stop_auto_play()
    check("停止 _auto_play=False", app._auto_play is False)
    check("停止按钮变播放", app.btn_play.cget("text") == "▶ 播放")
    app._start_auto_play()
    for _ in range(10):
        app._auto_play_step()
        if not app._auto_play:
            break
    check("到末尾自动停", app._auto_play is False)
    check("末尾 depth=3", app.tree.current.depth == 3)
    app.do_goto_root(); app._start_auto_play()
    check("播放中", app._auto_play is True)
    app.play(5, 5)
    check("落子停止播放", app._auto_play is False)
    app.do_goto_mainline_end(); app._start_auto_play()
    check("末尾启动播放不开始", app._auto_play is False)


# ===================== 段：点目 =====================
def _section_scoring(app):
    _clean(app)
    app.play(15, 3); app.play(3, 15)
    app.update_idletasks()
    depth0 = app.tree.current.depth
    check("铺子后手数=2", depth0 == 2, str(depth0))
    app.enter_scoring()
    app.update_idletasks()
    check("进入点目模式", app.scoring_mode is True)
    check("ScoreEstimator 已建", app.score_estimator is not None)
    check("初始 ScoreResult 已算", app._scoring_result is not None)
    r0 = app._scoring_result
    check("黑活子≥1", r0.black_stones >= 1, str(r0.black_stones))
    check("白活子≥1", r0.white_stones >= 1, str(r0.white_stones))
    app._on_scoring_click(15, 3)
    check("标记 Q16 死子", "Q16" in app.dead_points, str(sorted(app.dead_points)))
    r1 = app._scoring_result
    check("死子后黑活子-1", r1.black_stones == r0.black_stones - 1, str(r1.black_stones))
    app._on_scoring_click(15, 3)
    check("取消 Q16 死子", "Q16" not in app.dead_points)
    app._on_scoring_click(10, 10)
    check("空点点击不改 MoveTree", app.tree.current.depth == depth0)
    app.play(5, 5)
    check("点目模式落子被拦截", app.tree.current.depth == depth0)
    app.dead_points = set(); app._refresh_scoring()
    app.confirm_score()
    check("确认写回 tree.score_result", app.tree.score_result is not None)
    check("确认后退出点目", app.scoring_mode is False)
    app.update_idletasks()
    check("退出点目后面板收起（无空隙）", not app._scoring_frame.winfo_ismapped())
    sgf = export_sgf(app.tree, komi=app.komi, rule=app.rules, score_result=app.tree.score_result)
    check("SGF 含 RE[]", "RE[" in sgf, sgf[:120])
    check("SGF 含点目摘要", "终局点目" in sgf, sgf[:200])
    app.play(5, 5)
    check("退出后落子恢复", app.tree.current.depth == depth0 + 1)


# ===================== 段：复盘（失误/曲线/跳转）=====================
def _section_review(app):
    _clean(app)
    app.play(15, 3); app.play(3, 15)
    line = ReviewReport(app.tree).mainline_nodes()
    line[0].analysis = analysis(0.0, 0.50, [mi("D4", 3.0, 0.55, 0), mi("Q16", 1.0, 0.51, 1)])
    line[1].analysis = analysis(1.0, 0.51, [mi("D4", -3.0, 0.40, 0)])
    line[2].analysis = analysis(-3.0, 0.40, [mi("Q16", -3.0, 0.40, 0)])
    app._update_review_state()
    check("问题棋表只保留明显问题", len(app._review_map) == 1, str(len(app._review_map)))
    problem_iid = app._tv_review.get_children()[0]
    check("问题棋包含目损/AI建议",
          app._tv_review.set(problem_iid, "loss") == "2.0"
          and app._tv_review.set(problem_iid, "best") == "D4",
          str(app._tv_review.item(problem_iid, "values")))
    check("阶段概览显示关键问题数",
          "关键问题 1 手" in app.lbl_review_summary.cget("text"),
          app.lbl_review_summary.cget("text"))
    check("当前(第2手) loss≈0", app._current_loss_val is not None
          and abs(app._current_loss_val) < 1e-9, str(app._current_loss_val))
    # 单局表现：仅两手时必须明确提示样本不足，不输出虚假的精确段位。
    rating_rows = [app._tv_rating.item(iid, "values") for iid in app._tv_rating.get_children()]
    check("棋力统计表固定两行", len(rating_rows) == 2, str(rating_rows))
    check("短局双方提示样本不足",
          all(row[1] == "样本不足" for row in rating_rows), str(rating_rows))
    commentary = app.txt_game_commentary.get("1.0", "end").strip()
    check("复盘文字分析已生成", "全局" in commentary and "复盘重点" in commentary, commentary)
    app.do_undo()
    app.update_idletasks()
    check("第1手 loss≈2", app._current_loss_val is not None
          and abs(app._current_loss_val - 2.0) < 1e-9, str(app._current_loss_val))
    app.toggle_graph(); app.update_idletasks()
    check("曲线窗口存在", app._graph_win is not None and app._graph_win.winfo_exists())
    check("曲线点已算", len(app._graph_pts) >= 1, str(len(app._graph_pts)))
    if app._graph_pts:
        app._on_graph_click(SimpleNamespace(x=int(app._graph_pts[-1][1])))
        check("曲线点击跳转到末节点", app.tree.current is line[-1])
    if app._review_map:
        iid = next(iter(app._review_map))
        app._tv_review.selection_set(iid)
        app._on_review_double_click(None)
        check("问题棋双击跳转", app.tree.current in app._review_map.values())
    app.analyze_mainline()
    check("未启动引擎 analyze_mainline 不崩", True)
    app.toggle_graph()
    check("曲线窗口已关闭", app._graph_win is None)
    # 单盘复盘默认跟随画像身份，只展示本人走子；无身份时回退到双方。
    app.tree._profile_side = "B"
    app._review_scope_mode = "profile"
    app._update_review_state()
    check("复盘范围按钮显示我方",
          "我方" in app.btn_review_scope.cget("text"),
          app.btn_review_scope.cget("text"))
    check("个人范围问题榜只含黑方",
          all(item.color == "B" for item in app._problem_eval_map.values()),
          str([item.color for item in app._problem_eval_map.values()]))
    check("阶段概览显示分析覆盖",
          "分析覆盖" in app.lbl_review_summary.cget("text"),
          app.lbl_review_summary.cget("text"))
    app.tree._profile_side = "unknown"
    app._review_scope_mode = "both"
    app._update_review_state()
    # 分支节点不画等级环（评价表/概览仅主线，避免环与表不一致、无法跳转）
    app.do_undo()             # 回第 1 手
    app.play(5, 5)            # 建非主线分支
    app.update_idletasks()
    check("分支节点不画等级环", app._current_loss_val is None, str(app._current_loss_val))

    # 多问题连续导航：刷新后必须保持目标问题，不能总跳回第一条。
    _clean(app)
    app.play(15, 3); app.play(3, 15); app.play(15, 15)
    app.play(3, 3); app.play(9, 9)
    line = ReviewReport(app.tree).mainline_nodes()
    line[0].analysis = analysis(
        0.0, 0.50, [mi("D4", 7.0, 0.50, 0), mi("Q16", 0.0, 0.50, 1)])
    line[1].analysis = analysis(
        0.0, 0.50, [mi("D4", 0.0, 0.50, 0)])
    line[2].analysis = analysis(
        0.0, 0.50, [mi("D16", 8.0, 0.50, 0), mi("Q4", 0.0, 0.50, 1)])
    line[3].analysis = analysis(
        0.0, 0.50, [mi("D16", 0.0, 0.50, 0)])
    line[4].analysis = analysis(
        0.0, 0.50, [mi("K10", 0.0, 0.50, 0)])
    app._update_review_state()
    rows = list(app._tv_review.get_children())
    check("多问题测试生成两条问题", len(rows) == 2, str(len(rows)))
    check("覆盖进度条显示80%", abs(float(app.review_coverage_bar["value"]) - 80.0) < 0.01,
          str(app.review_coverage_bar["value"]))
    check("缺失分析时补全按钮可用",
          str(app.btn_complete_analysis.cget("state")) == "normal",
          str(app.btn_complete_analysis.cget("state")))
    app._tv_review.selection_set(rows[0])
    app._on_problem_select()
    first_move = app._problem_eval_map[rows[0]].move_number
    app._navigate_problem(1)
    selected = app._tv_review.selection()[0]
    second_move = app._problem_eval_map[selected].move_number
    check("下一问题刷新后保持目标", second_move != first_move
          and app._review_selected_move_no == second_move,
          "%s -> %s" % (first_move, second_move))
    check("下一问题跳到对应棋盘节点",
          app.tree.current is app._review_map[selected], str(second_move))
    check("问题位置标签同步", "问题 2/2" in app.lbl_problem_position.cget("text"),
          app.lbl_problem_position.cget("text"))
    check("单局表现与画像摘要不再重叠",
          app._tv_rating.winfo_manager() == ""
          and app.lbl_profile.winfo_manager() == "pack"
          and app._tv_rating.master is not app.lbl_profile.master)


# ===================== 段：恶手选点意图 =====================
def _section_bad_move_intent(app):
    _clean(app)
    app.play(3, 3)  # B D16
    line = ReviewReport(app.tree).mainline_nodes()
    line[0].analysis = analysis(0.0, 0.5, [
        {"move": "Q16", "scoreLead": 7.0, "winrate": 0.75,
         "order": 0, "pv": ["Q16", "D4", "Q3"]}])
    line[1].analysis = analysis(0.0, 0.3, [mi("D4", 0.0, 0.3, 0)])
    app._update_review_state()
    text = app.txt_problem_intent.get("1.0", "end").strip()
    check("恶手意图面板包含实战和AI", "实战意图" in text and "AI意图" in text, text)
    check("恶手意图面板包含选点", "D16" in text and "Q16" in text, text)


# ===================== 段：批量回流实时刷新（Bug A 回归）=====================
def _section_batch(app):
    _clean(app)
    app.toggle_graph(); app.update_idletasks()
    app.play(15, 3); app.play(3, 15); app.play(15, 15)
    line = ReviewReport(app.tree).mainline_nodes()      # root, B1, B2, B3
    line[3].analysis = analysis(-3.0, 0.4, [mi("Q16", -3.0, 0.4, 0)])
    todo = [line[0], line[1], line[2]]
    app._reset_batch_state()
    app._batch_target_nids = set(nd.nid for nd in todo)
    app._batch_total = len(todo)
    app._batch_done0 = 1
    app._batch_mainline_total = 4
    line[0].analysis = analysis(0.0, 0.5, [mi("D4", 3.0, 0.55, 0), mi("Q16", 1.0, 0.51, 1)])
    app._apply_analysis_result(line[0], line[0].analysis)
    n1 = len(app._graph_pts)
    line[1].analysis = analysis(1.0, 0.51, [mi("D4", 1.0, 0.51, 0)])
    app._apply_analysis_result(line[1], line[1].analysis)
    n2 = len(app._graph_pts)
    check("曲线随回流实时增长", n2 > n1, "%d->%d" % (n1, n2))
    check("批量进行中", app._batch_total == 3 and app._batch_done == 2,
          "%d/%d" % (app._batch_done, app._batch_total))
    line[2].analysis = analysis(-3.0, 0.4, [mi("Q16", -3.0, 0.4, 0)])
    app._apply_analysis_result(line[2], line[2].analysis)
    check("批量完成→计数清零", app._batch_total == 0 and app._batch_done == 0)
    check("失误榜含 B1(loss≈2)", len(app._review_map) >= 1, str(len(app._review_map)))


# ===================== 段：导入清空 review 缓存（Bug C 回归）=====================
def _section_import(app):
    _clean(app)
    app.play(15, 3)                                     # current = B1
    line = ReviewReport(app.tree).mainline_nodes()
    line[0].analysis = analysis(0.0, 0.5, [mi("D4", 3.0, 0.55, 0), mi("Q16", 1.0, 0.51, 1)])
    line[1].analysis = analysis(1.0, 0.51, [mi("D4", 1.0, 0.51, 0)])
    app._update_review_state()
    check("导入前 loss≈2", app._current_loss_val is not None
          and abs(app._current_loss_val - 2.0) < 1e-9, str(app._current_loss_val))
    check("导入前问题棋非空", len(app._review_map) >= 1)
    sgf_text = "(;GM[1]FF[4]SZ[19]KM[7.5];B[dd];W[pp])"
    fd_orig = _fd.askopenfilename
    with tempfile.NamedTemporaryFile("w", suffix=".sgf", delete=False, encoding="utf-8") as f:
        f.write(sgf_text)
        tmp = f.name
    _fd.askopenfilename = lambda **k: tmp
    try:
        app.do_import_sgf()
    finally:
        _fd.askopenfilename = fd_orig
    app.update_idletasks()
    check("导入后 _current_loss_val 清空", app._current_loss_val is None, str(app._current_loss_val))
    check("导入后失误榜清空", len(app._review_map) == 0, str(len(app._review_map)))
    check("导入后是新树(depth=2)", app.tree.current.depth == 2, str(app.tree.current.depth))
    check("导入后顶栏显示棋局上下文",
          app.lbl_game_title.cget("text") != "新棋局"
          and "2/2 手" in app.lbl_game_meta.cget("text"),
          "%s / %s" % (
              app.lbl_game_title.cget("text"),
              app.lbl_game_meta.cget("text")))


# ===================== 段：形势判断领地统计 + 进度条 =====================
def _section_territory_scale(app):
    _clean(app)
    # 领地统计：ownership 黑60/白60
    own = [0.0] * 361
    for i in range(60):
        own[i] = 0.8
    for i in range(60, 120):
        own[i] = -0.8
    app._render_territory({"ownership": own})
    check("领地 黑60·白60",
          "黑 60" in app.lbl_territory.cget("text") and "白 60" in app.lbl_territory.cget("text"),
          app.lbl_territory.cget("text"))
    app._render_territory({})
    check("无 ownership→领地空", app.lbl_territory.cget("text") == "")

    # 进度条：5 手主线
    _clean(app)
    for (x, y) in [(15, 3), (3, 15), (15, 15), (3, 3), (9, 9)]:
        app.tree.play(x, y)
    app._after_navigate()
    app.update_idletasks()
    check("进度条范围=5", app.scale._max == 5, str(app.scale._max))
    check("末尾进度=5", app.scale._pos == 5, str(app.scale._pos))
    check("lbl_scale=5/5", app.lbl_scale.cget("text") == "5/5", app.lbl_scale.cget("text"))
    # 回根后拖到第 3 手 → 跳转
    app.tree.reset(); app._update_scale()
    app._scrubber_change(3)
    check("拖动进度条跳到 depth=3", app.tree.current.depth == 3, str(app.tree.current.depth))
    # 点目模式进度条被拦
    app.enter_scoring(); app.update_idletasks()
    d0 = app.tree.current.depth
    app._scrubber_change(0)
    check("点目模式进度条被拦", app.tree.current.depth == d0)
    app.exit_scoring()

    # 进度条同步不能把分支节点拽回主线（set_position 不触发回调，天然不会拽回）
    _clean(app)
    app.tree.play(15, 3)    # B1 主线
    app.tree.play(3, 15)    # W2 主线
    app.tree.undo()         # 回 B1
    app.tree.play(15, 15)   # 分支 W2'（B1 现有 2 子）
    branch_node = app.tree.current
    app._after_navigate()   # 触发 _update_scale → set_position（不触发跳转）
    app.update_idletasks()
    check("进度条同步不把分支拽回主线", app.tree.current is branch_node)


# ===================== 段：可配置候选与主变 =====================
def _section_pv(app):
    _clean(app)
    app.play(15, 3)                      # 黑 Q16，轮白（to_move=W → 首标号白底）
    app.tree.current.analysis = {
        "rootInfo": {"winrate": 0.5, "scoreLead": 0.0, "currentPlayer": "W"},
        "moveInfos": [{"order": 0, "move": "D4", "winrate": 0.5, "scoreLead": 0.0,
                       "visits": 10, "pv": ["D4", "Q4", "D16", "Q3", "pass", "R3"]}],
    }
    app._render_analysis(app.tree.current.analysis)
    app.redraw(); app.update_idletasks()
    app.toggle_pv()
    check("主变模式开", app._show_pv is True)
    check("主变序列含 1.D4", "1.D4" in app.lbl_msg.cget("text"), app.lbl_msg.cget("text"))
    items = app.canvas.find_all()
    nums = sorted([app.canvas.itemcget(t, "text") for t in items
                   if app.canvas.type(t) == "text" and "bold" in app.canvas.itemcget(t, "font")
                   and app.canvas.itemcget(t, "text").isdigit()], key=int)
    check("主变标号 1-4-6（pass 跳过 5）", nums == ["1", "2", "3", "4", "6"], str(nums))
    # 黑白底交替（PIL 锐利化后画的是 image，改读绘制序列 _pv_marker_fills；to_move=W → 0 白、1 黑）
    fills = list(getattr(app, "_pv_marker_fills", []))
    check("主变标号黑白底交替（0白 1黑）",
          len(fills) >= 2 and fills[0] == COLORS["white"] and fills[1] == COLORS["black"], str(fills))
    big13 = [app.canvas.itemcget(t, "text") for t in items
             if app.canvas.type(t) == "text" and "13" in app.canvas.itemcget(t, "font")]
    check("主变模式下候选字母不画（互斥）", big13 == [], str(big13))
    app.toggle_pv()
    check("主变模式关", app._show_pv is False)
    # 无 analysis / 短 pv 不崩
    app.tree.current.analysis = None
    app.toggle_pv(); app.toggle_pv()
    app.tree.current.analysis = {"rootInfo": {}, "moveInfos": [{"order": 0, "pv": []}]}
    app.toggle_pv(); app.toggle_pv()
    check("无 analysis / 空 pv 时主变开关不崩", True)
    # 五候选切换：切 _pv_idx 看不同选的主变
    app.tree.current.analysis = {
        "rootInfo": {"winrate": 0.5, "scoreLead": 0.0, "currentPlayer": "W"},
        "moveInfos": [
            {"order": 0, "move": "D4", "pv": ["D4", "Q16"]},
            {"order": 1, "move": "Q4", "pv": ["Q4", "D16"]},
            {"order": 2, "move": "D16", "pv": ["D16", "Q4"]},
            {"order": 3, "move": "Q16", "pv": ["Q16", "D4"]},
            {"order": 4, "move": "K10", "pv": ["K10", "K11"]},
        ],
    }
    app._render_analysis(app.tree.current.analysis)
    app.update_idletasks()
    check("推荐模块按设置启用五个按钮",
          len(app._candidate_actions) == app._candidate_count
          and all(str(btn.cget("state")) != "disabled" for btn in app._candidate_buttons),
          str([btn.cget("text") for btn in app._candidate_buttons]))
    check("有结果时只显示有效候选",
          app._candidate_empty_label.winfo_manager() == ""
          and all(row.winfo_manager() == "grid" for row in app._candidate_rows))
    check("推荐按钮显示坐标和胜率",
          app._candidate_buttons[0].cget("text") == "A  D4"
          and "胜率" in app._candidate_win_labels[0].cget("text"),
          "%s / %s" % (app._candidate_buttons[0].cget("text"),
                       app._candidate_win_labels[0].cget("text")))
    app._show_candidates = True   # 候选点叠加层默认关闭（设计决策），此处模拟用户开启
    app.redraw()
    check("前三推荐同步显示在棋盘",
          len(app.canvas.find_withtag("candidate-marker")) >= 6,
          str(len(app.canvas.find_withtag("candidate-marker"))))
    app._select_candidate(1)
    check("候选列表选择态与棋盘同步",
          app._pv_idx == 1
          and app._candidate_buttons[1].cget("style") == "Accent.TButton",
          "%s / %s" % (
              app._pv_idx, app._candidate_buttons[1].cget("style")))
    app.toggle_pv()                                    # 开主变 → 默认第 1 选
    check("当前选择主变", "第2选" in app.lbl_msg.cget("text") and "1.Q4" in app.lbl_msg.cget("text"),
          app.lbl_msg.cget("text"))
    # 点推荐按钮 B，切到第 2 选
    app._select_candidate(1)
    check("点候选B行→第2选主变", "第2选" in app.lbl_msg.cget("text") and "1.Q4" in app.lbl_msg.cget("text"),
          app.lbl_msg.cget("text"))
    check("_pv_idx=1", app._pv_idx == 1, str(app._pv_idx))
    # 点推荐按钮 C
    app._select_candidate(2)
    check("点候选C行→第3选主变", "第3选" in app.lbl_msg.cget("text") and "1.D16" in app.lbl_msg.cget("text"),
          app.lbl_msg.cget("text"))
    # _pv_idx 超界 clamp
    app._pv_idx = 99; app._show_pv_sequence()
    check("_pv_idx 超界 clamp 到末位", "第5选" in app.lbl_msg.cget("text"), app.lbl_msg.cget("text"))
    app.toggle_pv()                                    # 关主变
    # pass 在 top 候选：按钮索引须与过滤后的候选一致
    app.tree.current.analysis = {
        "rootInfo": {"winrate": 0.5, "scoreLead": 0.0, "currentPlayer": "W"},
        "moveInfos": [
            {"order": 0, "move": "pass", "pv": ["pass"]},
            {"order": 1, "move": "D4", "pv": ["D4", "Q16"]},
            {"order": 2, "move": "Q4", "pv": ["Q4", "D16"]},
        ],
    }
    app._render_analysis(app.tree.current.analysis)     # pass 被跳过 → 表只显示 D4, Q4
    app.toggle_pv()                                     # 默认 _pv_idx=0 → 第1选 D4（非 pass）
    check("pass跳过后默认第1选=D4", "第1选" in app.lbl_msg.cget("text") and "1.D4" in app.lbl_msg.cget("text"),
          app.lbl_msg.cget("text"))
    check("推荐模块跳过pass只启用2项", len(app._candidate_actions) == 2,
          str(len(app._candidate_actions)))
    app._select_candidate(1)                            # 点 Q4 按钮
    check("点Q4行→第2选Q4（非D4/pass，索引一致）",
          "第2选" in app.lbl_msg.cget("text") and "1.Q4" in app.lbl_msg.cget("text"),
          app.lbl_msg.cget("text"))
    app.toggle_pv()                                     # 关主变（为后续 do_reset 段）
    # do_reset 关闭主变模式（按钮不残留 ✓）
    app.tree.current.analysis = {"rootInfo": {}, "moveInfos": [{"order": 0, "pv": ["D4"]}],
                                 "ownership": []}
    app.toggle_pv(); app.do_reset()
    check("do_reset 关闭主变模式",
          app._show_pv is False
          and ("主变 %d 步" % app._pv_length) == app.btn_pv.cget("text"))


def _section_profile_foundation(app):
    profile = PlayerProfile(
        games_count=2, evaluated_moves_count=40,
        overall=ProfileStats(moves=40, avg_score_loss=2.0),
        trend_points=[
            GameTrendPoint(
                game_id="g1", order=0, evaluated_moves=20,
                avg_score_loss=3.0, blunder_rate=None, top3_match_rate=None),
            GameTrendPoint(
                game_id="g2", order=1, evaluated_moves=20,
                avg_score_loss=1.5, blunder_rate=None, top3_match_rate=None),
        ])
    benchmark = GameBenchmark(
        status="better", confidence="low", prior_games=1,
        current_avg_loss=1.5, baseline_avg_loss=3.0, loss_improvement=1.5,
        stage_comparisons={
            "opening": {
                "current_avg_loss": 1.0, "baseline_avg_loss": 2.0,
                "loss_improvement": 1.0,
            }},
        evidence=["本局优于基线。"])
    canvas = tk.Canvas(app, width=700, height=150)
    app._draw_profile_trend(canvas, profile)
    check("个人趋势图绘制折线",
          any(canvas.type(item) == "line" for item in canvas.find_all()))
    check("个人趋势图绘制逐盘点",
          len([item for item in canvas.find_all() if canvas.type(item) == "oval"]) == 2)
    lines = app._profile_display_lines(
        profile,
        [{"id": "g1", "profileSummary": {}}, {"id": "g2", "profileSummary": {}}],
        30, benchmark=benchmark)
    text = "\n".join(lines)
    check("个人画像显示单局基线", "优于个人基线" in text and "本局优于基线" in text)
    canvas.destroy()

    check("弱点趋势改善文案",
          "改善" in app._weakness_trend_text({
              "trend": {"status": "improving", "delta_per_100": -2.5}}))
    app.open_player_profile()
    app.update_idletasks()

    def descendants(widget):
        result = []
        for child in widget.winfo_children():
            result.append(child)
            result.extend(descendants(child))
        return result

    widgets = descendants(app._profile_win)
    # _make_button 在装有 CustomTkinter 时返回 CTkButton，按类型名收集两种工厂产物
    button_texts = [
        widget.cget("text") for widget in widgets
        if "Button" in type(widget).__name__
    ]
    label_texts = [
        widget.cget("text") for widget in widgets
        if isinstance(widget, tk.Label)
    ]
    priority_tables = []
    for widget in widgets:
        if not isinstance(widget, ttk.Treeview):
            continue
        try:
            if widget.heading("focus", "text") == "训练重点":
                priority_tables.append(widget)
        except tk.TclError:
            pass
    check("个人画像提供今日复习入口",
          "开始今日复习" in button_texts and "打开错题本" in button_texts,
          str(button_texts))
    check("个人画像显示核心指标卡",
          "平均目损" in label_texts and "今日复习" in label_texts,
          str(label_texts))
    check("个人画像使用结构化优先训练表",
          len(priority_tables) == 1, str(len(priority_tables)))
    check("个人画像窗口尺寸适配桌面",
          app._profile_win.winfo_width() <= 1000
          and app._profile_win.winfo_height() <= 900,
          "%dx%d" % (
              app._profile_win.winfo_width(),
              app._profile_win.winfo_height()))
    app.open_style_profile()
    app.update_idletasks()
    check("个人画像可打开棋风窗口",
          app._style_win is not None and app._style_win.winfo_exists())
    check("棋风窗口显示八个维度",
          len(app._style_win.dimension_tree.get_children()) == 8,
          str(len(app._style_win.dimension_tree.get_children())))
    style_widgets = descendants(app._style_win)
    style_buttons = [
        widget.cget("text") for widget in style_widgets
        if "Button" in type(widget).__name__]
    check("棋风窗口提供报告与复核入口",
          all(text in style_buttons for text in (
              "导出报告", "生成复核队列", "开始复核选中项")),
          str(style_buttons))
    check("棋风窗口适配桌面",
          app._style_win.winfo_width() <= 1000
          and app._style_win.winfo_height() <= 900,
          "%dx%d" % (
              app._style_win.winfo_width(),
              app._style_win.winfo_height()))
    app._close_style_window()
    app._close_profile_window()


def main():
    app = GoAnalyzer()
    app._auto_start_attempted = True
    app.update_idletasks()
    try:
        check("主窗口适配常见桌面",
              app.winfo_reqwidth() <= 1900 and app.winfo_reqheight() <= 1000,
              "%dx%d" % (app.winfo_reqwidth(), app.winfo_reqheight()))
        check("右侧复盘工作区足够宽",
              app.tabs.master.winfo_reqwidth() >= 460,
              str(app.tabs.master.winfo_reqwidth()))
        check("研究路径标签命名清晰",
              [app.tabs.tab(tab_id, "text") for tab_id in app.tabs.tabs()]
              == ["研究", "复盘", "棋谱", "导航"],
              str([app.tabs.tab(tab_id, "text") for tab_id in app.tabs.tabs()]))
        check("复盘内容拆分为总结和问题手",
              [app.review_views.tab(tab_id, "text")
               for tab_id in app.review_views.tabs()]
              == ["总结", "问题手"])
        parent = app.btn_play
        while parent is not None and parent is not app._board_panel:
            parent = getattr(parent, "master", None)
        check("高频导航常驻棋盘区域", parent is app._board_panel)
        check("主工作区支持拖动分栏",
              isinstance(app.workspace, tk.PanedWindow))
        check("空棋盘显示产品化起步提示",
              len(app.canvas.find_withtag("board-empty-state")) == 5,
              str(len(app.canvas.find_withtag("board-empty-state"))))
        check("窗口高度适配当前屏幕",
              app.winfo_height() <= app.winfo_screenheight() - 70,
              "%d/%d" % (app.winfo_height(), app.winfo_screenheight()))
        check("顶栏显示新棋局上下文",
              app.lbl_game_title.cget("text") == "新棋局"
              and "中国规则" in app.lbl_game_meta.cget("text"),
              "%s / %s" % (
                  app.lbl_game_title.cget("text"),
                  app.lbl_game_meta.cget("text")))
        app.tabs.select(0)
        app.update_idletasks()
        check("候选区空状态替代无效按钮",
              app._candidate_empty_label.winfo_manager() == "grid"
              and not any(row.winfo_manager() for row in app._candidate_rows),
              app._candidate_empty_label.cget("text"))
        app.open_settings()
        settings_win = app._settings_win
        app.open_settings()
        check("设置窗口避免重复打开",
              settings_win is app._settings_win
              and bool(settings_win.transient()))
        app._close_settings_window()
        app._set_msg("已保存项目：demo.kga.json")
        check("成功状态使用语义色",
              app.lbl_msg.cget("fg") == COLORS["green"])
        app._set_msg("导入失败：模拟错误")
        check("错误状态使用语义色",
              app.lbl_msg.cget("fg") == COLORS["red"])
        app.tabs.select(1)
        app.review_views.select(1)
        app._remember_workspace_selection()
        check("工作区记住标签选择",
              app._ui_state["main_tab"] == 1
              and app._ui_state["review_tab"] == 1)
        app.tabs.select(0)
        app.review_views.select(0)
        _section_autostart(app)
        _section_hover_preview(app)
        _section_global_hint_takeback(app)
        _section_hotkeys(app)
        _section_autoplay(app)
        _section_scoring(app)
        _section_review(app)
        _section_bad_move_intent(app)
        _section_batch(app)
        _section_import(app)
        _section_territory_scale(app)
        _section_pv(app)
        _section_profile_foundation(app)
        print("UI smoke OK ✅")
    finally:
        app.destroy()


def test_smoke():
    main()


def test_pass_transport_button():
    """停一手（虚手）：传输栏常驻按钮全模式可见，do_pass 创建虚手节点。"""
    app = GoAnalyzer()
    try:
        app._auto_start_attempted = True
        app.update_idletasks()
        check("传输栏有停一手按钮", hasattr(app, "btn_pass"))
        check("停一手按钮文案", app.btn_pass.cget("text") == "停一手",
              app.btn_pass.cget("text"))
        depth0 = app.tree.current.depth
        app.do_pass()                       # 当前方（黑）虚手
        check("停一手创建虚手节点", app.tree.current.move is not None
              and app.tree.current.move[1] is None, str(app.tree.current.move))
        check("停一手后 depth+1", app.tree.current.depth == depth0 + 1,
              str(app.tree.current.depth))
        # 点目模式拦截 Pass（直接置标志，避免点目 widget 交互）
        app.scoring_mode = True
        app.do_pass()
        check("点目模式拦截停一手", app.tree.current.depth == depth0 + 1)
        app.scoring_mode = False
        # 问题手训练作答阶段（棋盘锁定）同样拦截
        app._drill_overlay = {"letters": {}}
        app.do_pass()
        check("问题手作答中拦截停一手", app.tree.current.depth == depth0 + 1)
        app._drill_overlay = None
    finally:
        app.destroy()


def test_strength_eval_window():
    """棋力评估：窗口可开关；阶段进度条对下得不错的阶段标亮。"""
    app = GoAnalyzer()
    try:
        app._auto_start_attempted = True
        app.update_idletasks()
        app.toggle_strength_eval()
        check("棋力评估窗口打开", app._strength_win is not None
              and app._strength_win.winfo_exists())
        check("进度条 canvas 存在", app._strength_canvas is not None)
        check("无数据时不标亮", all(not s["is_good"] for s in app._strength_segs),
              str([(s["label"], s["is_good"]) for s in app._strength_segs]))
        check("无数据时空段不武装点击（不误跳棋盘）", app._strength_segs == [],
              str(len(app._strength_segs)))
        # 挂一手最佳分析（布局 loss=0 → 优秀 → 标亮）
        app.tree.play(3, 15)  # B D4
        app.tree.root.analysis = {
            "rootInfo": {"scoreLead": 0.0, "winrate": 0.5},
            "moveInfos": [{"move": "D4", "scoreLead": 3.0, "winrate": 0.52, "order": 0}]}
        app.tree.current.analysis = {
            "rootInfo": {"scoreLead": 3.0, "winrate": 0.52},
            "moveInfos": [{"move": "Q16", "scoreLead": 3.0, "winrate": 0.52, "order": 0}]}
        app._refresh_strength_eval()
        check("下得不错阶段标亮", any(s["is_good"] for s in app._strength_segs),
              str([(s["label"], s["is_good"], s["quality"]) for s in app._strength_segs]))
        app._close_strength_eval()
        check("棋力评估窗口关闭", app._strength_win is None)
    finally:
        app.destroy()


if __name__ == "__main__":
    print("=" * 60)
    print(" UI 综合冒烟（点计 / 复盘 / 易用性，单 app）")
    print("=" * 60)
    main()
    test_pass_transport_button()
    test_strength_eval_window()
