"""基于现有 KataGo 候选的可解释个人化推荐层。

这里的 policy 只作为“引擎策略信号”，绝不解释为人类落子概率。没有
Human SL 模型时也不会伪装成人类棋力模型。
"""
from __future__ import annotations


def _num(item, key, default=None):
    value = (item or {}).get(key, default)
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return default


def _skill_tolerance(label):
    text = str(label or "")
    if "AI" in text or "职业" in text:
        return 0.8
    if "段" in text:
        return 1.3
    if "1-3级" in text or "高级位" in text:
        return 1.8
    if "级" in text or "入门" in text or "新手" in text:
        return 2.8
    return 1.8


def _performance_label(label):
    """把单局表现档的空值统一成面向用户的诚实文案。"""
    text = str(label or "").strip()
    return text if text and text not in ("—", "-", "未知") else "样本不足"


def _loss(best_score, score, color):
    if best_score is None or score is None:
        return None
    return max(0.0, best_score - score) if color == "B" else max(0.0, score - best_score)


def build_candidate_recommendations(candidates, color="B", performance_label=None):
    """为候选添加 AI最优 / 稳健易懂 / 易执行候选标签。

    注意："易执行候选"（曾用名"当前棋力参考"）不是棋力模型：在 Human SL
    接入前，它只表示小目损、短主变、引擎策略信号清晰的选点，容差来自
    当前单局表现档的粗略启发式（项目大纲 §5）。

    返回值顺序与输入一致，每项含 ``badges``、``reason``、``scoreLoss``。
    """
    ordered = sorted(list(candidates or []), key=lambda item: item.get("order", 999))
    if not ordered:
        return []
    color = str(color or "B").upper()
    performance_label = _performance_label(performance_label)
    best_score = _num(ordered[0], "scoreLead")
    tolerance = _skill_tolerance(performance_label)
    rows = []
    for index, item in enumerate(ordered):
        pv_len = len(item.get("pv") or [])
        # KataGo analysis 的候选常用 prior；兼容少量旧缓存中的 policy。
        policy = _num(item, "prior")
        if policy is None:
            policy = _num(item, "policy", 0.0)
        policy = policy or 0.0
        loss = _loss(best_score, _num(item, "scoreLead"), color)
        # 小损失、短主变、较清晰的引擎策略信号，作为“易执行”代理。
        cost = (loss if loss is not None else tolerance + 2.0) + min(pv_len, 20) * 0.045 - min(policy, 1.0) * 0.35
        rows.append({
            "move": item.get("move") or "pass",
            "index": index,
            "policy": policy,
            "pvLength": pv_len,
            "scoreLoss": loss,
            "cost": cost,
            "badges": [],
            "reason": [],
            "basis": "KataGo候选损失 + PV长度 + 引擎prior策略信号 + 当前表现档",
            "humanModel": False,
        })
    rows[0]["badges"].append("AI最优")
    rows[0]["reason"].append("KataGo 当前返回的一选")

    safe_pool = [r for r in rows if r["scoreLoss"] is not None and r["scoreLoss"] <= min(1.5, tolerance)]
    safe = min(safe_pool or rows[:1], key=lambda r: r["cost"])
    safe["badges"].append("稳健易懂")
    safe["reason"].append("在小目损范围内，优先短主变与清晰策略信号")

    skill_pool = [r for r in rows if r["scoreLoss"] is not None and r["scoreLoss"] <= tolerance]
    if not skill_pool:
        skill_pool = rows[:1]
    skill = min(
        skill_pool,
        key=lambda r: r["cost"] - min(r["policy"], 1.0) * 0.45)
    skill["badges"].append("易执行候选")
    skill["reason"].append("按%s的 %.1f 目容差筛选；prior 仅作引擎策略信号" % (
        performance_label, tolerance))

    for row in rows:
        if row["scoreLoss"] is not None:
            row["reason"].append("相对一选约损失 %.1f 目" % row["scoreLoss"])
        row["reason"] = "；".join(row["reason"]) if row["reason"] else "普通备选"
    return rows
