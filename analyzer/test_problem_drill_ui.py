"""test_problem_drill_ui —— 问题手训练窗口的无头冒烟（单 Tk root，不启动 KataGo）。

覆盖：空局不崩 / 构建钻取 / quiz 棋盘字母 / quiz 期间禁止落子 / 作答判分 /
选点对比表填充 / 变化图按钮 / 总结 / 关闭。纯逻辑由 test_problem_drill.py 覆盖。
"""
import os
import sys
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import tkinter.messagebox as _mb
import tkinter as tk
from tkinter import ttk

from app import GoAnalyzer
from movetree import MoveTree

_mb.showinfo = lambda *a, **k: None
_mb.showyesno = lambda *a, **k: False
_mb.showerror = lambda *a, **k: None


def check(name, cond, extra=""):
    print(("[CHECK] %-34s %s %s" % (name, "OK" if cond else "FAIL", extra)))
    if not cond:
        raise AssertionError(name)


def _clean(app):
    if app.scoring_mode:
        app.exit_scoring()
    app._stop_auto_play()
    app._auto_start_attempted = True
    app.tree = MoveTree(app.size)
    app._quality_by_move = {}


def _setup_one_blunder(app):
    """root 有 3 个 AI 候选；黑实战下出远离候选的 D16 → 目损 5.0 的问题手。"""
    _clean(app)
    app.tree._profile_side = "B"
    app.tree.current.analysis = {
        "rootInfo": {"currentPlayer": "B", "winrate": 0.55, "scoreLead": 3.0},
        "moveInfos": [
            {"move": "Q16", "order": 0, "winrate": 0.55, "scoreLead": 3.0,
             "visits": 5000, "prior": 0.20, "pv": ["Q16", "D4"]},
            {"move": "D4", "order": 1, "winrate": 0.54, "scoreLead": 2.5,
             "visits": 3000, "prior": 0.15, "pv": ["D4", "Q16"]},
            {"move": "R16", "order": 2, "winrate": 0.53, "scoreLead": 2.0,
             "visits": 1500, "prior": 0.10, "pv": ["R16"]},
        ],
    }
    app.play(3, 3)                       # D16：远离候选，构成问题手
    app.tree.current.analysis = {
        "rootInfo": {"currentPlayer": "W", "winrate": 0.40, "scoreLead": -2.0}}
    app.do_goto_root()


def main():
    import usage_log
    import backup
    usage_log.set_enabled(False)   # 测试不写使用埋点
    backup.set_enabled(False)      # 测试不触发真实备份
    app = GoAnalyzer()
    app.update_idletasks()

    # ---- 空局：不崩，不建窗口 ----
    _clean(app)
    app.open_problem_drill()
    check("空局不建训练窗", app._drill is None and app._drill_win is None)

    # ---- 构建钻取 ----
    _setup_one_blunder(app)
    app.open_problem_drill()
    check("建立训练窗", app._drill_win is not None and app._drill_win.winfo_exists())
    check("生成 1 道题", app._drill is not None and len(app._drill.moves) == 1,
          str(len(app._drill.moves) if app._drill else "None"))
    dm = app._drill.moves[0]
    check("题目识别为黑方", dm.color == "B")
    check("实战目损≈5", abs(dm.loss - 5.0) < 1e-6, str(dm.loss))
    check("一选=Q16", dm.best_move == "Q16", dm.best_move)

    # ---- quiz 棋盘字母 ----
    check("quiz 阶段棋盘有字母标记",
          len(app.canvas.find_withtag("drill-marker")) >= 4,
          str(len(app.canvas.find_withtag("drill-marker"))))
    check("quiz 阶段隐藏右侧 AI 候选",
          app._candidate_empty_label is not None
          and "隐藏" in app._candidate_empty_label.cget("text"),
          str(app._candidate_empty_label.cget("text")))
    check("header 显示第 1 题",
          "第 1 / 1 题" in app._drill_header.cget("text"), app._drill_header.cget("text"))

    # ---- quiz 期间禁止落子 ----
    depth_before = app.tree.current.depth
    app._on_click(SimpleNamespace(x=app.MARGIN + 15 * app.CELL,
                                  y=app.MARGIN + 3 * app.CELL))
    check("quiz 期间点击不落子", app.tree.current.depth == depth_before)

    # ---- 作答：选一选字母 → 答对 ----
    best_letter = dm.letter_of("c0")
    check("一选有字母", best_letter is not None, str(best_letter))
    app._drill_answer(best_letter)
    check("作答后已揭示", app._drill_revealed is True)
    check("答对计分", app._drill_result.correct == 1, str(app._drill_result.correct))
    check("作答后棋盘字母清除", not app.canvas.find_withtag("drill-marker"))
    rows = app._drill_tv.get_children()
    check("对比表填充 4 行(3 AI + 实战)", len(rows) == 4, str(len(rows)))
    check("揭示后变化图按钮可用",
          str(app._drill_var_buttons["正解图"].cget("state")) != "disabled")
    check("揭示后默认显示正解图(棋盘有变化标记)",
          len(app.canvas.find_withtag("problem-branch")) > 0,
          str(len(app.canvas.find_withtag("problem-branch"))))

    # ---- 误答路径：重新打开，选实战字母 → 答错 ----
    app._close_problem_drill()
    _setup_one_blunder(app)
    app.open_problem_drill()
    dm2 = app._drill.moves[0]
    actual_letter = dm2.letter_of("actual")
    check("实战有字母", actual_letter is not None, str(actual_letter))
    app._drill_answer(actual_letter)
    check("选实战→答错", app._drill_result.correct == 0)

    # ---- 查看答案(不作答)不计分但揭示 ----
    app._close_problem_drill()
    _setup_one_blunder(app)
    app.open_problem_drill()
    app._drill_reveal(answered_letter=None)
    check("查看答案→揭示", app._drill_revealed is True)
    check("查看答案→未计分", app._drill_result.answered == 0, str(app._drill_result.answered))

    # ---- 总结（只有 1 题，下一题直接进总结）----
    app._drill_next()
    check("下一题→总结文案", "训练总结" in app._drill_summary.cget("text"))
    check("总结含得分", "得分" in app._drill_summary.cget("text"))

    # ---- 关闭清理 ----
    app._close_problem_drill()
    check("关闭后窗口清空", app._drill_win is None and app._drill is None)

    app.destroy()
    print("test_problem_drill_ui: PASS")


if __name__ == "__main__":
    main()
