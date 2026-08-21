"""基于现有 KataGo 候选的可解释个人化推荐层。

这里的 policy 只作为“引擎策略信号”，绝不解释为人类落子概率。没有
Human SL 模型时也不会伪装成人类棋力模型。
"""
from __future__ import annotations

import re


def _num(item, key, default=None):
    value = (item or {}).get(key, default)
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return default


# ---- 棋力档 → 目损容差分档表（第一版产品参数）----
# 校准依据（围棋常识，非本项目实测数据）：各档棋手相对 AI 一选的平均
# 单手目损大致为——职业/AI ≈0.3-0.6 目；高段(≥5段) ≈0.8-1.5；
# 低段(1-4段) ≈1.5-2.5；高级位(1-3级) ≈2-3；其余级位 ≈3-5；入门 >5。
# 容差取"该档平均目损的保守下沿"：档内正常波动判"当前水平可接受"，
# 明显低于本档水平的选点才被标记。级位宽、段位递减、职业最严。
# 未来应换成本项目真实复盘数据（按 user_learning_rank 分桶统计目损
# 分布）再校准，届时只需调整本表数值。
SKILL_TOLERANCE_TIERS = (
    ("pro", 0.8),        # 职业 / AI
    ("high_dan", 1.2),   # ≥5 段
    ("low_dan", 1.5),    # 1-4 段 / 泛"段位"描述
    ("high_kyu", 2.0),   # 1-3 级（高级位）
    ("kyu", 2.6),        # 其余级位（4 级以下）
    ("beginner", 3.0),   # 入门 / 新手 / 启蒙
)
_TIER_TOLERANCE = dict(SKILL_TOLERANCE_TIERS)
# 未设置（user_learning_rank 为空，回退单局表现档也识别不出）或无法
# 识别的描述：低段与高级位之间的中性值，不偏向任何一档。
SKILL_TOLERANCE_DEFAULT = 1.8

_DAN_RE = re.compile(r"(\d+)\s*[段dD]")
_KYU_RE = re.compile(r"(\d+)\s*[级kK]")
_BEGINNER_WORDS = ("入门", "新手", "启蒙", "初学")


def skill_tier(label):
    """把棋力描述解析成档位键（SKILL_TOLERANCE_TIERS 的键）；识别不出
    返回 None。支持 "业余1段"/"野狐3D"/"15级"/"1-3级"/"高级位" 等写法。"""
    text = str(label or "").strip()
    if not text:
        return None
    if "AI" in text or "职业" in text:
        return "pro"
    m = _DAN_RE.search(text)
    if m:
        return "high_dan" if int(m.group(1)) >= 5 else "low_dan"
    if "段" in text:
        return "low_dan"
    if any(w in text for w in _BEGINNER_WORDS):
        return "beginner"
    m = _KYU_RE.search(text)
    if m:
        return "high_kyu" if int(m.group(1)) <= 3 else "kyu"
    if "高级位" in text:
        return "high_kyu"
    if "级" in text:
        return "kyu"
    return None


def skill_tolerance(label):
    """按棋力档给目损容差（candidate_assessment 复用）。

    分档表见 SKILL_TOLERANCE_TIERS（校准依据在其注释）；未设置/无法
    识别用中性默认 SKILL_TOLERANCE_DEFAULT。"""
    return _TIER_TOLERANCE.get(skill_tier(label), SKILL_TOLERANCE_DEFAULT)


_skill_tolerance = skill_tolerance   # 旧私有名兼容


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
