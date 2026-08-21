"""move_quality —— 精细手段评价引擎（纯逻辑，不依赖 tkinter / KataGo 进程）。

接收单手 KataGo 分析结果，统一走子方视角，输出精细评价（最佳/好手/一般/不佳/恶手/未评价）+
综合评分 + 评价原因 + 置信度 + 可选问题标签。

设计原则（见 当前任务.md §3-8）：
  * 评价标准透明（quality_score + 原因列表）
  * 胜负已定时降级（is_meaningful_position）
  * 不重复 review.py 的 loss 计算——score_loss 输入时已转为走子方视角
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Optional

# ===================== 评价标签 =====================
QUALITY_BEST = "best"
QUALITY_GOOD = "good"
QUALITY_NORMAL = "normal"
QUALITY_INACCURACY = "inaccuracy"
QUALITY_BLUNDER = "blunder"
QUALITY_UNKNOWN = "unknown"

QUALITY_LABELS = {
    QUALITY_BEST: "最佳",
    QUALITY_GOOD: "好手",
    QUALITY_NORMAL: "一般",
    QUALITY_INACCURACY: "不佳",
    QUALITY_BLUNDER: "恶手",
    QUALITY_UNKNOWN: "未评价",
}

QUALITY_SCORE_RANGES = {
    QUALITY_BEST: (95, 100),
    QUALITY_GOOD: (80, 94),
    QUALITY_NORMAL: (60, 79),
    QUALITY_INACCURACY: (30, 59),
    QUALITY_BLUNDER: (0, 29),
}

# ===================== 问题标签 =====================
PROBLEM_TAGS = {
    "opening_direction": "布局方向",
    "endgame_value": "官子大小",
    "advantage_management": "优势保持",
    "comeback_attempt": "劣势胜负手",
    "tenuki_timing": "脱先时机",
    "overplay": "过分",
    "slack_move": "缓手",
    "life_and_death": "死活判断",
}

# ===================== 置信度 =====================
CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"
CONFIDENCE_UNKNOWN = "unknown"

VERSION = 1


# ===================== 数据结构 =====================
@dataclass
class MoveQualityInput:
    """单手评价输入（所有 loss/winrate 已转为走子方视角）。"""
    move_no: int
    color: str                                   # "B" / "W"
    played_move: str                             # GTP 坐标
    best_move: Optional[str] = None
    ai_rank: Optional[int] = None                # 实战手在父 moveInfos 排名（1=首选）
    score_loss: Optional[float] = None           # 走子方视角目损（≥0）
    winrate_drop: Optional[float] = None         # 走子方视角胜率损失（百分点）
    parent_winrate: Optional[float] = None       # 走子前黑方胜率 [0,1]
    parent_score_lead: Optional[float] = None    # 走子前黑方目差
    visits: Optional[int] = None
    analysis_available: bool = True              # 已分析但可能未进入返回候选
    candidate_count: Optional[int] = None
    stage: str = "opening"                       # opening / middle / endgame
    board_size: int = 19
    komi: float = 7.5
    rules: str = "chinese"


@dataclass
class MoveQualityResult:
    """单手评价输出。"""
    move_no: int
    color: str
    played_move: str
    best_move: Optional[str] = None

    quality_key: str = QUALITY_UNKNOWN            # 英文 key
    quality_label: str = "未评价"                  # 中文显示
    quality_score: int = 0                        # 0-100

    score_loss: Optional[float] = None
    winrate_drop: Optional[float] = None
    ai_rank: Optional[int] = None                 # 对外统一 1-based
    top1_match: bool = False
    top3_match: bool = False
    top5_match: bool = False

    stage: str = "opening"
    is_meaningful_position: bool = True
    confidence: str = CONFIDENCE_UNKNOWN
    visits: Optional[int] = None
    analysis_available: bool = True
    candidate_count: Optional[int] = None

    problem_tags: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    # 训练对齐元数据；普通复盘可留空。
    position_key: Optional[str] = None
    source_move_no: Optional[int] = None

    version: int = VERSION

    def to_dict(self) -> dict:
        """转成稳定、只含 JSON 基本类型的轻量结构。"""
        return {
            "move_no": self.move_no,
            "color": self.color,
            "played_move": self.played_move,
            "best_move": self.best_move,
            "quality_key": self.quality_key,
            "quality_label": self.quality_label,
            "quality_score": self.quality_score,
            "score_loss": self.score_loss,
            "winrate_drop": self.winrate_drop,
            "ai_rank": self.ai_rank,
            "top1_match": self.top1_match,
            "top3_match": self.top3_match,
            "top5_match": self.top5_match,
            "stage": self.stage,
            "is_meaningful_position": self.is_meaningful_position,
            "confidence": self.confidence,
            "visits": self.visits,
            "analysis_available": self.analysis_available,
            "candidate_count": self.candidate_count,
            "problem_tags": list(self.problem_tags or []),
            "reasons": list(self.reasons or []),
            "position_key": self.position_key,
            "source_move_no": self.source_move_no,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MoveQualityResult":
        """容忍旧字段缺失及未来未知字段。"""
        raw = data if isinstance(data, dict) else {}
        known = {item.name for item in fields(cls)}
        kwargs = {key: value for key, value in raw.items() if key in known}
        # 早期草稿曾把 ai_rank 保存为 0-based；显式标记时进行迁移。
        if raw.get("rank_base") == 0 and kwargs.get("ai_rank") is not None:
            kwargs["ai_rank"] = int(kwargs["ai_rank"]) + 1
        kwargs["problem_tags"] = list(kwargs.get("problem_tags") or [])
        kwargs["reasons"] = list(kwargs.get("reasons") or [])
        result = cls(**kwargs)
        result.quality_label = QUALITY_LABELS.get(
            result.quality_key, result.quality_label or QUALITY_LABELS[QUALITY_UNKNOWN])
        return result


# ===================== 评价算法 =====================
def is_meaningful_position(parent_winrate: Optional[float],
                           parent_score_lead: Optional[float]) -> bool:
    """是否胜负未定（避免碾压局巨大目差污染评价）。

    胜率 >= 0.98 或 <= 0.02 且目差 >= 30 → 胜负已定（False）。
    """
    if parent_winrate is None or parent_score_lead is None:
        return True
    if parent_winrate >= 0.98 or parent_winrate <= 0.02:
        if abs(parent_score_lead) >= 30:
            return False
    return True


def compute_quality_score(score_loss: Optional[float],
                          winrate_drop: Optional[float],
                          ai_rank: Optional[int],
                          meaningful: bool = True) -> int:
    """综合评分（0-100，越高越好）。"""
    score = 100
    if score_loss is not None:
        score -= min(score_loss * 7.0, 55)
    if winrate_drop is not None:
        score -= min(winrate_drop * 2.0, 35)
    if ai_rank is None:
        score -= 5
    elif ai_rank in (0, 1):      # 0 仅兼容旧调用；对外结果始终保存为 1
        score += 3
    elif ai_rank <= 3:
        score -= 3
    elif ai_rank <= 5:
        score -= 8
    else:
        score -= 15
    if not meaningful:
        score = max(score, 45)
        score = min(score, 80)
    return max(0, min(100, round(score)))


def score_to_quality(score: int) -> str:
    """评分 → 英文 quality key。"""
    if score >= 95:
        return QUALITY_BEST
    if score >= 80:
        return QUALITY_GOOD
    if score >= 60:
        return QUALITY_NORMAL
    if score >= 30:
        return QUALITY_INACCURACY
    return QUALITY_BLUNDER


def _determine_confidence(visits: Optional[int],
                          score_loss: Optional[float],
                          winrate_drop: Optional[float],
                          ai_rank: Optional[int]) -> str:
    """置信度：visits + 数据完整性。"""
    if score_loss is None and winrate_drop is None:
        return CONFIDENCE_UNKNOWN
    if (visits is not None and visits >= 150
            and score_loss is not None and winrate_drop is not None
            and ai_rank is not None):
        return CONFIDENCE_HIGH
    if visits is not None and visits >= 80:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_LOW


def evaluate_move(inp: MoveQualityInput) -> MoveQualityResult:
    """主入口：单手精细评价。"""
    sl = inp.score_loss
    wd = inp.winrate_drop
    # 对外契约统一 1-based；仅把历史调用中的 0 解释为第一推荐。
    ar = 1 if inp.ai_rank == 0 else inp.ai_rank
    meaningful = is_meaningful_position(inp.parent_winrate, inp.parent_score_lead)
    confidence = _determine_confidence(inp.visits, sl, wd, ar)

    # 数据不足 → unknown
    if sl is None and wd is None:
        result = MoveQualityResult(
            move_no=inp.move_no, color=inp.color, played_move=inp.played_move,
            best_move=inp.best_move, quality_key=QUALITY_UNKNOWN,
            quality_label=QUALITY_LABELS[QUALITY_UNKNOWN],
            score_loss=sl, winrate_drop=wd, ai_rank=ar,
            top1_match=(ar == 1), top3_match=(ar is not None and ar <= 3),
            top5_match=(ar is not None and ar <= 5),
            stage=inp.stage, is_meaningful_position=meaningful,
            confidence=confidence, visits=inp.visits,
            analysis_available=inp.analysis_available,
            candidate_count=inp.candidate_count, version=VERSION)
        result.reasons = build_quality_reasons(result)
        return result

    # 综合评分
    qscore = compute_quality_score(sl, wd, ar, meaningful)

    # 规则覆盖（§6.5）
    quality = score_to_quality(qscore)

    # 强制规则
    if ar == 1 and sl is not None and sl <= 0.3 and (wd is None or wd <= 0.5):
        quality = QUALITY_BEST
        qscore = max(qscore, 95)
    elif ((sl is not None and sl > 10) or (wd is not None and wd > 18)) and meaningful:
        quality = QUALITY_BLUNDER
        qscore = min(qscore, 29)
    elif not meaningful and quality == QUALITY_BLUNDER:
        # 胜负已定：最多降到「不佳」（除非 winrate_drop 仍 > 15）
        if wd is None or wd <= 15:
            quality = QUALITY_INACCURACY
            qscore = max(qscore, 30)

    # 胜负已定但 winrate_drop 仍 > 15 + 大目损 → 维持恶手（spec §6.5 规则 5）
    if not meaningful and wd is not None and wd > 15 and (sl or 0.0) > 5.0:
        quality = QUALITY_BLUNDER
        qscore = min(qscore, 29)

    # 阈值交叉检查（§6.3 基础阈值，补充覆盖）
    if quality == QUALITY_NORMAL and (sl or 0.0) > 3.0 and (wd is None or wd > 5.0):
        quality = QUALITY_INACCURACY
        qscore = min(qscore, 59)

    tags = _assign_problem_tags(inp, quality, meaningful)
    result = MoveQualityResult(
        move_no=inp.move_no, color=inp.color, played_move=inp.played_move,
        best_move=inp.best_move, quality_key=quality,
        quality_label=QUALITY_LABELS.get(quality, quality),
        quality_score=qscore, score_loss=sl, winrate_drop=wd, ai_rank=ar,
        top1_match=(ar == 1), top3_match=(ar is not None and ar <= 3),
        top5_match=(ar is not None and ar <= 5),
        stage=inp.stage, is_meaningful_position=meaningful,
        confidence=confidence, visits=inp.visits,
        analysis_available=inp.analysis_available,
        candidate_count=inp.candidate_count,
        problem_tags=tags, version=VERSION)
    result.reasons = build_quality_reasons(result)
    return result


def build_quality_reasons(result: MoveQualityResult) -> list[str]:
    """生成人类可读的中文评价原因（不过度断言）。"""
    reasons = []
    sl = result.score_loss
    wd = result.winrate_drop
    ar = result.ai_rank

    if result.quality_key == QUALITY_UNKNOWN:
        reasons.append("当前局面缺少完整分析，暂不评价。")
        if result.visits is not None:
            reasons.append("当前分析为 %d visits。" % result.visits)
        return reasons

    if result.top1_match:
        reasons.append("实战手与 AI 第一推荐一致。")
    elif ar is not None and ar <= 3:
        reasons.append("实战手位于 AI 前三推荐（第 %d 选）。" % ar)
    elif ar is not None and ar <= 5:
        reasons.append("实战手位于 AI 前五推荐（第 %d 选）。" % ar)
    elif ar is not None:
        reasons.append("实战手是当前返回候选中的第 %d 选。" % ar)
    elif result.analysis_available:
        count = result.candidate_count
        if count:
            reasons.append("实战手未进入当前返回的 %d 个 AI 候选。" % count)
        else:
            reasons.append("实战手未进入当前返回的 AI 候选。")

    if sl is not None:
        if sl < 0.5:
            reasons.append("目损 %.1f，接近最佳。" % sl)
        elif sl < 3.0:
            reasons.append("目损 %.1f，影响较小。" % sl)
        elif sl < 7.0:
            reasons.append("目损 %.1f，属于需要复盘的问题手。" % sl)
        else:
            reasons.append("目损 %.1f，AI 视角下该手损失明显。" % sl)

    if wd is not None:
        if wd > 5.0:
            reasons.append("胜率下降 %.1f 个百分点。" % wd)

    if not result.is_meaningful_position:
        reasons.append("此局面胜负已定，评价严重度已降低。")

    stage_labels = {"opening": "布局", "middle": "中盘", "endgame": "官子"}
    stage = stage_labels.get(result.stage, result.stage)
    reasons.append("阶段：%s。" % stage)

    if result.confidence == CONFIDENCE_LOW:
        reasons.append("置信度较低（visits 不足或数据缺失）。")
    if result.visits is not None:
        reasons.append("该判断基于当前模型和 %d visits。" % result.visits)
    else:
        reasons.append("该判断基于当前模型；本次分析未提供 visits。")

    return reasons


def _assign_problem_tags(inp: MoveQualityInput, quality: str,
                         meaningful: bool) -> list[str]:
    """启发式问题标签（第一版 6-8 个高置信标签）。"""
    tags = []
    sl = inp.score_loss or 0
    wd = inp.winrate_drop or 0
    ar = 1 if inp.ai_rank == 0 else inp.ai_rank

    # 布局方向：布局阶段 + 明显目损 + 不在前 5
    if inp.stage == "opening" and sl >= 3.0 and (ar is None or ar > 5):
        tags.append("opening_direction")

    # 官子大小：官子阶段 + 目损 >= 2 + 胜率变化小
    if inp.stage == "endgame" and sl >= 2.0 and wd < 5.0:
        tags.append("endgame_value")

    # 优势保持：走子前明显优势 + 该手明显目损
    if inp.parent_winrate is not None:
        player_wr = inp.parent_winrate if inp.color == "B" else (1 - inp.parent_winrate)
        if player_wr >= 0.65 and sl >= 3.0:
            tags.append("advantage_management")

    # 劣势胜负手：走子前明显劣势 + 目损大但胜率变化小
    if inp.parent_winrate is not None:
        player_wr = inp.parent_winrate if inp.color == "B" else (1 - inp.parent_winrate)
        if player_wr <= 0.35 and sl >= 4.0 and wd < 6.0:
            tags.append("comeback_attempt")

    # 过分：目损大 + 胜率大降
    if sl >= 5.0 and wd >= 8.0:
        tags.append("overplay")

    # 缓手：中盘 + 中等目损 + 胜率小变
    if inp.stage == "middle" and 1.5 <= sl < 4.0 and wd < 3.0:
        tags.append("slack_move")

    return tags
