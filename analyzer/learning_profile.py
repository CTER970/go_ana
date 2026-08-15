"""learning_profile —— 从 LearningEvent 聚合长期学习画像（项目大纲 M10、§43）。

个人主页的新指标体系（大纲 §43 的排序）全部从 LearningEvent 计算，
不再只依赖单局 MoveQuality：
- 重复错误率：本盘问题中属于历史已出现类别的比例；
- 分类分布：九类技术错误的占比；
- 主动纠正率：复盘重选明显改善的比例；
- 延迟保留 / 实战迁移：从 attempts 与 mastery_state 聚合；
- 第一训练主题：一次只给一个（大纲 §65）。
"""
from __future__ import annotations

from collections import defaultdict

from learning_event import (
    MASTERY_TRANSFERRED, MASTERY_UNSTABLE, RETRY_CORRECTED,
    RETRY_ALTERNATIVE_CORRECT, RETRY_IMPROVED,
)

PROFILE_VERSION = 1
_CORRECTED_STATES = (RETRY_CORRECTED, RETRY_ALTERNATIVE_CORRECT)
_IMPROVED_STATES = _CORRECTED_STATES + (RETRY_IMPROVED,)


def summarize_learning(events, *, recent_games=10):
    """LearningEvent 列表 → 学习画像摘要 dict（UI/报告直接消费）。"""
    events = list(events or [])
    by_game = defaultdict(list)
    for evt in events:
        by_game[evt.game_id].append(evt)
    games = sorted(by_game, key=lambda g: by_game[g][0].created_at)
    recent_set = set(games[-recent_games:]) if games else set()
    recent_events = [e for e in events if e.game_id in recent_set]

    # ---- 分类分布（近窗口）----
    category_counts = defaultdict(int)
    for evt in recent_events:
        category_counts[evt.primary_category or "unclassified"] += 1

    # ---- 重复错误率：最近窗口内，所属类别在更早窗口也出现过的比例 ----
    earlier_categories = set()
    for evt in events:
        if evt.game_id not in recent_set and evt.primary_category:
            earlier_categories.add(evt.primary_category)
    recent_classified = [e for e in recent_events if e.primary_category]
    repeated = [e for e in recent_classified
                if e.primary_category in earlier_categories]
    repeat_rate = (len(repeated) / len(recent_classified)) if recent_classified \
        else None

    # ---- 实战复发率：类别维度的 过去 vs 最近（大纲 §43 第七位）----
    earlier_by_cat = defaultdict(int)
    for evt in events:
        if evt.game_id not in recent_set and evt.primary_category:
            earlier_by_cat[evt.primary_category] += 1
    recent_by_cat = defaultdict(int)
    for evt in recent_classified:
        recent_by_cat[evt.primary_category] += 1

    # ---- 主动纠正率：复盘重选过的事件中明显改善的比例 ----
    retried = [e for e in recent_events if e.retry_status]
    corrected = [e for e in retried if e.retry_status in _IMPROVED_STATES]
    correction_rate = (len(corrected) / len(retried)) if retried else None

    # ---- 延迟保留：复习过 ≥2 次的事件里最新一次作答仍合理的比例 ----
    retained = 0
    reviewed_multi = 0
    for evt in recent_events:
        if len(evt.attempts) >= 2:
            reviewed_multi += 1
            last = evt.attempts[-1]
            if last.get("assessment") in ("best", "excellent", "acceptable"):
                retained += 1
    retention_rate = (retained / reviewed_multi) if reviewed_multi else None

    # ---- 掌握状态分布 ----
    mastery_counts = defaultdict(int)
    for evt in events:
        mastery_counts[evt.mastery_state] += 1

    # ---- 第一训练主题（一次只给一个，大纲 §65）----
    # 评分 = 类别频次 × 平均优先级 ×（unstable 提权 / transferred 降权）
    theme_scores = defaultdict(float)
    theme_losses = defaultdict(list)
    for evt in recent_classified:
        weight = 1.2 if evt.mastery_state == MASTERY_UNSTABLE else \
            0.2 if evt.mastery_state == MASTERY_TRANSFERRED else 1.0
        theme_scores[evt.primary_category] += \
            max(evt.learning_priority, 0.05) * weight
        theme_losses[evt.primary_category].append(evt.score_loss)
    top_theme = None
    if theme_scores:
        category = max(theme_scores, key=lambda c: theme_scores[c])
        losses = theme_losses[category]
        recent_n = recent_by_cat.get(category, 0)
        earlier_n = earlier_by_cat.get(category, 0)
        top_theme = {
            "category": category,
            "count": category_counts.get(category, 0),
            "avg_loss": (sum(losses) / len(losses)) if losses else 0.0,
            "recent_count": recent_n,
            "earlier_count": earlier_n,
            "score": round(theme_scores[category], 3),
        }

    return {
        "version": PROFILE_VERSION,
        "games_total": len(games),
        "recent_games": len(recent_set),
        "events_total": len(events),
        "recent_events": len(recent_events),
        "category_distribution": {
            c: {"count": n,
                "pct": round(n * 100.0 / len(recent_classified), 1)
                if recent_classified else 0.0}
            for c, n in sorted(category_counts.items(),
                               key=lambda kv: -kv[1])},
        "repeat_error_rate": (round(repeat_rate * 100, 1)
                              if repeat_rate is not None else None),
        "correction_rate": (round(correction_rate * 100, 1)
                            if correction_rate is not None else None),
        "retention_rate": (round(retention_rate * 100, 1)
                           if retention_rate is not None else None),
        "recurrence_by_category": {
            c: {"earlier": earlier_by_cat[c], "recent": recent_by_cat[c]}
            for c in sorted(set(earlier_by_cat) | set(recent_by_cat),
                            key=lambda c: -(earlier_by_cat[c] + recent_by_cat[c]))
            if c != "unclassified"},
        "mastery_distribution": dict(mastery_counts),
        "top_training_theme": top_theme,
    }


def format_learning_summary(summary):
    """画像摘要 → 个人主页文本块（大纲 §64 的五指标 + 训练主题）。"""
    summary = summary or {}
    lines = []

    def _pct(value):
        return "—" if value is None else "%.0f%%" % value

    lines.append("重复错误率（近%d盘）：%s" % (
        summary.get("recent_games", 0), _pct(summary.get("repeat_error_rate"))))
    lines.append("主动纠正率：%s" % _pct(summary.get("correction_rate")))
    lines.append("延迟保留率：%s" % _pct(summary.get("retention_rate")))
    dist = summary.get("category_distribution") or {}
    if dist:
        top = list(dist.items())[:3]
        lines.append("主要问题：" + "、".join(
            "%s %.0f%%" % (c, v["pct"]) for c, v in top))
    theme = summary.get("top_training_theme")
    if theme:
        lines.append("当前第一训练主题：%s（近%d盘出现 %d 次，平均损失 %.1f 目）" % (
            theme["category"], summary.get("recent_games", 0),
            theme["count"], theme["avg_loss"]))
    unstable = (summary.get("mastery_distribution") or {}).get(MASTERY_UNSTABLE, 0)
    if unstable:
        lines.append("⚠ %d 个问题：复习会但实战仍复发（unstable）" % unstable)
    return "\n".join(lines)
