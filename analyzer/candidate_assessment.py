"""candidate_assessment —— 按实际目损判定用户选点（项目大纲 §20-25）。

废除"AI 前 3 名 = 正确"的排名判分：围棋第 4 选可能只亏 0.4 目（完全正确），
第 2 选可能亏 5 目（明显错误）。正确性的核心标准是相对 AI 首选的实际目损，
按用户棋力档与局面复杂度动态放宽。

数据来源二选一（§23）：
1. 父局面 moveInfos 已含该手 → 直接取目损/排名；
2. 榜外手 → 调用方用 forced_move_query 让 KataGo 强制分析（allowMoves），
   把结果的 scoreLead 传入本模块再判定——绝不能因为"不在 Top5"直接判错。
"""
from __future__ import annotations

from candidate_recommendation import skill_tolerance

# 判定档位（§22，第一版产品参数，后续用真实数据与强棋手标注校准）
ASSESSMENT_BEST = "best"                # ≤0.2 目
ASSESSMENT_EXCELLENT = "excellent"      # ≤0.8 目
ASSESSMENT_ACCEPTABLE = "acceptable"    # ≤1.5 目
ASSESSMENT_QUESTIONABLE = "questionable"
ASSESSMENT_BAD = "bad"
ASSESSMENT_UNKNOWN = "unknown"

ASSESSMENT_LABELS = {
    ASSESSMENT_BEST: "AI最佳",
    ASSESSMENT_EXCELLENT: "优秀",
    ASSESSMENT_ACCEPTABLE: "合理",
    ASSESSMENT_QUESTIONABLE: "可疑",
    ASSESSMENT_BAD: "明显问题",
    ASSESSMENT_UNKNOWN: "数据不足",
}

THRESHOLD_BEST = 0.2
THRESHOLD_EXCELLENT = 0.8
THRESHOLD_ACCEPTABLE = 1.5
QUESTIONABLE_MARGIN = 3.0      # 超出动态容差 3 目以内 = 可疑，再往上 = 明显问题
COMPLEXITY_SLACK = 0.5         # 复杂度 0-1 每满档放宽 0.5 目

# 主动复盘四分类（§25）
RETRY_CORRECTED = "corrected"
RETRY_IMPROVED = "improved"
RETRY_REPEATED = "repeated"
RETRY_ALTERNATIVE_CORRECT = "alternative_correct"

REASONABLE = (ASSESSMENT_BEST, ASSESSMENT_EXCELLENT, ASSESSMENT_ACCEPTABLE)

SRS_BY_ASSESSMENT = {
    ASSESSMENT_BEST: "good",
    ASSESSMENT_EXCELLENT: "good",
    ASSESSMENT_ACCEPTABLE: "hard",
    ASSESSMENT_QUESTIONABLE: "again",
    ASSESSMENT_BAD: "again",
    ASSESSMENT_UNKNOWN: "again",   # 数据不足按保守处理，不白给通过
}


def _num(value, default=None):
    try:
        return default if value is None else float(value)
    except (TypeError, ValueError):
        return default


def _mover_delta(best_score, move_score, color):
    """走子方视角的一选-本手目差。"""
    sign = 1.0 if str(color).upper() == "B" else -1.0
    return max(0.0, sign * (best_score - move_score))


def dynamic_tolerance(performance_label=None, complexity=0.0):
    """当前水平可接受的动态目损容差（§22）。"""
    base = max(THRESHOLD_ACCEPTABLE, skill_tolerance(performance_label))
    try:
        complexity = min(max(float(complexity or 0.0), 0.0), 1.0)
    except (TypeError, ValueError):
        complexity = 0.0
    return base + complexity * COMPLEXITY_SLACK


def forced_move_query(query, move):
    """把父局面查询改为强制分析某选点（allowMoves 限定根搜索，§23）。"""
    q = dict(query or {})
    q["allowMoves"] = [str(move)]
    return q


def forced_move_result(resp, move):
    """从强制分析响应取该手的 (scoreLead, winrate, order)；无数据返回 (None,)*3。"""
    move = str(move)
    for info in (resp or {}).get("moveInfos") or []:
        if str(info.get("move") or "").lower() == move.lower():
            return _num(info.get("scoreLead")), _num(info.get("winrate")), \
                int(info.get("order", 0))
    return None, None, None


def assess_candidate(move, move_infos=None, color="B", *,
                     forced_score_lead=None, forced_winrate=None,
                     best_score_lead=None, best_winrate=None,
                     performance_label=None, complexity=0.0):
    """判定一个用户选点，返回 CandidateAssessment dict（§21）。

    move_infos 含该手时优先用（source="moveInfos"）；否则用强制分析结果
    （source="forced"，需同时给 best_score_lead 一选目差）；都没有则
    assessment=unknown（source="insufficient"），不猜。
    """
    move = str(move or "pass")
    color = str(color or "B").upper()
    tolerance = dynamic_tolerance(performance_label, complexity)

    result = {
        "move": move,
        "score_loss": None,
        "winrate_loss": None,
        "ai_rank": None,
        "complexity": float(complexity or 0.0),
        "tolerance": round(tolerance, 3),
        "current_level_ok": False,
        "assessment": ASSESSMENT_UNKNOWN,
        "assessment_label": ASSESSMENT_LABELS[ASSESSMENT_UNKNOWN],
        "source": "insufficient",
    }

    ordered = sorted(move_infos or [], key=lambda m: m.get("order", 999))
    info = None
    rank = None
    for idx, m in enumerate(ordered):
        if str(m.get("move") or "").lower() == move.lower():
            info = m
            rank = int(m.get("order", idx)) + 1
            break

    if info is not None:
        best = ordered[0]
        score_loss = _mover_delta(_num(best.get("scoreLead"), 0.0),
                                  _num(info.get("scoreLead"), 0.0), color)
        best_wr = _num(best.get("winrate"))
        wr = _num(info.get("winrate"))
        wr_loss = None
        if best_wr is not None and wr is not None:
            delta = (best_wr - wr) if color == "B" else (wr - best_wr)
            wr_loss = max(0.0, delta * 100.0)
        result.update(score_loss=round(score_loss, 3), winrate_loss=wr_loss,
                      ai_rank=rank, source="moveInfos")
    elif forced_score_lead is not None and best_score_lead is not None:
        score_loss = _mover_delta(best_score_lead, forced_score_lead, color)
        wr_loss = None
        if best_winrate is not None and forced_winrate is not None:
            delta = (best_winrate - forced_winrate) if color == "B" \
                else (forced_winrate - best_winrate)
            wr_loss = max(0.0, delta * 100.0)
        result.update(score_loss=round(score_loss, 3), winrate_loss=wr_loss,
                      source="forced")

    loss = result["score_loss"]
    if loss is not None:
        if loss <= THRESHOLD_BEST:
            level = ASSESSMENT_BEST
        elif loss <= THRESHOLD_EXCELLENT:
            level = ASSESSMENT_EXCELLENT
        elif loss <= THRESHOLD_ACCEPTABLE:
            level = ASSESSMENT_ACCEPTABLE
        elif loss <= tolerance:
            level = ASSESSMENT_ACCEPTABLE
            result["current_level_ok"] = True
        elif loss <= tolerance + QUESTIONABLE_MARGIN:
            level = ASSESSMENT_QUESTIONABLE
        else:
            level = ASSESSMENT_BAD
        result["assessment"] = level
        result["assessment_label"] = ASSESSMENT_LABELS[level]

    return result


def classify_retry(actual_loss, retry_assessment, retry_loss=None,
                   *, improved_ratio=0.5, repeat_margin=1.0):
    """主动复盘结果四分类（§25）。

    - corrected：重选达到优秀（接近 AI 方案）
    - alternative_correct：重选合理但非最优（另一个完全可行的方案）
    - improved：目损比实战改善 ≥50%，但尚未达到合理标准
    - repeated：目损与实战相当或更差（真正的知识盲区）
    """
    if retry_assessment in (ASSESSMENT_BEST, ASSESSMENT_EXCELLENT):
        return RETRY_CORRECTED
    if retry_assessment == ASSESSMENT_ACCEPTABLE:
        return RETRY_ALTERNATIVE_CORRECT
    actual = _num(actual_loss)
    retry = _num(retry_loss)
    if actual is None or retry is None:
        return RETRY_REPEATED
    if retry <= actual * improved_ratio:
        return RETRY_IMPROVED
    return RETRY_REPEATED


def srs_result(assessment):
    """判定档位 → 错题本 SRS 结果（good/hard/again）。"""
    return SRS_BY_ASSESSMENT.get(assessment, "again")
