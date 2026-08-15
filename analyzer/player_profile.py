"""player_profile —— 长期个人画像纯逻辑（不依赖 tkinter / KataGo 进程）。

把多盘棋的每手精细评价（move_quality.MoveQualityResult）聚合为长期画像，
回答"我长期弱在哪里、最近有没有进步"。本模块只做统计与结论生成，
不直接读写 index.json（持久化统一走 game_library 接口，见 当前任务.md §27.7）。

设计原则（见 当前任务.md §9 / §27）：
  * 聚合按【有效手数】加权——所有有效手的总目损 / 有效手数，
    绝不用"各盘平均目损的平均数"（§27.2）。
  * 主要样本只取 confidence != unknown 且 is_meaningful_position=True 的评价；
    胜负已定的评价另存，不污染平均目损与错误率（§27.1）。
  * 样本不足时降级：阶段 < 8 手不下强结论，趋势 < 10 盘不下进步/下降结论。
  * 结论可追溯到统计证据——文案带"最近 N 盘、第 X 阶段、多少手"，
    不输出虚构段位（§27.5）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from move_quality import (
    MoveQualityResult,
    QUALITY_BEST, QUALITY_GOOD, QUALITY_NORMAL, QUALITY_INACCURACY,
    QUALITY_BLUNDER, QUALITY_UNKNOWN,
    CONFIDENCE_UNKNOWN,
    PROBLEM_TAGS,
)

# ===================== 常量 =====================
VERSION = 2

# 三阶段
PHASES = ("opening", "middle", "endgame")
PHASE_LABELS = {"opening": "布局", "middle": "中盘", "endgame": "官子"}

# 走子方
SIDES = ("B", "W")
SIDE_LABELS = {"B": "执黑", "W": "执白"}

# user_side 取值
SIDE_BLACK = "B"
SIDE_WHITE = "W"
SIDE_BOTH = "both"
SIDE_UNKNOWN = "unknown"

# 质量分布的固定 key 顺序
QUALITY_KEYS = (QUALITY_BEST, QUALITY_GOOD, QUALITY_NORMAL,
                QUALITY_INACCURACY, QUALITY_BLUNDER, QUALITY_UNKNOWN)

# 样本门槛（§27.5）
MIN_MOVES_PHASE_CONCLUSION = 8      # 阶段下强结论所需最少有效手
MIN_TAG_COUNT_FOR_ADVICE = 3        # 问题标签进入建议的最少出现次数
MIN_GAMES_STRONG_TREND = 10         # 下"进步/下降"结论所需最少盘数
MIN_GAMES_TREND_LISTING = 5         # 逐盘趋势所需最少盘数
RECENT_TREND_WINDOW = 5             # 最近窗口盘数
BASELINE_TREND_WINDOW = 10          # 基线窗口盘数

# 趋势判定阈值（§27.4）
TREND_LOSS_DELTA = 0.3              # 加权平均目损变化（目）
TREND_BLUNDER_DELTA = 2.0           # 恶手率变化（百分点）
TREND_TOP3_DELTA = 5.0              # 前 3 吻合度变化（百分点）

# 速率单位：match_rate / error_rate 一律用百分点（0-100），与 review.py 一致


# ===================== 数据结构 =====================
@dataclass
class GameProfileSummary:
    """单局画像摘要（轻量、可持久化）。

    关键字段 score_loss_sum / winrate_drop_sum / evaluated_moves 用于
    让长期聚合按有效手数加权，避免"平均数的平均数"（§27.2）。
    """
    game_id: str
    game_name: str = ""
    black_player: Optional[str] = None
    white_player: Optional[str] = None
    user_side: str = SIDE_UNKNOWN     # "B" / "W" / "both" / "unknown"

    analyzed_at: str = ""
    model: Optional[str] = None
    visits: Optional[int] = None
    analysis_signature: dict = field(default_factory=dict)

    total_moves: int = 0                         # 全部评价手数（含 unknown / 胜负已定）
    evaluated_moves: int = 0                     # 有效手数（主要样本）

    score_loss_sum: float = 0.0                  # 有效手目损总和（走子方视角，≥0）
    winrate_drop_sum: float = 0.0                # 有效手胜率损失总和（百分点）
    avg_score_loss: Optional[float] = None
    avg_winrate_drop: Optional[float] = None

    # 各质量计数：{best/good/normal/inaccuracy/blunder/unknown: int}
    quality_counts: dict = field(default_factory=dict)
    # 三阶段：{opening/middle/endgame: GameStageStat.to_dict()}
    stage_stats: dict = field(default_factory=dict)
    # 黑白：{"B": {...}, "W": {...}}
    color_stats: dict = field(default_factory=dict)

    top1_match_rate: Optional[float] = None      # 百分点
    top3_match_rate: Optional[float] = None
    top5_match_rate: Optional[float] = None

    # 问题标签计数：{tag_key: count}（仅在有效手上统计）
    problem_tag_counts: dict = field(default_factory=dict)

    # 最值得复盘的问题手（取前若干个，目损最大的有效手）——展示/错题本队列用
    top_problem_moves: list = field(default_factory=list)
    # 全部达到学习阈值（≥2 目或恶手）的问题手——LearningEvent 入库用。
    # 长期复发统计必须看到全部问题，"亏 4 目但反复犯"的题不能因单盘目损
    # 排不进 Top5 而从学习数据库里消失（项目大纲 §9 的核心场景）。
    problem_moves_all: list = field(default_factory=list)

    # 胜负已定手数（不计入主要样本，仅备查）
    settled_moves: int = 0

    version: int = VERSION

    def to_dict(self) -> dict:
        return {
            "game_id": self.game_id,
            "game_name": self.game_name,
            "black_player": self.black_player,
            "white_player": self.white_player,
            "user_side": self.user_side,
            "analyzed_at": self.analyzed_at,
            "model": self.model,
            "visits": self.visits,
            "analysis_signature": dict(self.analysis_signature or {}),
            "total_moves": self.total_moves,
            "total_evaluated_moves": self.evaluated_moves,
            "evaluated_moves": self.evaluated_moves,
            "score_loss_sum": self.score_loss_sum,
            "winrate_drop_sum": self.winrate_drop_sum,
            "avg_score_loss": self.avg_score_loss,
            "avg_winrate_drop": self.avg_winrate_drop,
            "quality_counts": dict(self.quality_counts or {}),
            "stage_stats": dict(self.stage_stats or {}),
            "color_stats": dict(self.color_stats or {}),
            "top1_match_rate": self.top1_match_rate,
            "top3_match_rate": self.top3_match_rate,
            "top5_match_rate": self.top5_match_rate,
            "problem_tag_counts": dict(self.problem_tag_counts or {}),
            "top_problem_moves": list(self.top_problem_moves or []),
            "problem_moves_all": list(self.problem_moves_all or []),
            "settled_moves": self.settled_moves,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GameProfileSummary":
        raw = data if isinstance(data, dict) else {}
        aliases = {
            "game_id": ("game_id", "gameId", "id"),
            "game_name": ("game_name", "gameName", "name"),
            "black_player": ("black_player", "blackPlayer"),
            "white_player": ("white_player", "whitePlayer"),
            "user_side": ("user_side", "profileSide", "profile_side"),
            "analyzed_at": ("analyzed_at", "analyzedAt", "updatedAt"),
            "analysis_signature": ("analysis_signature", "analysisSignature"),
            "total_moves": ("total_moves", "totalMoves"),
            "evaluated_moves": (
                "evaluated_moves", "total_evaluated_moves", "evaluatedMoves"),
            "score_loss_sum": ("score_loss_sum", "scoreLossSum"),
            "winrate_drop_sum": ("winrate_drop_sum", "winrateDropSum"),
            "avg_score_loss": ("avg_score_loss", "avgScoreLoss"),
            "avg_winrate_drop": ("avg_winrate_drop", "avgWinrateDrop"),
            "quality_counts": ("quality_counts", "qualityCounts"),
            "stage_stats": ("stage_stats", "stageStats"),
            "color_stats": ("color_stats", "colorStats"),
            "top1_match_rate": ("top1_match_rate", "top1MatchRate"),
            "top3_match_rate": ("top3_match_rate", "top3MatchRate"),
            "top5_match_rate": ("top5_match_rate", "top5MatchRate"),
            "problem_tag_counts": ("problem_tag_counts", "problemTagCounts"),
            "top_problem_moves": ("top_problem_moves", "topProblemMoves"),
            "problem_moves_all": ("problem_moves_all", "problemMovesAll"),
            "settled_moves": ("settled_moves", "settledMoves"),
        }

        def pick(name, default=None):
            for key in aliases.get(name, (name,)):
                if key in raw:
                    return raw[key]
            return default

        return cls(
            game_id=str(pick("game_id", "")),
            game_name=pick("game_name", "") or "",
            black_player=pick("black_player"),
            white_player=pick("white_player"),
            user_side=pick("user_side", SIDE_UNKNOWN) or SIDE_UNKNOWN,
            analyzed_at=pick("analyzed_at", "") or "",
            model=raw.get("model"),
            visits=raw.get("visits"),
            analysis_signature=dict(pick("analysis_signature", {}) or {}),
            total_moves=int(pick("total_moves", 0) or 0),
            evaluated_moves=int(pick("evaluated_moves", 0) or 0),
            score_loss_sum=float(pick("score_loss_sum", 0.0) or 0.0),
            winrate_drop_sum=float(pick("winrate_drop_sum", 0.0) or 0.0),
            avg_score_loss=pick("avg_score_loss"),
            avg_winrate_drop=pick("avg_winrate_drop"),
            quality_counts=dict(pick("quality_counts", {}) or {}),
            stage_stats=dict(pick("stage_stats", {}) or {}),
            color_stats=dict(pick("color_stats", {}) or {}),
            top1_match_rate=pick("top1_match_rate"),
            top3_match_rate=pick("top3_match_rate"),
            top5_match_rate=pick("top5_match_rate"),
            problem_tag_counts=dict(pick("problem_tag_counts", {}) or {}),
            top_problem_moves=list(pick("top_problem_moves", []) or []),
            problem_moves_all=list(pick("problem_moves_all", []) or []),
            settled_moves=int(pick("settled_moves", 0) or 0),
            version=int(raw.get("version", VERSION) or VERSION),
        )


@dataclass
class GameStageStat:
    """单局某阶段的聚合（用于 GameProfileSummary.stage_stats）。"""
    moves: int = 0
    score_loss_sum: float = 0.0
    winrate_drop_sum: float = 0.0
    quality_counts: dict = field(default_factory=dict)
    top1_match: int = 0
    top3_match: int = 0
    top5_match: int = 0
    problem_tag_counts: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "moves": self.moves,
            "score_loss_sum": self.score_loss_sum,
            "winrate_drop_sum": self.winrate_drop_sum,
            "quality_counts": dict(self.quality_counts),
            "top1_match": self.top1_match,
            "top3_match": self.top3_match,
            "top5_match": self.top5_match,
            "problem_tag_counts": dict(self.problem_tag_counts),
        }


@dataclass
class ProfileStats:
    """画像某一维度的聚合统计（综合 / 执黑 / 执白 / 布局 / 中盘 / 官子）。

    聚合方式：把所有命中该维度的有效手"展平"后加权统计，
    score_loss_sum / winrate_drop_sum / moves 来自这些手的总和，
    而不是各盘均值的平均（§27.2 / §27.3）。
    """
    games: int = 0                       # 命中该维度的盘数
    moves: int = 0                       # 有效手数
    score_loss_sum: float = 0.0
    avg_score_loss: Optional[float] = None
    winrate_drop_sum: float = 0.0
    avg_winrate_drop: Optional[float] = None

    quality_counts: dict = field(default_factory=dict)   # 各质量计数
    blunder_rate: Optional[float] = None                 # 恶手率（百分点）
    inaccuracy_rate: Optional[float] = None              # 不佳率（百分点）
    top1_match_rate: Optional[float] = None              # 百分点
    top3_match_rate: Optional[float] = None
    top5_match_rate: Optional[float] = None
    problem_tag_counts: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "games": self.games,
            "moves": self.moves,
            "score_loss_sum": self.score_loss_sum,
            "avg_score_loss": self.avg_score_loss,
            "winrate_drop_sum": self.winrate_drop_sum,
            "avg_winrate_drop": self.avg_winrate_drop,
            "quality_counts": dict(self.quality_counts),
            "blunder_rate": self.blunder_rate,
            "inaccuracy_rate": self.inaccuracy_rate,
            "top1_match_rate": self.top1_match_rate,
            "top3_match_rate": self.top3_match_rate,
            "top5_match_rate": self.top5_match_rate,
            "problem_tag_counts": dict(self.problem_tag_counts),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ProfileStats":
        raw = data if isinstance(data, dict) else {}
        kwargs = {}
        for name in (
                "games", "moves", "score_loss_sum", "avg_score_loss",
                "winrate_drop_sum", "avg_winrate_drop", "quality_counts",
                "blunder_rate", "inaccuracy_rate", "top1_match_rate",
                "top3_match_rate", "top5_match_rate", "problem_tag_counts"):
            if name in raw:
                kwargs[name] = raw[name]
        kwargs["quality_counts"] = dict(kwargs.get("quality_counts") or {})
        kwargs["problem_tag_counts"] = dict(kwargs.get("problem_tag_counts") or {})
        return cls(**kwargs)


@dataclass
class GameTrendPoint:
    """趋势图上的一盘：按时间顺序。"""
    game_id: str
    order: int                          # 在画像样本中的序号（0=最早）
    evaluated_moves: int
    avg_score_loss: Optional[float]
    blunder_rate: Optional[float]       # 百分点
    top3_match_rate: Optional[float]    # 百分点

    def to_dict(self) -> dict:
        return {
            "game_id": self.game_id,
            "order": self.order,
            "evaluated_moves": self.evaluated_moves,
            "avg_score_loss": self.avg_score_loss,
            "blunder_rate": self.blunder_rate,
            "top3_match_rate": self.top3_match_rate,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GameTrendPoint":
        raw = data if isinstance(data, dict) else {}
        return cls(
            game_id=str(raw.get("game_id", raw.get("gameId", ""))),
            order=int(raw.get("order", 0) or 0),
            evaluated_moves=int(raw.get("evaluated_moves", 0) or 0),
            avg_score_loss=raw.get("avg_score_loss"),
            blunder_rate=raw.get("blunder_rate"),
            top3_match_rate=raw.get("top3_match_rate"),
        )


@dataclass
class TrendResult:
    """最近趋势结论（§27.4）。"""
    direction: str = "insufficient"     # improving / stable / declining / insufficient
    evidence: list = field(default_factory=list)   # 中文证据条目
    recent_games: int = 0
    baseline_games: int = 0
    # 窗口指标（用于 UI 展示与测试断言）
    recent_avg_loss: Optional[float] = None
    baseline_avg_loss: Optional[float] = None
    recent_blunder_rate: Optional[float] = None
    baseline_blunder_rate: Optional[float] = None
    recent_top3_rate: Optional[float] = None
    baseline_top3_rate: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "direction": self.direction,
            "evidence": list(self.evidence),
            "recent_games": self.recent_games,
            "baseline_games": self.baseline_games,
            "recent_avg_loss": self.recent_avg_loss,
            "baseline_avg_loss": self.baseline_avg_loss,
            "recent_blunder_rate": self.recent_blunder_rate,
            "baseline_blunder_rate": self.baseline_blunder_rate,
            "recent_top3_rate": self.recent_top3_rate,
            "baseline_top3_rate": self.baseline_top3_rate,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TrendResult":
        raw = data if isinstance(data, dict) else {}
        return cls(
            direction=raw.get("direction", "insufficient"),
            evidence=list(raw.get("evidence") or []),
            recent_games=int(raw.get("recent_games", 0) or 0),
            baseline_games=int(raw.get("baseline_games", 0) or 0),
            recent_avg_loss=raw.get("recent_avg_loss"),
            baseline_avg_loss=raw.get("baseline_avg_loss"),
            recent_blunder_rate=raw.get("recent_blunder_rate"),
            baseline_blunder_rate=raw.get("baseline_blunder_rate"),
            recent_top3_rate=raw.get("recent_top3_rate"),
            baseline_top3_rate=raw.get("baseline_top3_rate"),
        )


@dataclass
class GameBenchmark:
    """最近一盘相对既往个人基线的比较结果。"""
    status: str = "insufficient"       # better / similar / worse / insufficient
    confidence: str = "low"            # low / medium / high
    prior_games: int = 0
    current_moves: int = 0
    baseline_moves: int = 0
    current_avg_loss: Optional[float] = None
    baseline_avg_loss: Optional[float] = None
    loss_improvement: Optional[float] = None   # 正数=本局更好
    current_blunder_rate: Optional[float] = None
    baseline_blunder_rate: Optional[float] = None
    current_top3_rate: Optional[float] = None
    baseline_top3_rate: Optional[float] = None
    stage_comparisons: dict = field(default_factory=dict)
    evidence: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "confidence": self.confidence,
            "prior_games": self.prior_games,
            "current_moves": self.current_moves,
            "baseline_moves": self.baseline_moves,
            "current_avg_loss": self.current_avg_loss,
            "baseline_avg_loss": self.baseline_avg_loss,
            "loss_improvement": self.loss_improvement,
            "current_blunder_rate": self.current_blunder_rate,
            "baseline_blunder_rate": self.baseline_blunder_rate,
            "current_top3_rate": self.current_top3_rate,
            "baseline_top3_rate": self.baseline_top3_rate,
            "stage_comparisons": dict(self.stage_comparisons),
            "evidence": list(self.evidence),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GameBenchmark":
        raw = data if isinstance(data, dict) else {}
        return cls(
            status=raw.get("status", "insufficient"),
            confidence=raw.get("confidence", "low"),
            prior_games=int(raw.get("prior_games", 0) or 0),
            current_moves=int(raw.get("current_moves", 0) or 0),
            baseline_moves=int(raw.get("baseline_moves", 0) or 0),
            current_avg_loss=raw.get("current_avg_loss"),
            baseline_avg_loss=raw.get("baseline_avg_loss"),
            loss_improvement=raw.get("loss_improvement"),
            current_blunder_rate=raw.get("current_blunder_rate"),
            baseline_blunder_rate=raw.get("baseline_blunder_rate"),
            current_top3_rate=raw.get("current_top3_rate"),
            baseline_top3_rate=raw.get("baseline_top3_rate"),
            stage_comparisons=dict(raw.get("stage_comparisons") or {}),
            evidence=list(raw.get("evidence") or []),
        )


@dataclass
class PlayerProfile:
    """长期个人画像（§9.4）。"""
    profile_id: str = ""
    player_names: list = field(default_factory=list)
    generated_at: str = ""

    games_count: int = 0
    evaluated_moves_count: int = 0

    user_side: str = SIDE_UNKNOWN       # 画像覆盖的执棋方

    overall: ProfileStats = field(default_factory=ProfileStats)
    black: ProfileStats = field(default_factory=ProfileStats)
    white: ProfileStats = field(default_factory=ProfileStats)

    opening: ProfileStats = field(default_factory=ProfileStats)
    middle: ProfileStats = field(default_factory=ProfileStats)
    endgame: ProfileStats = field(default_factory=ProfileStats)

    recent_trend: TrendResult = field(default_factory=TrendResult)
    trend_points: list = field(default_factory=list)   # list[GameTrendPoint]
    quality_distribution: dict = field(default_factory=dict)
    problem_tag_distribution: dict = field(default_factory=dict)

    strengths: list = field(default_factory=list)      # list[str]
    weaknesses: list = field(default_factory=list)
    recommendations: list = field(default_factory=list)
    excluded_incompatible_games: int = 0

    version: int = VERSION

    def to_dict(self) -> dict:
        return {
            "profile_id": self.profile_id,
            "player_names": list(self.player_names),
            "generated_at": self.generated_at,
            "games_count": self.games_count,
            "evaluated_moves_count": self.evaluated_moves_count,
            "user_side": self.user_side,
            "overall": self.overall.to_dict(),
            "black": self.black.to_dict(),
            "white": self.white.to_dict(),
            "opening": self.opening.to_dict(),
            "middle": self.middle.to_dict(),
            "endgame": self.endgame.to_dict(),
            "recent_trend": self.recent_trend.to_dict(),
            "trend_points": [p.to_dict() for p in self.trend_points],
            "quality_distribution": dict(self.quality_distribution),
            "problem_tag_distribution": dict(self.problem_tag_distribution),
            "strengths": list(self.strengths),
            "weaknesses": list(self.weaknesses),
            "recommendations": list(self.recommendations),
            "excluded_incompatible_games": self.excluded_incompatible_games,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PlayerProfile":
        raw = data if isinstance(data, dict) else {}
        return cls(
            profile_id=raw.get("profile_id", ""),
            player_names=list(raw.get("player_names") or []),
            generated_at=raw.get("generated_at", ""),
            games_count=int(raw.get("games_count", 0) or 0),
            evaluated_moves_count=int(raw.get("evaluated_moves_count", 0) or 0),
            user_side=raw.get("user_side", SIDE_UNKNOWN),
            overall=ProfileStats.from_dict(raw.get("overall") or {}),
            black=ProfileStats.from_dict(raw.get("black") or {}),
            white=ProfileStats.from_dict(raw.get("white") or {}),
            opening=ProfileStats.from_dict(raw.get("opening") or {}),
            middle=ProfileStats.from_dict(raw.get("middle") or {}),
            endgame=ProfileStats.from_dict(raw.get("endgame") or {}),
            recent_trend=TrendResult.from_dict(raw.get("recent_trend") or {}),
            trend_points=[
                GameTrendPoint.from_dict(item)
                for item in (raw.get("trend_points") or [])
                if isinstance(item, dict)
            ],
            quality_distribution=dict(raw.get("quality_distribution") or {}),
            problem_tag_distribution=dict(raw.get("problem_tag_distribution") or {}),
            strengths=list(raw.get("strengths") or []),
            weaknesses=list(raw.get("weaknesses") or []),
            recommendations=list(raw.get("recommendations") or []),
            excluded_incompatible_games=int(
                raw.get("excluded_incompatible_games", 0) or 0),
            version=int(raw.get("version", VERSION) or VERSION),
        )

    @classmethod
    def from_summaries(cls, summaries, **kwargs) -> "PlayerProfile":
        """profile_store 的稳定重建入口，兼容字典摘要。"""
        normalized = [
            item if isinstance(item, GameProfileSummary)
            else GameProfileSummary.from_dict(item)
            for item in (summaries or [])
            if isinstance(item, (GameProfileSummary, dict))
        ]
        return build_profile(normalized, **kwargs)


# ===================== 单手筛选 / 工具 =====================
def is_primary_sample(result: MoveQualityResult) -> bool:
    """是否计入主要画像样本（§27.1）：置信度非 unknown 且 胜负未定。"""
    return (result.confidence != CONFIDENCE_UNKNOWN
            and result.is_meaningful_position)


def _empty_quality_counts() -> dict:
    return {k: 0 for k in QUALITY_KEYS}


def analysis_signatures_compatible(left, right, max_visit_ratio=1.25) -> bool:
    """判断两盘分析是否足以进入同一画像趋势。

    KataGo 实际 visits 可能因搜索结束条件产生小幅波动，因此不要求逐字段
    JSON 完全相等；模型、规则、贴目和评价版本必须一致，visits 允许 25% 波动。
    """
    a, b = dict(left or {}), dict(right or {})
    if not a and not b:
        return True
    for key in ("model", "rules"):
        av, bv = a.get(key), b.get(key)
        if av in (None, "") and bv in (None, ""):
            continue
        if str(av).casefold() != str(bv).casefold():
            return False
    aq = a.get("quality_version", a.get("qualityVersion"))
    bq = b.get("quality_version", b.get("qualityVersion"))
    if aq is not None or bq is not None:
        if str(aq) != str(bq):
            return False
    aboard = a.get("boardSize", a.get("board_size"))
    bboard = b.get("boardSize", b.get("board_size"))
    if aboard is not None or bboard is not None:
        if str(aboard) != str(bboard):
            return False
    ak, bk = a.get("komi"), b.get("komi")
    if ak is not None or bk is not None:
        try:
            if ak is None or bk is None or abs(float(ak) - float(bk)) > 0.01:
                return False
        except (TypeError, ValueError):
            return False
    av, bv = a.get("visits"), b.get("visits")
    if av is not None and bv is not None:
        try:
            av, bv = max(1, int(av)), max(1, int(bv))
            if max(av, bv) / min(av, bv) > float(max_visit_ratio):
                return False
        except (TypeError, ValueError):
            return False
    elif av is not None or bv is not None:
        return False
    return True


def _merge_quality_counts(target: dict, source: dict) -> None:
    """把 source 的质量计数并入 target（缺失 key 补 0）。"""
    for k in QUALITY_KEYS:
        target[k] = target.get(k, 0) + int(source.get(k, 0) or 0)


def _merge_tag_counts(target: dict, source: dict) -> None:
    for k, v in (source or {}).items():
        target[k] = target.get(k, 0) + int(v or 0)


def _game_stage_stat_from_results(results) -> GameStageStat:
    """从某阶段的有效手 MoveQualityResult 聚合单局阶段统计。"""
    st = GameStageStat(quality_counts=_empty_quality_counts())
    for r in results:
        st.moves += 1
        st.score_loss_sum += (r.score_loss or 0.0)
        st.winrate_drop_sum += (r.winrate_drop or 0.0)
        st.quality_counts[r.quality_key] = st.quality_counts.get(r.quality_key, 0) + 1
        if r.top1_match:
            st.top1_match += 1
        if r.top3_match:
            st.top3_match += 1
        if r.top5_match:
            st.top5_match += 1
        for t in r.problem_tags:
            st.problem_tag_counts[t] = st.problem_tag_counts.get(t, 0) + 1
    return st


# ===================== 单局摘要 =====================
def build_game_profile_summary(
    quality_results,
    *,
    game_id,
    game_name="",
    black_player=None,
    white_player=None,
    profile_side=SIDE_UNKNOWN,
    model=None,
    visits=None,
    analysis_signature=None,
    analyzed_at="",
    top_problem_limit=5,
) -> GameProfileSummary:
    """从一盘棋的每手精细评价生成单局画像摘要（§9.2 / §27.1）。

    quality_results: list[MoveQualityResult]。
    主要样本只取 is_primary_sample() 为 True 的手；胜负已定手计入 settled_moves。
    """
    summary = GameProfileSummary(
        game_id=str(game_id),
        game_name=game_name or "",
        black_player=black_player,
        white_player=white_player,
        user_side=profile_side,
        analyzed_at=analyzed_at,
        model=model,
        visits=visits,
        analysis_signature=dict(analysis_signature or {}),
        quality_counts=_empty_quality_counts(),
    )
    summary.total_moves = len(quality_results)

    # 分阶段 / 分颜色 收集主要样本
    stage_buckets = {p: [] for p in PHASES}
    color_buckets = {s: [] for s in SIDES}
    primary = []
    problems = []

    for r in quality_results:
        # 单局画像只统计用户执棋方；both/unknown 保留双方兼容视图。
        profile_color_match = (
            profile_side not in ("B", "W") or r.color == profile_side)
        if not profile_color_match:
            continue
        # 画像方质量分布（含 unknown / 胜负已定）。
        summary.quality_counts[r.quality_key] = summary.quality_counts.get(r.quality_key, 0) + 1
        if not is_primary_sample(r):
            if r.confidence != CONFIDENCE_UNKNOWN and not r.is_meaningful_position:
                summary.settled_moves += 1
            continue
        primary.append(r)
        phase = r.stage if r.stage in stage_buckets else "middle"
        stage_buckets[phase].append(r)
        color_buckets[r.color if r.color in color_buckets else "B"].append(r)
        for t in r.problem_tags:
            summary.problem_tag_counts[t] = summary.problem_tag_counts.get(t, 0) + 1
        # 入库资格与单盘展示阈值分开（反馈 #21）：小但反复的错误（如每次
        # 1.8 目的自动跟应）必须能进长期学习库参与复发统计，只是不进
        # 单盘 Top5 打扰用户。EVENT_ELIGIBILITY_LOSS = 1.0 目。
        if ((r.score_loss or 0.0) >= 1.0 or r.quality_key == QUALITY_BLUNDER):
            problems.append(r)

    summary.evaluated_moves = len(primary)

    # 主要样本的加权平均
    if primary:
        summary.score_loss_sum = sum((r.score_loss or 0.0) for r in primary)
        summary.winrate_drop_sum = sum((r.winrate_drop or 0.0) for r in primary)
        summary.avg_score_loss = summary.score_loss_sum / len(primary)
        summary.avg_winrate_drop = summary.winrate_drop_sum / len(primary)
        n = len(primary)
        summary.top1_match_rate = sum(1 for r in primary if r.top1_match) / n * 100.0
        summary.top3_match_rate = sum(1 for r in primary if r.top3_match) / n * 100.0
        summary.top5_match_rate = sum(1 for r in primary if r.top5_match) / n * 100.0

    # 阶段 / 颜色 统计
    for p in PHASES:
        summary.stage_stats[p] = _game_stage_stat_from_results(stage_buckets[p]).to_dict()
    for s in SIDES:
        summary.color_stats[s] = _game_stage_stat_from_results(color_buckets[s]).to_dict()

    # 问题手（目损降序）：全量入 problem_moves_all 供学习事件入库；
    # top_problem_moves 仅是展示/错题本队列切片。
    problems.sort(key=lambda r: (r.score_loss or 0.0), reverse=True)
    problem_entries = [{
        "move_no": r.move_no,
        "color": r.color,
        "played_move": r.played_move,
        "best_move": r.best_move,
        "quality_key": r.quality_key,
        "score_loss": r.score_loss,
        "winrate_drop": r.winrate_drop,
        "stage": r.stage,
        "problem_tags": list(r.problem_tags),
    } for r in problems]
    summary.problem_moves_all = problem_entries
    # 单盘展示切片仍按 2 目/恶手门槛：小目损错误入库但不打扰
    summary.top_problem_moves = [
        e for e in problem_entries
        if (e["score_loss"] or 0.0) >= 2.0 or e["quality_key"] == QUALITY_BLUNDER
    ][:top_problem_limit]

    return summary


# ===================== 内部：从单局摘要聚合 =====================
def _accumulate_into_profile_stats(stats: ProfileStats, summary: GameProfileSummary,
                                    stage_filter: Optional[str] = None,
                                    color_filter: Optional[str] = None) -> None:
    """把一盘摘要中命中维度的有效手"展平"累加进 ProfileStats。

    stage_filter / color_filter 任一为 None 表示不按该维度过滤；
    两者同时 None = overall（综合）。
    本函数只累加 sum/moves/counts，派生 rate/avg 由 finalize 计算。
    """
    if stage_filter is not None:
        st_dict = summary.stage_stats.get(stage_filter)
        if not st_dict or st_dict.get("moves", 0) <= 0:
            return
        moves = int(st_dict["moves"])
        stats.games += 1
        stats.moves += moves
        stats.score_loss_sum += float(st_dict.get("score_loss_sum", 0.0) or 0.0)
        stats.winrate_drop_sum += float(st_dict.get("winrate_drop_sum", 0.0) or 0.0)
        _merge_quality_counts(stats.quality_counts, st_dict.get("quality_counts", {}))
        _merge_tag_counts(stats.problem_tag_counts, st_dict.get("problem_tag_counts", {}))
        # 用临时键暂存 match 计数（避免污染 quality_counts）
        stats.problem_tag_counts.setdefault("__top1", 0)
        stats.problem_tag_counts["__top1"] += int(st_dict.get("top1_match", 0) or 0)
        stats.problem_tag_counts.setdefault("__top3", 0)
        stats.problem_tag_counts["__top3"] += int(st_dict.get("top3_match", 0) or 0)
        stats.problem_tag_counts.setdefault("__top5", 0)
        stats.problem_tag_counts["__top5"] += int(st_dict.get("top5_match", 0) or 0)
        return

    if color_filter is not None:
        col_dict = summary.color_stats.get(color_filter)
        if not col_dict or col_dict.get("moves", 0) <= 0:
            return
        moves = int(col_dict["moves"])
        stats.games += 1
        stats.moves += moves
        stats.score_loss_sum += float(col_dict.get("score_loss_sum", 0.0) or 0.0)
        stats.winrate_drop_sum += float(col_dict.get("winrate_drop_sum", 0.0) or 0.0)
        _merge_quality_counts(stats.quality_counts, col_dict.get("quality_counts", {}))
        _merge_tag_counts(stats.problem_tag_counts, col_dict.get("problem_tag_counts", {}))
        stats.problem_tag_counts.setdefault("__top1", 0)
        stats.problem_tag_counts["__top1"] += int(col_dict.get("top1_match", 0) or 0)
        stats.problem_tag_counts.setdefault("__top3", 0)
        stats.problem_tag_counts["__top3"] += int(col_dict.get("top3_match", 0) or 0)
        stats.problem_tag_counts.setdefault("__top5", 0)
        stats.problem_tag_counts["__top5"] += int(col_dict.get("top5_match", 0) or 0)
        return

    # overall：直接用摘要主要样本
    if summary.evaluated_moves <= 0:
        return
    stats.games += 1
    stats.moves += summary.evaluated_moves
    stats.score_loss_sum += summary.score_loss_sum
    stats.winrate_drop_sum += summary.winrate_drop_sum
    # overall 的质量分布用主要样本（重新从阶段桶合并，去掉 unknown/胜负已定）
    primary_qc = _empty_quality_counts()
    for p in PHASES:
        ps = summary.stage_stats.get(p, {})
        _merge_quality_counts(primary_qc, ps.get("quality_counts", {}))
    _merge_quality_counts(stats.quality_counts, primary_qc)
    tag_merge = {}
    for p in PHASES:
        _merge_tag_counts(tag_merge, summary.stage_stats.get(p, {}).get("problem_tag_counts", {}))
    _merge_tag_counts(stats.problem_tag_counts, tag_merge)
    top1 = sum(int(summary.stage_stats.get(p, {}).get("top1_match", 0) or 0) for p in PHASES)
    top3 = sum(int(summary.stage_stats.get(p, {}).get("top3_match", 0) or 0) for p in PHASES)
    top5 = sum(int(summary.stage_stats.get(p, {}).get("top5_match", 0) or 0) for p in PHASES)
    stats.problem_tag_counts.setdefault("__top1", 0)
    stats.problem_tag_counts["__top1"] += top1
    stats.problem_tag_counts.setdefault("__top3", 0)
    stats.problem_tag_counts["__top3"] += top3
    stats.problem_tag_counts.setdefault("__top5", 0)
    stats.problem_tag_counts["__top5"] += top5


def _finalize(stats: ProfileStats) -> ProfileStats:
    """计算 avg / rate，并剥离临时 __top* 键（§27.3）。"""
    top1 = stats.problem_tag_counts.pop("__top1", 0)
    top3 = stats.problem_tag_counts.pop("__top3", 0)
    top5 = stats.problem_tag_counts.pop("__top5", 0)
    if stats.moves > 0:
        n = stats.moves
        stats.avg_score_loss = stats.score_loss_sum / n
        stats.avg_winrate_drop = stats.winrate_drop_sum / n
        stats.blunder_rate = stats.quality_counts.get(QUALITY_BLUNDER, 0) / n * 100.0
        stats.inaccuracy_rate = stats.quality_counts.get(QUALITY_INACCURACY, 0) / n * 100.0
        stats.top1_match_rate = top1 / n * 100.0
        stats.top3_match_rate = top3 / n * 100.0
        stats.top5_match_rate = top5 / n * 100.0
    return stats


# ===================== 趋势 =====================
def _weighted_loss_of_summaries(summaries) -> Optional[float]:
    """一组摘要的加权平均目损（按有效手数加权）。"""
    total_loss = 0.0
    total_moves = 0
    for s in summaries:
        if s.evaluated_moves > 0:
            total_loss += s.score_loss_sum
            total_moves += s.evaluated_moves
    if total_moves <= 0:
        return None
    return total_loss / total_moves


def _blunder_rate_of_summaries(summaries) -> Optional[float]:
    """一组摘要的合并恶手率（百分点）。"""
    total_blunder = 0
    total_moves = 0
    for s in summaries:
        for p in PHASES:
            ps = s.stage_stats.get(p, {})
            total_blunder += int(ps.get("quality_counts", {}).get(QUALITY_BLUNDER, 0) or 0)
            total_moves += int(ps.get("moves", 0) or 0)
    if total_moves <= 0:
        return None
    return total_blunder / total_moves * 100.0


def _top3_rate_of_summaries(summaries) -> Optional[float]:
    """一组摘要的合并前 3 吻合度（百分点）。"""
    total_top3 = 0
    total_moves = 0
    for s in summaries:
        for p in PHASES:
            ps = s.stage_stats.get(p, {})
            total_top3 += int(ps.get("top3_match", 0) or 0)
            total_moves += int(ps.get("moves", 0) or 0)
    if total_moves <= 0:
        return None
    return total_top3 / total_moves * 100.0


def profile_trend(summaries) -> TrendResult:
    """最近趋势（§27.4）：窗口比较，避免复杂回归。

    summaries 必须按时间【从旧到新】顺序传入（最早在前）。
    返回 TrendResult，direction ∈ improving/stable/declining/insufficient。
    """
    n = len(summaries)
    if n < MIN_GAMES_TREND_LISTING:
        return TrendResult(direction="insufficient",
                           evidence=["样本不足：少于 %d 盘，不下进步/下降结论。"
                                     % MIN_GAMES_TREND_LISTING])
    if n < MIN_GAMES_STRONG_TREND:
        return TrendResult(direction="insufficient",
                           evidence=["样本不足：%d 盘（< %d），仅显示逐盘趋势，"
                                     "不下进步/下降结论。" % (n, MIN_GAMES_STRONG_TREND)],
                           recent_games=n)

    recent = summaries[-RECENT_TREND_WINDOW:]
    # 基线：最近窗口之前的最多 BASELINE_TREND_WINDOW 盘（不与最近窗口重叠，§31.1）
    baseline = summaries[max(0, n - RECENT_TREND_WINDOW - BASELINE_TREND_WINDOW):
                         n - RECENT_TREND_WINDOW]
    if not baseline:
        return TrendResult(direction="insufficient",
                           evidence=["样本不足：缺少与最近 %d 盘对比的基线窗口。"
                                     % RECENT_TREND_WINDOW],
                           recent_games=len(recent))

    rec_loss = _weighted_loss_of_summaries(recent)
    base_loss = _weighted_loss_of_summaries(baseline)
    rec_blunder = _blunder_rate_of_summaries(recent)
    base_blunder = _blunder_rate_of_summaries(baseline)
    rec_top3 = _top3_rate_of_summaries(recent)
    base_top3 = _top3_rate_of_summaries(baseline)

    result = TrendResult(
        recent_games=len(recent),
        baseline_games=len(baseline),
        recent_avg_loss=rec_loss, baseline_avg_loss=base_loss,
        recent_blunder_rate=rec_blunder, baseline_blunder_rate=base_blunder,
        recent_top3_rate=rec_top3, baseline_top3_rate=base_top3,
    )

    improving_signals = []
    declining_signals = []

    if rec_loss is not None and base_loss is not None:
        d = base_loss - rec_loss              # 目损下降 = 进步
        if d >= TREND_LOSS_DELTA:
            improving_signals.append(
                "加权平均目损由 %.2f 降至 %.2f（下降 %.2f 目）。"
                % (base_loss, rec_loss, d))
        elif d <= -TREND_LOSS_DELTA:
            declining_signals.append(
                "加权平均目损由 %.2f 升至 %.2f（上升 %.2f 目）。"
                % (base_loss, rec_loss, -d))

    if rec_blunder is not None and base_blunder is not None:
        d = base_blunder - rec_blunder        # 恶手率下降 = 进步
        if d >= TREND_BLUNDER_DELTA:
            improving_signals.append(
                "恶手率由 %.1f%% 降至 %.1f%%（下降 %.1f 个百分点）。"
                % (base_blunder, rec_blunder, d))
        elif d <= -TREND_BLUNDER_DELTA:
            declining_signals.append(
                "恶手率由 %.1f%% 升至 %.1f%%（上升 %.1f 个百分点）。"
                % (base_blunder, rec_blunder, -d))

    if rec_top3 is not None and base_top3 is not None:
        d = rec_top3 - base_top3              # 前 3 吻合度上升 = 进步
        if d >= TREND_TOP3_DELTA:
            improving_signals.append(
                "AI 前 3 吻合度由 %.1f%% 提升至 %.1f%%（提高 %.1f 个百分点）。"
                % (base_top3, rec_top3, d))
        elif d <= -TREND_TOP3_DELTA:
            declining_signals.append(
                "AI 前 3 吻合度由 %.1f%% 下降至 %.1f%%（降低 %.1f 个百分点）。"
                % (base_top3, rec_top3, -d))

    if improving_signals and not declining_signals:
        result.direction = "improving"
        result.evidence = improving_signals
    elif declining_signals and not improving_signals:
        result.direction = "declining"
        result.evidence = declining_signals
    elif improving_signals and declining_signals:
        # 信号矛盾 → 总体稳定，但列出两侧证据
        result.direction = "stable"
        result.evidence = (["进步信号："] + improving_signals
                           + ["退步信号："] + declining_signals
                           + ["两项指标方向相反，整体判断为稳定。"])
    else:
        result.direction = "stable"
        result.evidence = ["最近 %d 盘与基线 %d 盘相比，各项指标变化均在阈值内，总体稳定。"
                           % (len(recent), len(baseline))]
    return result


def compare_game_to_baseline(current, prior_summaries,
                             *, meaningful_delta=0.5) -> GameBenchmark:
    """比较最近一盘与同分析口径的既往个人基线。

    均值按有效手数加权；正的 ``loss_improvement`` 表示本局平均目损更低。
    一盘基线也会返回结果，但置信度为 low，UI 必须明确展示样本数。
    """
    if isinstance(current, dict):
        current = GameProfileSummary.from_dict(current)
    if not isinstance(current, GameProfileSummary) or current.evaluated_moves <= 0:
        return GameBenchmark(
            evidence=["本局有效评价手数不足，无法与个人基线比较。"])

    normalized = []
    for item in prior_summaries or []:
        if isinstance(item, GameProfileSummary):
            summary = item
        elif isinstance(item, dict):
            summary = GameProfileSummary.from_dict(
                item.get("profileSummary")
                or item.get("profile_summary")
                or item.get("summary")
                or item)
        else:
            continue
        if summary.game_id == current.game_id:
            continue
        if summary.evaluated_moves <= 0 or summary.version != current.version:
            continue
        if not analysis_signatures_compatible(
                summary.analysis_signature, current.analysis_signature):
            continue
        normalized.append(summary)

    if not normalized:
        return GameBenchmark(
            current_moves=current.evaluated_moves,
            current_avg_loss=current.avg_score_loss,
            evidence=["没有同模型、规则、visits 与评价版本的历史棋局可作基线。"])

    baseline_loss = _weighted_loss_of_summaries(normalized)
    baseline_moves = sum(s.evaluated_moves for s in normalized)
    current_loss = current.avg_score_loss
    improvement = (
        baseline_loss - current_loss
        if baseline_loss is not None and current_loss is not None else None)
    confidence = (
        "high" if len(normalized) >= 10
        else "medium" if len(normalized) >= 3 else "low")
    if improvement is None:
        status = "insufficient"
    elif improvement >= meaningful_delta:
        status = "better"
    elif improvement <= -meaningful_delta:
        status = "worse"
    else:
        status = "similar"

    current_blunder = _blunder_rate_of_summaries([current])
    baseline_blunder = _blunder_rate_of_summaries(normalized)
    current_top3 = _top3_rate_of_summaries([current])
    baseline_top3 = _top3_rate_of_summaries(normalized)

    stage_comparisons = {}
    for phase in PHASES:
        current_stat = current.stage_stats.get(phase, {})
        current_moves = int(current_stat.get("moves", 0) or 0)
        current_stage_loss = (
            float(current_stat.get("score_loss_sum", 0.0) or 0.0) / current_moves
            if current_moves else None)
        base_moves = 0
        base_loss_sum = 0.0
        for summary in normalized:
            stat = summary.stage_stats.get(phase, {})
            moves = int(stat.get("moves", 0) or 0)
            base_moves += moves
            base_loss_sum += float(stat.get("score_loss_sum", 0.0) or 0.0)
        base_stage_loss = base_loss_sum / base_moves if base_moves else None
        stage_comparisons[phase] = {
            "current_moves": current_moves,
            "baseline_moves": base_moves,
            "current_avg_loss": current_stage_loss,
            "baseline_avg_loss": base_stage_loss,
            "loss_improvement": (
                base_stage_loss - current_stage_loss
                if base_stage_loss is not None and current_stage_loss is not None
                else None),
        }

    evidence = []
    if improvement is not None:
        if status == "better":
            evidence.append(
                "本局平均目损 %.2f，较此前 %d 盘加权基线 %.2f 下降 %.2f 目。"
                % (current_loss, len(normalized), baseline_loss, improvement))
        elif status == "worse":
            evidence.append(
                "本局平均目损 %.2f，较此前 %d 盘加权基线 %.2f 上升 %.2f 目。"
                % (current_loss, len(normalized), baseline_loss, -improvement))
        else:
            evidence.append(
                "本局平均目损 %.2f，与此前 %d 盘加权基线 %.2f 接近。"
                % (current_loss, len(normalized), baseline_loss))
    if confidence == "low":
        evidence.append("历史基线少于 3 盘，本次比较仅作低置信参考。")

    return GameBenchmark(
        status=status,
        confidence=confidence,
        prior_games=len(normalized),
        current_moves=current.evaluated_moves,
        baseline_moves=baseline_moves,
        current_avg_loss=current_loss,
        baseline_avg_loss=baseline_loss,
        loss_improvement=improvement,
        current_blunder_rate=current_blunder,
        baseline_blunder_rate=baseline_blunder,
        current_top3_rate=current_top3,
        baseline_top3_rate=baseline_top3,
        stage_comparisons=stage_comparisons,
        evidence=evidence,
    )


def weakness_trends(games_data, tags=None, recent_window=5, baseline_window=5):
    """比较问题标签在最近与此前棋局中的每百手出现率。

    两个窗口不重叠，且前后各至少 2 盘才输出方向，避免用单盘波动制造趋势。
    输入顺序要求从旧到新；兼容 ``GameProfileSummary``、棋局库记录和摘要字典。
    """
    summaries = []
    for item in games_data or []:
        if isinstance(item, GameProfileSummary):
            summary = item
        elif isinstance(item, dict) and isinstance(item.get("summary"), GameProfileSummary):
            summary = item["summary"]
        elif isinstance(item, dict) and isinstance(item.get("summary"), dict):
            summary = GameProfileSummary.from_dict(item["summary"])
        elif isinstance(item, dict) and isinstance(item.get("profileSummary"), dict):
            summary = GameProfileSummary.from_dict(item["profileSummary"])
        elif isinstance(item, dict) and (
                "evaluated_moves" in item or "total_evaluated_moves" in item):
            summary = GameProfileSummary.from_dict(item)
        else:
            continue
        if summary.user_side not in (SIDE_BLACK, SIDE_WHITE, SIDE_BOTH):
            continue
        if summary.evaluated_moves <= 0:
            continue
        summaries.append(summary)

    if summaries:
        latest = summaries[-1]
        summaries = [
            summary for summary in summaries
            if summary.version == latest.version
            and analysis_signatures_compatible(
                summary.analysis_signature, latest.analysis_signature)
        ]

    tag_keys = set(tags or [])
    if not tag_keys:
        for summary in summaries:
            tag_keys.update(summary.problem_tag_counts)

    count = len(summaries)
    recent_size = min(
        max(1, int(recent_window)), max(1, count // 2)) if count else 0
    recent = summaries[-recent_size:] if recent_size else []
    baseline_end = max(0, count - recent_size)
    baseline_start = max(0, baseline_end - max(1, int(baseline_window)))
    baseline = summaries[baseline_start:baseline_end]

    def window_stats(items, tag):
        moves = sum(int(item.evaluated_moves or 0) for item in items)
        occurrences = sum(
            int((item.problem_tag_counts or {}).get(tag, 0) or 0)
            for item in items)
        return {
            "games": len(items),
            "moves": moves,
            "occurrences": occurrences,
            "rate_per_100": occurrences / moves * 100.0 if moves else None,
        }

    result = {}
    for tag in sorted(tag_keys):
        current = window_stats(recent, tag)
        previous = window_stats(baseline, tag)
        enough = (
            current["games"] >= 2 and previous["games"] >= 2
            and current["rate_per_100"] is not None
            and previous["rate_per_100"] is not None)
        status = "insufficient"
        delta = None
        confidence = "low"
        reason = "趋势样本不足（至少需要前后各 2 盘同口径棋局）。"
        if enough:
            delta = current["rate_per_100"] - previous["rate_per_100"]
            threshold = max(1.0, previous["rate_per_100"] * 0.15)
            if delta <= -threshold:
                status = "improving"
                reason = "最近每百手 %.1f 次，较此前下降 %.1f 次。" % (
                    current["rate_per_100"], -delta)
            elif delta >= threshold:
                status = "worsening"
                reason = "最近每百手 %.1f 次，较此前上升 %.1f 次。" % (
                    current["rate_per_100"], delta)
            else:
                status = "stable"
                reason = "最近每百手 %.1f 次，与此前基本持平。" % (
                    current["rate_per_100"])
            confidence = (
                "high" if current["games"] >= 5 and previous["games"] >= 5
                else "medium")
        result[tag] = {
            "tag": tag,
            "status": status,
            "confidence": confidence,
            "recent": current,
            "baseline": previous,
            "delta_per_100": delta,
            "reason": reason,
        }
    return result


def prioritize_weaknesses(profile, mistakes=None, limit=5, trends=None):
    """按跨局出现次数、到期错题、平均目损和近期方向生成透明优先级。"""
    if isinstance(profile, dict):
        profile = PlayerProfile.from_dict(profile)
    if not isinstance(profile, PlayerProfile):
        return []
    mistakes = [
        item for item in (mistakes or [])
        if item.get("active", True) and not item.get("mastered")
    ]
    tags = set(profile.problem_tag_distribution)
    for item in mistakes:
        tags.update(item.get("problemTags") or [])
    priorities = []
    for tag in tags:
        count = int(profile.problem_tag_distribution.get(tag, 0) or 0)
        tagged = [
            item for item in mistakes
            if tag in (item.get("problemTags") or [])
        ]
        due_count = sum(1 for item in tagged if item.get("isDue"))
        losses = [
            float(item.get("scoreLoss"))
            for item in tagged if item.get("scoreLoss") is not None
        ]
        avg_loss = sum(losses) / len(losses) if losses else None
        trend = (trends or {}).get(tag) or {}
        score = count + due_count * 2.0 + min(avg_loss or 0.0, 15.0) / 5.0
        if trend.get("status") == "worsening":
            score += 2.0
        elif trend.get("status") == "improving":
            score -= 0.5
        reason = "跨局出现 %d 次，活跃错题 %d 道（今日到期 %d）%s。" % (
            count, len(tagged), due_count,
            "" if avg_loss is None else "，错题平均目损 %.1f" % avg_loss)
        if trend:
            reason += " " + trend.get("reason", "")
        priorities.append({
            "tag": tag,
            "label": PROBLEM_TAGS.get(tag, tag),
            "occurrences": count,
            "active_mistakes": len(tagged),
            "due_mistakes": due_count,
            "avg_mistake_loss": avg_loss,
            "trend": trend,
            "priority_score": round(score, 3),
            "reason": reason,
        })
    priorities.sort(key=lambda item: (
        item["priority_score"], item["due_mistakes"],
        item["occurrences"]), reverse=True)
    return priorities[:max(0, int(limit))]


# ===================== 阶段弱点 / 优势 / 建议 =====================
def phase_weaknesses(profile: PlayerProfile) -> list:
    """找出有效手数足够的阶段，按 平均目损 / 恶手率 / 不佳率 综合排序（§27.5）。

    返回 [(phase_key, ProfileStats, score), ...]，score 越大越弱。
    样本 < MIN_MOVES_PHASE_CONCLUSION 的阶段不进入结论。
    """
    candidates = []
    for p in PHASES:
        st = getattr(profile, p)
        if st.moves < MIN_MOVES_PHASE_CONCLUSION:
            continue
        if st.avg_score_loss is None:
            continue
        # 综合分：目损为主，恶手率与不佳率为辅
        blunder = st.blunder_rate or 0.0
        inacc = st.inaccuracy_rate or 0.0
        score = st.avg_score_loss * 1.0 + blunder * 0.15 + inacc * 0.05
        candidates.append((p, st, score))
    candidates.sort(key=lambda x: x[2], reverse=True)
    return candidates


def _phase_label(phase_key: str) -> str:
    return PHASE_LABELS.get(phase_key, phase_key)


def _tag_label(tag_key: str) -> str:
    return PROBLEM_TAGS.get(tag_key, tag_key)


def build_profile_insights(profile: PlayerProfile) -> list:
    """生成画像结论文案（§9.5 / §27.5），每条都带统计证据，不输出段位。

    返回 (strengths, weaknesses, recommendations) 三段中文列表。
    """
    strengths = []
    weaknesses = []
    recommendations = []

    # ---- 阶段对比（弱点 / 优势）----
    ranked = phase_weaknesses(profile)
    valid_phases = {p: st for p, st, _ in ranked}
    if len(valid_phases) >= 2:
        # 最弱阶段
        weak_key, weak_st, _ = ranked[0]
        # 找最强的阶段（score 最小）
        strong_key, strong_st, _ = min(ranked, key=lambda x: x[2])
        weaknesses.append(
            "最近 %d 盘中，%s平均目损 %.2f 高于%s的 %.2f，是当前主要短板（样本 %d 手）。"
            % (profile.games_count, _phase_label(weak_key),
               weak_st.avg_score_loss, _phase_label(strong_key),
               strong_st.avg_score_loss, weak_st.moves))
        # 官子不佳多但恶手少（小官子价值判断问题）
        for p, st in valid_phases.items():
            inacc = st.quality_counts.get(QUALITY_INACCURACY, 0)
            blunder = st.quality_counts.get(QUALITY_BLUNDER, 0)
            if inacc >= 3 and blunder <= max(1, inacc // 3):
                weaknesses.append(
                    "%s阶段「不佳」数量偏多（%d）但恶手较少（%d），"
                    "主要是价值判断问题而非严重误算。" %
                    (_phase_label(p), inacc, blunder))
                break
    elif len(valid_phases) == 1:
        p, st = next(iter(valid_phases.items()))
        weaknesses.append("样本足够的阶段只有%s（%d 手），其他阶段样本不足以下结论。"
                          % (_phase_label(p), st.moves))
    else:
        weaknesses.append("各阶段有效手数均不足 %d 手，无法定位阶段短板。"
                          % MIN_MOVES_PHASE_CONCLUSION)

    # ---- 黑白对比 ----
    if (profile.black.moves >= MIN_MOVES_PHASE_CONCLUSION
            and profile.white.moves >= MIN_MOVES_PHASE_CONCLUSION
            and profile.black.avg_score_loss is not None
            and profile.white.avg_score_loss is not None):
        diff = profile.white.avg_score_loss - profile.black.avg_score_loss
        if abs(diff) >= 0.3:
            better, worse = ("执黑", "执白") if diff > 0 else ("执白", "执黑")
            strengths.append("%s表现优于%s：平均目损 %.2f vs %.2f。"
                             % (better, worse,
                                min(profile.black.avg_score_loss, profile.white.avg_score_loss),
                                max(profile.black.avg_score_loss, profile.white.avg_score_loss)))
        # 前 3 吻合度差距
        bt = profile.black.top3_match_rate
        wt = profile.white.top3_match_rate
        if bt is not None and wt is not None and abs(bt - wt) >= 5.0:
            low_side, high_side = ("执白", "执黑") if wt < bt else ("执黑", "执白")
            recommendations.append(
                "%s前 3 吻合度比%s低 %.1f 个百分点，建议在低吻合方多对照 AI 选点。"
                % (low_side, high_side, abs(bt - wt)))

    # ---- 趋势 ----
    trend = profile.recent_trend
    if trend.direction == "improving":
        strengths.append("近期呈进步趋势。")
        for e in trend.evidence[:2]:
            recommendations.append("保持：" + e)
    elif trend.direction == "declining":
        weaknesses.append("近期呈退步趋势。")
        for e in trend.evidence[:2]:
            recommendations.append("关注：" + e)

    # ---- 高频问题标签 → 建议（至少出现 MIN_TAG_COUNT_FOR_ADVICE 次）----
    tag_items = [(k, v) for k, v in profile.problem_tag_distribution.items()
                 if not k.startswith("__") and v >= MIN_TAG_COUNT_FOR_ADVICE]
    tag_items.sort(key=lambda x: x[1], reverse=True)
    for tag_key, count in tag_items[:5]:
        recommendations.append(
            "「%s」问题累计出现 %d 次，建议针对该类型集中复盘。"
            % (_tag_label(tag_key), count))

    # 兜底：没有建议时给一条中性提示
    if not recommendations:
        if profile.games_count == 0:
            recommendations.append("尚无已分析棋局，完成几盘分析后即可生成个人建议。")
        else:
            recommendations.append("当前各项指标较为均衡，继续保持规律复盘即可。")

    return strengths, weaknesses, recommendations


# ===================== 主入口：build_profile =====================
def build_profile(games_data,
                  *,
                  profile_id="default",
                  player_names=None,
                  user_side=SIDE_BOTH,
                  window_games=None) -> PlayerProfile:
    """从多盘数据聚合长期画像。

    games_data: list[dict]，每项形如：
        {
            "summary": GameProfileSummary,            # 必填
            # 可选原始 results，便于在没有 summary 时即时构建
            "quality_results": list[MoveQualityResult],
            "game_id": str, "game_name": str,
            "black_player": str, "white_player": str,
        }
    也允许直接传入 GameProfileSummary 列表。

    user_side: "B" / "W" / "both" / "unknown"。
        "B" → 只聚合 user_side=="B" 的盘；"W" 同理；
        "both" → 聚合所有已知 side（B/W/both）的盘；
        "unknown" → 聚合所有盘（无法识别身份时兜底，§9.1）。

    window_games: 取最近 N 盘（按传入顺序的末尾 N 盘）；None 表示全部。

    时间顺序：调用方按【从旧到新】传入；本函数按原顺序处理，window 取末尾。
    """
    # ---- 归一化为一组 GameProfileSummary ----
    summaries: list = []
    names_found = set(player_names or [])
    for item in games_data:
        if isinstance(item, GameProfileSummary):
            s = item
        elif isinstance(item, dict) and isinstance(item.get("summary"), dict):
            s = GameProfileSummary.from_dict(item["summary"])
        elif isinstance(item, dict) and isinstance(item.get("summary"), GameProfileSummary):
            s = item["summary"]
        elif isinstance(item, dict) and (
                "profileSummary" in item or "profile_summary" in item):
            s = GameProfileSummary.from_dict(
                item.get("profileSummary") or item.get("profile_summary") or {})
        elif isinstance(item, dict) and item.get("quality_results") is not None:
            s = build_game_profile_summary(
                item["quality_results"],
                game_id=item.get("game_id", ""),
                game_name=item.get("game_name", ""),
                black_player=item.get("black_player"),
                white_player=item.get("white_player"),
                profile_side=item.get("profile_side", item.get("user_side", SIDE_UNKNOWN)),
                model=item.get("model"),
                visits=item.get("visits"),
                analysis_signature=item.get(
                    "analysis_signature", item.get("analysisSignature")),
                analyzed_at=item.get("analyzed_at", ""),
            )
        elif isinstance(item, dict) and (
                "evaluated_moves" in item or "total_evaluated_moves" in item):
            s = GameProfileSummary.from_dict(item)
        else:
            continue
        summaries.append(s)

    # ---- 身份过滤 ----
    def _side_matches(s: GameProfileSummary) -> bool:
        if user_side == SIDE_UNKNOWN:
            return True
        if user_side == SIDE_BOTH:
            return s.user_side in (SIDE_BLACK, SIDE_WHITE, SIDE_BOTH)
        return s.user_side == user_side

    summaries = [s for s in summaries if _side_matches(s)]

    # 不静默混合不同评价版本或分析签名。以最新一盘为当前口径，
    # 只聚合同口径摘要；被排除数量在画像中明确暴露给 UI。
    excluded_incompatible = 0
    if summaries:
        latest = summaries[-1]
        latest_signature = latest.analysis_signature or {}
        compatible = [
            s for s in summaries
            if s.version == latest.version
            and analysis_signatures_compatible(
                s.analysis_signature, latest_signature)
        ]
        excluded_incompatible = len(summaries) - len(compatible)
        summaries = compatible

    # ---- 最近窗口 ----
    if window_games is not None and window_games > 0:
        summaries = summaries[-window_games:]

    profile = PlayerProfile(
        profile_id=profile_id,
        player_names=sorted(names_found),
        user_side=user_side,
        games_count=len(summaries),
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        excluded_incompatible_games=excluded_incompatible,
    )

    # 空数据 → 直接返回默认画像（不崩溃，§17.2 / §31.1）
    if not summaries:
        profile.recent_trend = profile_trend([])
        profile.quality_distribution = _empty_quality_counts()
        profile.problem_tag_distribution = {}
        profile.strengths, profile.weaknesses, profile.recommendations = build_profile_insights(profile)
        return profile

    # ---- 各维度加权聚合 ----
    overall = ProfileStats(quality_counts=_empty_quality_counts())
    black = ProfileStats(quality_counts=_empty_quality_counts())
    white = ProfileStats(quality_counts=_empty_quality_counts())
    opening = ProfileStats(quality_counts=_empty_quality_counts())
    middle = ProfileStats(quality_counts=_empty_quality_counts())
    endgame = ProfileStats(quality_counts=_empty_quality_counts())

    for s in summaries:
        _accumulate_into_profile_stats(overall, s)
        _accumulate_into_profile_stats(black, s, color_filter="B")
        _accumulate_into_profile_stats(white, s, color_filter="W")
        _accumulate_into_profile_stats(opening, s, stage_filter="opening")
        _accumulate_into_profile_stats(middle, s, stage_filter="middle")
        _accumulate_into_profile_stats(endgame, s, stage_filter="endgame")

    profile.overall = _finalize(overall)
    profile.black = _finalize(black)
    profile.white = _finalize(white)
    profile.opening = _finalize(opening)
    profile.middle = _finalize(middle)
    profile.endgame = _finalize(endgame)

    profile.evaluated_moves_count = profile.overall.moves

    # ---- 全局质量分布 / 问题标签分布 ----
    profile.quality_distribution = dict(profile.overall.quality_counts)
    tag_dist = {}
    _merge_tag_counts(tag_dist, profile.overall.problem_tag_counts)
    profile.problem_tag_distribution = tag_dist

    # ---- 趋势 ----
    profile.recent_trend = profile_trend(summaries)
    profile.trend_points = []
    for i, s in enumerate(summaries):
        n = s.evaluated_moves
        profile.trend_points.append(GameTrendPoint(
            game_id=s.game_id,
            order=i,
            evaluated_moves=n,
            avg_score_loss=s.avg_score_loss,
            blunder_rate=_blunder_rate_of_summaries([s]),
            top3_match_rate=_top3_rate_of_summaries([s]),
        ))

    # ---- 结论 ----
    profile.strengths, profile.weaknesses, profile.recommendations = build_profile_insights(profile)
    return profile
