"""个人棋风与成长路线 Markdown 报告。"""
from __future__ import annotations

from datetime import datetime


def _number(value, digits=2):
    return "—" if value is None else ("%.*f" % (digits, float(value)))


def _label(value, kind):
    maps = {
        "tendency": {
            "high": "高", "medium": "中", "low": "低", "unknown": "未知"},
        "cost": {
            "low_cost": "低", "medium_cost": "中",
            "high_cost": "高", "unknown": "未知"},
        "conclusion": {
            "keep": "可保留", "observe": "继续观察",
            "fix": "需要修正", "insufficient": "样本不足"},
        "trend": {
            "improving": "改善", "stable": "稳定",
            "worsening": "反复", "insufficient": "样本不足"},
    }
    return maps[kind].get(value, value or "—")


def render_style_report(style_profile, growth_path, verification=None,
                        generated_at=None):
    generated_at = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M")
    costs = list(style_profile.cost_results or [])
    signature = style_profile.analysis_signature_summary or {}
    findings = list(verification or [])
    lines = [
        "# 个人棋风与成长路线报告",
        "",
        "> 基于本机已分析棋局生成，不代表正式段位；结论受模型、visits、规则和样本数量影响。",
        "",
        "## 数据范围",
        "",
        "- 生成时间：%s" % generated_at,
        "- 纳入棋局：%d 盘" % style_profile.games_count,
        "- 有效本人走子：%d 手" % style_profile.evaluated_moves_count,
        "- 分析口径：%s / %s visits / %s / 贴目 %s" % (
            signature.get("model") or "当前模型",
            signature.get("visits") or "—",
            signature.get("rules") or "—",
            signature.get("komi") if signature.get("komi") is not None else "—"),
        "- 样本置信度：%s" % style_profile.confidence,
    ]
    lines.extend("- ⚠ %s" % item for item in style_profile.warnings)
    lines.extend([
        "",
        "## 棋风摘要",
        "",
        style_profile.style_summary,
        "",
        "## 风格维度",
        "",
        "| 维度 | 样本 | 倾向 | 代价 | 平均目损 | 不佳/恶手率 | 最近趋势 | 结论 | 置信度 |",
        "|---|---:|---|---|---:|---:|---|---|---|",
    ])
    by_key = {item.dimension_key: item for item in costs}
    for dimension in style_profile.dimensions:
        cost = by_key.get(dimension.key)
        lines.append("| %s | %d | %s | %s | %s | %.1f%% | %s | %s | %s |" % (
            dimension.label,
            dimension.sample_count,
            _label(cost.tendency_level if cost else "unknown", "tendency"),
            _label(cost.cost_level if cost else "unknown", "cost"),
            _number(dimension.avg_score_loss),
            (dimension.inaccuracy_rate + dimension.blunder_rate) * 100.0,
            _label(dimension.recent_trend, "trend"),
            _label(cost.conclusion if cost else "insufficient", "conclusion"),
            dimension.confidence,
        ))

    lines.extend(["", "## 可保留的风格", ""])
    if growth_path.keep_styles:
        for item in growth_path.keep_styles:
            lines.extend([
                "### %s" % item.get("label"),
                "",
                "- 证据：%s" % (item.get("evidence") or "—"),
                "- 边界：%s" % (item.get("action") or "继续观察代表局面。"),
                "",
            ])
    else:
        lines.append("当前没有达到样本门槛的低成本稳定倾向。")

    lines.extend(["", "## 高成本习惯", ""])
    if growth_path.fix_habits:
        for item in growth_path.fix_habits:
            lines.extend([
                "### %s" % item.get("label"),
                "",
                "- 证据：%s" % (item.get("evidence") or "—"),
                "- 建议：%s" % (item.get("action") or "复盘代表局面。"),
                "",
            ])
    else:
        lines.append("当前没有达到样本与成本门槛的高成本习惯。")

    lines.extend([
        "",
        "## 下一阶段成长路线",
        "",
        "- 主线目标：%s" % growth_path.main_goal,
        "- 建议保留：%s" % (
            "、".join(item.get("label", "") for item in growth_path.keep_styles)
            or "暂无强结论"),
        "- 建议修正：%s" % (
            "、".join(item.get("label", "") for item in growth_path.fix_habits)
            or "暂无强结论"),
        "- 复盘优先看：%s" % "、".join(growth_path.next_review_focus),
        "",
        "## 高强度复核结果",
        "",
    ])
    if findings:
        lines.extend([
            "| 结论 | 复核样本 | 稳定样本 | 稳定性 | 报告处理 |",
            "|---|---:|---:|---|---|",
        ])
        for item in findings:
            lines.append("| %s | %d | %d | %s | %s |" % (
                item.get("conclusion_label", ""),
                int(item.get("checked_samples", 0)),
                int(item.get("stable_samples", 0)),
                item.get("stability", "insufficient"),
                item.get("message", ""),
            ))
    else:
        lines.append(
            "关键结论尚未高强度复核。建议对影响成长路线的代表样本进行 800 visits 复核。")

    lines.extend([
        "",
        "## 代表问题手",
        "",
        "| 棋局 | 手数 | 方 | 实战 | AI 推荐 | 评价 | 目损 | 关联结论 |",
        "|---|---:|:---:|---|---|---|---:|---|",
    ])
    for move in growth_path.recommended_positions[:10]:
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
            move.get("game_name") or move.get("game_id") or "棋局",
            move.get("move_no", move.get("moveNo", "?")),
            "黑" if move.get("color") == "B" else "白",
            move.get("played_move", move.get("playedMove", "?")),
            move.get("best_move", move.get("bestMove", "?")),
            move.get("quality_key", move.get("qualityKey", "?")),
            _number(move.get("score_loss", move.get("scoreLoss")), 1),
            move.get("conclusion_label", ""),
        ))
    lines.extend([
        "",
        "## 边界说明",
        "",
        "本报告基于 KataGo 当前模型、规则、visits 和本机样本生成。"
        "棋风判断是统计倾向，不是正式棋力认证。样本不足或低置信结论不应作为训练重点。",
        "",
    ])
    return "\n".join(lines)

