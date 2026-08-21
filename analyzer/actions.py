"""动作原子表：对抗检测的操作单元。

每个动作是 Action(name, seed, apply_fn)：
- name：动作名（报告用）
- seed：前置 fixture（None=无需前置，"blunder"/"analyzed"/"simple"=需装配对应树）
- apply_fn(app)：执行动作（含 fixture 装配），返回 "applied" 或 "blocked"

设计要点：
- 每个动作自己装配 fixture（seed 非 None 时先 seed_fixture），保证可独立执行
- apply_fn 捕获异常，返回 "blocked"（被守卫拦）或 "applied"（成功执行）
- 动作不假设前置状态——seed_fixture 会先 clean 再装配，保证确定性
"""
from adversarial_harness import seed_fixture


class Action:
    """动作原子。

    category 决定执行后检查哪组不变式：
    - "normal"：只查互斥不变式（每步都查）
    - "switch"：额外查换棋谱不变式（do_reset/switch_game 后应清空所有临时模式）
    - "close"：额外查窗口生命周期不变式（close_drill/exit_scoring 后引用应清）
    - "entry"：额外查入口拦截不变式（注入 mistake_review/auto_play 后调模式入口，
      入口守卫必须拦下或先停冲突状态——I19/I20 回归网）
    """
    __slots__ = ("name", "seed", "apply", "category")

    def __init__(self, name, seed, apply_fn, category="normal"):
        self.name = name
        self.seed = seed        # None 或 fixture kind
        self.apply = apply_fn
        self.category = category


def _try(fn):
    """执行 fn，返回 "applied"（无异常）或 "blocked"（抛异常视为被拦/前置不满足）。"""
    try:
        fn()
        return "applied"
    except Exception:
        return "blocked"


# ===================== 动作原子（15 个：12 基础 + 3 入口拦截回归） =====================

ACTIONS = [
    Action("play", "simple", lambda app: (
        seed_fixture(app, "simple"),
        _try(lambda: app.play(3, 3)),
    )),
    Action("do_takeback", "simple", lambda app: (
        seed_fixture(app, "simple"),
        _try(lambda: app.play(3, 3)),
        _try(lambda: app.do_takeback()),
    )),
    Action("do_redo", "simple", lambda app: (
        seed_fixture(app, "simple"),
        _try(lambda: app.play(3, 3)),
        _try(lambda: app.do_takeback()),
        _try(lambda: app.do_redo()),
    )),
    Action("do_reset", "simple", lambda app: (
        seed_fixture(app, "simple"),
        _try(lambda: app.play(3, 3)),
        _try(lambda: app.do_reset()),
    ), category="switch"),
    Action("do_pass", "simple", lambda app: (
        seed_fixture(app, "simple"),
        _try(lambda: app.do_pass()),
    )),
    Action("enter_scoring", "simple", lambda app: (
        seed_fixture(app, "simple"),
        _try(lambda: app.enter_scoring()),
    )),
    Action("exit_scoring", "simple", lambda app: (
        seed_fixture(app, "simple"),
        _try(lambda: app.enter_scoring()),
        _try(lambda: app.exit_scoring()),
    ), category="close"),
    Action("toggle_pv", "simple", lambda app: (
        seed_fixture(app, "analyzed"),
        _try(lambda: app.toggle_pv()),
    )),
    # drill 需 blunder fixture（有问题手才能建 drill）
    Action("open_drill", "blunder", lambda app: (
        seed_fixture(app, "blunder"),
        _try(lambda: app.open_problem_drill()),
    )),
    Action("close_drill", "blunder", lambda app: (
        seed_fixture(app, "blunder"),
        _try(lambda: app.open_problem_drill()),
        _try(lambda: app._close_problem_drill()),
    ), category="close"),
    # enter_scoring + toggle_situation（测试点目与 HUD 共存）
    Action("toggle_situation", "simple", lambda app: (
        seed_fixture(app, "simple"),
        _try(lambda: app.toggle_situation()),
    )),
    # 换棋谱（用 do_reset 模拟，因为它不依赖文件系统）
    Action("switch_game", "blunder", lambda app: (
        seed_fixture(app, "blunder"),
        _try(lambda: app.do_reset()),
    ), category="switch"),
    # ---- 入口拦截回归（第一波 bug A/B 补转化，硬规矩第 0 条）----
    # 复习态注入按 app.py:_start_selected_mistake_review 的真实 dict 形状构造，
    # 守卫只读 .get("active")，其余键供 redraw 分支安全兜底。
    Action("entry_training_in_review", "blunder", lambda app: (
        seed_fixture(app, "blunder"),
        setattr(app, "_mistake_review",
                {"active": True, "item": {}, "parent": None, "attempts": 0}),
        _try(lambda: app._start_stage_training({
            "startNodeMove": 0, "playerColor": "B", "targetMoves": 10,
            "phase": "opening", "startMove": 1})),
    ), category="entry"),
    Action("entry_drill_in_review", "blunder", lambda app: (
        seed_fixture(app, "blunder"),
        setattr(app, "_mistake_review",
                {"active": True, "item": {}, "parent": None, "attempts": 0}),
        _try(lambda: app.open_problem_drill()),
    ), category="entry"),
    Action("entry_drill_autoplay", "blunder", lambda app: (
        seed_fixture(app, "blunder"),
        setattr(app, "_auto_play", True),   # 模拟自动播放运行中（无 after job）
        _try(lambda: app.open_problem_drill()),
    ), category="entry"),
]


def action_names():
    """返回所有动作名列表。"""
    return [a.name for a in ACTIONS]
