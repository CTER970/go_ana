"""profile_report —— 生成可长期留存的个人分析 Markdown 报告。"""
from __future__ import annotations

from datetime import datetime

from move_quality import PROBLEM_TAGS
from player_profile import (
    GameBenchmark, PlayerProfile, prioritize_weaknesses, weakness_trends)


def _number(value, digits=2, suffix=""):
    if value is None:
        return "—"
    return ("%.*f%s" % (digits, float(value), suffix))


def _trend_label(direction):
    return {
        "improving": "上升",
        "stable": "稳定",
        "declining": "下降",
        "insufficient": "样本不足",
    }.get(direction, "样本不足")


def _benchmark_label(status):
    return {
        "better": "优于个人基线",
        "similar": "接近个人基线",
        "worse": "低于个人基线",
        "insufficient": "基线不足",
    }.get(status, "基线不足")


def generate_profile_markdown(profile, records=None, benchmark=None,
                              mistakes=None, generated_at=None):
    """生成不依赖 UI 的个人画像报告。"""
    if isinstance(profile, dict):
        profile = PlayerProfile.from_dict(profile)
    if not isinstance(profile, PlayerProfile):
        profile = PlayerProfile()
    if isinstance(benchmark, dict):
        benchmark = GameBenchmark.from_dict(benchmark)
    records = list(records or [])
    mistakes = list(mistakes or [])
    generated_at = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M")

    overall = profile.overall
    lines = [
        "# KataGo 个人分析报告",
        "",
        "- 生成时间：%s" % generated_at,
        "- 纳入棋局：%d 盘" % profile.games_count,
        "- 有效评价：%d 手" % profile.evaluated_moves_count,
        "- 分析口径不兼容而排除：%d 盘" % profile.excluded_incompatible_games,
        "",
        "## 核心指标",
        "",
        "| 平均目损 | 恶手率 | 不佳率 | AI 前3吻合 | 长期趋势 |",
        "|---:|---:|---:|---:|---|",
        "| %s | %s | %s | %s | %s |" % (
            _number(overall.avg_score_loss),
            _number(overall.blunder_rate, 1, "%"),
            _number(overall.inaccuracy_rate, 1, "%"),
            _number(overall.top3_match_rate, 1, "%"),
            _trend_label(profile.recent_trend.direction)),
    ]

    if benchmark is not None:
        lines.extend([
            "",
            "## 最近一盘与个人基线",
            "",
            "- 结论：%s（置信度 %s，历史 %d 盘）。" % (
                _benchmark_label(benchmark.status),
                benchmark.confidence,
                benchmark.prior_games),
            "- 本局平均目损：%s；历史加权基线：%s；改善值：%s。" % (
                _number(benchmark.current_avg_loss),
                _number(benchmark.baseline_avg_loss),
                _number(benchmark.loss_improvement)),
        ])
        lines.extend("- " + item for item in benchmark.evidence)

    lines.extend([
        "",
        "## 阶段表现",
        "",
        "| 阶段 | 有效手数 | 平均目损 | 恶手率 | AI 前3吻合 |",
        "|---|---:|---:|---:|---:|",
    ])
    for key, label in (("opening", "布局"), ("middle", "中盘"), ("endgame", "官子")):
        stat = getattr(profile, key)
        lines.append("| %s | %d | %s | %s | %s |" % (
            label, stat.moves,
            _number(stat.avg_score_loss),
            _number(stat.blunder_rate, 1, "%"),
            _number(stat.top3_match_rate, 1, "%")))

    lines.extend([
        "",
        "## 执黑 / 执白",
        "",
        "| 执棋方 | 有效手数 | 平均目损 | 恶手率 | AI 前3吻合 |",
        "|---|---:|---:|---:|---:|",
    ])
    for key, label in (("black", "执黑"), ("white", "执白")):
        stat = getattr(profile, key)
        lines.append("| %s | %d | %s | %s | %s |" % (
            label, stat.moves,
            _number(stat.avg_score_loss),
            _number(stat.blunder_rate, 1, "%"),
            _number(stat.top3_match_rate, 1, "%")))

    lines.extend(["", "## 逐盘趋势", ""])
    if profile.trend_points:
        lines.extend([
            "| 序号 | 棋局 | 有效手数 | 平均目损 | 恶手率 | AI 前3吻合 |",
            "|---:|---|---:|---:|---:|---:|",
        ])
        name_by_id = {
            str(record.get("id")): record.get("name") or str(record.get("id"))
            for record in records
        }
        for point in profile.trend_points:
            lines.append("| %d | %s | %d | %s | %s | %s |" % (
                point.order + 1,
                name_by_id.get(point.game_id, point.game_id or "棋局"),
                point.evaluated_moves,
                _number(point.avg_score_loss),
                _number(point.blunder_rate, 1, "%"),
                _number(point.top3_match_rate, 1, "%")))
    else:
        lines.append("暂无足够逐盘趋势数据。")

    lines.extend(["", "## 常见问题", ""])
    tags = sorted(
        profile.problem_tag_distribution.items(),
        key=lambda item: item[1], reverse=True)
    if tags:
        for tag, count in tags[:8]:
            lines.append("- %s：%d 次" % (PROBLEM_TAGS.get(tag, tag), count))
    else:
        lines.append("- 暂无达到统计门槛的问题标签。")

    lines.extend(["", "## 优先训练", ""])
    trends = weakness_trends(
        records, tags=profile.problem_tag_distribution.keys())
    priorities = prioritize_weaknesses(
        profile, mistakes, trends=trends)
    if priorities:
        for index, item in enumerate(priorities, start=1):
            lines.append("%d. **%s**：%s" % (
                index, item["label"], item["reason"]))
    else:
        lines.append("当前没有足够的跨局问题与错题证据可排序。")

    lines.extend(["", "## 个人建议", ""])
    advice = profile.weaknesses[:3] + profile.recommendations[:6]
    lines.extend("- " + item for item in (
        advice or ["继续积累完整分析棋局后再生成稳定结论。"]))

    due = [item for item in mistakes if item.get("isDue")]
    lines.extend(["", "## 当前复习队列", ""])
    lines.append("- 活跃错题：%d；今日到期：%d。" % (len(mistakes), len(due)))
    for item in due[:10]:
        lines.append("- %s 第%s手：%s → AI %s，目损 %s。" % (
            item.get("gameName") or item.get("gameId") or "棋局",
            item.get("moveNo") or "?",
            item.get("playedMove") or "?",
            item.get("bestMove") or "?",
            _number(item.get("scoreLoss"), 1)))

    lines.extend([
        "",
        "## 口径说明",
        "",
        "- 仅统计已识别为本人执棋方、且处于有效胜负阶段的走子。",
        "- 不同模型、规则、贴目、visits 或评价版本的数据不会静默混入同一趋势。",
        "- 所有评价均为当前 KataGo 模型下的个人训练参考，不代表正式段位。",
        "",
    ])
    return "\n".join(lines)
