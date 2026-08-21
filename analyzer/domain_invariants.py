"""domain_invariants —— 学习领域不变式（审查 A2，D1-D10）。

这些是 go_ana 学习系统的核心语义约束，违反任何一条都意味着
学习数据不可信。与 UI 状态不变式（invariants.py）互补。

每条不变式是 def dX() -> (ok, msg)，纯逻辑，不依赖 app 实例。
"""
from __future__ import annotations

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


# ===================== D1: CandidateAssessment 唯一性 =====================

def d1_assessment_consistency():
    """同一选点 + 同一上下文 → 无论入口（字母/自由落子/复习）判定必须一致。"""
    from candidate_assessment import (
        build_assessment_context, assess_candidate, assessment_for_loss,
    )
    ctx = build_assessment_context(stable_rank="15级")
    mis = [
        {"move": "P9", "order": 0, "scoreLead": 1.0, "winrate": 0.52},
        {"move": "Q11", "order": 1, "scoreLead": -0.8, "winrate": 0.51},
        {"move": "K10", "order": 3, "scoreLead": 0.6, "winrate": 0.51},
    ]
    free = assess_candidate("K10", mis, "B",
                            performance_label=ctx["performance_label"],
                            complexity=0.0)
    letter, _ = assessment_for_loss(
        free["score_loss"], performance_label=ctx["performance_label"],
        complexity=0.0)
    if free["assessment"] != letter:
        return False, "自由落子=%s 字母=%s（应相同）" % (free["assessment"], letter)
    return True, ""


# ===================== D2: AI Top1 不是唯一正确答案 =====================

def d2_top4_not_wrong():
    """第4选亏 0.4 目不能判错——这是产品核心原则。"""
    from candidate_assessment import assess_candidate
    mis = [
        {"move": "A", "order": 0, "scoreLead": 0.0, "winrate": 0.5},
        {"move": "B", "order": 1, "scoreLead": -4.8, "winrate": 0.45},
        {"move": "C", "order": 2, "scoreLead": -6.2, "winrate": 0.43},
        {"move": "D", "order": 3, "scoreLead": -0.4, "winrate": 0.49},
    ]
    result = assess_candidate("D", mis, "B")
    if result["assessment"] in ("questionable", "bad"):
        return False, "第4选亏0.4目被判 %s（应 ≥ acceptable）" % result["assessment"]
    return True, ""


# ===================== D4: mastery 状态机合法迁移 =====================

VALID_MASTERY_TRANSITIONS = {
    ("new", "new"), ("new", "understanding"),
    ("understanding", "understanding"), ("understanding", "retained"),
    ("understanding", "new"), ("understanding", "unstable"),
    ("retained", "retained"), ("retained", "transferred"),
    ("retained", "unstable"), ("retained", "understanding"),
    ("transferred", "transferred"), ("transferred", "unstable"),
    ("unstable", "unstable"), ("unstable", "understanding"),
    ("unstable", "retained"),
}

def d4_mastery_transitions():
    """new 不能直接跳 transferred/retained（无合法证据路径）。"""
    illegal = [("new", "transferred"), ("new", "retained")]
    for old, new in illegal:
        if (old, new) in VALID_MASTERY_TRANSITIONS:
            return False, "非法迁移 %s→%s 被允许" % (old, new)
    return True, ""


# ===================== D5: 训练不能证明 retention =====================

def d5_training_no_retention():
    """训练 source=good 封顶 understanding，不能直接 retained。"""
    from learning_store import apply_review_outcome
    from learning_event import event_id
    tmp = tempfile.mkdtemp(prefix="d5-")
    path = os.path.join(tmp, "le.json")
    try:
        from learning_store import sync_profile_summary
        sync_profile_summary({"id": "gD5", "profileSide": "B"},
                             {"problem_moves_all": [{
                                 "move_no": 1, "color": "B",
                                 "played_move": "A1", "best_move": "B2",
                                 "score_loss": 5.0}]}, path)
        eid = event_id("gD5", 1, "B")
        evt = apply_review_outcome(eid, "good", path=path, source="training")
        if evt.mastery_state == "retained":
            return False, "训练 good 直接设 retained（应 understanding）"
        return True, ""
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


# ===================== D7: HumanSL fail closed =====================

def d7_humansl_fail_closed():
    """没有 humanPrior 时 level_gap 必须为 0，不回退普通 prior。"""
    from human_sl import level_gap_component
    # 无数据（unknown verdict）→ 0
    if level_gap_component(None) != 0.0:
        return False, "无 humanPrior 时 level_gap ≠ 0"
    if level_gap_component({}) != 0.0:
        return False, "空结果时 level_gap ≠ 0"
    if level_gap_component({"verdict": "unknown"}) != 0.0:
        return False, "unknown verdict 时 level_gap ≠ 0"
    # common_both 也不是 level_gap → 0
    if level_gap_component({"verdict": "common_both"}) != 0.0:
        return False, "common_both 不应产生 level_gap"
    # 只有 level_gap verdict 才给分
    if level_gap_component({"verdict": "level_gap", "delta": 0.25}) <= 0:
        return False, "level_gap verdict 未给分"
    return True, ""


# ===================== D9: EvidencePacket 不可污染 =====================

def d9_evidence_immutable():
    """LLM 教练输出不能改写 EvidencePacket 中的 KataGo 数值。"""
    from coach_provider import validate_against_packet
    from coach_schema import empty_explanation
    packet = {
        "score_loss": 6.3, "winrate_before": 0.55,
        "best_move": "P9", "played_move": "R10",
        "move_no": 82, "candidate_moves": [
            {"move": "P9", "order": 0, "score_lead": 1.2, "winrate": 0.55},
        ],
    }
    # 编造数字的输出应被拒绝
    liar = dict(empty_explanation())
    liar.update(summary="亏了 9.9 目。")
    ok, issues = validate_against_packet(liar, packet)
    if ok:
        return False, "LLM 编造 9.9 目未被拦截（应为幻觉）"
    # 编造选点应被拒绝
    liar2 = dict(empty_explanation())
    liar2.update(reasonable_moves=["Z99"])
    ok2, issues2 = validate_against_packet(liar2, packet)
    if ok2:
        return False, "LLM 推荐 Z99 未被拦截（应为幻觉）"
    return True, ""


# ===================== D10: recurrence 按盘数不按事件数 =====================

def d10_recurrence_game_count():
    """同盘多事件只算 1 盘，不按事件数。"""
    from learning_priority import build_recurrence_index
    from types import SimpleNamespace
    events = [
        SimpleNamespace(game_id="gA", primary_category="attack"),
        SimpleNamespace(game_id="gA", primary_category="attack"),
        SimpleNamespace(game_id="gA", primary_category="attack"),
        SimpleNamespace(game_id="gB", primary_category="attack"),
    ]
    index = build_recurrence_index(events)
    if index.get("attack") != 2:
        return False, "attack 复发=%s（应为 2 盘，不是 4 事件）" % index.get("attack")
    return True, ""


# ===================== 注册表 =====================

DOMAIN_INVARIANTS = [
    ("D1", d1_assessment_consistency, "判分唯一性"),
    ("D2", d2_top4_not_wrong, "Top1 非唯一正确"),
    ("D4", d4_mastery_transitions, "掌握状态机合法"),
    ("D5", d5_training_no_retention, "训练不证 retention"),
    ("D7", d7_humansl_fail_closed, "HumanSL fail closed"),
    ("D9", d9_evidence_immutable, "EvidencePacket 不可污染"),
    ("D10", d10_recurrence_game_count, "recurrence 按盘数"),
]


def check_all_domain():
    """检查所有领域不变式，返回违规列表。"""
    violations = []
    for inv_id, fn, desc in DOMAIN_INVARIANTS:
        try:
            ok, msg = fn()
            if not ok:
                violations.append((inv_id, "%s: %s" % (desc, msg)))
        except Exception as e:
            violations.append((inv_id, "%s: 检查异常 %r" % (desc, e)))
    return violations
