"""review_report —— 生成可分享的复盘 Markdown 报告。"""
from __future__ import annotations

from datetime import datetime

from review import ReviewReport, LOSS_DEFAULT_THRESHOLD
from move_quality import PROBLEM_TAGS


def _pct(v):
    return "%d%%" % int(v + 0.5)


def _loss(v):
    return "—" if v is None else "%.1f" % v


def _coord(e):
    return "pass" if e.is_pass else (e.coord or "?")


def _player_line(rr, color, name):
    s = rr.player_performance(color)
    if s is None:
        return "| %s | — | — | — | — | 0 |" % name
    if s["rank"] == "—":
        return "| %s | 样本不足 | — | %s | %s | %d/%d |" % (
            name, s["rank_range"], s["confidence"], s["rated_moves"], s["moves"])
    return "| %s | %s | %.2f | %s | %s | %d/%d |" % (
        name, s["rank"], s["performance_loss"],
        s["rank_range"], s["confidence"], s["rated_moves"], s["moves"])


def _side(color):
    return "黑" if color == "B" else "白"


def _stats_text(rr, color, name):
    s = rr.player_performance(color)
    if s is None:
        return "%s：暂无足够分析。" % name
    if s["rank"] == "—":
        return "%s：有效样本 %d 手，暂不估计段位。" % (name, s["rated_moves"])
    elo = s.get("elo")
    elo_txt = "" if elo is None else "，等价 Elo %d-%d（%s，参考估算）" % (
        elo[0], elo[1], elo[2])
    diff = s.get("avg_difficulty")
    diff_txt = "" if diff is None else "，平均难度 %.0f%%" % (diff * 100)
    ai_hint = s.get("ai_hint") or ""
    ai_txt = ("。%s" % ai_hint) if ai_hint else ""
    from review import quality_distribution
    dist = quality_distribution(rr.move_quality_results(), color)
    dist_txt = "。发挥水准：最佳 %d / 好手 %d / 一般 %d / 欠佳 %d / 恶手 %d / 未评 %d" % (
        dist["best"], dist["good"], dist["normal"],
        dist["inaccuracy"], dist["blunder"], dist["unknown"])
    return "%s：%s，稳健目损 %.2f，估计区间 %s（有效 %d/%d 手）%s%s%s%s" % (
        name, s["rank"], s["performance_loss"], s["rank_range"],
        s["rated_moves"], s["moves"], elo_txt, diff_txt, ai_txt, dist_txt)


def _good_reason(e):
    parts = []
    if e.agreement_rank == 0:
        parts.append("命中 AI 首选")
    elif e.agreement_rank is not None and e.agreement_rank < 3:
        parts.append("落在 AI 前3选")
    if e.loss is not None and e.loss < 0.5:
        parts.append("几乎无目损")
    elif e.loss is not None:
        parts.append("目损 %.1f" % e.loss)
    return "；".join(parts) if parts else "选择稳健"


def _phase_move_text(e):
    if e is None:
        return "—"
    return "%d手 %s%s（目损 %s）" % (e.move_number, _side(e.color), _coord(e), _loss(e.loss))


def _phase_comment(s):
    if s["moves"] <= 0:
        return "暂无足够分析"
    if s["problem_count"] == 0:
        return "整体稳定，未见明显问题手"
    top = s["top_problem"]
    return "主要问题在第 %d 手；建议对照 AI 首选 %s" % (top.move_number, top.best_move)


def _trend_text(series):
    if len(series) < 2:
        return "已分析节点不足，暂无法判断趋势。"
    start = series[0][1]
    end = series[-1][1]
    delta = end - start
    leader = "黑" if end > 0 else "白" if end < 0 else "双方"
    return "从 %.1f 目到 %.1f 目，变化 %+0.1f 目；当前倾向：%s。" % (
        start, end, delta, leader)


def _top_good_moves(evs, limit=8):
    good = [e for e in evs
            if e.analyzed and e.loss is not None
            and (e.loss < 1.0 or e.agreement_rank == 0)]
    good.sort(key=lambda e: (
        e.loss,
        9 if e.agreement_rank is None else e.agreement_rank,
        e.move_number,
    ))
    return good[:limit]


def generate_markdown_report(tree, black_name="黑方", white_name="白方",
                             komi=7.5, rule="chinese", score_result=None,
                             generated_at=None, focus_color=None):
    """基于已缓存 analysis 的 MoveTree 生成 Markdown 文本。"""
    rr = ReviewReport(tree)
    evs = rr.evaluate()
    analyzed = [e for e in evs if e.analyzed and e.loss is not None]
    if focus_color in ("B", "W"):
        analyzed = [e for e in analyzed if e.color == focus_color]
    series = rr.score_lead_series()
    problem_moves = rr.meaningful_problems(
        n=12, min_loss=LOSS_DEFAULT_THRESHOLD, min_winrate_loss=0.03,
        color=focus_color)
    good_moves = _top_good_moves(analyzed)
    phase_rows = rr.phase_summary(color=focus_color)
    commentary = rr.game_commentary(
        black_name, white_name, focus_color=focus_color)
    bad_intents = rr.bad_move_intents(limit=6, color=focus_color)
    quality_by_move = {
        result.move_no: result
        for result in rr.move_quality_results(include_unknown=True)
    }
    generated_at = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M")
    score_result = score_result if score_result is not None else getattr(tree, "score_result", None)
    total_moves = max(0, len(rr.mainline_nodes()) - 1)
    scope_text = (
        ("%s（%s）" % (
            black_name if focus_color == "B" else white_name,
            "黑" if focus_color == "B" else "白"))
        if focus_color in ("B", "W") else "双方")
    coverage = rr.analysis_coverage(focus_color)

    lines = [
        "# KataGo 复盘报告",
        "",
        "- 生成时间：%s" % generated_at,
        "- 规则：%s" % rule,
        "- 贴目：%s" % komi,
        "- 主线手数：%d" % total_moves,
        "- 复盘范围：%s" % scope_text,
        "- 分析覆盖：%d/%d（%.1f%%），有效评价 %d 手" % (
            coverage["analyzed"], coverage["total"],
            coverage["percent"], coverage["meaningful"]),
        "- 已评价手数：%d" % len(analyzed),
    ]
    if score_result is not None:
        lines.append("- 终局结果：%s" % getattr(score_result, "result_text", "—"))
    lines.extend([
        "",
        "## 结论摘要",
        "",
        *([
            "- %s" % _stats_text(rr, focus_color,
                                  black_name if focus_color == "B" else white_name)
        ] if focus_color in ("B", "W") else [
            "- %s" % _stats_text(rr, "B", black_name),
            "- %s" % _stats_text(rr, "W", white_name),
        ]),
        "- 形势趋势：%s" % _trend_text(series),
        "- 有效问题棋：%d 手。" % len(problem_moves),
        "- 阶段表现：%s。" % "；".join("%s%s" % (s["label"], s["quality"]) for s in phase_rows),
        "",
        "## 对局文字分析",
        "",
        commentary,
        "",
        "## 单局表现估计（非官方段位）",
        "",
        "| 方 | 单局表现 | 稳健目损 | 估计区间 | 可信度 | 有效/全部手数 |",
        "|---|---:|---:|---:|---:|---:|",
        *([
            _player_line(
                rr, focus_color,
                black_name if focus_color == "B" else white_name)
        ] if focus_color in ("B", "W") else [
            _player_line(rr, "B", black_name),
            _player_line(rr, "W", white_name),
        ]),
        "",
        "## 复盘重点概览",
        "",
        "- 有效问题棋：%d 手" % len(problem_moves),
        "- 形势趋势：%s" % _trend_text(series),
        "",
        "## 三阶段水平分析",
        "",
        "| 阶段 | 手数范围 | 已评手数 | 平均目损 | 阶段表现 | 好/普通/疑问/恶 | 代表好手 | 重点问题 | 阶段评价 |",
        "|---|---:|---:|---:|---|---|---|---|---|",
    ])
    for s in phase_rows:
        lo, hi = s["range"]
        rng = "%d-%d" % (lo, hi) if hi >= lo else "—"
        grade_line = "%d/%d/%d/%d" % (s["good"], s["normal"], s["doubt"], s["bad"])
        lines.append("| %s | %s | %d | %s | %s | %s | %s | %s | %s |" % (
            s["label"], rng, s["moves"], _loss(s["avg_loss"]), s["quality"],
            grade_line, _phase_move_text(s["best_move"]), _phase_move_text(s["top_problem"]),
            _phase_comment(s)))

    # 双方分段对比（对标涨棋网 P3，仅双方报告时输出黑白分别统计）
    if focus_color is None:
        bphases = rr.phase_summary(color="B")
        wphases = rr.phase_summary(color="W")
        lines.extend([
            "",
            "## 双方分段对比（目损 / 阶段质量 / 好·疑·恶）",
            "",
            "| 阶段 | 黑方目损 | 黑方质量 | 黑好·疑·恶 | 白方目损 | 白方质量 | 白好·疑·恶 |",
            "|---|---:|---|---|---:|---|---|",
        ])
        for b, w in zip(bphases, wphases):
            lines.append("| %s | %s | %s | %d·%d·%d | %s | %s | %d·%d·%d |" % (
                b["label"], _loss(b["avg_loss"]), b["quality"],
                b["good"], b["doubt"], b["bad"],
                _loss(w["avg_loss"]), w["quality"],
                w["good"], w["doubt"], w["bad"]))

    lines.extend([
        "",
        "## 下得好的地方",
        "",
        "| 手数 | 阶段 | 方 | 坐标 | 说明 |",
        "|---:|---|:---:|---|---|",
    ])
    if good_moves:
        for e in good_moves:
            lines.append("| %d | %s | %s | %s | %s |" % (
                e.move_number, rr.phase_label(rr.phase_of_move(e.move_number, total_moves)), _side(e.color),
                _coord(e), _good_reason(e)))
    else:
        lines.append("| — | — | — | — | 暂无足够数据。 |")

    lines.extend([
        "",
        "## 需要重点复盘的问题棋",
        "",
        "| 手数 | 阶段 | 方 | 实战 | 目损 | AI参考评价 | 胜率损失 | AI 建议 | 标签 |",
        "|---:|---|:---:|---|---:|---|---:|---|---|",
    ])
    if problem_moves:
        for e in problem_moves:
            quality = quality_by_move.get(e.move_number)
            quality_label = (
                "%s（%d分）" % (quality.quality_label, quality.quality_score)
                if quality is not None else "未评价")
            tags = (
                "、".join(PROBLEM_TAGS.get(tag, tag) for tag in quality.problem_tags)
                if quality is not None else "—") or "—"
            lines.append("| %d | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                e.move_number, rr.phase_label(rr.phase_of_move(e.move_number, total_moves)),
                _side(e.color), _coord(e), _loss(e.loss), quality_label,
                "%.1f%%" % rr.winrate_loss_pct(e), e.best_move, tags))
    else:
        lines.append("| — | — | — | — | — | — | — | 未发现有效问题棋。 | — |")

    if problem_moves:
        lines.extend(["", "### 评价原因", ""])
        for e in problem_moves:
            quality = quality_by_move.get(e.move_number)
            if quality is None:
                continue
            lines.append("- 第 %d 手（%s，置信度 %s）：%s" % (
                e.move_number, quality.quality_label, quality.confidence,
                "；".join(quality.reasons or ["暂无可用原因"])))

    deep_comparisons = getattr(tree, "_deep_comparisons", {}) or {}
    lines.extend([
        "",
        "## 问题手双分支深度对比",
        "",
    ])
    if deep_comparisons:
        visible_problem_moves = {e.move_number for e in problem_moves}
        for key in sorted(deep_comparisons, key=lambda value: int(value)):
            if focus_color in ("B", "W") and int(key) not in visible_problem_moves:
                continue
            comparison = deep_comparisons[key]
            lines.extend([
                "### 第 %s 手：实战 %s / AI %s" % (
                    comparison.get("move", key), comparison.get("actualMove", "—"),
                    comparison.get("aiMove", "—")),
                "",
                "- 量化结论：%s" % comparison.get("summary", "—"),
                "- 实战主变：%s" % " → ".join(
                    (comparison.get("actual") or {}).get("pv") or []),
                "- AI 主变：%s" % " → ".join(
                    (comparison.get("ai") or {}).get("pv") or []),
                "- 诊断：%s" % comparison.get("diagnosis", "—"),
                "",
            ])
    else:
        lines.extend(["尚未生成双分支深度对比；在问题棋模块中选择恶手后会自动生成。", ""])

    lines.extend([
        "",
        "## 恶手意图对比",
        "",
    ])
    if bad_intents:
        for e, intent in bad_intents:
            lines.extend([
                "### 第 %d 手 %s：%s → AI %s" % (
                    e.move_number, _side(e.color), intent["actualMove"], intent["aiMove"]),
                "",
                "- 实战选点可能意图：%s" % intent["actualIntent"],
                "- AI 推荐选点意图：%s" % intent["aiIntent"],
                "- 核心差异：%s" % intent["difference"],
                "",
            ])
    else:
        lines.extend(["本局没有可生成意图对比的恶手。", ""])

    lines.extend([
        "> 说明：单局表现仅统计落子前该方胜率在 2%-98% 的局面，且单手最多计 3 目，避免胜负已定后的目差波动和一次崩盘支配结论；它不是正式段位认证。",
        "",
    ])
    return "\n".join(lines)
