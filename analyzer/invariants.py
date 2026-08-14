"""状态不变式：对抗检测的检查基准。

每条不变式是 def inv_IX(app) -> (ok: bool, msg: str)，只读 app 状态，不调 redraw。
所有不变式基于对 app.py 实际守卫和 redraw 分支的阅读（带行号证据）。

分类：
- I1-I4  互斥不变式（独占模式两两不能同时 active）
- I5-I5c 换棋谱不变式（换棋谱后所有临时模式必须 False/None，缓存全清）
- I6-I11 Canvas 图层不变式（互斥图层不能同屏）
- I12-I15 导航拦截不变式（各模式下破坏性动作不改 tree）
- I16-I18 窗口生命周期不变式（关闭后引用全清）
"""
from adversarial_harness import snapshot_modes, canvas_marker_counts, _safe_drill_active


def _training_active(app):
    tr = getattr(app, "_training", None)
    return bool(tr and tr.get("active") and not tr.get("finished"))


def _mistake_active(app):
    mr = getattr(app, "_mistake_review", None)
    return bool(mr and mr.get("active"))


# ===================== 互斥不变式 =====================

def inv_I1(app):
    """scoring_mode 与 training active 不能同时为真。"""
    if app.scoring_mode and _training_active(app):
        return False, "scoring_mode=True 时 training 仍 active"
    return True, ""


def inv_I2(app):
    """scoring_mode 与 drill active 不能同时为真。"""
    if app.scoring_mode and _safe_drill_active(app):
        return False, "scoring_mode=True 时 drill 仍 active"
    return True, ""


def inv_I3(app):
    """training active 与 drill active 不能同时为真。"""
    if _training_active(app) and _safe_drill_active(app):
        return False, "training active 时 drill 仍 active"
    return True, ""


def inv_I4(app):
    """独占模式（scoring/training/drill）激活时 mistake_review 不能 active。"""
    exclusive = (app.scoring_mode or _training_active(app) or _safe_drill_active(app))
    if exclusive and _mistake_active(app):
        return False, "独占模式激活时 mistake_review 仍 active"
    return True, ""


def inv_I13(app):
    """统一互斥：active_modes() 至多返回 1 个元素（替代手写两两 I1-I4，防未来加第五模式漏查）。"""
    modes = app.active_modes()
    if len(modes) > 1:
        return False, "多个独占模式同时激活: %s" % ", ".join(sorted(modes))
    return True, ""


# ===================== 换棋谱不变式 =====================
# 这些不变式只在"换棋谱/重置"类动作后检查（由 verifier 控制），
# 不能每步都查——因为 enter_scoring/open_drill 等动作本就该留下 active 状态。

def inv_I5(app):
    """换棋谱完成后所有临时模式必须 False/None。

    仅在 do_import_sgf/_load_project_from_path/do_reset 后检查。
    """
    snap = snapshot_modes(app)
    violations = []
    if snap["scoring_mode"]:
        violations.append("scoring_mode")
    if snap["training_active"]:
        violations.append("training_active")
    if snap["drill_active"]:
        violations.append("drill_active")
    if snap["mistake_review_active"]:
        violations.append("mistake_review_active")
    if snap["show_pv"]:
        violations.append("show_pv")
    if violations:
        return False, "换棋谱后残留: " + ", ".join(violations)
    return True, ""


def inv_I5b(app):
    """换棋谱后训练缓存必须全清（prefetch/deferred/active_cache）。"""
    if getattr(app, "_training_prefetch_cache", None):
        return False, "_training_prefetch_cache 未清"
    if getattr(app, "_training_deferred_nodes", None):
        return False, "_training_deferred_nodes 未清"
    if getattr(app, "_active_training_cache", None) is not None:
        return False, "_active_training_cache 未清"
    return True, ""


def inv_I5c(app):
    """换棋谱后复盘缓存必须清空（loss_val/quality_result/review_map/batch）。"""
    if getattr(app, "_current_loss_val", None) is not None:
        return False, "_current_loss_val 未清"
    if getattr(app, "_current_quality_result", None) is not None:
        return False, "_current_quality_result 未清"
    if getattr(app, "_batch_total", 0) != 0:
        return False, "_batch_total 未归零"
    return True, ""


def inv_I14(app):
    """换棋谱后 nid 缓存必须归零（防新树 nid 误命中旧节点）。

    _double_pass_prompted / _scoring_suggestion_prompted 按 nid 记录"已提示过"，
    新树 nid 从 0 重新计数，残留会导致错误抑制（双 pass 弹窗/死子建议在错误节点触发）。
    """
    if getattr(app, "_double_pass_prompted", None) is not None:
        return False, "_double_pass_prompted 未清（nid 缓存残留）"
    if getattr(app, "_scoring_suggestion_prompted", None) is not None:
        return False, "_scoring_suggestion_prompted 未清（nid 缓存残留）"
    return True, ""


# ===================== Canvas 图层不变式 =====================

def inv_I6(app):
    """scoring_mode=True 时 canvas 不能同时出现 candidate-marker 和 scoring 标记。"""
    if not app.scoring_mode:
        return True, ""
    counts = canvas_marker_counts(app)
    if counts.get("candidate-marker", 0) > 0:
        return False, "点目模式下仍画了 candidate-marker"
    return True, ""


def inv_I7(app):
    """show_pv=True 时不能画 candidate-marker（主变标号替代候选）。"""
    if not getattr(app, "_show_pv", False):
        return True, ""
    counts = canvas_marker_counts(app)
    if counts.get("candidate-marker", 0) > 0 and counts.get("pv-marker", 0) > 0:
        return False, "show_pv 时 candidate 和 pv 标记同屏"
    return True, ""


def inv_I8(app):
    """盲测模式（drill 未揭示 / 错题复习 active）下不能有 pv-marker（防泄题）。

    主变第一步正是 AI 首选，画出来等于公布答案。
    """
    drill_blind = (_safe_drill_active(app) and not getattr(app, "_drill_revealed", False))
    mr_active = _mistake_active(app)
    if not (drill_blind or mr_active):
        return True, ""
    counts = canvas_marker_counts(app)
    if counts.get("pv-marker", 0) > 0:
        return False, "盲测模式下画了 pv-marker（泄题）"
    return True, ""


def inv_I9(app):
    """drill active 时不画 candidate-marker（防 quiz 期间泄露 AI 候选）。"""
    if not _safe_drill_active(app):
        return True, ""
    counts = canvas_marker_counts(app)
    if counts.get("candidate-marker", 0) > 0:
        return False, "drill active 时仍画了 candidate-marker"
    return True, ""


# ===================== 导航拦截不变式 =====================

def inv_I12(app, tree_depth_before, tree_identity_before):
    """scoring_mode=True 时破坏性动作不应改变 tree.current。

    verifier 在调用 play/do_redo/do_pass/do_reset 前记录 tree 状态，
    调用后用此不变式验证 tree 未被改变。
    """
    if not app.scoring_mode:
        return True, ""
    node = app.tree.current
    if node.depth != tree_depth_before:
        return False, "点目模式下 tree.current.depth 被改变 (%d→%d)" % (tree_depth_before, node.depth)
    return True, ""


# ===================== 窗口生命周期不变式 =====================

def inv_I16(app):
    """_close_problem_drill 后 drill 相关引用全清。"""
    if getattr(app, "_drill_win", None) is not None:
        return False, "_drill_win 未清"
    if getattr(app, "_drill", None) is not None:
        return False, "_drill 未清"
    if getattr(app, "_drill_overlay", None) is not None:
        return False, "_drill_overlay 未清"
    return True, ""


def inv_I18(app):
    """exit_scoring 后 scoring 相关状态全清。"""
    if app.scoring_mode:
        return False, "scoring_mode 仍为 True"
    if getattr(app, "score_estimator", None) is not None:
        return False, "score_estimator 未清"
    if getattr(app, "_await_scoring_ownership", False):
        return False, "_await_scoring_ownership 仍为 True"
    return True, ""


# ===================== 不变式注册表 =====================

# 互斥不变式（每步后都检查）——这些是"任何时候都不能违反"的硬约束
UNCONDITIONAL_INVARIANTS = [
    ("I1", inv_I1), ("I2", inv_I2), ("I3", inv_I3), ("I4", inv_I4), ("I13", inv_I13),
    ("I6", inv_I6), ("I7", inv_I7), ("I8", inv_I8), ("I9", inv_I9),
]

# 换棋谱不变式（仅在 switch_game/reset 类动作后检查）
# I5/I5b/I5c/I14 检查的是"换棋谱后应清空"，不能在 enter_scoring 等动作后查
POST_GAME_SWITCH_INVARIANTS = [
    ("I5", inv_I5), ("I5b", inv_I5b), ("I5c", inv_I5c), ("I14", inv_I14),
]

# 窗口生命周期不变式（仅在 close/exit 类动作后检查）
POST_CLOSE_INVARIANTS = [
    ("I16", inv_I16), ("I18", inv_I18),
]


def check_all_unconditional(app):
    """检查所有无条件（互斥）不变式，返回违规列表。每步后调用。"""
    violations = []
    for inv_id, fn in UNCONDITIONAL_INVARIANTS:
        try:
            ok, msg = fn(app)
            if not ok:
                violations.append((inv_id, msg))
        except Exception as e:
            violations.append((inv_id, "不变式检查异常: %r" % e))
    return violations


def check_post_game_switch(app):
    """检查换棋谱不变式，仅在 switch_game/do_reset 等动作后调用。"""
    violations = []
    for inv_id, fn in POST_GAME_SWITCH_INVARIANTS:
        try:
            ok, msg = fn(app)
            if not ok:
                violations.append((inv_id, msg))
        except Exception as e:
            violations.append((inv_id, "不变式检查异常: %r" % e))
    return violations


def check_post_close(app):
    """检查窗口生命周期不变式，仅在 close/exit 类动作后调用。"""
    violations = []
    for inv_id, fn in POST_CLOSE_INVARIANTS:
        try:
            ok, msg = fn(app)
            if not ok:
                violations.append((inv_id, msg))
        except Exception as e:
            violations.append((inv_id, "不变式检查异常: %r" % e))
    return violations
