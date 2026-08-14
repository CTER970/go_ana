"""Deep comparison helpers for an actual move versus KataGo's best move."""
from __future__ import annotations

from datetime import datetime

from movetree import point_to_xy


OWNERSHIP_THRESHOLD = 0.35
PV_LIMIT = 14


def _number(mapping, key, default=0.0):
    value = (mapping or {}).get(key, default)
    return default if value is None else float(value)


def _root(response):
    return (response or {}).get("rootInfo") or {}


def _mover_winrate(response, color):
    winrate = _number(_root(response), "winrate", 0.5)
    return winrate if color == "B" else 1.0 - winrate


def _mover_score(response, color):
    score = _number(_root(response), "scoreLead", 0.0)
    return score if color == "B" else -score


def _control_points(response, color):
    ownership = (response or {}).get("ownership") or []
    if color == "B":
        return sum(1 for value in ownership if value is not None and value >= OWNERSHIP_THRESHOLD)
    return sum(1 for value in ownership if value is not None and value <= -OWNERSHIP_THRESHOLD)


def _principal_variation(first_move, response, limit=PV_LIMIT):
    infos = sorted(
        (response or {}).get("moveInfos") or [],
        key=lambda item: item.get("order", 999),
    )
    continuation = list((infos[0].get("pv") or []) if infos else [])
    line = [first_move or "pass"] + continuation
    return line[:max(1, int(limit))]


def _region(move, size=19):
    if not move or str(move).lower() == "pass":
        return "全局"
    try:
        x, y = point_to_xy(move, size)
    except Exception:
        return "未知"
    near_x = x <= 5 or x >= size - 6
    near_y = y <= 5 or y >= size - 6
    if near_x and near_y:
        return "角部"
    if min(x, y, size - 1 - x, size - 1 - y) <= 3:
        return "边上"
    return "中央"


def _diagnosis(actual_move, ai_move, phase_label, score_gain, winrate_gain, control_gain, size):
    actual_region = _region(actual_move, size)
    ai_region = _region(ai_move, size)
    reasons = []
    if actual_region != ai_region:
        reasons.append("主要问题更接近全局方向选择：实战处理%s，AI优先处理%s" % (
            actual_region, ai_region))
    if control_gain >= 5:
        reasons.append("AI分支多保留约%d个稳定控制点，实地与厚薄效率差异明显" % control_gain)
    elif control_gain <= -5:
        reasons.append("实战分支表面控制点更多，但综合目差仍较差，说明这些地盘可能伴随先手或厚薄代价")
    if phase_label == "关子":
        reasons.append("这一差距发生在关子阶段，重点检查官子价值、先后手和收束次序")
    elif score_gain >= 5.0 or winrate_gain >= 8.0:
        reasons.append("数值损失较大，重点检查局部战斗次序、断点与弱棋负担")
    elif actual_region == ai_region:
        reasons.append("两手方向接近，差异更可能来自落点精度、棋形效率和交换次序")
    if not reasons:
        reasons.append("AI分支优势较小，主要是局部效率和后续次序的累积差异")
    return "；".join(reasons) + "。"


def build_branch_comparison(evaluation, actual_response, ai_response,
                            phase_label="", board_size=19, visits=None):
    """Build a JSON-serializable mover-perspective comparison."""
    color = evaluation.color
    actual_move = "pass" if evaluation.is_pass else (evaluation.coord or "pass")
    ai_move = evaluation.best_move or "pass"
    actual_score = _mover_score(actual_response, color)
    ai_score = _mover_score(ai_response, color)
    actual_winrate = _mover_winrate(actual_response, color)
    ai_winrate = _mover_winrate(ai_response, color)
    actual_control = _control_points(actual_response, color)
    ai_control = _control_points(ai_response, color)
    score_gain = ai_score - actual_score
    winrate_gain = (ai_winrate - actual_winrate) * 100.0
    control_gain = ai_control - actual_control
    diagnosis = _diagnosis(
        actual_move, ai_move, phase_label, score_gain, winrate_gain, control_gain, board_size)
    summary = (
        "AI分支相对实战为%s多保留 %.1f 目、%.1f 个胜率百分点，稳定控制点 %+d。%s"
        % ("黑方" if color == "B" else "白方", score_gain, winrate_gain, control_gain, diagnosis)
    )
    return {
        "version": 1,
        "move": int(evaluation.move_number),
        "color": color,
        "phaseLabel": phase_label,
        "actualMove": actual_move,
        "aiMove": ai_move,
        "visits": int(visits) if visits is not None else None,
        "actual": {
            "score": round(actual_score, 3),
            "winrate": round(actual_winrate, 6),
            "controlPoints": int(actual_control),
            "pv": _principal_variation(actual_move, actual_response),
        },
        "ai": {
            "score": round(ai_score, 3),
            "winrate": round(ai_winrate, 6),
            "controlPoints": int(ai_control),
            "pv": _principal_variation(ai_move, ai_response),
        },
        "scoreGain": round(score_gain, 3),
        "winrateGainPct": round(winrate_gain, 3),
        "controlGain": int(control_gain),
        "diagnosis": diagnosis,
        "summary": summary,
        "updatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
