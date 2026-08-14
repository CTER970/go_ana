"""training -- worst-stage training task and session grading helpers.

The training mode is intentionally stage-based instead of single-blunder based:
it finds the worst 30-40 move window on the analyzed main line, then lets the
player replay from the start of that window against KataGo.
"""
from __future__ import annotations

from datetime import datetime

from review import GRADE_BAD, GRADE_DOUBT, LOSS_DEFAULT_THRESHOLD, ReviewReport, grade_of, rank_of

DEFAULT_WINDOW = 36
DEFAULT_STEP = 12
MIN_ANALYZED_MOVES = 8
MIN_FOCUS_MOVES = 4
PLAYER_BOTH = "both"


def normalize_player_color(color):
    """Return 'B', 'W', or 'both' for training ownership."""
    if color is None:
        return PLAYER_BOTH
    c = str(color).strip().lower()
    if c in ("b", "black", "黑", "黑方", "我执黑"):
        return "B"
    if c in ("w", "white", "白", "白方", "我执白"):
        return "W"
    return PLAYER_BOTH


def player_color_label(color):
    color = normalize_player_color(color)
    if color == "B":
        return "黑方"
    if color == "W":
        return "白方"
    return "双方"


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _wr_loss(e):
    """Player-perspective winrate loss for one evaluated move, in percentage points."""
    before = e.winrate_before
    after = e.winrate_after
    if before is None or after is None:
        return 0.0
    loss = before - after if e.color == "B" else after - before
    return max(0.0, loss * 100.0)


def _top_problem_dict(e):
    return {
        "move": e.move_number,
        "color": e.color,
        "coord": "pass" if e.is_pass else (e.coord or "?"),
        "loss": round(float(e.loss or 0.0), 3),
        "winrateLossPct": round(_wr_loss(e), 3),
        "bestMove": e.best_move,
        "grade": grade_of(e.loss),
    }


def _phase_for_window(rr, start, end, total):
    mid = (start + end) // 2
    return rr.phase_of_move(mid, total)


def _window_score(evs):
    total_loss = sum(e.loss or 0.0 for e in evs)
    wr_loss = sum(_wr_loss(e) for e in evs)
    bad = sum(1 for e in evs if (e.loss or 0.0) >= GRADE_BAD)
    doubt = sum(1 for e in evs if (e.loss or 0.0) >= GRADE_DOUBT)
    problem = sum(1 for e in evs if (e.loss or 0.0) >= LOSS_DEFAULT_THRESHOLD)
    streak = 0
    best_streak = 0
    for e in evs:
        if (e.loss or 0.0) >= LOSS_DEFAULT_THRESHOLD:
            streak += 1
            best_streak = max(best_streak, streak)
        else:
            streak = 0
    return total_loss + wr_loss * 0.35 + bad * 8.0 + doubt * 3.0 + problem * 1.5 + best_streak * 2.0


def generate_training_task(tree, window=DEFAULT_WINDOW, step=DEFAULT_STEP, player_color=None):
    """Return the worst stage-training task for an analyzed game, or None.

    The selected task is a dict so it can be stored directly in the game library
    index. Windows are around 36 moves by default, with a 12-move slide.
    """
    rr = ReviewReport(tree)
    total = len(rr.mainline_nodes()) - 1
    evs = [e for e in rr.evaluate() if e.analyzed and e.loss is not None]
    if len(evs) < MIN_ANALYZED_MOVES:
        return None
    player_color = normalize_player_color(player_color)

    window = max(MIN_ANALYZED_MOVES, min(int(window), max(total, MIN_ANALYZED_MOVES)))
    step = max(1, int(step))
    candidates = []
    starts = list(range(1, max(2, total - window + 2), step))
    if not starts:
        starts = [1]
    last_start = max(1, total - window + 1)
    if last_start not in starts:
        starts.append(last_start)

    by_move = {e.move_number: e for e in evs}
    for start in sorted(set(starts)):
        end = min(total, start + window - 1)
        cur = [by_move[m] for m in range(start, end + 1) if m in by_move]
        if len(cur) < MIN_ANALYZED_MOVES:
            continue
        if len(cur) < max(MIN_ANALYZED_MOVES, int((end - start + 1) * 0.5)):
            continue
        focus = cur if player_color == PLAYER_BOTH else [e for e in cur if e.color == player_color]
        if len(focus) < (MIN_ANALYZED_MOVES if player_color == PLAYER_BOTH else MIN_FOCUS_MOVES):
            continue
        score = _window_score(focus)
        candidates.append((score, start, end, cur, focus))
    if not candidates:
        return None

    score, start, end, cur, focus = max(candidates, key=lambda item: (item[0], item[2] - item[1], -item[1]))
    total_loss = sum(e.loss or 0.0 for e in focus)
    avg_loss = total_loss / len(focus)
    wr_loss = sum(_wr_loss(e) for e in focus)
    bad = sum(1 for e in focus if (e.loss or 0.0) >= GRADE_BAD)
    doubt = sum(1 for e in focus if (e.loss or 0.0) >= GRADE_DOUBT)
    problem = sum(1 for e in focus if (e.loss or 0.0) >= LOSS_DEFAULT_THRESHOLD)
    top = sorted(focus, key=lambda e: (e.loss or 0.0, _wr_loss(e)), reverse=True)[:5]
    phase = _phase_for_window(rr, start, end, total)
    label = rr.phase_label(phase)
    return {
        "id": "stage-%d-%d" % (start, end),
        "kind": "worst-stage",
        "startMove": start,
        "endMove": end,
        "startNodeMove": max(0, start - 1),
        "targetMoves": end - start + 1,
        "playerColor": player_color,
        "playerColorLabel": player_color_label(player_color),
        "phase": phase,
        "phaseLabel": label,
        "moves": end - start + 1,
        "analyzedMoves": len(cur),
        "focusMoves": len(focus),
        "score": round(score, 3),
        "totalLoss": round(total_loss, 3),
        "avgLoss": round(avg_loss, 3),
        "rank": rank_of(avg_loss),
        "winrateLossPct": round(wr_loss, 3),
        "problemCount": problem,
        "doubtCount": doubt,
        "badCount": bad,
        "topProblems": [_top_problem_dict(e) for e in top],
        "summary": describe_training_task({
            "startMove": start, "endMove": end, "phaseLabel": label,
            "playerColor": player_color, "playerColorLabel": player_color_label(player_color),
            "avgLoss": avg_loss, "totalLoss": total_loss,
            "winrateLossPct": wr_loss, "problemCount": problem,
            "doubtCount": doubt, "badCount": bad,
        }),
        "updatedAt": _now(),
    }


def describe_training_task(task):
    if not task:
        return "暂无可训练阶段"
    start = task.get("startMove")
    end = task.get("endMove")
    phase = task.get("phaseLabel") or "阶段"
    side = task.get("playerColorLabel") or player_color_label(task.get("playerColor"))
    avg = float(task.get("avgLoss") or 0.0)
    total = float(task.get("totalLoss") or 0.0)
    wr = float(task.get("winrateLossPct") or 0.0)
    bad = int(task.get("badCount") or 0)
    doubt = int(task.get("doubtCount") or 0)
    problems = int(task.get("problemCount") or 0)
    if bad:
        focus = "连续判断里有明显恶手"
    elif doubt:
        focus = "方向选择多次偏缓或偏重"
    elif avg >= LOSS_DEFAULT_THRESHOLD:
        focus = "整体效率低于 AI 推荐"
    else:
        focus = "细节累积亏损"
    return ("%s第 %s-%s 手（%s）：平均目损 %.1f，累计目损 %.1f，胜率累计损失 %.1f%%；"
            "问题手 %d 手（疑问 %d / 恶 %d），训练重点是%s。"
            % (phase, start, end, side, avg, total, wr, problems, doubt, bad, focus))


def _session_eval_dict(e):
    d = _top_problem_dict(e)
    d["nodeNid"] = e.node_nid
    return d


def grade_training_session(task, evaluations, user_color):
    """Grade a finished training branch.

    ``evaluations`` should contain MoveEvaluation values for the played branch.
    Only the user's color is counted in the main score; AI replies are context.
    """
    user_color = normalize_player_color(user_color)
    if user_color == PLAYER_BOTH:
        user_color = normalize_player_color(task.get("playerColor")) if task else PLAYER_BOTH
    if user_color == PLAYER_BOTH and evaluations:
        user_color = evaluations[0].color
    user_evs = [e for e in evaluations
                if (user_color == PLAYER_BOTH or e.color == user_color)
                and e.analyzed and e.loss is not None]
    all_analyzed = [e for e in evaluations if e.analyzed and e.loss is not None]
    if not user_evs:
        return {
            "createdAt": _now(),
            "userColor": user_color,
            "moves": len(evaluations),
            "analyzedMoves": len(all_analyzed),
            "avgLoss": None,
            "grade": "未完成",
            "summary": "训练变化还没有足够的用户手分析结果，暂时无法评价。",
            "topProblems": [],
        }

    avg = sum(e.loss for e in user_evs) / len(user_evs)
    original = float(task.get("avgLoss") or 0.0) if task else 0.0
    improvement = original - avg
    good = sum(1 for e in user_evs if grade_of(e.loss) == "好")
    normal = sum(1 for e in user_evs if grade_of(e.loss) == "普通")
    doubt = sum(1 for e in user_evs if grade_of(e.loss) == "疑问")
    bad = sum(1 for e in user_evs if grade_of(e.loss) == "恶")
    if improvement >= 1.0 and bad == 0:
        grade = "A"
        verdict = "明显优于实战阶段，主要问题已经避开。"
    elif improvement >= 0.3:
        grade = "B"
        verdict = "比实战有改善，但仍有几处需要复盘。"
    elif avg <= original + 0.3:
        grade = "C"
        verdict = "大体接近实战水平，问题模式仍然存在。"
    else:
        grade = "D"
        verdict = "训练变化比实战更亏，需要重新看这一段的方向。"
    top = sorted(user_evs, key=lambda e: (e.loss or 0.0, _wr_loss(e)), reverse=True)[:5]
    best = sorted(user_evs, key=lambda e: (e.loss or 0.0, 9 if e.agreement_rank is None else e.agreement_rank, e.move_number))[:5]
    advice = "继续用这个阶段重复训练，目标是把平均目损压到 %.1f 以下。" % max(0.5, original * 0.6)
    if bad:
        advice = "优先复盘训练中的恶手，先把单手大亏降下来。"
    elif doubt:
        advice = "重点看疑问手前后的方向选择，避免连续偏缓。"
    elif improvement > 0:
        advice = "这次方向已有改善，下一轮可以减少提示再练一次。"
    return {
        "createdAt": _now(),
        "taskId": task.get("id") if task else None,
        "userColor": user_color,
        "moves": len(evaluations),
        "userMoves": len(user_evs),
        "analyzedMoves": len(all_analyzed),
        "avgLoss": round(avg, 3),
        "originalAvgLoss": round(original, 3),
        "improvement": round(improvement, 3),
        "rank": rank_of(avg),
        "grade": grade,
        "good": good,
        "normal": normal,
        "doubt": doubt,
        "bad": bad,
        "topProblems": [_session_eval_dict(e) for e in top],
        "bestMoves": [_session_eval_dict(e) for e in best],
        "verdict": verdict,
        "advice": advice,
        "originalSummary": task.get("summary", "") if task else "",
        "summary": ("训练评分 %s：%s 用户手平均目损 %.1f（原阶段同方 %.1f，改善 %.1f）。"
                    "好 %d / 普通 %d / 疑问 %d / 恶 %d。%s"
                    % (grade, verdict, avg, original, improvement, good, normal, doubt, bad, advice)),
    }
