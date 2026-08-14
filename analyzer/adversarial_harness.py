"""对抗检测基础设施：无头实例化 + 段间清理 + fixture 装配 + 异步回调推进。

复用 test_ui_smoke.py 验证过的范式（GoAnalyzer + _auto_start_attempted=True +
mock 弹窗 + update_idletasks），不进 mainloop。供 invariants.py / actions.py /
test_adversarial.py 共用。

设计原则：
- 不引入 pytest/hypothesis，沿用现有 check()/run() 约定
- 所有 fixture 都基于 _clean() 彻底重置，保证序列间隔离
- self.after() 挂起的异步回调不会自己跑（无 mainloop），需 pump_after_callbacks 同步推进
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
import tkinter.filedialog as _fd
import tkinter as tk

from app import GoAnalyzer
from movetree import MoveTree


# ===================== 无头实例化 =====================

_app_instance = None  # 全局单例：一个进程内只创建一个 Tk root（Windows Tcl 稳定性）


def make_headless_app():
    """创建无头 GoAnalyzer 实例（不进 mainloop，不启动 KataGo）。

    关键约束（来自 test_ui_smoke.py 验证）：
    - _auto_start_attempted=True 关掉引擎自启，否则 _maybe_autostart 会尝试启 KataGo
    - mock 掉所有弹窗（askyesno/showinfo/showerror/askopenfilename），防阻塞
    - update_idletasks() 替代 mainloop 驱动几何/重绘
    """
    global _app_instance
    if _app_instance is not None:
        try:
            _app_instance.destroy()
        except Exception:
            pass
    # 防弹窗阻塞无头测试
    _mb.askyesno = lambda *a, **k: False
    _mb.showinfo = lambda *a, **k: None
    _mb.showerror = lambda *a, **k: None
    _fd.askopenfilename = lambda *a, **k: ""
    _fd.askopenfilenames = lambda *a, **k: ()
    _fd.asksaveasfilename = lambda *a, **k: ""
    app = GoAnalyzer()
    app._auto_start_attempted = True   # 关掉自动启 KataGo
    app.update_idletasks()
    _app_instance = app
    return app


def destroy_app():
    """销毁全局单例（测试结束时调用）。"""
    global _app_instance
    if _app_instance is not None:
        try:
            _app_instance.destroy()
        except Exception:
            pass
        _app_instance = None


# ===================== 段间清理（搬自 test_ui_smoke.py:_clean）=====================

def clean(app):
    """彻底重置：全新空树 + 清所有临时模式/缓存，保证对抗序列间隔离。

    这是 _clean()（test_ui_smoke.py:51）的增强版：补清了训练/drill/错题复习等
    更多模式状态，确保对抗检测的每个序列都从干净起点开始。
    """
    if app.scoring_mode:
        app.exit_scoring()
    app._stop_auto_play()
    if app._graph_win is not None:
        try:
            app._close_graph()
        except Exception:
            pass
    if app._drill_win is not None:
        try:
            app._close_problem_drill()
        except Exception:
            pass
    app._auto_start_attempted = True
    app.tree = MoveTree(app.size)
    app._abandon_training_state()
    app._mistake_review = None
    app._reset_batch_state()
    app._reset_pv_state()
    app._reset_problem_comparison_state()
    app._current_loss_val = None
    app._current_quality_result = None
    app._hover_point = None
    app._hint_point = None
    app._hint_pending_nid = None
    app._candidate_actions = []
    try:
        app._clear_candidate_module()
    except Exception:
        pass
    try:
        app._clear_analysis()
    except Exception:
        pass
    try:
        app._refresh_treeview()
    except Exception:
        pass
    try:
        app.redraw()
        app.update_idletasks()
    except Exception:
        pass


# ===================== Fixture 装配 =====================

def _mi(move, sl, wr, order=0, prior=0.1, pv=None):
    """构造 moveInfo dict（KataGo 候选格式）。"""
    return {"move": move, "scoreLead": sl, "winrate": wr, "order": order,
            "visits": 1000, "prior": prior, "pv": pv or [move]}


def _analysis(sl, wr, mis):
    """构造 analysis dict。"""
    return {"rootInfo": {"scoreLead": sl, "winrate": wr}, "moveInfos": mis}


def seed_fixture(app, kind="simple"):
    """装配带 analysis 的树，供需要前置棋局的动作（drill/训练/错题复习）使用。

    kind：
    - "simple"：两手空树（root + 黑D4 + 白Q16），无 analysis
    - "blunder"：搬自 test_problem_drill_ui._setup_one_blunder——root 有3候选，
      黑实战下D16远离候选构成目损5.0问题手（供 drill/错题复习）
    - "analyzed"：blunder 基础上，所有节点都带 analysis（供复盘/曲线）
    """
    clean(app)
    if kind == "simple":
        app.tree._profile_side = "B"
        app.tree.play(3, 3)    # 黑 D4
        app.tree.play(15, 15)  # 白 Q16
        app.tree.reset()
        return

    # blunder / analyzed 共用这个布局
    app.tree._profile_side = "B"
    app.tree.current.analysis = _analysis(3.0, 0.55, [
        _mi("Q16", 3.0, 0.55, 0, 0.20, ["Q16", "D4"]),
        _mi("D4", 2.5, 0.54, 1, 0.15, ["D4", "Q16"]),
        _mi("R16", 2.0, 0.53, 2, 0.10, ["R16"]),
    ])
    app.play(3, 3)  # 黑 D16：远离候选，构成问题手（目损≈5.0）
    app.tree.current.analysis = _analysis(-2.0, 0.40, [])
    if kind == "analyzed":
        app.tree.play(15, 15)  # 白 Q16
        app.tree.current.analysis = _analysis(2.0, 0.52, [
            _mi("D4", 2.0, 0.52, 0, 0.20, ["D4"]),
        ])
    app.do_goto_root()
    try:
        app._update_review_state()
    except Exception:
        pass


# ===================== 异步回调同步推进 =====================

def pump_after_callbacks(app):
    """同步推进 self.after() 挂起的异步回调。

    无 mainloop 时，self.after(ms, fn) 注册的回调不会自己执行。训练的 AI 应手
    （_training_drive_to_user_turn）等靠 after 推进的逻辑会卡住。
    update() 会 pump 一次待办 after 队列（比 update_idletasks 更彻底，但会触发
    _poll_loop 等副作用——在对抗检测场景下可接受，因为我们不连真实引擎）。
    """
    try:
        app.update()        # pump after 队列 + idletasks
    except Exception:
        pass


def drive_training_to_user_turn(app):
    """显式推进训练的 AI 应手（训练进入后靠 after 异步驱动，无头环境需手动调）。"""
    try:
        if app._training and app._training.get("active") and not app._training.get("finished"):
            app._training_drive_to_user_turn()
            pump_after_callbacks(app)
    except Exception:
        pass


# ===================== 状态快照（违规报告用）=====================

def snapshot_modes(app):
    """返回 6 个核心模式标志的快照 dict（违规报告诊断用）。"""
    return {
        "scoring_mode": getattr(app, "scoring_mode", False),
        "training_active": bool(
            app._training and app._training.get("active") and not app._training.get("finished")),
        "drill_active": bool(_safe_drill_active(app)),
        "mistake_review_active": bool(
            app._mistake_review and app._mistake_review.get("active")),
        "show_pv": getattr(app, "_show_pv", False),
        "auto_play": getattr(app, "_auto_play", False),
    }


def _safe_drill_active(app):
    """安全查询 drill 激活态（不依赖 winfo_exists，避免无头环境下 TclError）。"""
    win = getattr(app, "_drill_win", None)
    if win is None:
        return False
    try:
        return bool(win.winfo_exists())
    except Exception:
        return False


def canvas_marker_counts(app):
    """返回 canvas 上各 marker tag 的计数（图层冲突诊断用）。

    返回 dict：{tag_prefix: count}，如 {"candidate-marker": 3, "scoring-marker": 5}。
    若 canvas 不可用返回空 dict。
    """
    c = getattr(app, "canvas", None)
    if c is None:
        return {}
    counts = {}
    for tag in ("candidate-marker", "scoring-marker", "drill-marker",
                "pv-marker", "hint-marker", "hover-stone", "ripple",
                "problem-branch", "situation", "training-banner"):
        try:
            n = len(c.find_withtag(tag))
            if n:
                counts[tag] = n
        except Exception:
            pass
    return counts
