"""test_workflow_sim —— 用户仿真对抗测试：按真实操作路径编排多步工作流。

与 test_adversarial（棋盘动作 pairwise）互补：这里覆盖窗口类动作、多步闭环、
并行交错与中断恢复——pairwise 够不到的真实使用形态。

场景：
W1 复盘闭环：blunder 局 → 曲线 → 棋力 → 失误榜双击跳转 → drill → 关闭全链
W2 窗口轰炸：全部 Toplevel 同开 → 模式切换 → do_reset → 逐窗关闭查引用
W3 连打与边界：toggle 连按 ×5 → 导航越界 → 空盘开窗口/导出
W4 队列让路：批量队列在有前台模式时不得领任务（不 spawn 引擎）
W5 分析回流 + 切谱：注入假分析结果 → 立即换谱 → 清场断言
"""
import os
import sys
import json
import shutil
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import adversarial_harness as ah
from invariants import (check_all_unconditional, check_post_game_switch,
                        check_post_close)
import queue as _queue
import tempfile
import datetime
import threading
import zipfile
import game_library as gl
from analysis_queue import AnalysisQueue
from movetree import MoveTree
import backup as bk
import project_store as ps
from app import HEAT_KEYS, HEAT_LABELS


class FakeClient:
    """桩引擎：analyze 即回流假结果（真实 KataGo IPC 的受控替身）。

    ready=True 让 _poll_loop 全链活跃（kick 队列/前台请求/状态灯），
    结果经 poll() 走与真引擎完全相同的分发路径。
    """

    def __init__(self):
        self.ready = True
        self._n = 0
        self._results = _queue.Queue()

    def is_alive(self):
        return True

    def analyze(self, query):
        self._n += 1
        rid = "fake-%d" % self._n
        self._results.put((rid, self._fake_resp(query)))
        return rid

    def poll(self):
        out = []
        try:
            while True:
                out.append(self._results.get_nowait())
        except _queue.Empty:
            pass
        return out

    def stop(self):
        pass

    @staticmethod
    def _fake_resp(query):
        # moves 为嵌套对 [["B","Q16"],["W","D4"],...]（pass 为 "pass"）
        pts = []
        for pair in query.get("moves") or []:
            if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                pts.append(pair[1])
            elif isinstance(pair, str) and pair not in ("B", "W"):
                pts.append(pair)   # 容错：已扁平的旧格式
        pts = pts[-3:] or ["Q16"]
        move_infos = []
        for order, mv in enumerate(pts):
            move_infos.append({
                "move": mv, "order": order,
                "winrate": 0.52 - order * 0.01,
                "scoreLead": 1.5 - order * 0.1,
                "visits": 100 - order * 20, "prior": 0.4 - order * 0.1,
                "pv": [mv]})
        return {
            "id": query.get("id", "fake"),
            "rootInfo": {"winrate": 0.52, "scoreLead": 1.5,
                         "currentPlayer": "B"},
            "moveInfos": move_infos,
            "ownership": [0.0] * 361,
        }


def check(name, cond, extra=""):
    print("[CHECK] %-44s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        raise AssertionError(name)


class Scenario:
    """单场景执行器：逐步执行并收集异常/不变式违规，失败带场景+步骤上下文。"""

    def __init__(self, app, name):
        self.app = app
        self.name = name
        self.step_no = 0

    def step(self, label, fn, *args, **kw):
        self.step_no += 1
        tag = "%s#%d %s" % (self.name, self.step_no, label)
        try:
            fn(*args, **kw)
        except Exception as e:
            raise AssertionError("步骤异常 %s → %r\n%s" % (
                tag, e, traceback.format_exc(limit=4)))
        violations = check_all_unconditional(self.app)
        if violations:
            raise AssertionError("不变式违规 %s → %s" % (tag, violations))

    def assert_ok(self, label, cond, extra=""):
        check("%s#%d %s" % (self.name, self.step_no, label), cond, extra)


# ===================== W1 复盘闭环 =====================

def scenario_w1_review_loop(app):
    ah.seed_fixture(app, "blunder")
    sc = Scenario(app, "W1")
    sc.step("开曲线窗口", app.toggle_graph)
    sc.assert_ok("曲线窗口存在", app._graph_win is not None)
    sc.step("开棋力评估", app.toggle_strength_eval)
    sc.assert_ok("棋力窗口存在", app._strength_win is not None)
    sc.step("刷新复盘状态", app._update_review_state)
    # 失误榜双击跳转
    rows = app._tv_review.get_children() if app._tv_review else ()
    if rows:
        sc.step("选中第一行失误", lambda: app._tv_review.selection_set(rows[0]))
        sc.step("双击跳转", lambda: app._on_review_double_click(None))
        sc.assert_ok("跳转后仍在棋谱", app.tree.current is not None)
    # 问题手训练
    sc.step("开问题手训练", app.open_problem_drill)
    sc.assert_ok("drill 建立或空局跳过", True)
    sc.step("关闭 drill", app._close_problem_drill)
    sc.assert_ok("drill 引用清", app._drill_win is None and app._drill is None)
    sc.step("导出报告入口", app.do_export_review_report)   # 文件对话框 mock 为取消
    sc.step("关曲线", app._close_graph)
    sc.assert_ok("曲线引用清", app._graph_win is None)
    sc.step("关棋力", app._close_strength_eval)
    sc.assert_ok("棋力引用清", app._strength_win is None)
    for inv, msg in check_post_close(app):
        sc.assert_ok("关闭后生命周期 %s" % inv, False if msg else True, msg)


# ===================== W2 窗口轰炸 =====================

WINDOW_CLOSERS = [
    ("graph", "_graph_win", "_close_graph"),
    ("strength", "_strength_win", "_close_strength_eval"),
    ("treeview", "_tv_win", "_close_treeview"),
    ("library", "_lib_win", "_close_library_window"),
    ("mistake", "_mistake_book_win", "_close_mistake_book"),
    ("profile", "_profile_win", "_close_profile_window"),
    ("style", "_style_win", "_close_style_window"),
    ("queue", "_analysis_queue_win", "_close_analysis_queue_window"),
]


def scenario_w2_window_bomb(app):
    ah.seed_fixture(app, "blunder")
    sc = Scenario(app, "W2")
    openers = [
        ("曲线", app.toggle_graph), ("棋力", app.toggle_strength_eval),
        ("树视图", app.toggle_treeview), ("棋谱库", app.open_game_library),
        ("错题本", app.open_mistake_book), ("画像", app.open_player_profile),
        ("棋风", app.open_style_profile), ("队列", app.open_analysis_queue),
    ]
    for name, fn in openers:
        sc.step("开%s" % name, fn)
    sc.step("全开状态下进入点目", app.enter_scoring)
    sc.assert_ok("点目生效", app.scoring_mode is True)
    sc.step("退出点目", app.exit_scoring)
    sc.assert_ok("点目已退", app.scoring_mode is False)
    sc.step("全开状态下清空回根", app.do_reset)
    violations = check_post_game_switch(app)
    sc.assert_ok("换谱清场", not violations, str(violations))
    for name, attr, closer in WINDOW_CLOSERS:
        sc.step("关%s" % name, lambda c=getattr(app, closer): c())
        # V6 内嵌页模式：页面容器保留复用是设计行为；Toplevel 模式必须清引用。
        shell = getattr(app, "shell", None)
        page = shell.pages.get("library") if shell is not None else None
        if name == "library" and getattr(app, attr) is not None:
            sc.assert_ok("library 内嵌页保留复用", getattr(app, attr) is page)
        else:
            sc.assert_ok("%s 引用清" % name, getattr(app, attr) is None)
    sc.step("全部关闭后重绘", app.redraw)
    sc.assert_ok("重绘存活", app.canvas.winfo_exists())


# ===================== W3 连打与边界 =====================

def scenario_w3_toggle_spam(app):
    ah.seed_fixture(app, "analyzed")
    sc = Scenario(app, "W3")
    for i in range(6):   # 偶数次连打，最终回原态
        sc.step("候选点 toggle #%d" % i, app.toggle_candidates)
        sc.step("主变 toggle #%d" % i, app.toggle_pv)
    sc.assert_ok("连打后候选点状态回原", app._show_candidates is False)
    sc.assert_ok("连打后主变状态回原", getattr(app, "_show_pv", False) is False)
    sc.step("形势判断连按", lambda: [app.toggle_situation() for _ in range(4)])
    sc.step("热力图循环", lambda: [app.cycle_heatmap() for _ in range(3)])
    # 导航越界
    sc.step("根上再 undo", app.do_undo)
    sc.step("根上再 Left", app.do_undo)
    for _ in range(3):
        sc.step("末尾方向 redo", app.do_redo)
    sc.step("PageDown 越界", lambda: app.do_step(50))
    sc.step("PageUp 越界", lambda: app.do_step(-50))
    sc.assert_ok("导航后树完整", app.tree.current is not None)
    # 空盘场景
    ah.clean(app)
    sc.step("空盘开 drill", app.open_problem_drill)
    sc.assert_ok("空盘不建 drill", app._drill is None)
    sc.step("空盘开曲线", app.toggle_graph)
    sc.step("空盘关曲线", app._close_graph)
    sc.step("空盘导出报告", app.do_export_review_report)


# ===================== W4 队列让路 =====================

def scenario_w4_queue_yields(app):
    ah.seed_fixture(app, "simple")
    sc = Scenario(app, "W4")
    sc.step("进入点目（前台独占）", app.enter_scoring)
    before = app._analysis_queue_current
    sc.step("点目中触发队列 kick", app._kick_analysis_queue)
    sc.assert_ok("队列未领任务", app._analysis_queue_current is None or
                 app._analysis_queue_current == before)
    sc.step("退出点目", app.exit_scoring)
    # 空闲时 kick（无引擎不得 spawn 死循环：验证不抛异常即可）
    sc.step("空闲队列 kick（无引擎）", app._kick_analysis_queue)
    sc.assert_ok("空闲 kick 后应用存活", app.winfo_exists())


# ===================== W5 分析回流 + 切谱 =====================

def scenario_w5_result_then_switch(app):
    ah.seed_fixture(app, "analyzed")
    sc = Scenario(app, "W5")
    node = app.tree.current

    def inject():
        resp = {"rootInfo": {"scoreLead": 1.2, "winrate": 0.55},
                "moveInfos": [{"move": "Q16", "scoreLead": 1.2,
                               "winrate": 0.55, "order": 0,
                               "visits": 100, "prior": 0.3,
                               "pv": ["Q16"]}]}
        app._apply_analysis_result(node, resp)

    sc.step("注入分析回流", inject)
    sc.assert_ok("回流后节点有分析", node.analysis is not None)
    # 立即换谱（模拟用户在回流瞬间切棋）
    ah.seed_fixture(app, "simple")
    violations = check_post_game_switch(app)
    sc.assert_ok("回流后切谱清场", not violations, str(violations))
    sc.step("切谱后落子", lambda: app.play(3, 3))
    sc.assert_ok("落子成功", app.tree.current.depth == 1)


def advance(app, seconds):
    """推进真实时间并泵事件循环（处理 after(300) 重试 / POLL_MS 轮询）。"""
    import time as _time
    end = _time.monotonic() + seconds
    while _time.monotonic() < end:
        _time.sleep(0.03)
        app.update()


class ManualClient(FakeClient):
    """受控桩：analyze 只登记不发结果，测试决定何时回流（隔离陈旧结果）。"""

    def __init__(self):
        super().__init__()
        self.queries = {}

    def analyze(self, query):
        self._n += 1
        rid = "manual-%d" % self._n
        self.queries[rid] = query
        return rid

    def deliver(self, rid):
        self._results.put((rid, self._fake_resp(self.queries[rid])))


# ===================== W6 桩引擎队列并发 =====================

def scenario_w6_fake_engine_queue(app):
    """桩引擎让批量队列真跑：入队→领取→逐节点收发→完成。

    验证设计语义：队列在前台独占模式（点目）期间暂停发送、状态不被破坏，
    退出后自动续跑完成。库重定向到临时目录，不碰真实 game_library。
    """
    tmp = tempfile.mkdtemp(prefix="sim_w6_")
    orig = (gl.LIBRARY_DIR, gl.INBOX_DIR, gl.SGF_DIR, gl.PROJECT_DIR,
            gl.INDEX_PATH, gl.PROFILE_CACHE_PATH)
    gl.LIBRARY_DIR = tmp
    gl.INBOX_DIR = os.path.join(tmp, "inbox")
    gl.SGF_DIR = os.path.join(tmp, "sgf")
    gl.PROJECT_DIR = os.path.join(tmp, "projects")
    gl.INDEX_PATH = os.path.join(tmp, "index.json")
    gl.PROFILE_CACHE_PATH = os.path.join(tmp, "profile_cache.json")
    real_client = app.client
    real_queue = app._analysis_queue
    try:
        ah.seed_fixture(app, "simple")
        tree = MoveTree(19)
        tree.play(3, 3)
        tree.play(15, 15)
        sgf_text = "(;GM[1]FF[4]SZ[19];B[dd];W[pp])"
        sgf_path = os.path.join(tmp, "g6.sgf")
        with open(sgf_path, "w", encoding="utf-8") as f:
            f.write(sgf_text)
        rec = gl.add_sgf_to_library(sgf_path, sgf_text, tree,
                                    rules="chinese", komi=7.5)
        app._analysis_queue = AnalysisQueue(os.path.join(tmp, "queue.json"))
        app.client = FakeClient()
        sc = Scenario(app, "W6")
        sc.step("入队一盘", lambda: app._enqueue_records_for_analysis([rec]))
        sc.step("手动 kick 领取", app._kick_analysis_queue)
        sc.assert_ok("已领任务", app._analysis_queue_current is not None)
        # 前台抢占：进点目（独占模式）
        sc.step("任务在飞时进点目", app.enter_scoring)
        sc.assert_ok("点目生效", app.scoring_mode is True)
        sc.step("推进 0.5s（在飞回流+让路重试）", lambda: advance(app, 0.5))
        task = app._analysis_queue.tasks()[0]
        sc.assert_ok("点目期间队列暂停不损坏",
                     task.get("status") in ("running", "queued")
                     and app._analysis_queue_current is not None,
                     str(task.get("status")))
        sc.assert_ok("点目未被队列破坏", app.scoring_mode is True)
        # 退出独占 → 队列自动续跑完成
        sc.step("退出点目", app.exit_scoring)
        sc.step("推进 2s（逐节点续跑）", lambda: advance(app, 2.0))
        statuses = [t.get("status") for t in app._analysis_queue.tasks()]
        sc.assert_ok("退出后任务自动完成", "completed" in statuses,
                     str(statuses))
        # I14 是换谱不变式：走 app 真实换谱路径（do_reset→_reset_for_new_game）
        sc.step("真实换谱路径：清空回根", app.do_reset)
        violations = check_post_game_switch(app)
        sc.assert_ok("换谱后状态干净（含 I14 nid 缓存）", not violations,
                     str(violations))
        # 增强轮回归：训练已结束（finished）不得阻塞队列领取
        # （旧 kick/send 手抄链漏查 finished——统一进 _foreground_busy 后修正）
        sc.step("注入已结束的训练态", lambda: setattr(
            app, "_training",
            {"active": True, "finished": True, "user_color": "B"}))
        sgf2 = "(;GM[1]FF[4]SZ[19];B[ee])"
        p2 = os.path.join(tmp, "g6b.sgf")
        with open(p2, "w", encoding="utf-8") as f:
            f.write(sgf2)
        tree2 = MoveTree(19)
        tree2.play(2, 2)

        def _enqueue2():
            rec2 = gl.add_sgf_to_library(p2, sgf2, tree2,
                                         rules="chinese", komi=7.5)
            app._enqueue_records_for_analysis([rec2])
        sc.step("入队第二盘", _enqueue2)
        sc.step("kick 领取（训练已结束应放行）", app._kick_analysis_queue)
        sc.assert_ok("已结束训练不阻塞队列",
                     app._analysis_queue_current is not None)
    finally:
        gl.LIBRARY_DIR, gl.INBOX_DIR, gl.SGF_DIR, gl.PROJECT_DIR, \
            gl.INDEX_PATH, gl.PROFILE_CACHE_PATH = orig
        app.client = real_client
        app._analysis_queue = real_queue
        app._analysis_queue_current = None
        # 队列挂账也必须清：桩 rid 计数器每实例重置，残留会劫持后续场景
        # 同号 rid 的结果（与真实 App _interrupt_analysis_queue 语义对齐）
        app._analysis_queue_pending = {}


# ===================== W7 陈旧结果防护 =====================

def scenario_w7_stale_result_guard(app):
    """请求发出→用户立即导航走→结果回流：不得污染当前节点。

    用 ManualClient 隔离：根节点有自己的缓存分析（analyzed 局），
    回根不会再发新请求；此时回流 A 的结果，断言：
    - 当前节点不移动（guard 按对象绑定）
    - 根节点 analysis 对象身份不变（陈旧结果不得覆盖/复用到根）
    - A 的缓存正确写入，导航回 A 立即可用
    """
    ah.seed_fixture(app, "analyzed")
    app.client = ManualClient()
    try:
        sc = Scenario(app, "W7")
        root = app.tree.current
        root_analysis = root.analysis
        sc.assert_ok("根有缓存分析（回根不再发请求）",
                     root_analysis is not None)
        sc.step("落子到 A（depth1）", lambda: app.play(3, 3))
        node_a = app.tree.current
        sc.step("对 A 发起分析（不回流）", app.force_analyze)
        sc.assert_ok("请求已注册", app.guard.pending_count() >= 1)
        rids = list(app.client.queries)
        sc.step("立即回根（结果未回流）", app.do_goto_root)
        sc.assert_ok("当前在根", app.tree.current.depth == 0)
        sc.step("回流 A 的陈旧结果",
                lambda: app.client.deliver(rids[-1]))
        sc.step("泵循环", lambda: [ah.pump_after_callbacks(app)
                                   for _ in range(3)])
        sc.assert_ok("陈旧结果未移动当前节点",
                     app.tree.current.depth == 0)
        sc.assert_ok("根 analysis 未被陈旧结果覆盖（身份不变）",
                     app.tree.current.analysis is root_analysis)
        sc.assert_ok("A 的缓存已写入", node_a.analysis is not None)
        # 导航回 A：缓存立即可用
        sc.step("前进回 A", app.do_redo)
        sc.assert_ok("回到 A", app.tree.current is node_a)
        sc.step("泵循环（缓存渲染）",
                lambda: [ah.pump_after_callbacks(app) for _ in range(2)])
        sc.assert_ok("A 上候选可用",
                     bool(getattr(app, "_candidate_actions", None)))
        # 点目中结果回流分支
        sc.step("进点目", app.enter_scoring)
        sc.step("点目中强制分析", app.force_analyze)
        rids2 = list(app.client.queries)
        sc.step("回流点目请求",
                lambda: app.client.deliver(rids2[-1]))
        sc.step("泵循环×2", lambda: [ah.pump_after_callbacks(app)
                                      for _ in range(2)])
        sc.assert_ok("点目中回流不崩", app.scoring_mode is True)
        sc.step("退出点目", app.exit_scoring)
    finally:
        app.client = None


# ===================== W8 键盘×模式全矩阵 =====================

def scenario_w8_keyboard_mode_matrix(app):
    """每个快捷键动作 × 每种模式：守卫必须一致拦截/放行且零异常。

    训练/错题复习用状态注入法（守卫只读标志，注入等价状态即可验证，
    无需跑完整进入流程）。scoring/drill 走真实入口。
    """
    ah.seed_fixture(app, "analyzed")
    sc = Scenario(app, "W8")

    # 导航类动作：这些在独占模式下都应被拦（tree.depth 不变）
    nav_actions = [
        ("Left/undo", app.do_undo),
        ("Right/redo", app.do_redo),
        ("Home/回根", app.do_goto_root),
        ("End/跳末", app.do_goto_mainline_end),
        ("PageDown+10", lambda: app.do_step(10)),
        ("PageUp-10", lambda: app.do_step(-10)),
        ("Pass(Ctrl+P)", app.do_pass),
        ("Space/自动播放", app.toggle_auto_play),
        ("F1/提示", app.show_hint),
        ("Ctrl+R/强刷", app.force_analyze),
    ]

    def run_matrix(mode_label, exclusive, allowed_keys=()):
        """逐键执行。exclusive 模式下：导航键默认必须拦（节点不变）；
        allowed_keys 中的键是该模式的合法交互（只验证零异常+不变式）。
        undo 语义单独验证（点目=退出并悔棋是 T-8 设计）。
        F1 加强断言（R4，原 P2 泄题弱断言）：被拦模式下提示不得给出
        选点（_hint_point）也不得画 hint-marker——节点不变挡不住泄题。"""
        for key_label, fn in nav_actions:
            if exclusive and key_label == "Left/undo":
                continue
            depth_before = app.tree.current.depth
            nid_before = app.tree.current.nid
            sc.step("[%s] %s" % (mode_label, key_label), fn)
            if exclusive and key_label not in allowed_keys:
                sc.assert_ok("[%s] %s 被拦（节点不变）" % (mode_label, key_label),
                             app.tree.current.depth == depth_before
                             and app.tree.current.nid == nid_before)
                if key_label == "F1/提示":
                    sc.assert_ok("[%s] F1 未泄题（无提示点/标记）" % mode_label,
                                 app._hint_point is None
                                 and not app.canvas.find_withtag("hint-marker"))
            if exclusive and key_label == "F1/提示" and \
                    mode_label == "点目" and key_label in allowed_keys:
                # 点目提示只走消息分支，不得在棋盘上画选点提示
                sc.assert_ok("[点目] F1 不画棋盘提示",
                             app._hint_point is None
                             and not app.canvas.find_withtag("hint-marker"))

    def check_undo_semantics(mode_label, *, blocked=True, exits_scoring=False):
        """undo 的逐模式期望：
        - 点目：设计行为=退出点目并悔棋（T-8），scoring 变 False 且 depth-1
        - 训练/错题/drill：必须拦截（节点不变、模式保持）
        """
        depth_before = app.tree.current.depth
        sc.step("[%s] Left/undo" % mode_label, app.do_undo)
        if exits_scoring:
            sc.assert_ok("[%s] undo=退出点目并悔棋（T-8 设计）" % mode_label,
                         app.scoring_mode is False
                         and app.tree.current.depth == depth_before - 1)
        elif blocked:
            sc.assert_ok("[%s] undo 被拦（节点不变）" % mode_label,
                         app.tree.current.depth == depth_before)

    # 模式1：普通模式（导航应放行——只验证零异常，位移合法）
    sc.step("普通模式基线导航", lambda: app.do_goto_mainline_end())
    run_matrix("普通", exclusive=False)

    # 模式2：点目（真实进入）：F1 点目分支/Ctrl+R ownership 是合法交互
    sc.step("进入点目", app.enter_scoring)
    run_matrix("点目", exclusive=True, allowed_keys=("F1/提示", "Ctrl+R/强刷"))
    check_undo_semantics("点目", blocked=False, exits_scoring=True)
    sc.step("退出点目", app.exit_scoring)

    # 模式3：训练（状态注入；user_color=对色→未轮到用户=全锁）：
    # F1 训练提示 / Ctrl+R 训练分析是合法交互
    app._training = {"active": True, "finished": False,
                     "user_color": "W", "nodes": [], "task": {}}
    run_matrix("训练", exclusive=True, allowed_keys=("F1/提示", "Ctrl+R/强刷"))
    check_undo_semantics("训练", blocked=True)
    app._training = None

    # 模式4：错题复习（状态注入）：F1 请求提示合法；Pass=作答后弹回题面（净位移 0）
    app._mistake_review = {"active": True, "item": {}, "parent":
                           app.tree.current, "attempts": 0}
    run_matrix("错题复习", exclusive=True,
               allowed_keys=("F1/提示", "Ctrl+R/强刷"))
    check_undo_semantics("错题复习", blocked=True)   # W8 抓出漏拦→已修，此为回归
    app._mistake_review = None

    # 模式5：drill（真实进入，盲测——F1 提示可能泄答案，期望拦）
    ah.seed_fixture(app, "blunder")
    sc.step("进入 drill", app.open_problem_drill)
    if app._drill is not None:
        run_matrix("drill", exclusive=True)
        check_undo_semantics("drill", blocked=True)
        sc.step("关闭 drill", app._close_problem_drill)
    violations = check_all_unconditional(app)
    sc.assert_ok("矩阵后无残留", not violations, str(violations))


# ===================== W9 真实 SGF 全链 =====================

class SgfScanClient(FakeClient):
    """W9 桩引擎：黑方每手净亏 2.5 目、白方中性——制造 3 个黑方问题手。

    scoreLead 惯例与 seed_fixture 一致（loss = 父 moveInfos[0].scoreLead
    - 子 rootInfo.scoreLead）：S(d) = 2.0 - 2.5*ceil(d/2)。
    黑落子（d 偶→奇）局面降 2.5；白落子（d 奇→偶）不变。
    """

    @staticmethod
    def _fake_resp(query):
        moves = [p for p in (query.get("moves") or [])
                 if isinstance(p, (list, tuple)) and len(p) >= 2]
        d = len(moves)
        s = 2.0 - 2.5 * ((d + 1) // 2)
        mis = []
        for order, mv in enumerate(("Q16", "D4", "R16")):
            sl = s - order * 0.2
            mis.append({
                "move": mv, "order": order,
                "scoreLead": sl, "winrate": 0.5 + sl / 40,
                "visits": 100 - order * 20, "prior": 0.3, "pv": [mv]})
        return {
            "id": query.get("id", "w9"),
            "rootInfo": {"winrate": 0.5 + s / 40, "scoreLead": s,
                         "currentPlayer": "B" if d % 2 == 0 else "W"},
            "moveInfos": mis,
            "ownership": [0.0] * 361,
        }


def scenario_w9_real_sgf_full_chain(app):
    """真实 .sgf 文件全链：导入（含换谱清理）→ 自动预扫回流 → 问题判定 → 导航。

    与 W1-W8 的 seed_fixture 合成局面互补：这里走 do_import_sgf 真实解析
    （文件对话框 mock 到临时 SGF），入库拿到 record_id，after(300) 自动
    预扫由 SgfScanClient 供结果，断言复盘问题榜从真实文件链路长出来。
    """
    import tkinter.filedialog as _fd
    tmp = tempfile.mkdtemp(prefix="sim_w9_")
    orig = (gl.LIBRARY_DIR, gl.INBOX_DIR, gl.SGF_DIR, gl.PROJECT_DIR,
            gl.INDEX_PATH, gl.PROFILE_CACHE_PATH)
    gl.LIBRARY_DIR = tmp
    gl.INBOX_DIR = os.path.join(tmp, "inbox")
    gl.SGF_DIR = os.path.join(tmp, "sgf")
    gl.PROJECT_DIR = os.path.join(tmp, "projects")
    gl.INDEX_PATH = os.path.join(tmp, "index.json")
    gl.PROFILE_CACHE_PATH = os.path.join(tmp, "profile_cache.json")
    real_client = app.client
    fd_orig = _fd.askopenfilename
    try:
        ah.clean(app)
        sgf_text = ("(;GM[1]FF[4]SZ[19]KM[7.5]PB[黑方]PW[白方]"
                    ";B[pd];W[dp];B[pp];W[dd];B[qj];W[cj])")
        sgf_path = os.path.join(tmp, "real_game.sgf")
        with open(sgf_path, "w", encoding="utf-8") as f:
            f.write(sgf_text)
        # 导入前制造一个会被换谱清理的状态（v1 修复的回归：点目锁棋盘）
        app.enter_scoring()
        _fd.askopenfilename = lambda **k: sgf_path
        app.client = SgfScanClient()
        sc = Scenario(app, "W9")
        sc.step("导入真实 SGF", app.do_import_sgf)
        sc.assert_ok("主线 6 手已解析", app.tree.current.depth == 6,
                     str(app.tree.current.depth))
        sc.assert_ok("点目随换谱退出（v1 回归）", app.scoring_mode is False)
        sc.assert_ok("拿到棋谱库记录 id", bool(app._library_record_id),
                     str(app._library_record_id))
        rec = gl.get_record(app._library_record_id)
        sc.assert_ok("记录可从库读回", rec is not None
                     and rec.get("name"), str(rec and rec.get("name")))
        violations = check_post_game_switch(app)
        sc.assert_ok("导入后换谱不变式干净", not violations, str(violations))
        # after(300) 自动预扫：advance 泵真实定时器，结果走真实 poll 分发
        # （分片推进直到完成：满负荷下 2.5s 可能差一个 poll 周期）
        deadline_waited = 0.0
        while deadline_waited < 6.0:
            analyzed = sum(1 for nd in _mainline_nodes(app)
                           if nd.analysis is not None)
            if analyzed >= 7:
                break
            advance(app, 0.5)
            deadline_waited += 0.5
        sc.assert_ok("主线全部拿到分析（预扫回流）", analyzed >= 7,
                     "%d/7（等 %.1fs）%s" % (
                         analyzed, deadline_waited,
                         [nd.depth for nd in _mainline_nodes(app)
                          if nd.analysis is None] if analyzed < 7 else ""))
        app.tree._profile_side = "B"
        sc.step("重算复盘状态", app._update_review_state)
        problems = app._review_map
        sc.assert_ok("真实链路长出问题手（黑 3 手×2.5 目）",
                     len(problems) >= 1, str(len(problems)))
        if problems:
            iid = next(iter(problems))
            sc.step("双击问题跳转", lambda: (app._tv_review.selection_set(iid),
                                              app._on_review_double_click(None)))
            sc.assert_ok("问题跳转成功", app.tree.current in problems.values())
    finally:
        gl.LIBRARY_DIR, gl.INBOX_DIR, gl.SGF_DIR, gl.PROJECT_DIR, \
            gl.INDEX_PATH, gl.PROFILE_CACHE_PATH = orig
        _fd.askopenfilename = fd_orig
        app.client = real_client
        app._library_record_id = None


def _mainline_nodes(app):
    """主线节点列表（root 起含未落子根）。"""
    from review import ReviewReport
    return ReviewReport(app.tree).mainline_nodes()


# ===================== W10 训练预取回流竞态 =====================

def scenario_w10_training_prefetch_race(app):
    """训练预取在飞 × 用户快速变化：退训练 / 换谱 / waiter 命中 / 榜外手。

    ManualClient 受控回流隔离陈旧结果；训练态用状态注入（守卫只读标志，
    与 W8 同法）。断言核心：迟到的预取结果不得写错节点、不得拽走当前
    节点、退出/换谱后不得复活任何预取状态。
    """
    ah.seed_fixture(app, "analyzed")   # 根有 3 候选（Q16/D4/R16），轮黑
    app.client = ManualClient()
    sc = Scenario(app, "W10")
    root = app.tree.current
    root_analysis_before = root.analysis

    def inject_training():
        app._training = {"active": True, "finished": False,
                         "user_color": "B", "awaiting": "user",
                         "nodes": [], "task": {}}

    def issue_prefetch(label):
        inject_training()
        before = set(app.client.queries)
        app._training_prefetch_user_moves()
        qids = [q for q in app.client.queries if q not in before]
        sc.assert_ok("%s：预取已发出" % label,
                     len(qids) >= 1 and len(app._training_prefetch_pending) == len(qids),
                     "%d 请求/%d 挂账" % (len(qids), len(app._training_prefetch_pending)))
        return qids

    def deliver_all(qids):
        for rid in qids:
            app.client.deliver(rid)
        # pump×3 不够：poll 定时器重排后需真实时间流逝才触发（A/B 段曾因
        # 未真正分发而"弱通过"）。advance 让回流走完整真实 poll 分发链。
        advance(app, 0.5)

    # A) 在飞 → 退出训练 → 迟到回流：全部丢弃，不写任何节点
    qids = issue_prefetch("A")
    sc.step("A: 退出训练（真实清理路径）", app._abandon_training_state)
    sc.assert_ok("A: 挂账/waiter 全清",
                 not app._training_prefetch_pending
                 and not app._training_prefetch_waiters
                 and app._training is None)
    sc.step("A: 迟到结果回流", lambda: deliver_all(qids))
    sc.assert_ok("A: 迟到结果未覆盖根分析（身份不变）",
                 root.analysis is root_analysis_before)
    sc.assert_ok("A: 当前仍在根", app.tree.current is root)

    # B) 在飞 → 清空回根（真实 do_reset 路径）→ 迟到回流：不写不拽
    #    注：do_reset 是回根导航（保留树与分析），非换新树——与 A 同以
    #    "根分析对象身份不变"断言迟到结果被丢弃
    qids = issue_prefetch("B")
    sc.step("B: 清空回根", app.do_reset)
    violations = check_post_game_switch(app)
    sc.assert_ok("B: 换谱后无预取残留",
                 not violations and not app._training_prefetch_pending,
                 str(violations))
    sc.step("B: 迟到结果回流", lambda: deliver_all(qids))
    sc.assert_ok("B: 迟到结果未覆盖根分析（身份不变）",
                 app.tree.current.analysis is root_analysis_before
                 and app.tree.current.depth == 0)

    # C) waiter 正常竞态：用户立即下出预取候选 → 在飞注册 waiter → 回流命中
    qids = issue_prefetch("C")
    sc.step("C: 用户下出预取候选 Q16", lambda: app.tree.play(15, 3))
    played = app.tree.current
    sc.assert_ok("C: 节点尚无分析（在飞）", played.analysis is None)
    box = {}
    sc.step("C: 消费预取（在飞→注册 waiter）",
            lambda: box.update(hit=app._consume_training_prefetch(played)))
    sc.assert_ok("C: waiter 已注册（等待回流）",
                 box.get("hit") is True and bool(app._training_prefetch_waiters),
                 str(list(app._training_prefetch_waiters)))
    sc.step("C: 预取结果回流", lambda: deliver_all(qids))
    sc.assert_ok("C: waiter 节点命中分析", played.analysis is not None)
    sc.assert_ok("C: 当前节点未被拽走", app.tree.current is played)

    # D) 榜外手：用户下预取之外的手 → 迟到结果只进缓存不写节点
    qids = issue_prefetch("D")
    sc.step("D: 用户下榜外手 K10", lambda: app.tree.play(9, 9))
    off = app.tree.current
    box = {}
    sc.step("D: 消费预取（榜外无缓存）",
            lambda: box.update(hit=app._consume_training_prefetch(off)))
    sc.assert_ok("D: 榜外手无缓存可消费（未注册 waiter）",
                 box.get("hit") is False and not app._training_prefetch_waiters)
    sc.step("D: 预取结果回流", lambda: deliver_all(qids))
    sc.assert_ok("D: 榜外节点未被写入", off.analysis is None)
    sc.assert_ok("D: 结果进预热缓存", len(app._training_prefetch_cache) >= 1,
                 str(len(app._training_prefetch_cache)))
    app._training = None


# ===================== W11 引擎生命周期挂账清理 =====================

def scenario_w11_engine_lifecycle_pending_cleanup(app):
    """引擎启动/停止/死亡三个生命周期边界必须清全部 rid 挂账。

    客户端 rid 计数器每实例从 1 重来（真实客户端是确定性 q1,q2…），
    残留挂账会劫持新会话同号结果派发给失效上下文（W9 场景互染的
    App 侧对偶，R5 收口为 _reset_engine_request_state 统一清理）。
    """
    ah.seed_fixture(app, "analyzed")
    sc = Scenario(app, "W11")
    app._analysis_queue_current = None   # _stop_katago 会走队列释放，先归位
    app._analysis_queue_pending["q1"] = {"ctx": {"task_id": "t"}, "node": None}
    app._problem_compare_pending["q2"] = {"ctx": {}, "branch": "actual"}
    app._drill_forced_pending["q3"] = (1, "Q16")
    app._human_sl_pending["q4"] = (1, "current", "rank_1d", "Q16", "B")
    app._library_bg_pending["q5"] = {"record_id": "r"}
    app._training_prefetch_pending["q6"] = {"key": (1, "B", "q16"), "moves": []}
    app._training_cache_bg_pending["q7"] = {"record_id": "r"}
    app._mistake_forced_pending["q8"] = (1, "Q16")
    app._style_verification_pending["q9"] = {"item": {}}
    dicts = ["_analysis_queue_pending", "_problem_compare_pending",
             "_drill_forced_pending", "_human_sl_pending",
             "_library_bg_pending", "_training_prefetch_pending",
             "_training_cache_bg_pending", "_mistake_forced_pending",
             "_style_verification_pending"]
    sc.assert_ok("注入九路在飞挂账（真实客户端 rid 格式）",
                 all(getattr(app, d) for d in dicts))
    sc.step("停止引擎（真实 _stop_katago 路径）", app._stop_katago)
    sc.assert_ok("停止边界九路挂账全清",
                 all(not getattr(app, d) for d in dicts),
                 str([d for d in dicts if getattr(app, d)]))
    # 重挂九路 → 启动边界（_start_katago 内联 _reset_engine_request_state，
    # 无头下不真启引擎，直接验统一方法）
    app._analysis_queue_pending["q1"] = {"ctx": {}, "node": None}
    app._problem_compare_pending["q2"] = {"ctx": {}, "branch": "ai"}
    app._drill_forced_pending["q3"] = (2, "D4")
    app._human_sl_pending["q4"] = (2, "stronger", "rank_3d", "D4", "W")
    app._library_bg_pending["q5"] = {"record_id": "r2"}
    app._training_prefetch_pending["q6"] = {"key": (2, "W", "d4"), "moves": []}
    app._training_cache_bg_pending["q7"] = {"record_id": "r2"}
    app._mistake_forced_pending["q8"] = (2, "D4")
    app._style_verification_pending["q9"] = {"item": {}}
    sc.step("启动边界清理（统一方法）", app._reset_engine_request_state)
    sc.assert_ok("启动边界九路挂账全清",
                 all(not getattr(app, d) for d in dicts))


# ===================== W12 棋谱库×换谱谱系 =====================

def scenario_w12_library_open_chains(app):
    """库窗口打开棋谱全链：入库 → 开库窗 → 选中打开 → 换谱清理 + 窗口自动关。

    用户痛点回归（打开库棋谱后库窗口不再挡屏）；换谱三入口
    （导入/项目/库双击）共用 _reset_for_new_game 的谱系验证。
    """
    tmp = tempfile.mkdtemp(prefix="sim_w12_")
    orig = (gl.LIBRARY_DIR, gl.INBOX_DIR, gl.SGF_DIR, gl.PROJECT_DIR,
            gl.INDEX_PATH, gl.PROFILE_CACHE_PATH)
    gl.LIBRARY_DIR = tmp
    gl.INBOX_DIR = os.path.join(tmp, "inbox")
    gl.SGF_DIR = os.path.join(tmp, "sgf")
    gl.PROJECT_DIR = os.path.join(tmp, "projects")
    gl.INDEX_PATH = os.path.join(tmp, "index.json")
    gl.PROFILE_CACHE_PATH = os.path.join(tmp, "profile_cache.json")
    try:
        ah.clean(app)
        recs = {}
        for key, moves in (("A", ";B[dd];W[pp]"), ("B", ";B[pd];W[dp];B[pp]")):
            sgf_text = "(;GM[1]FF[4]SZ[19]KM[7.5]%s)" % moves
            path = os.path.join(tmp, "g_%s.sgf" % key)
            with open(path, "w", encoding="utf-8") as f:
                f.write(sgf_text)
            t = MoveTree(19)
            for mv in [m for m in moves.split(";") if m]:
                coord = mv[1:].strip("[]")     # SGF 行列都是字母：dd=(3,3)
                t.play(ord(coord[0]) - 97, ord(coord[1]) - 97)
            recs[key] = gl.add_sgf_to_library(path, sgf_text, t,
                                              rules="chinese", komi=7.5)
        sc = Scenario(app, "W12")
        depths = {"A": 2, "B": 3}
        for key in ("B", "A"):
            rec = recs[key]
            sc.step("开棋谱库窗口（%s 前）" % key, app.open_game_library)
            sc.assert_ok("库窗口已开（列表就绪）",
                         app._lib_win is not None and app._lib_tv is not None)
            sc.assert_ok("库列表含记录", len(app._lib_map) >= 2,
                         str(len(app._lib_map)))
            app.enter_scoring()   # 换谱前的独占态（v1 修复回归）
            iid = next(i for i, r in app._lib_map.items()
                       if r.get("id") == rec.get("id"))
            sc.step("选中并打开记录 %s" % key,
                    lambda: (app._lib_tv.selection_set(iid),
                             app._open_selected_library_record()))
            sc.assert_ok("%s：树已切换（%d 手）" % (key, depths[key]),
                         app.tree.current.depth == depths[key],
                         str(app.tree.current.depth))
            sc.assert_ok("%s：record_id 已绑定" % key,
                         app._library_record_id == rec.get("id"))
            sc.assert_ok("%s：点目随换谱退出" % key, app.scoring_mode is False)
            # 库窗口自动退场（用户痛点）：Toplevel 模式销毁置空；V6 内嵌页
            # 模式设计为切回工作区、页面容器保留复用（shell 路由不在库页）
            shell = getattr(app, "shell", None)
            if shell is not None:
                sc.assert_ok("%s：库页已切回工作区" % key,
                             app.router.current != "library",
                             str(app.router.current))
            else:
                sc.assert_ok("%s：库窗口自动关闭" % key,
                             app._lib_win is None and app._lib_tv is None)
            violations = check_post_game_switch(app)
            sc.assert_ok("%s：换谱不变式干净" % key, not violations,
                         str(violations))
    finally:
        gl.LIBRARY_DIR, gl.INBOX_DIR, gl.SGF_DIR, gl.PROJECT_DIR, \
            gl.INDEX_PATH, gl.PROFILE_CACHE_PATH = orig
        app._library_record_id = None


# ===================== W13 drill×批量队列交错 =====================

def scenario_w13_drill_queue_interleave(app):
    """批量队列真跑 × drill 前台独占：让路不损坏、退出自动续跑（W6 的 drill 变体）。

    W6 验证了点目×队列；drill 是另一前台独占态（含棋盘锁定/揭示子状态），
    交错时队列同样必须暂停让路、drill 关闭后自动续跑完成。
    """
    tmp = tempfile.mkdtemp(prefix="sim_w13_")
    orig = (gl.LIBRARY_DIR, gl.INBOX_DIR, gl.SGF_DIR, gl.PROJECT_DIR,
            gl.INDEX_PATH, gl.PROFILE_CACHE_PATH)
    gl.LIBRARY_DIR = tmp
    gl.INBOX_DIR = os.path.join(tmp, "inbox")
    gl.SGF_DIR = os.path.join(tmp, "sgf")
    gl.PROJECT_DIR = os.path.join(tmp, "projects")
    gl.INDEX_PATH = os.path.join(tmp, "index.json")
    gl.PROFILE_CACHE_PATH = os.path.join(tmp, "profile_cache.json")
    real_client = app.client
    real_queue = app._analysis_queue
    try:
        ah.seed_fixture(app, "blunder")   # 有问题手 → drill 可进
        sgf_text = "(;GM[1]FF[4]SZ[19];B[dd];W[pp])"
        sgf_path = os.path.join(tmp, "g13.sgf")
        with open(sgf_path, "w", encoding="utf-8") as f:
            f.write(sgf_text)
        t = MoveTree(19)
        t.play(3, 3)
        t.play(15, 15)
        rec = gl.add_sgf_to_library(sgf_path, sgf_text, t,
                                    rules="chinese", komi=7.5)
        app._analysis_queue = AnalysisQueue(os.path.join(tmp, "queue.json"))
        app.client = FakeClient()
        sc = Scenario(app, "W13")
        sc.step("入队一盘", lambda: app._enqueue_records_for_analysis([rec]))
        sc.step("kick 领取", app._kick_analysis_queue)
        sc.assert_ok("已领任务", app._analysis_queue_current is not None)
        sc.step("进入 drill（前台独占）", app.open_problem_drill)
        sc.assert_ok("drill 激活", app._drill is not None
                     and not app._drill.is_empty)
        sc.step("推进 0.5s（在飞回流+让路判定）", lambda: advance(app, 0.5))
        task = app._analysis_queue.tasks()[0]
        sc.assert_ok("drill 期间队列暂停不损坏",
                     task.get("status") in ("running", "queued")
                     and app._analysis_queue_current is not None,
                     str(task.get("status")))
        sc.assert_ok("drill 未被队列破坏", app._drill is not None)
        sc.step("关闭 drill", app._close_problem_drill)
        sc.step("推进 2s（队列续跑完成）", lambda: advance(app, 2.0))
        statuses = [t2.get("status") for t2 in app._analysis_queue.tasks()]
        sc.assert_ok("drill 退出后任务自动完成", "completed" in statuses,
                     str(statuses))
    finally:
        gl.LIBRARY_DIR, gl.INBOX_DIR, gl.SGF_DIR, gl.PROJECT_DIR, \
            gl.INDEX_PATH, gl.PROFILE_CACHE_PATH = orig
        app.client = real_client
        app._analysis_queue = real_queue
        app._analysis_queue_current = None
        app._analysis_queue_pending = {}


# ===================== W14 坏输入与异常路径 =====================

def scenario_w14_bad_input_paths(app):
    """坏输入必须拒绝而非静默破坏：垃圾 SGF / 缺失项目快照 / 分支 SGF 正常链。

    修复前：垃圾文件导入会把当前棋局静默替换成空盘（选错文件 = 丢失
    复盘会话）。库侧 scan_paths 本有校验，单文件路径此前裸奔。
    """
    import tkinter.messagebox as _mb
    tmp = tempfile.mkdtemp(prefix="sim_w14_")
    orig = (gl.LIBRARY_DIR, gl.INBOX_DIR, gl.SGF_DIR, gl.PROJECT_DIR,
            gl.INDEX_PATH, gl.PROFILE_CACHE_PATH)
    gl.LIBRARY_DIR = tmp
    gl.INBOX_DIR = os.path.join(tmp, "inbox")
    gl.SGF_DIR = os.path.join(tmp, "sgf")
    gl.PROJECT_DIR = os.path.join(tmp, "projects")
    gl.INDEX_PATH = os.path.join(tmp, "index.json")
    gl.PROFILE_CACHE_PATH = os.path.join(tmp, "profile_cache.json")
    real_fd = None
    import tkinter.filedialog as _fd
    real_fd = _fd.askopenfilename
    errs = []
    _mb.showerror = lambda *a, **k: errs.append(a)
    try:
        ah.clean(app)
        ah.seed_fixture(app, "analyzed")
        tree_before = app.tree
        rid_before = app._library_record_id
        sc = Scenario(app, "W14")

        # A) 垃圾文件：拒绝导入，当前棋局原封不动
        bad = os.path.join(tmp, "garbage.sgf")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("<html>下载出错的页面根本不是棋谱</html>")
        errs.clear()
        _fd.askopenfilename = lambda **k: bad
        sc.step("导入垃圾文件", app.do_import_sgf)
        sc.assert_ok("垃圾文件被拒绝（报错弹窗）", len(errs) == 1,
                     str(errs[:1]))
        sc.assert_ok("当前棋局未被替换（树对象不变）",
                     app.tree is tree_before
                     and app.tree.current.depth == 0)
        sc.assert_ok("record_id 未被破坏", app._library_record_id == rid_before)

        # B) 分支 SGF：主链加载 + 变化图不崩
        var = os.path.join(tmp, "vars.sgf")
        with open(var, "w", encoding="utf-8") as f:
            f.write("(;GM[1]FF[4]SZ[19];B[dd](;W[pp];B[dp])(;W[dp]))")
        errs.clear()
        _fd.askopenfilename = lambda **k: var
        sc.step("导入分支 SGF", app.do_import_sgf)
        sc.assert_ok("分支 SGF 主链 3 手", app.tree.current.depth == 3,
                     str(app.tree.current.depth))
        sc.assert_ok("分支导入无报错", not errs, str(errs[:1]))
        sc.step("重算复盘（分支存在）", app._update_review_state)
        sc.assert_ok("分支局面复盘可用", True)

        # C) 项目快照缺失：库记录打不开时棋局不动
        tree_b = app.tree
        sgf_text = "(;GM[1]FF[4]SZ[19];B[ee])"
        p = os.path.join(tmp, "gone.sgf")
        with open(p, "w", encoding="utf-8") as f:
            f.write(sgf_text)
        t = MoveTree(19)
        t.play(4, 4)
        rec = gl.add_sgf_to_library(p, sgf_text, t, rules="chinese", komi=7.5)
        os.remove(rec.get("projectPath"))     # 模拟快照被外部删除
        sc.step("打开快照缺失的记录", app.open_game_library)
        iid = next(i for i, r in app._lib_map.items()
                   if r.get("id") == rec.get("id"))
        errs.clear()
        sc.step("选中打开（快照已删）",
                lambda: (app._lib_tv.selection_set(iid),
                         app._open_selected_library_record()))
        sc.assert_ok("快照缺失报错不崩", len(errs) == 1, str(errs[:1]))
        sc.assert_ok("棋局未被替换", app.tree is tree_b)
    finally:
        gl.LIBRARY_DIR, gl.INBOX_DIR, gl.SGF_DIR, gl.PROJECT_DIR, \
            gl.INDEX_PATH, gl.PROFILE_CACHE_PATH = orig
        _fd.askopenfilename = real_fd
        _mb.showerror = lambda *a, **k: None
        app._library_record_id = None


# ===================== W15 完整训练闭环 =====================

class TrainingClient(FakeClient):
    """W15 桩：候选必须是空点（按查询 moves 推导占用集），AI 才能持续应手。

    FakeClient 回显"最近落子"作为候选——AI 会选到刚被占的点导致应手
    失败、训练卡死。固定空点池 + 黑方每手 -2.5 目与 SgfScanClient 一致。
    """
    POOL = ["Q16", "D4", "R16", "Q4", "D16", "R4", "C3", "Q17", "D17",
            "R17", "C17", "K10", "K9", "J10", "L10", "B2", "S2", "B17",
            "S17", "C5"]

    @staticmethod
    def _fake_resp(query):
        moves = [p for p in (query.get("moves") or [])
                 if isinstance(p, (list, tuple)) and len(p) >= 2]
        occupied = {str(p[1]).upper() for p in moves
                    if p[1] and str(p[1]).lower() != "pass"}
        d = len(moves)
        s = 2.0 - 2.5 * ((d + 1) // 2)
        mis = []
        for mv in TrainingClient.POOL:
            if mv in occupied:
                continue
            sl = s - len(mis) * 0.2
            mis.append({"move": mv, "order": len(mis), "scoreLead": sl,
                        "winrate": 0.5 + sl / 40, "visits": 100,
                        "prior": 0.3, "pv": [mv]})
            if len(mis) >= 3:
                break
        return {"id": query.get("id", "w15"),
                "rootInfo": {"winrate": 0.5 + s / 40, "scoreLead": s,
                             "currentPlayer": "B" if d % 2 == 0 else "W"},
                "moveInfos": mis, "ownership": [0.0] * 361}


def scenario_w15_training_closed_loop(app):
    """完整训练闭环：进入 → 用户落子 → AI 应手（真回流）→ 轮次推进 →
    结束 → 报告窗口（含对比表配色）全程无异常、不变式干净。

    R9 抓出的 P1：报告表 tagconfigure 笔误（ttk.Treeview 无此方法）——
    训练报告窗口自上线起构建即崩断，本场景固化为回归。
    """
    ah.seed_fixture(app, "analyzed")
    app.client = TrainingClient()
    sc = Scenario(app, "W15")
    try:
        sc.step("进入阶段训练", lambda: app._start_stage_training({
            "id": "w15", "startNodeMove": 0, "playerColor": "B",
            "targetMoves": 4, "phase": "opening", "startMove": 1}))
        sc.assert_ok("训练已激活", bool(
            app._training and app._training.get("active")
            and not app._training.get("finished")))
        user_points = [(9, 9), (10, 10), (8, 8), (11, 11),
                       (7, 7), (12, 12), (6, 6), (13, 13)]
        turns = 0
        for _ in range(30):
            tr = app._training
            if not tr or not tr.get("active") or tr.get("finished"):
                break
            if tr.get("awaiting") == "user":
                for (x, y) in user_points:
                    if app.tree.current.board.stone_at(x, y) == 0:
                        app.play(x, y)
                        turns += 1
                        break
            advance(app, 0.6)
        sc.assert_ok("用户完成 4 轮落子", turns == 4, str(turns))
        sc.assert_ok("训练自然结束", bool(
            app._training and app._training.get("finished")))
        sc.assert_ok("训练节点数=目标×2（用户+AI）",
                     len(app._training.get("nodes", [])) == 8,
                     str(len(app._training.get("nodes", []))))
        sc.assert_ok("报告已生成", app._training_report is not None)
        advance(app, 0.5)   # 报告窗口构建泵完
        sc.assert_ok("报告窗口存在且表格就绪",
                     getattr(app, "_training_report_tv", None) is not None)
        if getattr(app, "_training_report_tv", None) is not None:
            sc.assert_ok("报告对比表已填充",
                         len(app._training_report_tv.get_children()) >= 1)
        violations = check_all_unconditional(app)
        sc.assert_ok("闭环后无残留违规", not violations, str(violations))
    finally:
        app.client = None
        if app._training and app._training.get("active") \
                and not app._training.get("finished"):
            app._abandon_training_state()


# ===================== W16 错题复习闭环 =====================

def scenario_w16_mistake_review_closed_loop(app):
    """错题复习完整闭环：错题本 → 进入复习 → 答对出账 → 答错重试回题面。

    第四核心模式的首个端到端（此前仅 W8 状态注入级）。判分链与主动复盘
    同源（assess_candidate → record_graded_attempt）。book 路径与记账
    用模块别名注入临时实现，全程不碰真实 mistake_book.json。
    """
    import app as app_mod
    import mistake_book as mb
    from project_store import load_project, save_project
    import datetime
    tmp = tempfile.mkdtemp(prefix="sim_w16_")
    orig = (gl.LIBRARY_DIR, gl.INBOX_DIR, gl.SGF_DIR, gl.PROJECT_DIR,
            gl.INDEX_PATH, gl.PROFILE_CACHE_PATH)
    gl.LIBRARY_DIR = tmp
    gl.INBOX_DIR = os.path.join(tmp, "inbox")
    gl.SGF_DIR = os.path.join(tmp, "sgf")
    gl.PROJECT_DIR = os.path.join(tmp, "projects")
    gl.INDEX_PATH = os.path.join(tmp, "index.json")
    gl.PROFILE_CACHE_PATH = os.path.join(tmp, "profile_cache.json")
    book_path = os.path.join(tmp, "mistake_book.json")
    graded = []          # 记账桩：捕捉 (item_id, played)
    orig_list = app_mod.list_mistake_items
    orig_record = app_mod.record_graded_attempt_mb
    # 错题本窗口外迁 ui/dialogs.py 后，对话框直接 from mistake_book import
    # list_items——模块别名注入必须双点位（app 别名 + mistake_book 源），
    # 否则真实 mistake_book.json 泄入测试（W16 曾因此读到 7 条真实错题）。
    import mistake_book as _mb_mod
    orig_mb_list = _mb_mod.list_items
    try:
        ah.clean(app)
        # 一盘两手的真实入库棋 + 项目快照注入分析（Q16 首选）
        sgf_text = "(;GM[1]FF[4]SZ[19]KM[7.5];B[pd];W[dp])"
        p = os.path.join(tmp, "g16.sgf")
        with open(p, "w", encoding="utf-8") as f:
            f.write(sgf_text)
        t = MoveTree(19)
        t.play(15, 3)
        t.play(3, 15)
        rec = gl.add_sgf_to_library(p, sgf_text, t, rules="chinese", komi=7.5)
        tree_loaded, data = load_project(rec["projectPath"])
        tree_loaded.root.analysis = {
            "rootInfo": {"scoreLead": 0.0, "winrate": 0.5,
                         "currentPlayer": "B"},
            "moveInfos": [
                {"move": "Q16", "scoreLead": 0.0, "winrate": 0.52,
                 "order": 0, "visits": 100, "prior": 0.3, "pv": ["Q16"]},
                {"move": "D4", "scoreLead": 0.2, "winrate": 0.5,
                 "order": 1, "visits": 80, "prior": 0.2, "pv": ["D4"]},
                {"move": "R16", "scoreLead": 0.3, "winrate": 0.49,
                 "order": 2, "visits": 60, "prior": 0.1, "pv": ["R16"]},
            ],
        }
        save_project(rec["projectPath"], tree_loaded,
                     rules=data.get("rules", "chinese"),
                     komi=data.get("komi", 7.5))
        item = {
            "id": "w16-item", "gameId": rec["id"], "gameName": "w16局",
            "moveNo": 1, "color": "B", "bestMove": "Q16",
            "projectPath": rec["projectPath"], "scoreLoss": 4.8,
            "dueDate": datetime.date.today().isoformat(), "active": True,
        }
        mb.save_book({"version": 1, "items": [item]}, path=book_path)
        # 模块别名注入：列表走临时 book；记账进桩（不写真实文件）
        app_mod.list_mistake_items = (
            lambda *a, **k: orig_list(book_path, *a, **k))
        _mb_mod.list_items = (
            # 忽略调用方位置参数（book_stats 内部以位置传 path=DEFAULT_PATH），
            # 恒定重定向到临时 book——否则双路径 TypeError/真实库泄入
            lambda *a, **k: orig_mb_list(book_path, **k))
        app_mod.record_graded_attempt_mb = (
            lambda iid, played, mis, color, best=None, **k: (
                graded.append((iid, played)),
                {"dueDate": "2099-01-01"})[1])

        sc = Scenario(app, "W16")
        sc.step("打开错题本", app.open_mistake_book)
        sc.assert_ok("错题本列表含 1 条到期错题",
                     len(app._mistake_book_map) == 1,
                     str(len(app._mistake_book_map)))
        sc.step("进入复习（真实入口）", app._start_selected_mistake_review)
        sc.assert_ok("复习态激活", bool(app._mistake_review
                                        and app._mistake_review.get("active")))
        sc.assert_ok("题面回到错题父节点（根）",
                     app.tree.current.depth == 0)
        # 答错（榜外手，引擎未启 → 数据不足 → again 回题面）
        sc.step("答榜外手 K10", lambda: app.play(9, 9))
        sc.assert_ok("答错回题面（again）",
                     app.tree.current.depth == 0
                     and app._mistake_review is not None
                     and app._mistake_review.get("attempts") == 1,
                     str(app._mistake_review and app._mistake_review.get("attempts")))
        # 答对（首选 Q16）→ 复习出账
        sc.step("答首选 Q16", lambda: app.play(15, 3))
        sc.assert_ok("答对后复习结束（active 清空）",
                     app._mistake_review is None)
        sc.assert_ok("两次作答均已记账",
                     len(graded) == 2 and graded[-1] == ("w16-item", "Q16"),
                     str(graded))
        violations = check_all_unconditional(app)
        sc.assert_ok("闭环后无残留违规", not violations, str(violations))
    finally:
        app_mod.list_mistake_items = orig_list
        _mb_mod.list_items = orig_mb_list
        app_mod.record_graded_attempt_mb = orig_record
        gl.LIBRARY_DIR, gl.INBOX_DIR, gl.SGF_DIR, gl.PROJECT_DIR, \
            gl.INDEX_PATH, gl.PROFILE_CACHE_PATH = orig
        app._library_record_id = None


# ===================== W17 队列跨实例持久化 =====================

def scenario_w17_queue_persistence(app):
    """批量队列跨实例：入队 → 关应用 → 新实例读同一队列文件 → 续跑完成。

    真实用户旅程：今晚批量分析跑一半关机，明早开应用队列还在、能续跑。
    harness 单例的 destroy+recreate 即"重启应用"。
    """
    tmp = tempfile.mkdtemp(prefix="sim_w17_")
    orig = (gl.LIBRARY_DIR, gl.INBOX_DIR, gl.SGF_DIR, gl.PROJECT_DIR,
            gl.INDEX_PATH, gl.PROFILE_CACHE_PATH)
    gl.LIBRARY_DIR = tmp
    gl.INBOX_DIR = os.path.join(tmp, "inbox")
    gl.SGF_DIR = os.path.join(tmp, "sgf")
    gl.PROJECT_DIR = os.path.join(tmp, "projects")
    gl.INDEX_PATH = os.path.join(tmp, "index.json")
    gl.PROFILE_CACHE_PATH = os.path.join(tmp, "profile_cache.json")
    qpath = os.path.join(tmp, "queue.json")
    real_client = app.client
    real_queue = app._analysis_queue
    try:
        ah.clean(app)
        sgf_text = "(;GM[1]FF[4]SZ[19];B[cc];W[pp];B[dd])"
        p = os.path.join(tmp, "g17.sgf")
        with open(p, "w", encoding="utf-8") as f:
            f.write(sgf_text)
        t = MoveTree(19)
        t.play(2, 2)
        t.play(15, 15)
        t.play(3, 3)
        rec = gl.add_sgf_to_library(p, sgf_text, t, rules="chinese", komi=7.5)
        sc = Scenario(app, "W17")
        q1 = AnalysisQueue(qpath)
        app._analysis_queue = q1
        app._enqueue_records_for_analysis([rec])
        statuses = [task.get("status") for task in q1.tasks()]
        sc.assert_ok("实例1：任务已入队", "queued" in statuses, str(statuses))
        # 模拟关应用：不跑任务直接换实例（队列状态留在文件里）
        app._analysis_queue = real_queue
        app._analysis_queue_current = None
        new_app = ah.make_headless_app()     # 销毁旧实例建新实例
        new_app.client = FakeClient()
        q2 = AnalysisQueue(qpath)
        new_app._analysis_queue = q2
        try:
            statuses2 = [task.get("status") for task in q2.tasks()]
            sc.assert_ok("实例2：队列从磁盘恢复", "queued" in statuses2,
                         str(statuses2))
            new_app._kick_analysis_queue()
            sc.assert_ok("实例2：可领取恢复的任务",
                         new_app._analysis_queue_current is not None)
            advance(new_app, 2.5)
            final = [task.get("status") for task in q2.tasks()]
            sc.assert_ok("实例2：续跑到完成", "completed" in final, str(final))
        finally:
            new_app._analysis_queue = real_queue
            new_app._analysis_queue_current = None
            new_app._analysis_queue_pending = {}
            new_app.client = None
    finally:
        gl.LIBRARY_DIR, gl.INBOX_DIR, gl.SGF_DIR, gl.PROJECT_DIR, \
            gl.INDEX_PATH, gl.PROFILE_CACHE_PATH = orig
        app.client = real_client


# ===================== W18 前台整盘×曲线实时刷新 =====================

def scenario_w18_graph_live_batch(app):
    """前台整盘分析×曲线窗口实时刷新：结果逐个回流、曲线单调增长。

    与 smoke 的 _section_batch（直接注入 _apply_analysis_result）互补：
    这里走 analyze_mainline → 真实 poll 分发 → 曲线/问题榜实时长出。
    （后台队列结果写在记录快照树上、不动前台曲线——设计语义，另见 W6/W17。）
    """
    ah.seed_fixture(app, "simple")     # 2 手无分析
    app.client = SgfScanClient()       # 黑每手 -2.5 目 → 第 1 手成问题
    sc = Scenario(app, "W18")
    try:
        sc.step("打开曲线窗口", app.toggle_graph)
        sc.assert_ok("曲线窗口存在", app._graph_win is not None
                     and app._graph_win.winfo_exists())
        pts0 = len(app._graph_pts or [])
        sc.step("发起整盘分析", app.analyze_mainline)
        sc.assert_ok("批量计数=3（root+2 手）", app._batch_total == 3,
                     str(app._batch_total))
        grown = pts0
        for _ in range(12):
            advance(app, 0.5)
            now = len(app._graph_pts or [])
            sc.assert_ok("曲线点单调不减", now >= grown,
                         "%d -> %d" % (grown, now))
            grown = now
            if app._batch_total == 0 and app._batch_done == 0:
                break   # 批量完成自动清零
        sc.assert_ok("曲线点已长出（≥3）", grown - pts0 >= 3,
                     "%d -> %d" % (pts0, grown))
        sc.assert_ok("批量完成计数清零", app._batch_total == 0)
        sc.assert_ok("问题榜实时长出（黑第1手亏2.5）",
                     len(app._review_map) >= 1, str(len(app._review_map)))
        sc.assert_ok("当前节点候选已渲染",
                     bool(getattr(app, "_candidate_actions", None)))
        sc.step("关闭曲线窗口", app.toggle_graph)
        sc.assert_ok("曲线窗口已关", app._graph_win is None)
        violations = check_all_unconditional(app)
        sc.assert_ok("场景后无残留违规", not violations, str(violations))
    finally:
        app.client = None


# ===================== W19 引擎死亡×队列续跑 =====================

class DyingManualClient(ManualClient):
    """W19 桩：受控回流（结果手动投放）+ die 置 True 后报告进程死亡。

    FakeClient 即时回流会在死亡前把队列跑完；受控回流可把进度精确卡住，
    死亡中断时任务必然仍在途。"""

    def __init__(self):
        ManualClient.__init__(self)
        self.die = False

    def is_alive(self):
        return not self.die

    def recent_stderr(self, n=8):
        return ["W19 模拟引擎崩溃"]


def scenario_w19_engine_death_queue_resume(app):
    """引擎死亡×队列复合链：分析中引擎死 → 中断保进度 → 重启续跑到完成。

    覆盖 R5 统一销账（_reset_engine_request_state）在真实死亡路径的落点：
    挂账清空、任务释放回 queued 不丢失、重启后重新领取续跑完成。
    """
    tmp = tempfile.mkdtemp(prefix="sim_w19_")
    orig = (gl.LIBRARY_DIR, gl.INBOX_DIR, gl.SGF_DIR, gl.PROJECT_DIR,
            gl.INDEX_PATH, gl.PROFILE_CACHE_PATH)
    gl.LIBRARY_DIR = tmp
    gl.INBOX_DIR = os.path.join(tmp, "inbox")
    gl.SGF_DIR = os.path.join(tmp, "sgf")
    gl.PROJECT_DIR = os.path.join(tmp, "projects")
    gl.INDEX_PATH = os.path.join(tmp, "index.json")
    gl.PROFILE_CACHE_PATH = os.path.join(tmp, "profile_cache.json")
    real_client = app.client
    real_queue = app._analysis_queue
    try:
        ah.clean(app)
        sgf_text = "(;GM[1]FF[4]SZ[19];B[dd];W[pp];B[dp];W[dd])"
        p = os.path.join(tmp, "g19.sgf")
        with open(p, "w", encoding="utf-8") as f:
            f.write(sgf_text)
        t = MoveTree(19)
        for (x, y) in [(3, 3), (15, 15), (3, 15), (3, 3)]:
            t.play(x, y)
        rec = gl.add_sgf_to_library(p, sgf_text, t, rules="chinese", komi=7.5)
        app._analysis_queue = AnalysisQueue(os.path.join(tmp, "queue.json"))
        dying = DyingManualClient()
        app.client = dying
        sc = Scenario(app, "W19")
        sc.step("入队并领取", lambda: (
            app._enqueue_records_for_analysis([rec]),
            app._kick_analysis_queue()))
        sc.assert_ok("任务已领取", app._analysis_queue_current is not None)
        sc.step("推进 0.3s（首个请求在途）", lambda: advance(app, 0.3))
        sc.assert_ok("队列请求已发出未回流",
                     bool(app._analysis_queue_pending),
                     str(len(app._analysis_queue_pending)))
        qrid = next(iter(app._analysis_queue_pending))
        sc.step("回流首个结果（done=1）",
                lambda: (dying.deliver(qrid), advance(app, 0.4)))
        done_before = (app._analysis_queue_current or {}).get("done", 0)
        sc.assert_ok("进度已记录（done=1）", done_before == 1, str(done_before))
        # 引擎死亡 → poll 检测 → 中断释放
        dying.die = True
        sc.step("推进 0.5s（检测死亡+中断）", lambda: advance(app, 0.5))
        sc.assert_ok("死亡后 client 置空", app.client is None)
        sc.assert_ok("挂账清空（R5 统一销账）",
                     not app._analysis_queue_pending
                     and not app._problem_compare_pending)
        sc.assert_ok("当前任务上下文已清", app._analysis_queue_current is None)
        task = app._analysis_queue.tasks()[0]
        sc.assert_ok("任务释放回 queued（不丢失）",
                     task.get("status") == "queued",
                     "%s / %s" % (task.get("status"), task.get("message")))
        # 重启引擎 → 续跑（从保存的进度快照继续）
        app.client = FakeClient()
        sc.step("重启后 kick 续跑", app._kick_analysis_queue)
        sc.assert_ok("重新领取任务", app._analysis_queue_current is not None)
        sc.step("推进 3s（续跑完成）", lambda: advance(app, 3.0))
        final = [t2.get("status") for t2 in app._analysis_queue.tasks()]
        sc.assert_ok("续跑到完成", "completed" in final, str(final))
        violations = check_all_unconditional(app)
        sc.assert_ok("复合链后无残留违规", not violations, str(violations))
    finally:
        gl.LIBRARY_DIR, gl.INBOX_DIR, gl.SGF_DIR, gl.PROJECT_DIR, \
            gl.INDEX_PATH, gl.PROFILE_CACHE_PATH = orig
        app.client = real_client
        app._analysis_queue = real_queue
        app._analysis_queue_current = None
        app._analysis_queue_pending = {}


# ===================== W20 曲线窗口×导航双向联动 =====================

class _ClickEvent:
    """曲线点击的合成事件（Tk Event 替身，只带 x）。"""

    def __init__(self, x):
        self.x = x


def scenario_w20_graph_nav_linkage(app):
    """曲线×导航双向联动端到端：点曲线跳棋盘 / 棋盘导航曲线光标跟随 / 点目拦截。

    光标竖线是 create_line(fill="#fb0")，x 坐标 = pad_l + (move/max_move)*plot_w
    （W=540, pad_l=34, pad_r=12 → plot_w=494）。analyzed 棋局 mainline=3 节点，
    max_move=2：第 0/1/2 手的期望 x ≈ 34 / 281 / 528。
    """
    ah.seed_fixture(app, "analyzed")
    sc = Scenario(app, "W20")

    def cursor_x():
        c = app._graph_canvas
        for item in c.find_all():
            try:
                if c.itemcget(item, "fill") == "#fb0":
                    return c.coords(item)[0]
            except Exception:
                continue
        return None

    try:
        sc.step("打开曲线窗口", app.toggle_graph)
        sc.assert_ok("窗口+曲线点就绪（根+2手）",
                     app._graph_win is not None
                     and app._graph_win.winfo_exists()
                     and len(app._graph_pts or []) >= 3,
                     str(len(app._graph_pts or [])))
        sc.assert_ok("光标竖线在根（x≈34）",
                     abs((cursor_x() or -1) - 34) < 3, str(cursor_x()))
        # 曲线 → 棋盘：点第 2 手曲线点，棋盘跳转且光标跟随
        pt2 = next(p for p in app._graph_pts if p[0] == 2)
        sc.step("点击曲线第2手", lambda: app._on_graph_click(_ClickEvent(pt2[1])))
        sc.assert_ok("棋盘跳到第2手", app.tree.current.depth == 2,
                     str(app.tree.current.depth))
        sc.assert_ok("光标竖线跟随到第2手（x≈528）",
                     abs((cursor_x() or -1) - 528) < 3, str(cursor_x()))
        # 点当前手：无操作不报错
        sc.step("点击当前手（无操作）",
                lambda: app._on_graph_click(_ClickEvent(pt2[1])))
        sc.assert_ok("仍在第2手", app.tree.current.depth == 2,
                     str(app.tree.current.depth))
        # 棋盘 → 曲线：回根导航，光标竖线反向跟随
        sc.step("棋盘回到根", app.do_goto_root)
        sc.assert_ok("光标竖线回根（x≈34）",
                     abs((cursor_x() or -1) - 34) < 3, str(cursor_x()))
        # 点目前台独占：曲线跳转被拦截（节点不动）
        sc.step("进入点目", app.enter_scoring)
        sc.assert_ok("点目生效", app.scoring_mode is True)
        sc.step("点目中点击曲线",
                lambda: app._on_graph_click(_ClickEvent(pt2[1])))
        sc.assert_ok("点目拦截跳转（节点停在根）",
                     app.tree.current.depth == 0,
                     str(app.tree.current.depth))
        sc.step("退出点目", app.exit_scoring)
        sc.step("关闭曲线窗口", app.toggle_graph)
        sc.assert_ok("曲线窗口已关", app._graph_win is None)
        violations = check_all_unconditional(app)
        sc.assert_ok("场景后无残留违规", not violations, str(violations))
    finally:
        if app.scoring_mode:
            app.exit_scoring()
        if app._graph_win is not None:
            app.toggle_graph()


# ===================== W21 库记录自动快扫（crash 回归） =====================

def scenario_w21_library_auto_quick_scan(app):
    """库记录打开×自动快扫回归：crash.log 2026-08-20 21:09 三连崩路径。

    旧代码 quick_visits 把 TRAINING_SPEED_MODES["fast"] 整个元组传进
    _analysis_query_from_moves，int(visits) 抛 TypeError——用户每次从
    棋谱库打开记录都崩。沿 _maybe_auto_analyze_library_game →
    quick_scan_mainline 真实入口触发，断言每个查询的 maxVisits 都是
    fast 档的 int（防元组回归），且回流分发后批量计数正确清零。
    """
    from app import TRAINING_SPEED_MODES

    class RecordingClient(FakeClient):
        """记录发出的查询（继承即时回流），供 maxVisits 断言。"""

        def __init__(self):
            FakeClient.__init__(self)
            self.queries = []

        def analyze(self, query):
            self.queries.append(dict(query))
            return FakeClient.analyze(self, query)

    ah.seed_fixture(app, "simple")
    sc = Scenario(app, "W21")
    fake = RecordingClient()
    app.client = fake
    try:
        sc.step("挂库记录上下文",
                lambda: setattr(app, "_library_record_id", "w21-rec"))
        sc.step("自动快扫（崩溃现场入口）",
                lambda: app._maybe_auto_analyze_library_game(1))
        fast_visits = TRAINING_SPEED_MODES["fast"][1]
        sc.assert_ok("预扫请求已发出（root+2手=3）", len(fake.queries) == 3,
                     str(len(fake.queries)))
        bad = [q.get("maxVisits") for q in fake.queries
               if q.get("maxVisits") != fast_visits]
        sc.assert_ok("maxVisits=fast 档 int（防元组回归）", not bad,
                     "fast=%r 异常=%s" % (fast_visits, bad[:2]))
        for _ in range(10):
            advance(app, 0.4)
            if app._batch_total == 0:
                break
        sc.assert_ok("批量完成计数清零", app._batch_total == 0,
                     "%d/%d" % (app._batch_done, app._batch_total))
        sc.assert_ok("回流写入分析缓存", app.tree.root.analysis is not None)
        violations = check_all_unconditional(app)
        sc.assert_ok("场景后无残留违规", not violations, str(violations))
    finally:
        app.client = None
        app._library_record_id = None


# ===================== W22 训练报告窗口渲染（crash 回归） =====================

def scenario_w22_training_report_window(app):
    """训练报告窗口渲染回归：crash.log 2026-08-20 22:28 崩溃路径。

    旧代码 Treeview 染色写成 tv.tagconfigure（正确名 tag_configure），
    训练结束弹报告当场 AttributeError——训练白做完还看不到结果。
    用覆盖 improved/repeated_error/new_error 三类行的合成报告直接渲染。
    """
    ah.seed_fixture(app, "simple")
    sc = Scenario(app, "W22")
    report = {"trainingAnalysis": {
        "training_score": 72, "training_label": "明显改善",
        "original_avg_score_loss": 3.1, "training_avg_score_loss": 1.2,
        "improvement_score_loss": 1.9,
        "original_blunder_count": 2, "training_blunder_count": 0,
        "original_inaccuracy_count": 3, "training_inaccuracy_count": 1,
        "suggested_review_after_days": 3,
        "problem_tag_changes": {"direction": (2, 0, -2)},
        "recommended_review_positions": [{"move_no": 1}],
        "comparisons": [
            {"move_no": 1, "color": "B", "played_move": "Q16",
             "original_quality": "bad", "training_quality": "good",
             "training_score_loss": 0.3, "score_loss_improvement": 4.2,
             "category": "improved"},
            {"move_no": 2, "color": "W", "played_move": "D4",
             "original_quality": "bad", "training_quality": "bad",
             "training_score_loss": 5.1, "score_loss_improvement": -0.2,
             "category": "repeated_error"},
            {"move_no": 3, "color": "B", "played_move": "R16",
             "original_quality": "normal", "training_quality": "bad",
             "training_score_loss": 3.0, "score_loss_improvement": None,
             "category": "new_error"},
        ]}}
    try:
        sc.step("渲染训练报告（崩溃现场）",
                lambda: app._show_training_report(report))
        tv = getattr(app, "_training_report_tv", None)
        sc.assert_ok("报告表格就绪", tv is not None and tv.winfo_exists())
        sc.assert_ok("三类对比行全部渲染", len(tv.get_children()) == 3,
                     str(len(tv.get_children())))
        sc.assert_ok("行染色标签已注册",
                     all(tv.tag_has(t) for t in
                         ("improved", "repeated_error", "new_error")))
        violations = check_all_unconditional(app)
        sc.assert_ok("场景后无残留违规", not violations, str(violations))
    finally:
        tv = getattr(app, "_training_report_tv", None)
        if tv is not None:
            try:
                tv.winfo_toplevel().destroy()
            except Exception:
                pass
            app._training_report_tv = None


# ===================== W23 在线导入全链（URL/OGS/错误/中断） =====================

def _find_widgets(win, pred):
    """递归收集满足 pred 的子控件（对话框内部控件无引用，按结构定位）。"""
    out, stack = [], list(win.winfo_children())
    while stack:
        w = stack.pop()
        try:
            if pred(w):
                out.append(w)
            stack.extend(w.winfo_children())
        except Exception:
            continue
    return out


def _wtext(w):
    try:
        return str(w.cget("text"))
    except Exception:
        return ""


def scenario_w23_online_import_chain(app):
    """在线导入全链：URL 直链 / 重复导入 / 下载失败 / OGS 批量 / 下载中关窗。

    ui.dialogs.open_online_import 的真实 UI 路径：worker 线程下载（mock 到
    online_import 模块函数）→ events 队列 → after(100) 轮询 → UI 线程入库
    + 自动入队批量分析。判 bug 标准：消息不撒谎（新增/重复/失败计数与库
    一致）、失败不吞不挂、关窗后后台线程回流不崩。
    """
    import online_import as oi
    import tkinter.messagebox as _mb
    tmp = tempfile.mkdtemp(prefix="sim_w23_")
    orig = (gl.LIBRARY_DIR, gl.INBOX_DIR, gl.SGF_DIR, gl.PROJECT_DIR,
            gl.INDEX_PATH, gl.PROFILE_CACHE_PATH)
    gl.LIBRARY_DIR = tmp
    gl.INBOX_DIR = os.path.join(tmp, "inbox")
    gl.SGF_DIR = os.path.join(tmp, "sgf")
    gl.PROJECT_DIR = os.path.join(tmp, "projects")
    gl.INDEX_PATH = os.path.join(tmp, "index.json")
    gl.PROFILE_CACHE_PATH = os.path.join(tmp, "profile_cache.json")
    real_mct = app._make_centered_toplevel
    real_queue = app._analysis_queue
    real_warn = _mb.showwarning
    orig_dl_url = oi.download_from_url
    orig_list = oi.ogs_list_games
    orig_dl_ogs = oi.download_ogs_games
    warns = []
    opened = []

    def cap_mct(*a, **k):
        w = real_mct(*a, **k)
        opened.append(w)
        return w

    _mb.showwarning = lambda *a, **k: warns.append(a)
    msg_log = []
    real_set_msg = app._set_msg

    def log_set_msg(text, kind=None):
        msg_log.append(str(text))
        return real_set_msg(text, kind)
    app._set_msg = log_set_msg
    # ⚠ ui/dialogs 在 open_online_import 时 from online_import import ...
    # 把函数绑进闭包——桩必须在开窗前装好，中途 patch 模块属性无效
    # （首版在此踩坑：C 段换失败桩不生效，还触发了真实 OGS 网络请求）。
    # URL 桩用可变 mode 切换 ok/err/slow 分支。
    url_mode = {"kind": "ok"}
    SGF_A = ("(;GM[1]FF[4]SZ[19]KM[7.5]PB[网黑]PW[网白]"
             ";B[pd];W[dp];B[pp])")
    SGF_B = "(;GM[1]FF[4]SZ[19];B[dd];W[pp])"

    def fake_dl_url(url):
        import time as _t
        kind = url_mode["kind"]
        if kind == "err":
            raise oi.OnlineImportError("链接无效（W23 模拟）")
        if kind == "slow":
            _t.sleep(0.5)
            return (SGF_A, "迟到.sgf")
        return (SGF_A, "在线对局.sgf")

    def fake_ogs_list(name, limit=30):
        return ({"username": name, "rank": "1k"},
                [{"id": 1, "ended": "2026-08-01", "black": "甲", "white": "乙",
                  "result": "B+2.5", "size": 19},
                 {"id": 2, "ended": "2026-08-02", "black": "丙", "white": "丁",
                  "result": "W+R", "size": 19}])

    def fake_dl_ogs(chosen, progress=None):
        items = [{"name": "ogs-1.sgf", "text": SGF_B},
                 {"name": "ogs-2.sgf", "text": "<html>坏棋谱</html>"}]
        return {"items": items, "failed": []}
    oi.download_from_url = fake_dl_url
    oi.ogs_list_games = fake_ogs_list
    oi.download_ogs_games = fake_dl_ogs
    app.client = FakeClient()   # 队列用桩引擎跑，不得真启 KataGo
    try:
        ah.clean(app)
        app._analysis_queue = AnalysisQueue(os.path.join(tmp, "queue.json"))
        app._make_centered_toplevel = cap_mct
        sc = Scenario(app, "W23")

        def click(label):
            btns = _find_widgets(opened[-1],
                                 lambda w: _wtext(w) == label)
            if not btns:
                raise AssertionError("找不到按钮 %s" % label)
            btns[0].invoke()

        def msg():
            return str(app.lbl_msg.cget("text"))

        # A) URL 直链成功 → 入库 + 自动入队 + 消息不撒谎
        sc.step("打开在线导入对话框", app.open_online_import)
        win = opened[-1]
        sc.assert_ok("对话框已建立", win.winfo_exists())
        entries = _find_widgets(win, lambda w: w.winfo_class() in
                                ("TEntry", "Entry"))
        sc.assert_ok("两个输入框就绪（URL/OGS）", len(entries) >= 2,
                     str(len(entries)))
        sc.step("填入 URL", lambda: [e.insert(0, "https://x/g.sgf")
                                     for e in entries[:2]])
        sc.step("点击下载并入库", lambda: click("下载并入库"))
        sc.step("推进 1s（worker+轮询回流）", lambda: advance(app, 1.0))
        sc.assert_ok("成功消息计数正确（新增1）",
                     any("新增 1" in m and "重复 0" in m for m in msg_log),
                     msg_log[-1])
        recs = gl.list_records()
        sc.assert_ok("库中已有 1 条记录", len(recs) == 1, str(len(recs)))
        sc.assert_ok("来源标记 online-url",
                     recs[0].get("sourceKind") == "online-url",
                     str(recs[0].get("sourceKind")))
        sc.assert_ok("已自动入队批量分析",
                     len(app._analysis_queue.tasks()) >= 1,
                     str([t.get("status")
                          for t in app._analysis_queue.tasks()]))

        # B) 同一 URL 重复导入 → 计为重复，库不膨胀
        sc.step("再次点击下载（重复）", lambda: click("下载并入库"))
        sc.step("推进 1s", lambda: advance(app, 1.0))
        sc.assert_ok("重复消息计数正确（重复1）",
                     any("新增 0" in m and "重复 1" in m for m in msg_log),
                     msg_log[-1])
        sc.assert_ok("库未膨胀（去重生效）", len(gl.list_records()) == 1,
                     str(len(gl.list_records())))

        # C) 下载失败（OnlineImportError）→ 不入库、按钮复位、不挂死
        n_before = len(gl.list_records())
        url_mode["kind"] = "err"
        sc.step("点击下载（失败路径）", lambda: click("下载并入库"))
        sc.step("推进 1s（错误回流）", lambda: advance(app, 1.0))
        sc.assert_ok("失败后库不变", len(gl.list_records()) == n_before)
        busy = _find_widgets(opened[-1],
                             lambda w: _wtext(w) == "下载并入库")
        sc.assert_ok("失败后按钮已复位（可再试）",
                     busy and str(busy[0].cget("state")) == "normal",
                     busy and str(busy[0].cget("state")))

        # D) OGS 用户对局：查询 → 全选 → 批量下载（含 1 条坏棋谱）
        url_mode["kind"] = "ok"
        sc.step("清空输入重填 OGS 用户名",
                lambda: [ (e.delete(0, "end"), e.insert(0, "w23user"))
                          for e in entries[:2] ])
        sc.step("点击查询对局", lambda: click("查询对局"))
        sc.step("推进 1s（列表回流）", lambda: advance(app, 1.0))
        sc.step("全选对局", lambda: click("全选"))
        sc.step("点击下载所选", lambda: click("下载所选"))
        sc.step("推进 1s（批量回流）", lambda: advance(app, 1.0))
        # 消息是单行滚动条：导入摘要会被随后的队列进度提示覆盖——
        # 经 _set_msg 记录历史断言"某一时刻如实报告过"（消息不撒谎）
        sc.assert_ok("批量消息计数正确（新增1/失败1）",
                     any("新增 1" in m and "失败 1" in m for m in msg_log),
                     msg_log[-1])
        sc.assert_ok("坏棋谱触发警告弹窗", len(warns) == 1, str(len(warns)))
        sc.assert_ok("库共 2 条（URL 一条 + OGS 一条）",
                     len(gl.list_records()) == 2, str(len(gl.list_records())))

        # E) 下载中关窗：worker 仍在跑，事件回流不得崩
        url_mode["kind"] = "slow"
        sc.step("发起慢下载", lambda: click("下载并入库"))
        sc.step("下载中关闭对话框", lambda: opened[-1].destroy())
        sc.step("推进 1s（迟到事件）", lambda: advance(app, 1.0))
        sc.assert_ok("关窗后迟到回流不崩", app.winfo_exists())
    finally:
        for w in opened:
            try:
                if w.winfo_exists():
                    w.destroy()
            except Exception:
                pass
        oi.download_from_url = orig_dl_url
        oi.ogs_list_games = orig_list
        oi.download_ogs_games = orig_dl_ogs
        app._set_msg = real_set_msg
        _mb.showwarning = real_warn
        app._make_centered_toplevel = real_mct
        gl.LIBRARY_DIR, gl.INBOX_DIR, gl.SGF_DIR, gl.PROJECT_DIR, \
            gl.INDEX_PATH, gl.PROFILE_CACHE_PATH = orig
        app._analysis_queue = real_queue
        app._analysis_queue_current = None
        app._analysis_queue_pending = {}


# ===================== W24 V6 页面路由×前台模式互斥 =====================

def scenario_w24_v6_page_router_modes(app):
    """V6 Shell 页面路由×前台模式：点目/drill 激活时切页不清模式、棋局不动。

    真实用户路径：复盘一半切到首页/棋谱/复习/学习页看数据再切回来——
    前台模式与棋局是工作区状态，页面切换只是视图切换，不得顺手清掉。
    """
    shell = getattr(app, "shell", None)
    if shell is None:
        print("  [W24] 非 V6 布局，跳过")
        return
    ah.seed_fixture(app, "analyzed")
    sc = Scenario(app, "W24")
    sc.assert_ok("初始在复盘工作区", app.router.current == "review",
                 str(app.router.current))
    tree0 = app.tree
    root_analysis = tree0.current.analysis
    sc.step("进入点目（前台模式）", app.enter_scoring)
    sc.assert_ok("点目生效", app.scoring_mode is True)
    # 依次访问全部一级页面（含导航按钮真实点击一次）
    for page in ("home", "library", "practice", "learning"):
        sc.step("切到 %s 页" % page, lambda p=page: app.router.go(p))
        sc.assert_ok("路由已跟踪 %s" % page, app.router.current == page,
                     str(app.router.current))
    sc.assert_ok("页面切换不退出点目", app.scoring_mode is True)
    sc.assert_ok("页面切换不动棋局（树不变）", app.tree is tree0)
    # 棋谱页首次进入构建列表（复用库逻辑）
    sc.step("切到棋谱页（构建列表）", lambda: app.router.go("library"))
    sc.assert_ok("棋谱页列表已构建", app._lib_tv is not None
                 and app._lib_map is not None)
    # 导航按钮真实点击路径（Label Button-1 绑定）
    nav_btn = shell._nav_buttons["home"][0]
    sc.step("点击导航按钮回首页",
            lambda: (nav_btn.event_generate("<Button-1>"),
                     app.update_idletasks()))
    sc.assert_ok("按钮点击切到首页", app.router.current == "home",
                 str(app.router.current))
    # 回复盘页：点目仍在、可正常退出、无 scoring 残留
    sc.step("回复盘工作区", lambda: app.router.go("review"))
    sc.assert_ok("回复盘后点目仍在", app.scoring_mode is True)
    sc.step("退出点目", app.exit_scoring)
    counts = ah.canvas_marker_counts(app)
    sc.assert_ok("退出点目无 scoring 残留",
                 not counts.get("scoring-marker"), str(counts))
    sc.assert_ok("根分析未被页面切换破坏（身份不变）",
                 app.tree.current.analysis is root_analysis)
    # drill × 页面切换：训练窗口浮于页面上，关闭仍正常
    sc.step("开问题手训练", app.open_problem_drill)
    if app._drill is not None:
        sc.step("drill 激活时切首页", lambda: app.router.go("home"))
        sc.assert_ok("drill 存活", app._drill is not None)
        sc.step("回复盘页", lambda: app.router.go("review"))
        sc.step("关闭 drill", app._close_problem_drill)
        sc.assert_ok("drill 引用清", app._drill_win is None
                     and app._drill is None)
    violations = check_all_unconditional(app)
    sc.assert_ok("场景后无残留违规", not violations, str(violations))


# ===================== W25 批量队列×前台导航交错 =====================

def scenario_w25_queue_foreground_nav_interleave(app):
    """批量队列真跑×前台密集操作：导航/toggle/开窗交错不丢任务不扰前台。

    与 W6（点目让路）/W13（drill 让路）互补：这里前台是普通复盘操作
    （不独占），队列应持续跑完，前台树/分析缓存不被队列写入扰动。
    """
    tmp = tempfile.mkdtemp(prefix="sim_w25_")
    orig = (gl.LIBRARY_DIR, gl.INBOX_DIR, gl.SGF_DIR, gl.PROJECT_DIR,
            gl.INDEX_PATH, gl.PROFILE_CACHE_PATH)
    gl.LIBRARY_DIR = tmp
    gl.INBOX_DIR = os.path.join(tmp, "inbox")
    gl.SGF_DIR = os.path.join(tmp, "sgf")
    gl.PROJECT_DIR = os.path.join(tmp, "projects")
    gl.INDEX_PATH = os.path.join(tmp, "index.json")
    gl.PROFILE_CACHE_PATH = os.path.join(tmp, "profile_cache.json")
    real_client = app.client
    real_queue = app._analysis_queue
    try:
        ah.seed_fixture(app, "analyzed")
        tree0 = app.tree
        node0 = tree0.current
        root_analysis = node0.analysis
        sgf_text = "(;GM[1]FF[4]SZ[19];B[dd];W[pp];B[dp])"
        sgf_path = os.path.join(tmp, "g25.sgf")
        with open(sgf_path, "w", encoding="utf-8") as f:
            f.write(sgf_text)
        t = MoveTree(19)
        for (x, y) in [(3, 3), (15, 15), (3, 15)]:
            t.play(x, y)
        rec = gl.add_sgf_to_library(sgf_path, sgf_text, t,
                                    rules="chinese", komi=7.5)
        app._analysis_queue = AnalysisQueue(os.path.join(tmp, "queue.json"))
        app.client = FakeClient()
        sc = Scenario(app, "W25")
        sc.step("入队一盘", lambda: app._enqueue_records_for_analysis([rec]))
        sc.step("打开队列窗口", app.open_analysis_queue)
        sc.assert_ok("队列窗口存在", app._analysis_queue_win is not None)
        sc.step("kick 领取", app._kick_analysis_queue)
        sc.assert_ok("已领任务", app._analysis_queue_current is not None)
        sc.step("打开曲线窗口", app.toggle_graph)
        # 前台密集操作与队列推进交错（每片 0.4s 泵真实 poll 分发）
        fg_ops = [
            ("跳主线末", app.do_goto_mainline_end),
            ("回根", app.do_goto_root),
            ("候选点开关×2", lambda: [app.toggle_candidates()
                                      for _ in range(2)]),
            ("前进一手", app.do_redo),
            ("形势判断开关", app.toggle_situation),
        ]
        waited = 0.0
        done = False
        for label, op in fg_ops:
            sc.step("前台：%s" % label, op)
            advance(app, 0.4)
            waited += 0.4
            if all(t2.get("status") == "completed"
                   for t2 in app._analysis_queue.tasks()):
                done = True
                break
        while not done and waited < 8.0:
            advance(app, 0.5)
            waited += 0.5
            if all(t2.get("status") == "completed"
                   for t2 in app._analysis_queue.tasks()):
                done = True
        sc.assert_ok("队列在前台操作下跑完（%.1fs）" % waited, done,
                     str([t2.get("status")
                          for t2 in app._analysis_queue.tasks()]))
        sc.assert_ok("前台树未被队列替换", app.tree is tree0)
        sc.assert_ok("前台根分析未被队列覆盖（身份不变）",
                     node0.analysis is root_analysis)
        sc.step("关闭队列窗口", app._close_analysis_queue_window)
        sc.assert_ok("队列窗口引用清", app._analysis_queue_win is None)
        sc.step("关闭曲线窗口", app._close_graph)
        sc.assert_ok("曲线窗口引用清", app._graph_win is None)
        violations = check_all_unconditional(app)
        sc.assert_ok("交错后无残留违规", not violations, str(violations))
        post = check_post_game_switch(app)
        sc.assert_ok("post 换谱不变式干净", not post, str(post))
    finally:
        gl.LIBRARY_DIR, gl.INBOX_DIR, gl.SGF_DIR, gl.PROJECT_DIR, \
            gl.INDEX_PATH, gl.PROFILE_CACHE_PATH = orig
        app.client = real_client
        app._analysis_queue = real_queue
        app._analysis_queue_current = None
        app._analysis_queue_pending = {}


# ===================== W26 复习中启动训练/drill 被拦 =====================

def _find_button_by_text(root, text):
    """按文案在控件树中找可 invoke 的按钮（真实按钮路径调用用）。

    返回首个命中控件或 None——找不到时场景走注入法兜底，不算失败前提。
    """
    stack = [root]
    while stack:
        w = stack.pop()
        try:
            txt = w.cget("text")
            if isinstance(txt, str) and txt == text and hasattr(w, "invoke"):
                return w
        except Exception:
            pass
        try:
            stack.extend(w.winfo_children())
        except Exception:
            pass
    return None


def scenario_w26_review_blocks_training_entry(app):
    """错题复习进行中尝试启动阶段训练/问题手 drill：必须被拦且有提示。

    对应 trigger-flow-auditor 第一波 2 个高危守卫（_start_stage_training /
    open_problem_drill 的复习拦截）的场景级沉淀（硬规矩补账）：拦下后复习态
    完好可继续作答；退出复习后两个训练入口恢复可用。注入法 + 真实按钮路径
    双验。顺带验证 usage_log ui_exception 埋点在受控异常下确实落盘。
    """
    import app as app_mod
    import mistake_book as mb
    import mistake_book as _mb_mod
    from project_store import load_project, save_project
    import datetime
    import usage_log
    tmp = tempfile.mkdtemp(prefix="sim_w26_")
    orig = (gl.LIBRARY_DIR, gl.INBOX_DIR, gl.SGF_DIR, gl.PROJECT_DIR,
            gl.INDEX_PATH, gl.PROFILE_CACHE_PATH)
    gl.LIBRARY_DIR = tmp
    gl.INBOX_DIR = os.path.join(tmp, "inbox")
    gl.SGF_DIR = os.path.join(tmp, "sgf")
    gl.PROJECT_DIR = os.path.join(tmp, "projects")
    gl.INDEX_PATH = os.path.join(tmp, "index.json")
    gl.PROFILE_CACHE_PATH = os.path.join(tmp, "profile_cache.json")
    book_path = os.path.join(tmp, "mistake_book.json")
    graded = []
    orig_list = app_mod.list_mistake_items
    orig_record = app_mod.record_graded_attempt_mb
    orig_mb_list = _mb_mod.list_items
    task = {"id": "w26", "startNodeMove": 0, "playerColor": "B",
            "targetMoves": 2, "phase": "opening", "startMove": 1}
    try:
        ah.clean(app)
        sgf_text = "(;GM[1]FF[4]SZ[19]KM[7.5];B[pp];W[dp])"
        p = os.path.join(tmp, "g26.sgf")
        with open(p, "w", encoding="utf-8") as f:
            f.write(sgf_text)
        t = MoveTree(19)
        t.play(15, 15)   # 黑 pp：远离候选，构成问题手（供 drill）
        t.play(3, 15)
        rec = gl.add_sgf_to_library(p, sgf_text, t, rules="chinese", komi=7.5)
        tree_loaded, data = load_project(rec["projectPath"])
        tree_loaded.root.analysis = {
            "rootInfo": {"scoreLead": 0.0, "winrate": 0.5,
                         "currentPlayer": "B"},
            "moveInfos": [
                {"move": "Q16", "scoreLead": 0.0, "winrate": 0.52,
                 "order": 0, "visits": 100, "prior": 0.3, "pv": ["Q16"]},
                {"move": "D4", "scoreLead": 0.2, "winrate": 0.5,
                 "order": 1, "visits": 80, "prior": 0.2, "pv": ["D4"]},
                {"move": "R16", "scoreLead": 0.3, "winrate": 0.49,
                 "order": 2, "visits": 60, "prior": 0.1, "pv": ["R16"]},
            ],
        }
        # 问题手节点自身也要带 analysis（drill 判问题手需实测目损；
        # 与 harness blunder fixture 同构：候选外落子 + 负目损快照）
        if tree_loaded.root.children:
            tree_loaded.root.children[0].analysis = {
                "rootInfo": {"scoreLead": -2.0, "winrate": 0.40},
                "moveInfos": [],
            }
        save_project(rec["projectPath"], tree_loaded,
                     rules=data.get("rules", "chinese"),
                     komi=data.get("komi", 7.5))
        # 索引记录直挂训练题：真实按钮路径（棋谱库「开始阶段训练」）无需引擎
        idx = gl._load_index()
        for r in idx.get("records", []):
            if r.get("id") == rec["id"]:
                r["trainingTask"] = dict(task)
        gl._save_index(idx)
        item = {
            "id": "w26-item", "gameId": rec["id"], "gameName": "w26局",
            "moveNo": 1, "color": "B", "bestMove": "Q16",
            "projectPath": rec["projectPath"], "scoreLoss": 4.8,
            "dueDate": datetime.date.today().isoformat(), "active": True,
        }
        mb.save_book({"version": 1, "items": [item]}, path=book_path)
        app_mod.list_mistake_items = (
            lambda *a, **k: orig_list(book_path, *a, **k))
        _mb_mod.list_items = (
            lambda *a, **k: orig_mb_list(book_path, **k))
        app_mod.record_graded_attempt_mb = (
            lambda iid, played, mis, color, best=None, **k: (
                graded.append((iid, played)),
                {"dueDate": "2099-01-01"})[1])

        sc = Scenario(app, "W26")
        sc.step("打开错题本", app.open_mistake_book)
        rows = app._mistake_book_tv.get_children()
        sc.step("选中错题并进入复习", lambda: (
            app._mistake_book_tv.selection_set(rows[0]),
            app._start_selected_mistake_review()))
        sc.assert_ok("复习态激活", bool(app._mistake_review
                                        and app._mistake_review.get("active")))
        review_ctx = app._mistake_review

        # ---- 注入法：直接调训练入口 ----
        sc.step("复习中注入法启动阶段训练",
                lambda: app._start_stage_training(dict(task)))
        sc.assert_ok("阶段训练被拦（未激活）", app._training is None,
                     str(app._training))
        msg = app.lbl_msg.cget("text")
        sc.assert_ok("拦截有提示（提到错题复习）",
                     "错题复习" in str(msg), str(msg))
        sc.step("复习中注入法开问题手 drill", app.open_problem_drill)
        sc.assert_ok("drill 被拦（未开窗）",
                     app._drill is None and app._drill_win is None)
        msg = app.lbl_msg.cget("text")
        sc.assert_ok("drill 拦截有提示",
                     "错题复习" in str(msg) and "问题手" in str(msg), str(msg))

        # ---- 真实按钮路径：工具栏「问题手训练」----
        drill_btn = _find_button_by_text(app, "问题手训练")
        if drill_btn is not None:
            sc.step("真实按钮点击「问题手训练」", drill_btn.invoke)
            sc.assert_ok("按钮路径 drill 仍被拦",
                         app._drill is None and app._drill_win is None)
        else:
            print("  [W26] 未找到「问题手训练」按钮（布局差异），按钮路径跳过")

        # ---- 真实按钮路径：棋谱库「开始阶段训练」----
        sc.step("打开棋谱库", app.open_game_library)
        iid = next((k for k, v in (app._lib_map or {}).items()
                    if v.get("id") == rec["id"]), None)
        if iid is not None and app._lib_tv is not None:
            train_btn = _find_button_by_text(app, "开始阶段训练")
            if train_btn is not None:
                sc.step("选中棋谱并点「开始阶段训练」", lambda: (
                    app._lib_tv.selection_set(iid),
                    train_btn.invoke()))
                sc.assert_ok("按钮路径阶段训练仍被拦", app._training is None)
                sc.assert_ok("按钮路径拦截提示提到复习",
                             "错题复习" in str(app.lbl_msg.cget("text")),
                             str(app.lbl_msg.cget("text")))
            else:
                print("  [W26] 未找到「开始阶段训练」按钮，跳过")
        sc.step("关闭棋谱库窗口", app._close_library_window)
        shell = getattr(app, "shell", None)
        page = shell.pages.get("library") if shell is not None else None
        if app._lib_win is page and page is not None:
            # V6 布局：棋谱库是页面而非弹窗，"关闭"= 回复盘页
            sc.assert_ok("页面模式下已回复盘页",
                         app.router.current == "review",
                         str(app.router.current))
        else:
            sc.assert_ok("棋谱库窗口引用清", app._lib_win is None)

        # ---- 拦截后复习态完好，可继续作答 ----
        sc.assert_ok("复习上下文未被动过（同一对象且激活）",
                     app._mistake_review is review_ctx
                     and review_ctx.get("active"))
        sc.assert_ok("题面仍在（根）", app.tree.current.depth == 0)
        sc.step("答榜外手 K10（应 again 回题面）", lambda: app.play(9, 9))
        sc.assert_ok("答错回题面且 attempts=1",
                     app.tree.current.depth == 0
                     and app._mistake_review is not None
                     and app._mistake_review.get("attempts") == 1,
                     str(app._mistake_review
                         and app._mistake_review.get("attempts")))
        sc.step("答首选 Q16（出账结束复习）", lambda: app.play(15, 3))
        sc.assert_ok("答对后复习结束", app._mistake_review is None)
        sc.assert_ok("两次作答均已记账", len(graded) == 2, str(graded))

        # ---- 退出复习后两个训练入口恢复可用 ----
        sc.step("复习结束后再启动阶段训练",
                lambda: app._start_stage_training(dict(task)))
        sc.assert_ok("阶段训练成功激活", bool(
            app._training and app._training.get("active")
            and not app._training.get("finished")))
        sc.step("结束训练（清理）", app._abandon_training_state)
        sc.step("复习结束后再开 drill", app.open_problem_drill)
        sc.assert_ok("drill 成功打开", app._drill is not None)
        if app._drill_win is not None:
            sc.step("关闭 drill", app._close_problem_drill)
            sc.assert_ok("drill 引用清",
                         app._drill_win is None and app._drill is None)
        violations = check_all_unconditional(app)
        sc.assert_ok("场景后无残留违规", not violations, str(violations))

        # ---- 埋点验证：受控 ui_exception 确实写入 usage_log ----
        upath = os.path.join(tmp, "usage_events.jsonl")
        usage_log.set_path(upath)
        usage_log.set_enabled(True)
        try:
            sc.step("受控异常走统一落账口",
                    lambda: app._log_tk_exception(
                        ValueError, ValueError("sim-w26"), None))
            events = []
            with open(upath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        events.append(json.loads(line))
            hits = [e for e in events
                    if e.get("event") == "ui_exception"
                    and e.get("error_type") == "ValueError"
                    and "sim-w26" in str(e.get("message", ""))]
            sc.assert_ok("ui_exception 埋点已落盘", bool(hits), str(events))
        finally:
            usage_log.set_enabled(False)
            usage_log.set_path(None)
    finally:
        app_mod.list_mistake_items = orig_list
        _mb_mod.list_items = orig_mb_list
        app_mod.record_graded_attempt_mb = orig_record
        gl.LIBRARY_DIR, gl.INBOX_DIR, gl.SGF_DIR, gl.PROJECT_DIR, \
            gl.INDEX_PATH, gl.PROFILE_CACHE_PATH = orig
        app._library_record_id = None
        usage_log.set_enabled(False)
        usage_log.set_path(None)


# ===================== W27 教练解读窗口×导航/模式交错 =====================

def scenario_w27_coach_window_interleave(app):
    """教练解读（coach 链路）首场景：解读窗口×导航/重复打开/退化输入。

    真实用户路径：复盘看到问题手 → 点「教练解读（当前手）」看结构化解读 →
    窗口没关继续导航看别的手 → 再点一次（旧窗销毁重建，不叠窗）→ 关窗引用
    清；退化为根局面/无分析数据时给提示不崩；复习/点目态下请求解读不崩。
    """
    ah.seed_fixture(app, "analyzed")
    sc = Scenario(app, "W27")
    # 导航到第 1 手（黑 D16 问题手，父节点带候选分析）
    sc.step("导航到第 1 手", lambda: app.do_step(1))
    sc.assert_ok("已到第 1 手", app.tree.current.depth == 1)
    sc.step("请求教练解读", app.show_coach_explanation)
    sc.assert_ok("教练窗口已打开",
                 app._coach_win is not None
                 and app._coach_win.winfo_exists())
    # 窗口开着继续导航 + 再请求（旧窗销毁重建，不叠窗）
    sc.step("开窗状态下导航到末尾", lambda: app.do_step(1))
    sc.assert_ok("导航后教练窗口仍在", app._coach_win is not None)
    sc.step("再次请求解读（重建窗口）", app.show_coach_explanation)
    sc.assert_ok("窗口重建后仍单实例", app._coach_win is not None
                 and app._coach_win.winfo_exists())
    sc.step("关闭教练窗口", app._close_coach_window)
    sc.assert_ok("教练窗口引用清", app._coach_win is None)
    # 退化输入 1：根局面（没有落子）→ 提示不崩
    sc.step("回根后请求解读", app.do_goto_root)
    sc.step("根局面请求解读", app.show_coach_explanation)
    sc.assert_ok("根局面给提示（不崩不叠窗）",
                 app._coach_win is None
                 and "根局面" in str(app.lbl_msg.cget("text")),
                 str(app.lbl_msg.cget("text")))
    # 退化输入 2：无分析数据的一手 → 提示不崩
    ah.seed_fixture(app, "simple")
    sc.step("无分析局落一手", lambda: app.play(9, 9))
    sc.step("无分析手请求解读", app.show_coach_explanation)
    sc.assert_ok("无分析给提示",
                 app._coach_win is None
                 and "分析" in str(app.lbl_msg.cget("text")),
                 str(app.lbl_msg.cget("text")))
    # 复习态下请求解读（真实教练窗口浮在复习题面上，不干扰判分链）
    # 注意 seed_fixture 会 clean 掉复习态、do_step 被复习拦截——先布局面、
    # 先导航，最后注入复习态再请求解读。
    ah.seed_fixture(app, "blunder")
    sc.step("导航到 blunder 手", lambda: app.do_step(1))
    sc.assert_ok("已到 blunder 手", app.tree.current.depth == 1)
    app._mistake_review = {"active": True,
                           "item": {"id": "w27", "color": "B",
                                    "moveNo": 1},
                           "parent": app.tree.root,
                           "attempts": 0}
    try:
        sc.step("复习态下请求解读", app.show_coach_explanation)
        sc.assert_ok("复习态解读可用（窗口打开）",
                     app._coach_win is not None)
        sc.assert_ok("复习态未被解读破坏",
                     bool(app._mistake_review
                          and app._mistake_review.get("active")))
        sc.step("关闭教练窗口（复习态）", app._close_coach_window)
        sc.assert_ok("窗口引用清", app._coach_win is None)
    finally:
        app._mistake_review = None
    violations = check_all_unconditional(app)
    sc.assert_ok("场景后无残留违规", not violations, str(violations))


# ===================== W28 分栏拖动×棋盘自适应（主窗布局重构回归） =====================

def scenario_w28_sash_drag_board_fit(app):
    """主窗布局重构回归：sash 拖到极限棋盘必须随面板自适应，不被右栏裁剪。

    布局重构（棋盘 0.95/右栏 396/pane minsize 420）后，根窗口 <Configure>
    监听不到 sash 拖动产生的面板宽度变化——拖到左极限时棋盘保持大尺寸
    被右栏直接裁掉一截，且必须手动拉伸窗口才能恢复。修复：_board_panel
    自身 <Configure> 走同一条 80ms 防抖重算链（app._on_configure）。
    断言契约：任何 sash 位置下 面板宽 - BOARD_PIX >= 0（棋盘永远不被裁）。
    """
    if getattr(app, "workspace", None) is None:
        print("  [W28] 无分栏工作区，跳过")
        return
    sc = Scenario(app, "W28")
    sc.step("回复盘页", lambda: app.router.go("review"))
    advance(app, 0.4)   # 启动几何 + _restore_pane_position(180ms) + 防抖落定

    def _slack():
        return app._board_panel.winfo_width() - app.BOARD_PIX

    sc.assert_ok("初始棋盘未被裁剪", _slack() >= 0,
                 "panel=%d board=%d" % (app._board_panel.winfo_width(), app.BOARD_PIX))
    board_initial = app.BOARD_PIX

    # 拖 sash 到左极限（左 pane minsize 420）——修复前棋盘保持原尺寸被裁剪
    sc.step("sash 拖到左极限", lambda: app.workspace.sash_place(0, 420, 0))
    advance(app, 0.3)
    sc.assert_ok("极限左拖后棋盘已自适应（不裁剪）", _slack() >= 0,
                 "panel=%d board=%d" % (app._board_panel.winfo_width(), app.BOARD_PIX))
    board_squeezed = app.BOARD_PIX
    sc.assert_ok("左极限下棋盘确实缩小了（重算生效）",
                 board_squeezed <= board_initial,
                 "board %d -> %d" % (board_initial, board_squeezed))

    # 拖回右极限（右 pane 保 367）：面板变宽，棋盘单调放大且仍不裁剪
    sc.step("sash 拖回右极限",
            lambda: app.workspace.sash_place(
                0, max(420, app.workspace.winfo_width() - 367), 0))
    advance(app, 0.3)
    sc.assert_ok("右极限下棋盘放大（单调）", app.BOARD_PIX >= board_squeezed,
                 "board %d -> %d" % (board_squeezed, app.BOARD_PIX))
    sc.assert_ok("右极限下仍不裁剪", _slack() >= 0,
                 "panel=%d board=%d" % (app._board_panel.winfo_width(), app.BOARD_PIX))

    # 启动竞态回归：窗口未映射时触发 sash 恢复，不得把位置钉死在左极限。
    # 修复前：_restore_pane_position 在未映射窗口上读到宽度 1 →
    # max_position=420 → 保存的位置被钳到 420（棋盘挤小/右栏占满半屏）。
    # 修复后：未映射时进 120ms 重试链，映射后按真实宽度钳制恢复。
    sc.step("撤走窗口（模拟启动未映射）", app.withdraw)
    advance(app, 0.1)
    saved = max(500, app.workspace.winfo_width() - 500)
    sc.step("未映射时触发 sash 恢复", lambda: app._restore_pane_position(saved))
    advance(app, 0.1)
    sc.step("窗口重新映射", app.deiconify)
    advance(app, 1.2)   # 重试链（120ms×5）走完
    sash_x = app.workspace.sash_coord(0)[0]
    expect = min(saved, max(420, app.workspace.winfo_width() - 367))
    sc.assert_ok("sash 恢复到保存位（未被钉死在 420）", abs(sash_x - expect) <= 2,
                 "sash=%s expect=%d saved=%d" % (sash_x, expect, saved))
    violations = check_all_unconditional(app)
    sc.assert_ok("场景后无残留违规", not violations, str(violations))


# ===================== W29 画像/棋风窗口 × 批量分析队列交错 =====================

def scenario_w29_profile_style_queue_interleave(app):
    """跨棋局聚合窗口×批量队列：队列在后台完成一盘棋时，开着的画像/棋风
    窗口必须重开重算（前台整盘完成路径已有同款守卫），已关的窗口不得被
    复活；队列本体完成、索引长出 profileSummary。

    真实用户旅程：开画像看长期趋势 → 顺手把新棋丢进批量队列 → 队列跑完
    一盘 → 画像窗口若不刷新，用户看到的是过时聚合（"消息不撒谎"家族：
    界面静静展示旧数据，无任何提示）。
    """
    tmp = tempfile.mkdtemp(prefix="sim_w29_")
    orig = (gl.LIBRARY_DIR, gl.INBOX_DIR, gl.SGF_DIR, gl.PROJECT_DIR,
            gl.INDEX_PATH, gl.PROFILE_CACHE_PATH)
    gl.LIBRARY_DIR = tmp
    gl.INBOX_DIR = os.path.join(tmp, "inbox")
    gl.SGF_DIR = os.path.join(tmp, "sgf")
    gl.PROJECT_DIR = os.path.join(tmp, "projects")
    gl.INDEX_PATH = os.path.join(tmp, "index.json")
    gl.PROFILE_CACHE_PATH = os.path.join(tmp, "profile_cache.json")
    real_client = app.client
    real_queue = app._analysis_queue
    try:
        ah.seed_fixture(app, "simple")
        sgf_text = "(;GM[1]FF[4]SZ[19];B[dd];W[pp])"
        sgf_path = os.path.join(tmp, "g29.sgf")
        with open(sgf_path, "w", encoding="utf-8") as f:
            f.write(sgf_text)
        tree = MoveTree(19)
        tree.play(3, 3)
        tree.play(15, 15)
        rec = gl.add_sgf_to_library(sgf_path, sgf_text, tree,
                                    rules="chinese", komi=7.5)
        app._analysis_queue = AnalysisQueue(os.path.join(tmp, "queue.json"))
        app.client = FakeClient()
        sc = Scenario(app, "W29")
        # 队列空闲时先开聚合窗口（此刻库内 0 盘有画像摘要）
        sc.step("开画像窗口", app.open_player_profile)
        profile_win0 = app._profile_win
        sc.assert_ok("画像窗口已开", profile_win0 is not None)

        # 空库首开画像：必须空态说明而非 AttributeError（接力板#9 硬规矩
        # 回归锚——get_or_rebuild 空库返回 None，曾直接 .keys() 崩，
        # 新装用户点「个人画像」首崩点）
        def _all_texts(w, acc):
            for child in w.winfo_children():
                try:
                    acc.append(str(child.cget("text")))
                except Exception:
                    pass
                _all_texts(child, acc)
            return acc
        texts0 = _all_texts(profile_win0, [])
        sc.assert_ok("空库画像=空态说明窗（不崩）",
                     any("画像尚未生成" in t for t in texts0))
        sc.assert_ok("空态窗仍登记 _profile_win（关窗/重开链完好）",
                     app._profile_win is profile_win0)

        sc.step("开棋风窗口", app.open_style_profile)
        sc.assert_ok("棋风窗口已开", app._style_win is not None)
        # 入队；在飞时关掉棋风窗口（中断面：窗口生命周期×队列进行中）
        sc.step("入队一盘", lambda: app._enqueue_records_for_analysis([rec]))
        sc.step("手动 kick 领取", app._kick_analysis_queue)
        sc.assert_ok("已领任务", app._analysis_queue_current is not None)
        sc.step("在飞时关棋风窗口", app._close_style_window)
        sc.assert_ok("棋风窗口引用清", app._style_win is None)
        # 队列后台跑完（FakeClient 即时回流，advance 泵 after 链到完成）
        sc.step("推进 3s（逐节点回流到完成）", lambda: advance(app, 3.0))
        statuses = [t.get("status") for t in app._analysis_queue.tasks()]
        sc.assert_ok("队列任务完成", "completed" in statuses, str(statuses))
        rec_after = gl.get_record(rec.get("id"))
        sc.assert_ok("记录已生成画像摘要",
                     isinstance((rec_after or {}).get("profileSummary"), dict))
        # 核心：开着的画像窗口被重开重算（窗口身份变化且存活），
        # 已关的棋风窗口不得被复活
        sc.assert_ok("画像窗口已重开重算（不 stale）",
                     app._profile_win is not None
                     and app._profile_win is not profile_win0
                     and app._profile_win.winfo_exists())
        sc.assert_ok("已关棋风窗口未被复活", app._style_win is None)
        # 完成后重开棋风窗口可用新数据；随后关闭全部查引用清 + 换谱清场
        sc.step("完成后重开棋风窗口", app.open_style_profile)
        sc.assert_ok("棋风窗口重开成功", app._style_win is not None)
        sc.step("关棋风窗口", app._close_style_window)
        sc.step("关画像窗口", app._close_profile_window)
        sc.assert_ok("画像窗口引用清", app._profile_win is None)
        sc.step("真实换谱路径：清空回根", app.do_reset)
        violations = check_post_game_switch(app)
        sc.assert_ok("换谱后状态干净", not violations, str(violations))
    finally:
        gl.LIBRARY_DIR, gl.INBOX_DIR, gl.SGF_DIR, gl.PROJECT_DIR, \
            gl.INDEX_PATH, gl.PROFILE_CACHE_PATH = orig
        app.client = real_client
        app._analysis_queue = real_queue
        app._analysis_queue_current = None
        app._analysis_queue_pending = {}
        for closer in (getattr(app, "_close_profile_window", None),
                       getattr(app, "_close_style_window", None)):
            if closer is None:
                continue
            try:
                closer()
            except Exception:
                pass


# ===================== W30 配置热切换 × 缓存/队列/候选联动 =====================

def scenario_w30_config_hot_switch(app):
    """设置热切换（_apply_settings）全链：
    - 值热切换（visits/规则/贴目/候选数）：实例属性与 user_settings.json
      立即同步；当前局面候选按新 candidate_count 重渲染；点目中改值退出后
      也补齐；缓存签名变化清 _library_bg_recent；下一次分析查询携带新
      rules/komi/visits（缓存 key 完整性——"下次分析生效"承诺可验证）。
    - 换引擎/模型热切换×在飞队列任务：任务释放回 queued、rid 挂账清空
      （防同号劫持）、guard 清空；引擎恢复后可续跑完成。

    真实用户旅程：复盘到一半想起 visits 调低点/换规则 → 改设置 → 继续
    分析；或队列跑到一半换模型 → 队列不能丢任务/串数据。
    """
    import copy as _copy
    tmp = tempfile.mkdtemp(prefix="sim_w30_")
    real_client = app.client
    real_queue = app._analysis_queue
    orig_cfg_path = app.cfg.path
    orig_cfg_data = _copy.deepcopy(app.cfg.data)
    orig_attrs = (app.rules, app.komi, app.katago_exe, app.model_file,
                  app._candidate_count, app._pv_length)
    gl_orig = (gl.LIBRARY_DIR, gl.INBOX_DIR, gl.SGF_DIR, gl.PROJECT_DIR,
               gl.INDEX_PATH, gl.PROFILE_CACHE_PATH)
    msgs = []
    orig_set_msg = app._set_msg

    def _spy(text, kind=None):
        msgs.append(str(text))
        return orig_set_msg(text, kind)

    try:
        # 前置重定向（Phase A 要同步跑 _poll_loop：kick/后台预热必须落在
        # tmp 库与空队列上，不得触碰真实 game_library / 真实队列文件）
        gl.LIBRARY_DIR = tmp
        gl.INBOX_DIR = os.path.join(tmp, "inbox")
        gl.SGF_DIR = os.path.join(tmp, "sgf")
        gl.PROJECT_DIR = os.path.join(tmp, "projects")
        gl.INDEX_PATH = os.path.join(tmp, "index.json")
        gl.PROFILE_CACHE_PATH = os.path.join(tmp, "profile_cache.json")
        sgf_text = "(;GM[1]FF[4]SZ[19];B[ee];W[qq])"
        p30 = os.path.join(tmp, "g30.sgf")
        with open(p30, "w", encoding="utf-8") as f:
            f.write(sgf_text)
        t30 = MoveTree(19)
        t30.play(2, 2)
        t30.play(14, 14)
        rec30 = gl.add_sgf_to_library(p30, sgf_text, t30,
                                      rules="chinese", komi=7.5)
        app._analysis_queue = AnalysisQueue(os.path.join(tmp, "queue.json"))

        ah.seed_fixture(app, "analyzed")
        app.cfg.path = os.path.join(tmp, "settings.json")   # update() 写盘重定向
        app._set_msg = _spy
        app.client = ManualClient()
        sc = Scenario(app, "W30")
        root = app.tree.current
        an0 = root.analysis
        # ---- 阶段A：值热切换（不换引擎路径） ----
        app._library_bg_recent.add("w30-dummy")
        sc.step("应用设置（japanese/6.5/400visits/1候选）",
                lambda: app._apply_settings(
                    exe=app.katago_exe, model=app.model_file,
                    rules="japanese", komi=6.5, visits=400,
                    candidate_count=1, pv_length=8))
        sc.assert_ok("rules 已同步", app.rules == "japanese", app.rules)
        sc.assert_ok("komi 已同步", float(app.komi) == 6.5, str(app.komi))
        sc.assert_ok("visits 已持久化", int(app.cfg.get("max_visits")) == 400)
        sc.assert_ok("候选数已同步", app._candidate_count == 1)
        with open(app.cfg.path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        sc.assert_ok("设置已写盘", saved.get("rules") == "japanese"
                     and int(saved.get("max_visits")) == 400)
        sc.assert_ok("缓存签名变化清后台已试集合",
                     "w30-dummy" not in app._library_bg_recent)
        sc.assert_ok("当前候选按新数截断",
                     len(app._candidate_actions) == 1,
                     str(len(app._candidate_actions)))
        sc.assert_ok("消息不撒谎（保存+生效时机）",
                     any("已保存设置" in m for m in msgs)
                     and any("下次分析生效" in m for m in msgs),
                     str(msgs[-1:]))
        # 点目中改值：跳过重渲染可接受，但退出点目必须按新值补渲染。
        # 注意：seed 分析无 ownership，enter_scoring 会清空 node.analysis
        # 并发 ownership 请求——退出后须回流该请求才有候选（真实语义）。
        sc.step("进入点目", app.enter_scoring)
        own_rid = list(app.client.queries)[-1]
        sc.step("点目中改回（chinese/7.5/3候选）",
                lambda: app._apply_settings(
                    exe=app.katago_exe, model=app.model_file,
                    rules="chinese", komi=7.5, visits=400,
                    candidate_count=3, pv_length=8))
        sc.step("退出点目", app.exit_scoring)

        def _deliver_own():
            # 定制回流：4 个候选（FakeClient 对空 moves 根局面只回 1 个，
            # 验证截断语义需要多于新上限的候选供给）
            resp = {
                "rootInfo": {"winrate": 0.5, "scoreLead": 0.5,
                             "currentPlayer": "B"},
                "moveInfos": [
                    {"move": mv, "order": i, "winrate": 0.5,
                     "scoreLead": 0.5, "visits": 100, "prior": 0.2,
                     "pv": [mv]}
                    for i, mv in enumerate(["Q16", "D4", "C3", "R16"])],
                "ownership": [0.0] * 361,
            }
            app.client._results.put((own_rid, resp))

        sc.step("回流点目期 ownership 请求（4候选）", _deliver_own)
        sc.step("同步处理回流", app._poll_loop)
        sc.assert_ok("退出点目后候选按新数渲染（4→3 截断）",
                     len(app._candidate_actions) == 3,
                     str(len(app._candidate_actions)))
        # 缓存 key 完整性：下一次分析查询必须携带新口径
        sc.step("强制重析当前节点", app.force_analyze)
        force_rid = list(app.client.queries)[-1]
        q = app.client.queries[force_rid]
        sc.assert_ok("新查询携带新 rules/komi/visits",
                     q.get("rules") == "chinese" and float(q.get("komi")) == 7.5
                     and int(q.get("maxVisits")) == 400,
                     str({k: q.get(k) for k in ("rules", "komi", "maxVisits")}))
        sc.step("回流新结果", lambda: app.client.deliver(force_rid))
        sc.step("同步处理回流", app._poll_loop)
        sc.assert_ok("节点分析已按新结果替换",
                     app.tree.current.analysis is not an0)
        sc.assert_ok("前台挂账已清（不阻塞后续队列领取）",
                     app.guard.pending_count() == 0)
        # ---- 阶段B：换引擎/模型 × 在飞队列任务 ----

        def _claim30():
            app._enqueue_records_for_analysis([rec30])
            app._kick_analysis_queue()

        sc.step("入队并领取（受控不回流）", _claim30)
        sc.assert_ok("任务已领取", app._analysis_queue_current is not None)
        sc.assert_ok("在飞挂账 ≥1", len(app._analysis_queue_pending) >= 1)
        no_engine = os.path.join(tmp, "no_engine.exe")
        model2 = os.path.join(tmp, "model2.bin.gz")
        open(model2, "wb").close()   # 占位模型文件（preflight 只查存在性）
        msgs.clear()
        sc.step("热切换引擎/模型路径", lambda: app._apply_settings(
            exe=no_engine, model=model2, rules="chinese", komi=7.5,
            visits=400))
        sc.assert_ok("队列挂账清空（防同号劫持）",
                     app._analysis_queue_pending == {})
        sc.assert_ok("在飞任务释放", app._analysis_queue_current is None)
        task30 = app._analysis_queue.tasks()[0]
        sc.assert_ok("任务回 queued 可续跑",
                     task30.get("status") == "queued", str(task30.get("status")))
        sc.assert_ok("引擎重启路径消息不撒谎",
                     any("已切换引擎/模型" in m for m in msgs), str(msgs[-1:]))
        # 模拟新引擎就绪 → 续跑到完成
        app.client = FakeClient()
        sc.step("新引擎就绪后续跑", app._kick_analysis_queue)
        sc.assert_ok("任务重新领取", app._analysis_queue_current is not None)
        sc.step("推进 2.5s 跑完", lambda: advance(app, 2.5))
        statuses = [t.get("status") for t in app._analysis_queue.tasks()]
        sc.assert_ok("续跑完成", "completed" in statuses, str(statuses))
        sc.step("真实换谱路径：清空回根", app.do_reset)
        violations = check_post_game_switch(app)
        sc.assert_ok("换谱后状态干净", not violations, str(violations))
    finally:
        app._set_msg = orig_set_msg
        app.cfg.path = orig_cfg_path
        app.cfg.data = orig_cfg_data
        app.rules, app.komi, app.katago_exe, app.model_file, \
            app._candidate_count, app._pv_length = orig_attrs
        app.client = real_client
        app._analysis_queue = real_queue
        app._analysis_queue_current = None
        app._analysis_queue_pending = {}
        gl.LIBRARY_DIR, gl.INBOX_DIR, gl.SGF_DIR, gl.PROJECT_DIR, \
            gl.INDEX_PATH, gl.PROFILE_CACHE_PATH = gl_orig


# ===================== W31 引擎死亡 × 后台挂账 × 官子训练 =====================

def _endgame_fixture_tree():
    """官子训练 fixture：与 test_ui_smoke.test_endgame_drill_ui 同构
    （60 手散点谱 + 第 55 手目损收束题），保证能稳定出题。"""
    from review import ReviewReport
    t = MoveTree(19)
    for i in range(60):
        t.play(1 + (i % 6) * 3, 1 + i // 6)
    line = ReviewReport(t).mainline_nodes()

    def _ami(move, sl, order, pv=None):
        return {"move": move, "order": order, "winrate": 0.5,
                "scoreLead": sl, "visits": 1000, "prior": 0.2,
                "pv": pv or [move]}

    def _ana(sl, mis):
        return {"rootInfo": {"scoreLead": sl, "winrate": 0.5}, "moveInfos": mis}

    for node in line:
        node.analysis = _ana(0.0, [_ami("Q16", 0.5, 0), _ami("D4", -0.2, 1)])
    line[54].analysis = _ana(0.0, [_ami("C2", 2.0, 0, pv=["C2", "Q16", "C2"]),
                                   _ami("Q16", 0.5, 1), _ami("A1", -4.0, 2)])
    line[55].analysis = _ana(-3.0, [_ami("B9", -3.0, 0)])
    return t


def scenario_w31_engine_death_bg_endgame(app):
    """引擎死亡 × 后台挂账交错：后台发送器不得裸调 client.analyze。

    crash.log 2026-08-21 22:36 真实故障：引擎进程死亡未及 poll 检测 ×
    启动失败链留下 _training_cache_bg_current 而 client=None，
    after 回调走到 self.client.analyze(q) → AttributeError NoneType。
    修复面回归：①两路后台发送器入口判引擎存活（不在则中止本轮）；
    ②_start_katago 失败路径对齐 _stop_katago 清 rid 挂账与 bg current；
    ③官子训练纯缓存驱动，引擎不在时开练 + 轮询泵共存不崩。
    """
    import app as app_mod
    sc = Scenario(app, "W31")
    t = _endgame_fixture_tree()
    app.tree = t
    app._after_navigate()
    real_client = app.client
    orig_cls = app_mod.KataGoAnalysisClient
    orig_cfg_data = dict(app.cfg.data)
    orig_exe_model = (app.katago_exe, app.model_file)
    pkg = {"version": 1, "recordId": None, "taskId": "sim", "status": "building",
           "entries": {}, "preparedRounds": 0, "plannedRounds": 4}
    try:
        # 1) 训练应手缓存发送器 × client=None（22:36 崩溃类回归）
        app.client = None
        app._training_cache_bg_current = {
            "record_id": None, "name": "sim-w31", "tree": t,
            "rules": "chinese", "komi": 7.5, "visits": 10,
            "package": pkg, "rounds": 0, "planned_rounds": 4,
            "jobs": [{"kind": "user", "branch": 0, "userMove": "Q4",
                      "moves": [["B", "Q4"]]}],
        }
        sc.step("缓存发送器 × client=None", app._send_next_training_cache_bg_request)
        sc.assert_ok("缓存轮中止不崩溃", app._training_cache_bg_current is None)
        sc.assert_ok("缓存包落 partial 状态", pkg.get("status") == "partial",
                     str(pkg.get("status")))

        # 2) 棋局库后台发送器 × client=None（同款守卫）
        app._library_bg_current = {
            "todo": [t.current], "done": 0, "total": 1, "tree": t,
            "rules": "chinese", "komi": 7.5, "visits": 120,
            "name": "sim-w31", "record_id": None,
        }
        sc.step("库后台发送器 × client=None", app._send_next_library_bg_request)
        sc.assert_ok("库后台中止不崩溃", app._library_bg_current is None)

        # 3) _start_katago 失败路径清挂账（引擎生命周期边界对齐 _stop_katago）
        class _FailingClient:
            def __init__(self, *a, **k):
                pass

            def start(self):
                raise RuntimeError("sim-w31 启动失败")

        tmp = tempfile.mkdtemp(prefix="sim_w31_")
        fake_exe = os.path.join(tmp, "fake_engine.exe")
        fake_model = os.path.join(tmp, "fake_model.bin.gz")
        open(fake_exe, "wb").close()
        open(fake_model, "wb").close()
        app_mod.KataGoAnalysisClient = _FailingClient
        app.cfg.data["engine_path"] = fake_exe
        app.cfg.data["model_path"] = fake_model
        app.katago_exe, app.model_file = fake_exe, fake_model
        # 重挂"启动前"残留挂账（模拟死亡未及检测 + 在飞结果）
        app._training_cache_bg_current = {"record_id": None, "package": pkg,
                                          "jobs": [], "rounds": 0}
        app._library_bg_current = {"todo": [], "done": 0}
        app._library_bg_pending = {"stale-q1": object()}
        app._training_cache_bg_pending = {"stale-q2": object()}
        sc.step("_start_katago 失败（真实 except 分支）",
                lambda: app._start_katago(quiet=True))
        sc.assert_ok("启动失败后 client 归 None", app.client is None)
        sc.assert_ok("失败路径清两路 bg current",
                     app._training_cache_bg_current is None
                     and app._library_bg_current is None)
        sc.assert_ok("失败路径清 rid 挂账（防同号劫持）",
                     app._library_bg_pending == {}
                     and app._training_cache_bg_pending == {})

        # 4) 官子训练与引擎死亡共存：纯缓存驱动，开练 + 轮询泵不崩
        sc.step("引擎不在时开官子训练", app.open_endgame_drill)
        sc.assert_ok("官子训练正常开窗（不依赖引擎）",
                     app._endgame_win is not None and app._endgame_set is not None)
        sc.step("泵 0.6s 轮询（client=None × poll_loop）", lambda: advance(app, 0.6))
        sc.assert_ok("轮询后官子训练仍在", app._endgame_active())
        violations = check_all_unconditional(app)
        sc.assert_ok("场景后无残留违规", not violations, str(violations))
    finally:
        app_mod.KataGoAnalysisClient = orig_cls
        app.cfg.data = orig_cfg_data
        app.katago_exe, app.model_file = orig_exe_model
        app.client = real_client
        app._training_cache_bg_current = None
        app._library_bg_current = None
        app._library_bg_pending = {}
        app._training_cache_bg_pending = {}
        if app._endgame_active():
            app._close_endgame_drill()


# ===================== W32 换谱中断 × 官子训练互斥清理 =====================

def scenario_w32_endgame_game_switch_interrupt(app):
    """官子训练开着时换谱：do_reset 统一清理链必须关窗清态零残留。

    官子训练棋盘锁定题面（_block_endgame 拦落子/导航/点目），
    换谱（do_reset → _reset_for_new_game）必须关掉训练窗、清题集、
    退出独占模式；换谱后导航恢复、点目可进，且点目中反向开官子被拒；
    新棋谱重开是全新会话（作答记录不串局）。
    """
    sc = Scenario(app, "W32")
    t = _endgame_fixture_tree()
    app.tree = t
    app._after_navigate()
    try:
        app.open_endgame_drill()
        sc.assert_ok("官子训练开窗且独占", app._endgame_active()
                     and app.active_modes() == {"endgame"})
        start_depth = app.tree.current.depth

        # 训练锁定：导航/落子/进点目全被拦（题面不被拖走）
        sc.step("时间轴跳转（应被拦）", lambda: app._timeline_jump(10))
        sc.assert_ok("题面未被拖走", app.tree.current.depth == start_depth,
                     "%d vs %d" % (app.tree.current.depth, start_depth))
        sc.step("落子（应被拦）", lambda: app.play(4, 4))
        sc.assert_ok("题面未被落子破坏", app.tree.current.depth == start_depth)
        sc.step("进入点目（应被拒）", app.enter_scoring)
        sc.assert_ok("点目未开启", not app.scoring_mode)

        # 换谱真实路径：do_reset → _reset_for_new_game 统一清理
        sc.step("do_reset 换谱（统一清理链）", app.do_reset)
        sc.assert_ok("官子训练窗口已关", app._endgame_win is None
                     and app._endgame_set is None and app._endgame_result is None)
        sc.assert_ok("模式退出干净", app.active_modes() == set())
        sc.assert_ok("回到空盘根", app.tree.current.depth == 0)
        violations = check_post_game_switch(app)
        sc.assert_ok("换谱不变式无违规", not violations, str(violations))

        # 换谱后导航恢复 + 反向互斥：点目模式中开官子被拒
        sc.step("换谱后导航恢复", lambda: app._timeline_jump(0))
        sc.step("进入点目", app.enter_scoring)
        sc.assert_ok("点目已开启", app.scoring_mode)
        sc.step("点目中开官子训练（应拒绝）", app.open_endgame_drill)
        sc.assert_ok("点目中官子训练被拒", app._endgame_win is None
                     and app.scoring_mode)
        sc.step("退出点目", app.exit_scoring)

        # 新棋谱重开：全新会话（作答记录不串局）
        t2 = _endgame_fixture_tree()
        app.tree = t2
        app._after_navigate()
        sc.step("新棋谱重开官子训练", app.open_endgame_drill)
        sc.assert_ok("新棋谱官子训练重开", app._endgame_active()
                     and app._endgame_result == {"answered": 0, "correct": 0,
                                                 "answers": {}})
        violations = check_all_unconditional(app)
        sc.assert_ok("场景后无残留违规", not violations, str(violations))
    finally:
        if app._endgame_active():
            app._close_endgame_drill()
        if app.scoring_mode:
            app.exit_scoring()


# ===================== W33 备份与恢复链路 =====================

def _w33_make_library(root):
    """最小可备份库：sgf + 项目 JSON（project_store 真实产物）+ index + 设置。"""
    lib = os.path.join(root, "game_library")
    os.makedirs(os.path.join(lib, "sgf"))
    os.makedirs(os.path.join(lib, "projects"))
    with open(os.path.join(lib, "sgf", "w33.sgf"), "w", encoding="utf-8") as f:
        f.write("(;GM[1]SZ[19];B[pd];W[dp])")
    t = MoveTree(19)
    t.play(3, 3)     # 黑 D4
    t.play(15, 15)   # 白 Q16
    with open(os.path.join(lib, "projects", "w33.kga.json"), "w",
              encoding="utf-8") as f:
        json.dump(ps.tree_to_project(t, meta={"title": "w33"}), f,
                  ensure_ascii=False)
    with open(os.path.join(lib, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"records": []}, f)
    settings = os.path.join(root, "user_settings.json")
    with open(settings, "w", encoding="utf-8") as f:
        json.dump({"max_visits": 200}, f)
    return lib, settings


def scenario_w33_backup_restore_chain(app):
    """W33 备份与恢复链路：每日自动备份的按日打包/滚动清理/恢复产物可读/故障降级。

    项目无「恢复」UI 入口——恢复路径验证 = 备份产物结构可被真实加载函数读取：
    zip CRC 完整、库内文件逐字节一致、项目 JSON 经 project_store.project_to_tree
    复原为棋局树、index/设置 JSON 可解析。故障段：库目录缺失/备份目录被普通
    文件占用（磁盘不可写位置的等价模拟）→ 静默 None 不崩、无 .tmp 残留；
    _prune 遇被占用 zip（Windows 打开句柄）不得中断其余清理；启动入口线程内
    异常被吞。另固化无头保护：app 构造期 backup 即已禁用（仿真绝不写真实库）。
    """
    sc = Scenario(app, "W33")
    today = datetime.date.today()
    stamp = today.strftime("%Y%m%d")

    # ---- 段1：首次启动（备份目录不存在）→ 自动建目录产出当日 zip ----
    tmp1 = tempfile.mkdtemp(prefix="w33_first_")
    try:
        lib1, set1 = _w33_make_library(tmp1)
        bdir1 = os.path.join(lib1, "backups")
        sc.assert_ok("首备前备份目录不存在", not os.path.isdir(bdir1))
        dest1 = bk.create_daily_backup(backup_dir=bdir1, library_dir=lib1,
                                       settings_path=set1)
        sc.assert_ok("首备自动建目录并产出当日 zip",
                     bool(dest1) and os.path.isfile(dest1)
                     and dest1.endswith("go-ana-backup-%s.zip" % stamp))
    finally:
        shutil.rmtree(tmp1, ignore_errors=True)

    # ---- 段2：多日库状态 → 今日打包 + KEEP=14 滚动清理 + 当日幂等 ----
    tmp2 = tempfile.mkdtemp(prefix="w33_days_")
    try:
        lib2, set2 = _w33_make_library(tmp2)
        bdir2 = os.path.join(lib2, "backups")
        os.makedirs(bdir2)
        for d in range(1, 17):    # 昨日起往前 16 天的历史备份
            with open(os.path.join(
                    bdir2, "go-ana-backup-%s.zip" % (
                        today - datetime.timedelta(days=d)).strftime("%Y%m%d")),
                    "wb") as f:
                f.write(b"old-%d" % d)
        dest2 = bk.create_daily_backup(backup_dir=bdir2, library_dir=lib2,
                                       settings_path=set2)
        sc.assert_ok("多日状态今日打包成功",
                     bool(dest2) and os.path.isfile(dest2))
        zips = sorted(n for n in os.listdir(bdir2) if n.endswith(".zip"))
        sc.assert_ok("滚动清理后恰 KEEP=14 份", len(zips) == 14, str(len(zips)))
        stamps = sorted(n[len("go-ana-backup-"):-4] for n in zips)
        want = [(today - datetime.timedelta(days=d)).strftime("%Y%m%d")
                for d in range(13, 0, -1)] + [stamp]
        sc.assert_ok("保留=今日+最新13（最旧3份已删）", stamps == want, str(stamps))
        again = bk.create_daily_backup(backup_dir=bdir2, library_dir=lib2,
                                       settings_path=set2)
        zips = sorted(n for n in os.listdir(bdir2) if n.endswith(".zip"))
        sc.assert_ok("同日双开幂等不重复打包", again == dest2 and len(zips) == 14)

        # ---- 段3：恢复路径（产物可被真实加载函数读取）----
        with zipfile.ZipFile(dest2) as zf:
            sc.assert_ok("zip CRC 完整", zf.testzip() is None)
            names = set(zf.namelist())
            sc.assert_ok("备份含库文件与设置（库内相对路径+设置根前缀）",
                         {"sgf/w33.sgf", "projects/w33.kga.json",
                          "index.json", "user_settings.json"} <= names,
                         str(sorted(names)))
            same = all(zf.read(n) == open(os.path.join(
                lib2, *n.split("/")), "rb").read()
                for n in names if n != "user_settings.json")
            sc.assert_ok("库内文件逐字节一致（恢复零丢失）", same)
            sc.assert_ok("设置逐字节一致",
                         zf.read("user_settings.json")
                         == open(set2, "rb").read())
            proj = json.loads(zf.read("projects/w33.kga.json").decode("utf-8"))
            restored = ps.project_to_tree(proj)
            sc.assert_ok("项目可复原为棋局树（2 手在谱）",
                         restored.current.depth == 2)
            sc.assert_ok("index/设置 JSON 结构可解析",
                         isinstance(json.loads(zf.read("index.json")), dict)
                         and isinstance(
                             json.loads(zf.read("user_settings.json")), dict))

        # ---- 段4a：目录缺失/被占 → 静默降级 ----
        occupied = os.path.join(tmp2, "occupied_path")
        with open(occupied, "w", encoding="utf-8") as f:
            f.write("not a dir")
        r = bk.create_daily_backup(backup_dir=occupied, library_dir=lib2,
                                   settings_path=set2)
        sc.assert_ok("备份目录被普通文件占用→静默 None", r is None)
        sc.assert_ok("降级后无 .tmp 残留",
                     not any(n.endswith(".tmp") for n in os.listdir(tmp2)))
        nolib = os.path.join(tmp2, "no_lib")
        r2 = bk.create_daily_backup(backup_dir=os.path.join(tmp2, "nb"),
                                    library_dir=nolib, settings_path=set2)
        sc.assert_ok("库目录不存在→None 且不建目录",
                     r2 is None and not os.path.isdir(nolib))
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)

    # ---- 段4b：_prune 遇被占用 zip（Windows 句柄锁）不崩、其余照删 ----
    tmp3 = tempfile.mkdtemp(prefix="w33_prune_")
    try:
        bdir3 = os.path.join(tmp3, "backups")
        os.makedirs(bdir3)
        for i in range(1, 18):    # 17 份：删最旧 3 份后应剩 14
            with open(os.path.join(
                    bdir3, "go-ana-backup-202608%02d.zip" % i), "wb") as f:
                f.write(b"x")
        locked = open(os.path.join(bdir3, "go-ana-backup-20260802.zip"), "rb")
        try:
            bk._prune(bdir3, keep=14)   # 不得抛
            left = sorted(n for n in os.listdir(bdir3) if n.endswith(".zip"))
            sc.assert_ok("被占用件保留待次日重试",
                         "go-ana-backup-20260802.zip" in left)
            sc.assert_ok("单个占用不中断其余清理（留15=14+锁件）",
                         len(left) == 15, str(len(left)))
        finally:
            locked.close()
    finally:
        shutil.rmtree(tmp3, ignore_errors=True)

    # ---- 段5：启动入口静默契约（备份失败不影响启动）----
    orig = bk.create_daily_backup
    hits = {"n": 0}

    def _boom(*a, **k):
        hits["n"] += 1
        raise RuntimeError("disk on fire")

    bk.create_daily_backup = _boom
    try:
        bk.set_enabled(True)
        bk.start_background_daily_backup()   # 线程内异常必须被吞
        for th in threading.enumerate():
            if th.name == "daily-backup":
                th.join(2.0)
        sc.assert_ok("启动线程已跑桩（异常被吞不外泄）", hits["n"] == 1, str(hits))
    finally:
        bk.set_enabled(False)
        bk.create_daily_backup = orig

    # ---- 段6：无头保护（构造期已禁用，仿真不写真实库）----
    sc.assert_ok("无头环境备份已禁用", bk._state["enabled"] is False)
    seen = []
    ah.create_tk_root(lambda: (seen.append(bk._state["enabled"]), None)[1])
    sc.assert_ok("构造期即已禁用（守卫先于 factory）", seen == [False], str(seen))


# ===================== W34 热力图×导航×模式切换交错 =====================

def _w34_heat_analysis(sl=3.0, wr=0.55):
    """带 ownership+policy 的完整分析：地盘黑白角+天元，策略三点（供热力图层）。"""
    own = [0.0] * 361
    own[0], own[18 * 19 + 18], own[9 * 19 + 9] = 0.8, -0.8, 0.4
    pol = [0.0] * 362                     # 末位 pass
    pol[2 * 19 + 2], pol[16 * 19 + 16], pol[5 * 19 + 5] = 1.0, 0.5, 0.3
    mis = [{"move": mv, "order": i, "scoreLead": msl, "winrate": mwr,
            "visits": 1000, "prior": 0.2, "pv": [mv]}
           for i, (mv, msl, mwr) in enumerate(
               (("Q16", 3.0, 0.55), ("D4", 2.5, 0.54), ("R16", 2.0, 0.53)))]
    return {"rootInfo": {"winrate": wr, "scoreLead": sl, "currentPlayer": "B"},
            "moveInfos": mis, "ownership": own, "policy": pol}


def _w34_seed(app):
    """三手谱：黑 D16 问题手 + 白 Q16 + 黑 F4（末手无分析），节点带热力数据。"""
    ah.clean(app)
    app.tree._profile_side = "B"
    app.tree.current.analysis = _w34_heat_analysis()
    app.tree.play(3, 15)                  # 黑 D16：远离候选 → 问题手（供 drill）
    app.tree.current.analysis = _w34_heat_analysis(-2.0, 0.40)
    app.tree.play(15, 15)                 # 白 Q16
    app.tree.current.analysis = _w34_heat_analysis(2.0, 0.52)
    app.tree.play(5, 5)                   # 黑 F4：无分析（导航盲区节点）
    app.do_goto_root()
    app.redraw()


def _w34_items(app):
    """画布热力图叠加计数（own, pol）。"""
    return (len(app.canvas.find_withtag("heatmap-own")),
            len(app.canvas.find_withtag("heatmap-pol")))


def scenario_w34_heatmap_nav_mode_interleave(app):
    """W34 地盘/策略热力图 × 导航 × 点目/训练/drill/复习/换谱交错。

    语义契约：
    - 点目：热力图整体让位（不与点目地盘图叠加），退出后按原档恢复；
    - 盲测（训练用户回合/问题手 drill/错题复习）：热力图不上屏——策略图
      即 NN 推荐点=公布答案，须与候选/PV 同规隐藏；
    - 回流：热力图开着时 analysis 到达自动上屏，档位不漂移；
    - 连按：档位/按钮文案/cfg 持久化/画布四者一致；
    - 换谱：空盘无残留；形式判断 HUD 开→自动切地盘并记原档，关→回原档
      且暂存不残留。
    """
    sc = Scenario(app, "W34")
    orig_key = app.cfg.get("heatmap_mode")
    try:
        _w34_seed(app)
        presses = (3 - app._heat_mode) % 3   # 上一场景可能留档：先归零
        if presses:
            sc.step("热力图归零",
                    lambda: [app.cycle_heatmap() for _ in range(presses)])
        own_n, pol_n = _w34_items(app)
        sc.assert_ok("基线：热力图关无叠加",
                     app._heat_mode == 0 and (own_n, pol_n) == (0, 0))

        # ---- 地盘档 × 导航 ----
        sc.step("切地盘热力图", app.cycle_heatmap)
        own_n, pol_n = _w34_items(app)
        sc.assert_ok("地盘档画出归属点",
                     app._heat_mode == 1 and own_n >= 3 and pol_n == 0,
                     "own=%d pol=%d" % (own_n, pol_n))
        sc.assert_ok("按钮文案不撒谎",
                     app.btn_heat.cget("text") == "热力图: %s" % HEAT_LABELS[1])
        sc.step("导航到第 2 手", lambda: app._timeline_jump(2))
        own_n, _ = _w34_items(app)
        sc.assert_ok("导航后热力图跟随（有分析节点仍画）", own_n >= 3)
        sc.step("导航到无分析节点", lambda: app._timeline_jump(3))
        own_n, pol_n = _w34_items(app)
        sc.assert_ok("无分析节点不画不残留", (own_n, pol_n) == (0, 0))
        sc.step("回根", lambda: app._timeline_jump(0))
        own_n, _ = _w34_items(app)
        sc.assert_ok("回根热力图恢复", own_n >= 3)

        # ---- 策略档 × 点目让位/恢复 ----
        sc.step("切策略热力图", app.cycle_heatmap)
        own_n, pol_n = _w34_items(app)
        sc.assert_ok("策略档画推荐点",
                     app._heat_mode == 2 and pol_n >= 3 and own_n == 0,
                     "own=%d pol=%d" % (own_n, pol_n))
        sc.step("进入点目（热力图让位）", app.enter_scoring)
        own_n, pol_n = _w34_items(app)
        sc.assert_ok("点目中热力图整体让位",
                     app.scoring_mode and (own_n, pol_n) == (0, 0),
                     "own=%d pol=%d" % (own_n, pol_n))
        sc.step("退出点目（按原档恢复）", app.exit_scoring)
        own_n, pol_n = _w34_items(app)
        sc.assert_ok("退出点目热力图按原档恢复",
                     not app.scoring_mode and app._heat_mode == 2
                     and pol_n >= 3)

        # ---- ownership 回流一致性 ----
        sc.step("循环两档回地盘",
                lambda: [app.cycle_heatmap() for _ in range(2)])
        sc.step("导航到无分析节点", lambda: app._timeline_jump(3))
        own_n, _ = _w34_items(app)
        sc.assert_ok("回流前无热力图", app._heat_mode == 1 and own_n == 0)
        node = app.tree.current

        def backflow():
            resp = _w34_heat_analysis()
            node.analysis = resp   # 与 _poll_loop 同序：先挂节点再走回流分发
            app._apply_analysis_result(node, resp)

        sc.step("注入 ownership 回流", backflow)
        own_n, _ = _w34_items(app)
        sc.assert_ok("回流后热力图自动上屏且档位不漂移",
                     app._heat_mode == 1 and own_n >= 3,
                     "own=%d mode=%d" % (own_n, app._heat_mode))

        # ---- 盲测防泄底：训练用户回合 ----
        sc.step("切策略热力图", app.cycle_heatmap)
        app.client = TrainingClient()
        sc.step("进入阶段训练", lambda: app._start_stage_training({
            "id": "w34", "startNodeMove": 0, "playerColor": "B",
            "targetMoves": 1, "phase": "opening", "startMove": 1}))
        sc.assert_ok("训练已激活", bool(
            app._training and app._training.get("active")
            and not app._training.get("finished")))
        ah.drive_training_to_user_turn(app)
        own_n, pol_n = _w34_items(app)
        sc.assert_ok("训练用户回合热力图不上屏（防泄底）",
                     (own_n, pol_n) == (0, 0),
                     "own=%d pol=%d" % (own_n, pol_n))
        sc.step("训练中连按热力图键",
                lambda: [app.cycle_heatmap() for _ in range(2)])
        own_n, pol_n = _w34_items(app)
        sc.assert_ok("训练中切档仍不上屏", (own_n, pol_n) == (0, 0))
        sc.step("结束训练", app._abandon_training_state)
        sc.step("回根重绘", lambda: (app._timeline_jump(0), app.redraw()))
        own_n, pol_n = _w34_items(app)
        sc.assert_ok("训练结束后热力图按当前档恢复", own_n >= 3 or pol_n >= 3,
                     "own=%d pol=%d" % (own_n, pol_n))

        # ---- 盲测防泄底：错题复习态（同一 _hide_ai_for_training 守卫）----
        sc.step("注入错题复习激活态",
                lambda: setattr(app, "_mistake_review", {"active": True}))
        sc.step("复习态重绘", app.redraw)
        own_n, pol_n = _w34_items(app)
        sc.assert_ok("错题复习中热力图不上屏", (own_n, pol_n) == (0, 0),
                     "own=%d pol=%d" % (own_n, pol_n))
        sc.step("清除复习态", lambda: setattr(app, "_mistake_review", None))

        # ---- 盲测防泄底：问题手 drill（题面未揭示）----
        sc.step("开问题手训练", app.open_problem_drill)
        sc.assert_ok("drill 已激活", app._drill_active())
        own_n, pol_n = _w34_items(app)
        sc.assert_ok("drill 中热力图不上屏（防泄底）", (own_n, pol_n) == (0, 0),
                     "own=%d pol=%d" % (own_n, pol_n))
        sc.step("关闭 drill", app._close_problem_drill)
        own_n, pol_n = _w34_items(app)
        sc.assert_ok("drill 关闭后热力图恢复", own_n >= 3 or pol_n >= 3)

        # ---- 快速连按：档位/文案/持久化/画布四方一致 ----
        sc.step("回根", lambda: app._timeline_jump(0))
        before_mode = app._heat_mode
        sc.step("连按热力图 ×5",
                lambda: [app.cycle_heatmap() for _ in range(5)])
        mode = app._heat_mode
        sc.assert_ok("连按后档位=前+5（mod 3）",
                     mode == (before_mode + 5) % 3,
                     "%d→%d" % (before_mode, mode))
        own_n, pol_n = _w34_items(app)
        if mode == 0:
            canvas_ok = (own_n, pol_n) == (0, 0)
        elif mode == 1:
            canvas_ok = own_n >= 3 and pol_n == 0
        else:
            canvas_ok = own_n == 0 and pol_n >= 3
        sc.assert_ok("连按后画布与档位一致", canvas_ok,
                     "mode=%d own=%d pol=%d" % (mode, own_n, pol_n))
        sc.assert_ok("连按后文案/持久化一致",
                     app.btn_heat.cget("text")
                     == "热力图: %s" % HEAT_LABELS[mode]
                     and app.cfg.get("heatmap_mode") == HEAT_KEYS[mode])

        # ---- 换谱 × 形式判断 HUD 联动 ----
        presses = (3 - app._heat_mode) % 3
        if presses:
            sc.step("热力图归零",
                    lambda: [app.cycle_heatmap() for _ in range(presses)])
        sc.assert_ok("热力图已关", app._heat_mode == 0)
        sc.step("开形式判断 HUD", app.toggle_situation)
        sc.assert_ok("HUD 自动切地盘并记住原档",
                     app._heat_mode == 1
                     and getattr(app, "_heat_mode_before_situation", None) == 0)
        own_n, _ = _w34_items(app)
        sc.assert_ok("HUD 联动后地盘上屏", own_n >= 3)
        sc.step("换谱（do_reset 清空回根）", app.do_reset)
        own_n, pol_n = _w34_items(app)
        sc.assert_ok("回根后只画根节点分析（HUD 联动档、无策略残留）",
                     own_n >= 3 and pol_n == 0,
                     "own=%d pol=%d" % (own_n, pol_n))
        violations = check_post_game_switch(app)
        sc.assert_ok("换谱不变式干净", not violations, str(violations))
        sc.step("换空谱（全新 MoveTree）", lambda: (
            setattr(app, "tree", MoveTree(app.size)),
            app._after_navigate(), app.redraw()))
        own_n, pol_n = _w34_items(app)
        sc.assert_ok("空谱无热力图叠加", (own_n, pol_n) == (0, 0),
                     "own=%d pol=%d" % (own_n, pol_n))
        sc.step("HUD 关闭（回换谱前档位）", app.toggle_situation)
        sc.assert_ok("HUD 关→恢复原档且暂存无残留",
                     app._heat_mode == 0
                     and not hasattr(app, "_heat_mode_before_situation"))
        own_n, pol_n = _w34_items(app)
        sc.assert_ok("终态无热力图残留", (own_n, pol_n) == (0, 0))
        sc.assert_ok("模式全清", app.active_modes() == set(),
                     str(app.active_modes()))
    finally:
        app.client = None
        if app._training and app._training.get("active") \
                and not app._training.get("finished"):
            app._abandon_training_state()
        if app._drill_win is not None:
            try:
                app._close_problem_drill()
            except Exception:
                pass
        app._mistake_review = None
        if app.scoring_mode:
            app.exit_scoring()
        if hasattr(app, "_heat_mode_before_situation"):
            try:
                app.toggle_situation()   # HUD 关 → 恢复原档并删暂存
            except Exception:
                del app._heat_mode_before_situation
        try:   # 恢复进场前的持久化档位（不写脏真实设置）
            app._heat_mode = (HEAT_KEYS.index(orig_key)
                              if orig_key in HEAT_KEYS else 0)
            app.cfg.update(heatmap_mode=orig_key)
        except Exception:
            pass


# ===================== W35 时间轴拖动×训练模式守卫矩阵 =====================

class _MotionEv:
    """<_on_board_motion> 的最小事件桩（只需 x/y 像素坐标）。"""

    def __init__(self, x, y):
        self.x = x
        self.y = y


def scenario_w35_scrubber_mode_guard_matrix(app):
    """F1 修复的场景沉淀（硬规矩）：时间轴拖动层(_scrubber_change)与松手层
    (_scrubber_commit)×四种锁盘态矩阵。

    波5 trigger-flow-auditor 探针实证缺陷：曾 commit 层无守卫——拖动被拦
    提示"不能导航"，松手却把题面拖走（训练窗浮在错位局面上）。修复为两层
    共用 _scrubber_locked_reason。本场景把矩阵转为断言落网：每锁态验证
    拖动不位移 + 松手不位移 + 拦截原因已播报 + 进度条弹回题面（视觉与
    实际不脱节）；对照组：普通态拖/松真实位移。训练/错题/drill 作答用
    状态注入法（守卫只读标志，W8 同款）；官子走真实入口（W32 已覆盖
    点击跳转 _timeline_jump，此处补官子的拖/松两面 + 揭示后/关窗后放行）。
    """
    ah.seed_fixture(app, "analyzed")
    sc = Scenario(app, "W35")
    msgs = []
    orig_set_msg = app._set_msg

    def _spy(text, kind=None):
        msgs.append(str(text))
        return orig_set_msg(text, kind)

    app._set_msg = _spy
    try:
        # ---- 对照组：普通态拖动+松手都真实位移 ----
        app.do_goto_root()
        sc.step("普通态拖动到第2手", lambda: app._scrubber_change(2))
        sc.assert_ok("普通态拖动位移生效", app.tree.current.depth == 2)
        sc.step("普通态松手回根", lambda: app._scrubber_commit(0))
        sc.assert_ok("普通态松手位移生效", app.tree.current.depth == 0)

        # ---- 对照组：普通态 hover 幽灵子正常显示（接力板#10 修复对照）----
        def _hover_probe():
            """构造指向首个空交叉点的 motion 事件并触发 hover 判定。"""
            board = app.tree.current.board
            pt = next((x, y) for y in range(app.size)
                      for x in range(app.size)
                      if board.stone_at(x, y) == 0)
            ev = _MotionEv(app.MARGIN + pt[0] * app.CELL,
                           app.MARGIN + pt[1] * app.CELL)
            app._on_board_motion(ev)
            return pt

        pt_free = _hover_probe()
        sc.assert_ok("普通态 hover 显示幽灵子", app._hover_point == pt_free)
        app._on_board_leave(None)

        def check_locked(label, msg_hint, target=2):
            """锁态下的拖/松双面：节点钉死 + 播报原因 + 进度条回题面。"""
            d0 = app.tree.current.depth
            nid0 = app.tree.current.nid
            msgs.clear()
            sc.step("[%s] 拖动时间轴（应拦）" % label,
                    lambda: app._scrubber_change(target))
            sc.assert_ok("[%s] 拖动层被拦（节点不变）" % label,
                         app.tree.current.depth == d0
                         and app.tree.current.nid == nid0)
            sc.assert_ok("[%s] 拖动层播报拦截原因" % label,
                         any(msg_hint in m for m in msgs))
            sc.step("[%s] 松手（应拦）" % label,
                    lambda: app._scrubber_commit(target))
            sc.assert_ok("[%s] 松手层被拦（节点不变）" % label,
                         app.tree.current.depth == d0
                         and app.tree.current.nid == nid0)
            sc.assert_ok("[%s] 松手后进度条弹回题面" % label,
                         str(app.lbl_scale.cget("text")).startswith(
                             "%d/" % d0), app.lbl_scale.cget("text"))

        # ---- 锁态1：阶段训练（状态注入）----
        app._training = {"active": True, "finished": False,
                         "user_color": "W", "nodes": [], "task": {}}
        check_locked("阶段训练", "阶段训练中")
        app._training = None

        # ---- 锁态2：错题复习测验（状态注入）----
        app._mistake_review = {"active": True, "item": {}, "parent":
                               app.tree.current, "attempts": 0}
        check_locked("错题复习", "复习测验中")
        app._mistake_review = None

        # ---- 锁态3：问题手作答中（quiz 覆盖注入；揭示后放行是设计语义）----
        app._drill_overlay = {"letters": {}}
        app._drill_revealed = False
        pt_quiz = _hover_probe()
        sc.assert_ok("作答中 hover 正常（正要落子作答）",
                     app._hover_point == pt_quiz)
        app._on_board_leave(None)
        check_locked("问题手作答", "问题手作答中")
        app._drill_revealed = True
        _hover_probe()
        sc.assert_ok("drill 揭示锁定态 hover 无幽灵子（不暗示可落子）",
                     app._hover_point is None)
        sc.step("揭示后拖动（应放行）", lambda: app._scrubber_change(2))
        sc.assert_ok("揭示后拖动放行（不过度拦截）",
                     app.tree.current.depth == 2)
        app._drill_overlay = None
        app._drill_revealed = False
        app.do_goto_root()

        # ---- 锁态4：官子训练（真实入口；60 手谱题面锁定）----
        app.tree = _endgame_fixture_tree()
        app._after_navigate()
        app.open_endgame_drill()
        sc.assert_ok("官子训练已开且独占", app._endgame_active()
                     and app.active_modes() == {"endgame"})
        check_locked("官子训练", "官子训练中", target=30)
        # 官子揭示锁定态：点击只有提示，hover 也不得画幽灵子（#10 同款）
        sc.step("官子查看答案", app._endgame_reveal)
        sc.assert_ok("官子已进入揭示态", app._endgame_revealed is True)
        _hover_probe()
        sc.assert_ok("官子揭示锁定态 hover 无幽灵子",
                     app._hover_point is None)
        sc.step("关闭官子训练", app._close_endgame_drill)
        sc.step("关官子后拖动恢复", lambda: app._scrubber_change(10))
        sc.assert_ok("关官子后导航恢复", app.tree.current.depth == 10)

        violations = check_all_unconditional(app)
        sc.assert_ok("矩阵后无残留违规", not violations, str(violations))
    finally:
        app._set_msg = orig_set_msg
        app._training = None
        app._mistake_review = None
        app._drill_overlay = None
        app._drill_revealed = False
        if app._endgame_active():
            app._close_endgame_drill()


# ===================== W36 库记录侧设置 × 快照损坏反馈链 =====================

def scenario_w36_side_setting_snapshot_failure_feedback(app):
    """库右键「设置训练方/画像身份」× 快照损坏/缺失：设置写入索引成功但
    快照侧重算失败时，必须留痕 + 消息如实（"消息不撒谎"家族——此前
    两处 except-pass 让界面照常报成功，用户不知道画像摘要/训练题仍按旧
    配置生成）。

    真实用户旅程：库列表右键旧棋改画像身份/训练方，快照被备份软件占写
    损坏或被外部删除 → 静默成功 = 后续画像/训练拿旧 side 的数据且无处
    追查。
    """
    import io as _io
    import contextlib as _cl
    tmp = tempfile.mkdtemp(prefix="sim_w36_")
    orig = (gl.LIBRARY_DIR, gl.INBOX_DIR, gl.SGF_DIR, gl.PROJECT_DIR,
            gl.INDEX_PATH, gl.PROFILE_CACHE_PATH)
    gl.LIBRARY_DIR = tmp
    gl.INBOX_DIR = os.path.join(tmp, "inbox")
    gl.SGF_DIR = os.path.join(tmp, "sgf")
    gl.PROJECT_DIR = os.path.join(tmp, "projects")
    gl.INDEX_PATH = os.path.join(tmp, "index.json")
    gl.PROFILE_CACHE_PATH = os.path.join(tmp, "profile_cache.json")
    cap = {}
    try:
        ah.seed_fixture(app, "simple")
        # 两盘棋谱文本/内容都不同（入库按 SGF sha1 去重，同文合并成一条）
        games = (("w36a.sgf", "(;GM[1]FF[4]SZ[19];B[dd];W[pp])",
                  ((3, 3), (15, 15))),
                 ("w36b.sgf", "(;GM[1]FF[4]SZ[19];B[dd];W[pp];B[cc])",
                  ((3, 3), (15, 15), (2, 2))))
        recs = []
        for name, sgf_text, moves in games:
            p = os.path.join(tmp, name)
            with open(p, "w", encoding="utf-8") as f:
                f.write(sgf_text)
            t = MoveTree(19)
            for mv in moves:
                t.play(*mv)
            recs.append(gl.add_sgf_to_library(p, sgf_text, t,
                                              rules="chinese", komi=7.5))
        r_ok, r_bad = recs
        # 快照损坏：路径在、JSON 非法 → load_project 抛错走失败分支
        with open(r_bad.get("projectPath"), "w", encoding="utf-8") as f:
            f.write("{corrupted by w36")

        def _select(rec):
            iid = next(i for i, r in app._lib_map.items()
                       if r.get("id") == rec.get("id"))
            app._lib_tv.selection_set(iid)

        def _run_quiet(fn):
            buf = _io.StringIO()
            with _cl.redirect_stderr(buf):
                fn()
            cap["stderr"] = buf.getvalue()

        def _msg():
            return str(app.lbl_msg.cget("text"))

        sc = Scenario(app, "W36")
        sc.step("开棋谱库窗口", app.open_game_library)
        sc.assert_ok("库窗口已开（2 条记录）",
                     app._lib_tv is not None and len(app._lib_map) == 2,
                     str(len(app._lib_map)))
        sc.assert_ok("无当前棋局绑定（走非当前记录分支）",
                     app._library_record_id not in (r.get("id") for r in recs),
                     str(app._library_record_id))

        # 1) 坏快照 × 画像身份：设置落库 + 消息如实 + stderr 留痕
        sc.step("坏快照设画像身份", lambda: _run_quiet(
            lambda: (_select(r_bad), app._set_selected_profile_side("B"))))
        m = _msg()
        sc.assert_ok("画像身份：消息如实（承认摘要未重算）",
                     "已设置画像身份" in m and "未能重算" in m, m)
        sc.assert_ok("画像身份：失败留痕 stderr",
                     "[warn]" in cap.get("stderr", "")
                     and "读取快照失败" in cap.get("stderr", ""),
                     cap.get("stderr", ""))
        sc.assert_ok("画像身份：设置本身已落库",
                     (gl.get_record(r_bad.get("id")) or {}).get("profileSide")
                     == "B")

        # 2) 坏快照 × 训练方：同款三断言
        sc.step("坏快照设训练方", lambda: _run_quiet(
            lambda: (_select(r_bad), app._set_selected_training_side("W"))))
        m = _msg()
        sc.assert_ok("训练方：消息如实（承认训练题未重算）",
                     "已设置训练方" in m and "未能按新训练方重算" in m, m)
        sc.assert_ok("训练方：失败留痕 stderr",
                     "[warn]" in cap.get("stderr", ""), cap.get("stderr", ""))
        sc.assert_ok("训练方：设置本身已落库",
                     (gl.get_record(r_bad.get("id")) or {}).get("playerColor")
                     == "W")

        # 3) 好快照 × 双设置：成功路径消息干净、无 [warn] 噪声
        sc.step("好快照设画像身份", lambda: _run_quiet(
            lambda: (_select(r_ok), app._set_selected_profile_side("both"))))
        m = _msg()
        sc.assert_ok("好快照：画像身份报成功且无失败尾巴",
                     "已设置画像身份" in m and "未能" not in m, m)
        sc.assert_ok("好快照：stderr 无 [warn]",
                     "[warn]" not in cap.get("stderr", ""), cap.get("stderr", ""))
        sc.step("好快照设训练方", lambda: _run_quiet(
            lambda: (_select(r_ok), app._set_selected_training_side("B"))))
        m = _msg()
        sc.assert_ok("好快照：训练方报成功且无失败尾巴",
                     "已设置训练方" in m and "未能" not in m, m)

        # 4) 快照缺失（文件被外部删除）× 画像身份：无声分支同样消息如实
        os.remove(r_ok.get("projectPath"))
        sc.step("快照缺失设画像身份", lambda: _run_quiet(
            lambda: (_select(r_ok), app._set_selected_profile_side("W"))))
        m = _msg()
        sc.assert_ok("快照缺失：消息如实（无异常也承认未重算）",
                     "已设置画像身份" in m and "未能重算" in m, m)
        sc.assert_ok("快照缺失：设置仍落库",
                     (gl.get_record(r_ok.get("id")) or {}).get("profileSide")
                     == "W")

        violations = check_all_unconditional(app)
        sc.assert_ok("场景后无不变式违规", not violations, str(violations))
    finally:
        gl.LIBRARY_DIR, gl.INBOX_DIR, gl.SGF_DIR, gl.PROJECT_DIR, \
            gl.INDEX_PATH, gl.PROFILE_CACHE_PATH = orig
        try:
            app._close_library_window()
        except Exception:
            pass
        app._library_bg_recent = set(getattr(app, "_library_bg_recent", set()))


# ===================== W37 官子训练×真实库长局全链 =====================

_W37_LETTERS = "abcdefghijklmnopqrs"


def _w37_sgf_text(pb, pw):
    """60 手散点长局（与 _endgame_fixture_tree 同型）写成真实 SGF 文本。"""
    moves = []
    for i in range(60):
        x, y = 1 + (i % 6) * 3, 1 + i // 6
        moves.append("%s[%s%s]" % ("B" if i % 2 == 0 else "W",
                                   _W37_LETTERS[x], _W37_LETTERS[y]))
    return "(;GM[1]FF[4]SZ[19]KM[7.5]PB[%s]PW[%s];%s)" % (
        pb, pw, ";".join(moves))


def _w37_inject_analysis(app):
    """给当前主线注入终局段分析（与 fixture 同构 + 两点扩展）：
    第 55 手（黑）候选 C2/Q16/A1；第 56 手（白）候选 B9/C3——C3 是
    散点谱上确定空点（x∈{1,4,7,10,13,16}/y∈1..11 均不含），使第二题
    可真实落子作答（fixture 唯一候选 B9 落在被第 55 手占据的点）。"""
    from review import ReviewReport
    line = ReviewReport(app.tree).mainline_nodes()

    def _ami(move, sl, order, pv=None):
        return {"move": move, "order": order, "winrate": 0.5,
                "scoreLead": sl, "visits": 1000, "prior": 0.2,
                "pv": pv or [move]}

    def _ana(sl, mis):
        return {"rootInfo": {"scoreLead": sl, "winrate": 0.5},
                "moveInfos": mis}

    for node in line:
        node.analysis = _ana(0.0, [_ami("Q16", 0.5, 0), _ami("D4", -0.2, 1)])
    line[54].analysis = _ana(0.0, [_ami("C2", 2.0, 0, pv=["C2", "Q16", "C2"]),
                                   _ami("Q16", 0.5, 1), _ami("A1", -4.0, 2)])
    # 白方视角：mover_score = -scoreLead，C3(-1.5) 比 B9(-3.0) 对白差 1.5 目
    line[55].analysis = _ana(-3.0, [_ami("B9", -3.0, 0), _ami("C3", -1.5, 1)])
    return line


def scenario_w37_real_library_endgame_drill(app):
    """W37 官子训练×真实库长局全链（预约补做：官子功能的真实库覆盖缺口）。

    官子链此前只有合成 fixture：W31/W32 直接换 tree（无库记录）、
    test_ui_smoke 用假 record_id —— 真实库 id 驱动的 LearningEvent 回写
    从未被仿真过。本场景全链：库重定向防污染 → 导入 60 手真实 SGF
    （解析+入库拿到真实记录 id）→ 注入终局段分析 → 进官子出 2 题 →
    多题连做（错 A1 / 对 C3 双分支逐题落库）→ do_reset 换谱统一清理
    → 第二盘入库重开 → 作答不串局（新旧 game_id 事件各归各局）。
    """
    import tkinter.filedialog as _fd
    import learning_store as _ls
    from learning_event import KIND_ENDGAME_DRILL as _EG_KIND
    from movetree import point_to_xy
    tmp = tempfile.mkdtemp(prefix="sim_w37_")
    orig = (gl.LIBRARY_DIR, gl.INBOX_DIR, gl.SGF_DIR, gl.PROJECT_DIR,
            gl.INDEX_PATH, gl.PROFILE_CACHE_PATH)
    gl.LIBRARY_DIR = tmp
    gl.INBOX_DIR = os.path.join(tmp, "inbox")
    gl.SGF_DIR = os.path.join(tmp, "sgf")
    gl.PROJECT_DIR = os.path.join(tmp, "projects")
    gl.INDEX_PATH = os.path.join(tmp, "index.json")
    gl.PROFILE_CACHE_PATH = os.path.join(tmp, "profile_cache.json")
    _ls.set_path(os.path.join(tmp, "learning_events.json"))
    fd_orig = _fd.askopenfilename
    real_client = app.client
    sc = Scenario(app, "W37")
    try:
        ah.clean(app)
        # ---- 段1：导入第一盘 60 手长局（真实解析 + 入库 + 真实 id）----
        p1 = os.path.join(tmp, "w37_game1.sgf")
        with open(p1, "w", encoding="utf-8") as f:
            f.write(_w37_sgf_text("W37黑一", "W37白一"))
        _fd.askopenfilename = lambda **k: p1
        app.client = SgfScanClient()   # after(300) 自动快扫不 spawn 真引擎
        sc.step("导入第一盘真实 SGF", app.do_import_sgf)
        sc.assert_ok("主线 60 手已解析", app.tree.current.depth == 60,
                     str(app.tree.current.depth))
        gid1 = app._library_record_id
        sc.assert_ok("拿到真实库记录 id", bool(gid1), str(gid1))
        sc.assert_ok("记录可从重定向库读回",
                     (gl.get_record(gid1) or {}).get("name") is not None)
        sc.assert_ok("棋谱落进重定向库（生产库零污染）",
                     os.path.isfile(os.path.join(gl.SGF_DIR,
                                                 "%s.sgf" % gid1)))

        # ---- 段2：注入终局段分析 → 自动快扫被既有分析短路 ----
        line = _w37_inject_analysis(app)
        sc.step("泵 0.4s（导入挂的 after(300) 自动快扫）",
                lambda: advance(app, 0.4))
        analyzed = sum(1 for nd in line if nd.analysis is not None)
        sc.assert_ok("注入分析保持完整（快扫零重发）",
                     analyzed == len(line), "%d/%d" % (analyzed, len(line)))

        # ---- 段3：真实长局进官子：出 2 题 ----
        sc.step("开官子训练", app.open_endgame_drill)
        sc.assert_ok("真实长局出题 2 道",
                     app._endgame_set is not None
                     and len(app._endgame_set.problems) == 2,
                     str(app._endgame_set
                         and len(app._endgame_set.problems)))
        d0 = app._endgame_set.problems[0]
        sc.assert_ok("首题第 55 手目损收束（与 fixture 同构）",
                     d0.move_number == 55 and d0.best_move == "C2",
                     "%s/%s" % (d0.move_number, d0.best_move))

        # ---- 段4：多题连做：候选外不消耗 → 错/对双分支逐题落库 ----
        sc.step("候选外选点", lambda: app._endgame_free_answer(0, 0))
        sc.assert_ok("候选外不消耗作答",
                     app._endgame_result["answered"] == 0)
        ax, ay = point_to_xy("A1", app.size)
        sc.step("第 1 题作答 A1（三选·判错）",
                lambda: app._endgame_free_answer(ax, ay))
        a0 = app._endgame_result["answers"].get(55)
        sc.assert_ok("第 1 题判 bad 不计对",
                     a0 is not None and not a0["isCorrect"]
                     and a0["grade"]["assessment"] == "bad", str(a0))
        evts = _ls.get_events_by_game(gid1, kind=_EG_KIND)
        sc.assert_ok("第 1 题事件以真实 id 落库",
                     len(evts) == 1 and evts[0].move_no == 55, str(len(evts)))
        sc.step("下一题", app._endgame_next)
        sc.assert_ok("第 2 题题面就位（未揭示）",
                     not app._endgame_revealed and app._endgame_index == 1)
        cx, cy = point_to_xy("C3", app.size)
        sc.step("第 2 题作答 C3（二选·acceptable）",
                lambda: app._endgame_free_answer(cx, cy))
        sc.assert_ok("连做计数错对分明（2 答 1 对）",
                     app._endgame_result["answered"] == 2
                     and app._endgame_result["correct"] == 1,
                     str((app._endgame_result["answered"],
                          app._endgame_result["correct"])))
        evts = _ls.get_events_by_game(gid1, kind=_EG_KIND)
        sc.assert_ok("两题事件均落库（kind 双闸隔离）",
                     len(evts) == 2 and [e.move_no for e in evts] == [55, 56],
                     str([e.move_no for e in evts]))

        # ---- 段5：换谱（do_reset 统一清理链）→ 第二盘入库重开不串局 ----
        sc.step("do_reset 换谱", app.do_reset)
        sc.assert_ok("官子窗随换谱关闭零残留", app._endgame_win is None
                     and app._endgame_set is None
                     and app._endgame_result is None)
        violations = check_post_game_switch(app)
        sc.assert_ok("换谱不变式干净", not violations, str(violations))
        p2 = os.path.join(tmp, "w37_game2.sgf")
        with open(p2, "w", encoding="utf-8") as f:
            f.write(_w37_sgf_text("W37黑二", "W37白二"))
        _fd.askopenfilename = lambda **k: p2
        sc.step("导入第二盘", app.do_import_sgf)
        gid2 = app._library_record_id
        sc.assert_ok("第二盘新记录 id（内容哈希分叉）",
                     bool(gid2) and gid2 != gid1, "%s vs %s" % (gid2, gid1))
        _w37_inject_analysis(app)
        sc.step("第二盘重开官子", app.open_endgame_drill)
        sc.assert_ok("重开全新会话（计数归零）",
                     app._endgame_result == {"answered": 0, "correct": 0,
                                             "answers": {}})
        cx, cy = point_to_xy("C2", app.size)
        sc.step("第二盘作答一题（C2 一选）",
                lambda: app._endgame_free_answer(cx, cy))
        evts1 = _ls.get_events_by_game(gid1, kind=_EG_KIND)
        evts2 = _ls.get_events_by_game(gid2, kind=_EG_KIND)
        sc.assert_ok("第一盘事件仍 2 条（不被第二盘追加）", len(evts1) == 2,
                     str(len(evts1)))
        sc.assert_ok("第二盘事件独立 1 条（不串局）", len(evts2) == 1
                     and evts2[0].move_no == 55, str(len(evts2)))
        sc.assert_ok("全库官子事件恰 3 条（kind 过滤净）",
                     len(_ls.get_events(kind=_EG_KIND)) == 3)

        violations = check_all_unconditional(app)
        sc.assert_ok("场景后无残留违规", not violations, str(violations))
    finally:
        if app.__dict__.get("_endgame_win") is not None:
            app._close_endgame_drill()
        gl.LIBRARY_DIR, gl.INBOX_DIR, gl.SGF_DIR, gl.PROJECT_DIR, \
            gl.INDEX_PATH, gl.PROFILE_CACHE_PATH = orig
        _ls.set_path(None)
        _fd.askopenfilename = fd_orig
        app.client = real_client
        app._library_record_id = None
        shutil.rmtree(tmp, ignore_errors=True)


# ===================== W38 备份恢复 UI 全链 =====================

def scenario_w38_backup_restore_ui(app):
    """W38 备份恢复 UI 全链：备份窗口列表（日期/局数/完好性）→ 未选中如实
    提示 → 二次确认取消（库不动）→ 确认恢复（库+设置回到备份时刻、
    pre_restore 转存破坏现场、历史备份不丢）→ 损坏备份被拒（原库不动）
    → 关窗清引用。

    恢复是覆盖级操作：任何拒绝路径（未选/取消/损坏）现场必须原样，
    成功路径必须可验证地回去——"消息不撒谎"家族（接力板 #13：备份有
    产出无恢复入口，数据安全闭环缺一半）。
    """
    import tkinter.messagebox as _mb
    tmp = tempfile.mkdtemp(prefix="sim_w38_")
    orig = (bk.LIBRARY_DIR, bk.BACKUP_DIR, bk.SETTINGS_PATH)
    bk.LIBRARY_DIR = os.path.join(tmp, "game_library")
    bk.BACKUP_DIR = os.path.join(bk.LIBRARY_DIR, "backups")
    bk.SETTINGS_PATH = os.path.join(tmp, "user_settings.json")
    real_ask, real_info, real_err = _mb.askyesno, _mb.showinfo, _mb.showerror
    confirm = {"ret": True}
    asks, infos, errors = [], [], []
    _mb.askyesno = lambda *a, **k: (asks.append(a), confirm["ret"])[1]
    _mb.showinfo = lambda *a, **k: infos.append(a)
    _mb.showerror = lambda *a, **k: errors.append(a)

    def _state():
        idx = json.loads(open(os.path.join(bk.LIBRARY_DIR, "index.json"),
                              encoding="utf-8").read())
        return {
            "sgf": os.path.isfile(os.path.join(bk.LIBRARY_DIR, "sgf",
                                               "w33.sgf")),
            "games": len(idx.get("records") or []),
            "visits": json.loads(open(bk.SETTINGS_PATH,
                                      encoding="utf-8").read())
            .get("max_visits"),
        }

    try:
        sc = Scenario(app, "W38")
        sc.assert_ok("设置页入口已接线（open_backup_manager）",
                     callable(getattr(app, "open_backup_manager", None)))

        # ---- 备份时刻：2 局棋谱 + 200 visits ----
        _w33_make_library(tmp)
        with open(os.path.join(bk.LIBRARY_DIR, "index.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"version": 1,
                       "records": [{"id": "w38-1"}, {"id": "w38-2"}]}, f)
        zip_path = bk.create_daily_backup()
        sc.assert_ok("前置：备份已产出", bool(zip_path)
                     and os.path.isfile(zip_path))

        # ---- 破坏现场：棋谱没了、库变 3 局、设置被改 ----
        os.remove(os.path.join(bk.LIBRARY_DIR, "sgf", "w33.sgf"))
        with open(os.path.join(bk.LIBRARY_DIR, "index.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"version": 1,
                       "records": [{"id": "x-%d" % i} for i in range(3)]}, f)
        with open(bk.SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump({"max_visits": 999}, f)
        broken = _state()
        sc.assert_ok("破坏现场就绪（3 局/无棋谱/999）",
                     broken == {"sgf": False, "games": 3, "visits": 999},
                     str(broken))

        # ---- 开窗：列表如实展示（今日日期/2 局/完好）----
        sc.step("开备份与恢复窗口", app.open_backup_manager)
        tv = app._backup_mgr_tv
        rows = tv.get_children() if tv else ()
        sc.assert_ok("窗口与列表就绪且单条备份",
                     app._backup_mgr_win is not None and len(rows) == 1)
        vals = tv.item(rows[0], "values") if rows else ()
        stamp = datetime.date.today().strftime("%Y-%m-%d")
        sc.assert_ok("列表展示日期/局数/完好",
                     len(vals) == 4 and vals[0] == stamp and vals[2] == "2"
                     and vals[3] == "完好", str(vals))

        # ---- 未选中：如实提示、现场不动 ----
        sc.step("未选中直接恢复", app._restore_selected_backup)
        sc.assert_ok("未选中提示先选备份",
                     "请先" in str(app.lbl_msg.cget("text")))
        sc.assert_ok("未选中路径现场不动", _state() == broken)

        # ---- 选中但二次确认取消：现场不动 ----
        tv.selection_set(rows[0])
        confirm["ret"] = False
        sc.step("二次确认取消", app._restore_selected_backup)
        sc.assert_ok("取消路径弹过确认", len(asks) == 1, str(len(asks)))
        sc.assert_ok("取消路径未恢复（现场不动）", _state() == broken)
        sc.assert_ok("取消路径无成功弹窗", not infos)

        # ---- 确认恢复：库+设置回到备份时刻、pre_restore 存破坏现场 ----
        confirm["ret"] = True
        sc.step("确认恢复备份", app._restore_selected_backup)
        after = _state()
        sc.assert_ok("库回到备份时刻（棋谱在/2 局/200）",
                     after == {"sgf": True, "games": 2, "visits": 200},
                     str(after))
        sc.assert_ok("成功弹窗提示重启刷新内存态",
                     len(infos) == 1 and any("重启" in str(a) for a in infos),
                     str(infos)[:80])
        pres = [n for n in os.listdir(tmp)
                if n.startswith("game_library.pre_restore-")]
        sc.assert_ok("pre_restore 转存破坏现场（3 局）",
                     len(pres) >= 1 and len(json.loads(open(os.path.join(
                         tmp, pres[-1], "index.json"),
                         encoding="utf-8").read())["records"]) == 3,
                     str(pres))
        sc.assert_ok("恢复后窗口关闭且引用清",
                     app._backup_mgr_win is None and app._backup_mgr_tv is None
                     and app._backup_mgr_map == {})
        sc.assert_ok("历史备份 zip 不因恢复丢失",
                     os.path.isfile(os.path.join(
                         bk.BACKUP_DIR, os.path.basename(zip_path))))

        # ---- 损坏备份：列表标坏 → 恢复被拒、现场不动 ----
        with open(zip_path, "wb") as f:
            f.write(b"not a zip anymore (w38)")
        sc.step("重开窗口（备份已损坏）", app.open_backup_manager)
        tv = app._backup_mgr_tv
        rows = tv.get_children() if tv else ()
        vals = tv.item(rows[0], "values") if rows else ()
        sc.assert_ok("损坏备份在列表标「损坏」",
                     vals and vals[3] == "损坏", str(vals))
        tv.selection_set(rows[0])
        sc.step("恢复损坏备份被拒", app._restore_selected_backup)
        sc.assert_ok("损坏备份明确报错不恢复",
                     len(errors) == 1 and _state() == after, str(errors)[:60])
        sc.step("关备份窗口", app._close_backup_manager)
        sc.assert_ok("关窗后引用清",
                     app._backup_mgr_win is None and app._backup_mgr_tv is None)

        violations = check_all_unconditional(app)
        sc.assert_ok("场景后无不变式违规", not violations, str(violations))
    finally:
        bk.LIBRARY_DIR, bk.BACKUP_DIR, bk.SETTINGS_PATH = orig
        _mb.askyesno, _mb.showinfo, _mb.showerror = \
            real_ask, real_info, real_err
        try:
            app._close_backup_manager()
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)


# ===================== 编排 =====================

def run():
    # 队列持久化文件保护：真实队列可能含待跑任务，headless 下 kick 会尝试
    # 启动真引擎。先备份并置空，结束恢复。
    qpath = os.path.join(HERE, "game_library", "analysis_queue.json")
    backup = None
    if os.path.exists(qpath):
        backup = qpath + ".bak_sim"
        shutil.copy2(qpath, backup)
        try:
            with open(qpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["tasks"] = []
            with open(qpath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception:
            pass
    scenarios = [
        ("W1 复盘闭环", scenario_w1_review_loop),
        ("W2 窗口轰炸", scenario_w2_window_bomb),
        ("W3 连打与边界", scenario_w3_toggle_spam),
        ("W4 队列让路", scenario_w4_queue_yields),
        ("W5 分析回流+切谱", scenario_w5_result_then_switch),
        ("W6 桩引擎队列并发", scenario_w6_fake_engine_queue),
        ("W7 陈旧结果防护", scenario_w7_stale_result_guard),
        ("W8 键盘×模式全矩阵", scenario_w8_keyboard_mode_matrix),
        ("W9 真实SGF全链", scenario_w9_real_sgf_full_chain),
        ("W10 训练预取回流竞态", scenario_w10_training_prefetch_race),
        ("W11 引擎生命周期挂账清理", scenario_w11_engine_lifecycle_pending_cleanup),
        ("W12 棋谱库×换谱谱系", scenario_w12_library_open_chains),
        ("W13 drill×批量队列交错", scenario_w13_drill_queue_interleave),
        ("W14 坏输入与异常路径", scenario_w14_bad_input_paths),
        ("W15 完整训练闭环", scenario_w15_training_closed_loop),
        ("W16 错题复习闭环", scenario_w16_mistake_review_closed_loop),
        ("W17 队列跨实例持久化", scenario_w17_queue_persistence),
        ("W18 前台整盘×曲线实时刷新", scenario_w18_graph_live_batch),
        ("W19 引擎死亡×队列续跑", scenario_w19_engine_death_queue_resume),
        ("W20 曲线×导航双向联动", scenario_w20_graph_nav_linkage),
        ("W21 库记录自动快扫（crash 回归）", scenario_w21_library_auto_quick_scan),
        ("W22 训练报告窗口渲染（crash 回归）", scenario_w22_training_report_window),
        ("W23 在线导入全链", scenario_w23_online_import_chain),
        ("W24 V6页面路由×模式互斥", scenario_w24_v6_page_router_modes),
        ("W25 批量队列×前台导航交错", scenario_w25_queue_foreground_nav_interleave),
        ("W26 复习中启动训练/drill被拦", scenario_w26_review_blocks_training_entry),
        ("W27 教练解读窗口×导航交错", scenario_w27_coach_window_interleave),
        ("W28 分栏拖动×棋盘自适应", scenario_w28_sash_drag_board_fit),
        ("W29 画像/棋风窗口×批量队列交错", scenario_w29_profile_style_queue_interleave),
        ("W30 配置热切换×缓存/队列/候选联动", scenario_w30_config_hot_switch),
        ("W31 引擎死亡×后台挂账×官子训练", scenario_w31_engine_death_bg_endgame),
        ("W32 换谱中断×官子训练互斥清理", scenario_w32_endgame_game_switch_interrupt),
        ("W33 备份恢复链路", scenario_w33_backup_restore_chain),
        ("W34 热力图×导航×模式交错", scenario_w34_heatmap_nav_mode_interleave),
        ("W35 时间轴拖动×训练模式守卫矩阵", scenario_w35_scrubber_mode_guard_matrix),
        ("W36 库侧设置×快照损坏反馈链", scenario_w36_side_setting_snapshot_failure_feedback),
        ("W37 官子训练×真实库长局全链", scenario_w37_real_library_endgame_drill),
        ("W38 备份恢复UI全链", scenario_w38_backup_restore_ui),
    ]
    failed = []
    app = ah.make_headless_app()
    try:
        for name, fn in scenarios:
            # 场景可能换实例（W17 重启仿真）：每轮从 harness 单例重取
            app = getattr(ah, "_app_instance", None) or app
            ah.clean(app)
            try:
                fn(app)
                print("▶ %s：通过" % name)
            except AssertionError as e:
                failed.append(name)
                print("▶ %s：失败\n%s" % (name, e))
            except Exception as e:
                failed.append(name)
                print("▶ %s：崩溃 %r" % (name, e))
            finally:
                # 场景自装的桩引擎/注入的训练态不外溢到后续场景
                app.client = None
                app._training = None
    finally:
        ah.destroy_app()
        if backup and os.path.exists(backup):
            shutil.move(backup, qpath)
    print()
    if failed:
        print("失败场景：%s" % "、".join(failed))
        raise AssertionError("仿真场景失败：%s" % failed)
    print("test_workflow_sim 全部场景通过 ✅")


if __name__ == "__main__":
    run()
