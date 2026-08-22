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
import tkinter as tkinter
import time


def create_tk_root(factory):
    """创建 Tk root（GoAnalyzer 等），带 Windows Tcl 瞬态失败重试。

    批跑时同进程的前置 root 建销史会让 init.tcl / tk.tcl(ttk 加载) 偶发
    读取失败（实测 ~1/9，单次 150ms 重试仍不够）。创建前先清掉指向已
    销毁 root 的 _default_root（coach_ui 等建销后残留），失败时再配合
    gc + 递增退避重试。
    """
    import gc
    dead = getattr(tkinter, "_default_root", None)
    if dead is not None:
        try:
            if not dead.winfo_exists():
                tkinter._default_root = None
        except Exception:
            tkinter._default_root = None
    # R0 埋点与每日备份在测试环境整体关闭：仿真/冒烟不得写真实数据。
    # 所有测试入口（make_headless_app / smoke / 各 UI 测试）都经本工厂。
    # 必须先于 factory()：GoAnalyzer.__init__ 即调 start_background_daily_backup()，
    # 守卫放构造之后会让每个进程的首次构造对真实库起备份线程（W33 抓出）。
    import usage_log
    import backup
    usage_log.set_enabled(False)
    backup.set_enabled(False)
    for attempt in range(3):
        try:
            root = factory()
            return root
        except tk.TclError:
            if attempt == 2:
                raise
            gc.collect()
            time.sleep(0.2 * (attempt + 1))

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
    - root 创建带一次重试：Windows Tcl 偶发 init.tcl 瞬态读取失败（批跑时
      同进程有前置 root 建销史更易触发），重试即恢复
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
    app = create_tk_root(GoAnalyzer)
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
    if app.__dict__.get("_endgame_win") is not None:
        try:
            app._close_endgame_drill()
        except Exception:
            pass
    app._auto_start_attempted = True
    # 清全部 rid 挂账字典：桩引擎 rid 计数器每实例从 1 重来，任何残留挂账
    # 都会劫持后续场景同号 rid 的结果（W6→W9 曾因 _analysis_queue_pending
    # 残留 fake-5 吞掉 W9 的批量结果）。真实 App 在引擎停止/死亡时清，
    # 仿真段间没有引擎边界，必须由 clean 兜底。
    for attr in ("_analysis_queue_pending", "_style_verification_pending",
                 "_problem_compare_pending", "_drill_forced_pending",
                 "_human_sl_pending", "_mistake_forced_pending",
                 "_training_prefetch_pending", "_training_cache_bg_pending",
                 "_library_bg_pending"):
        setattr(app, attr, {})
    app.guard.clear()
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
    """返回 7 个核心模式标志的快照 dict（违规报告诊断用）。"""
    return {
        "scoring_mode": getattr(app, "scoring_mode", False),
        "training_active": bool(
            app._training and app._training.get("active") and not app._training.get("finished")),
        "drill_active": bool(_safe_drill_active(app)),
        "endgame_active": bool(_safe_endgame_active(app)),
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


def _safe_endgame_active(app):
    """安全查询官子训练激活态（与 _safe_drill_active 同款，无头环境不碰 winfo）。"""
    win = getattr(app, "_endgame_win", None)
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
