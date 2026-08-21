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
import game_library as gl
from analysis_queue import AnalysisQueue
from movetree import MoveTree


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
