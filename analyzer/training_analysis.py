"""training_analysis —— 训练后分析（纯逻辑，不依赖 tkinter / KataGo 进程）。

用户在一次训练中重下某个阶段（用自己执棋方的手），训练结束后用
move_quality.evaluate_move() 对训练手做精细评价，再与原实战同一阶段的
精细评价对比，识别：

  * 重复错误（同一问题再次出现）
  * 新错误（原实战没问题的位置，训练中新出现问题手）
  * 已改善（原实战问题手，训练中提升到一般及以上）

并生成训练评分、训练标签、复习建议。

设计原则（见 当前任务.md §11 / §26）：
  * 复用 move_quality.py 的 MoveQualityResult，不重复实现单手评价。
  * 对齐策略两级：精确局面匹配（position_key_before + 执棋方）→ 同步对齐
    （按 step 顺序）→ 阶段模式匹配（按 problem_tags 比较问题模式）。
  * 分支已经完全偏离时不轻易断言「同一手」，避免错误归因。
  * 评分透明可解释（§11.4 / §26.5），并带样本不足 / 提示比例 / 重复恶手
    限制。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from move_quality import (
    MoveQualityResult,
    QUALITY_BEST, QUALITY_GOOD, QUALITY_NORMAL,
    QUALITY_INACCURACY, QUALITY_BLUNDER, QUALITY_UNKNOWN,
)

VERSION = 1

# ===================== 常量 =====================
# 视为「问题手」的 quality key（用于重复 / 新增 / 改善判断）
PROBLEM_QUALITIES = (QUALITY_INACCURACY, QUALITY_BLUNDER)
# 视为「明显改善」的 quality key
IMPROVED_QUALITIES = (QUALITY_NORMAL, QUALITY_GOOD, QUALITY_BEST)
# 训练评分：样本下限（少于则标「样本不足」）
MIN_EFFECTIVE_MOVES = 4
# 提示比例阈值：超过则最高分限制为 85
HINT_RATIO_CAP = 0.5
HINT_CAP_SCORE = 85

# 对齐方式
MATCH_EXACT_POSITION = "exact_position"
MATCH_SAME_STEP = "same_step"
MATCH_STAGE_PATTERN = "stage_pattern"
MATCH_NONE = "none"

# 训练评分标签（§11.4）
SCORE_LABELS = [
    (90, "优秀"),
    (75, "明显改善"),
    (60, "基本合格"),
    (40, "仍需复习"),
    (0, "建议重练"),
]


def _label_for_score(score: int) -> str:
    for hi, label in SCORE_LABELS:
        if score >= hi:
            return label
    return "建议重练"


def _is_problem(result: MoveQualityResult) -> bool:
    """是否为问题手（不佳 / 恶手）；unknown 视为非问题（数据不足）。"""
    return result.quality_key in PROBLEM_QUALITIES


def _is_improved_quality(quality_key: str) -> bool:
    return quality_key in IMPROVED_QUALITIES


def _quality_rank(quality_key: str) -> int:
    """quality key → 有序数值（越大越好），用于比较改善程度。"""
    order = {
        QUALITY_BLUNDER: 0,
        QUALITY_INACCURACY: 1,
        QUALITY_NORMAL: 2,
        QUALITY_GOOD: 3,
        QUALITY_BEST: 4,
        QUALITY_UNKNOWN: -1,
    }
    return order.get(quality_key, -1)


# ===================== 对齐辅助 =====================
def _result_position_key(result: MoveQualityResult) -> Optional[str]:
    """从 MoveQualityResult 取局面 key（训练记录里自定义属性挂在 .position_key）。

    move_quality 不强制该字段；这里容错读取，缺失返回 None。
    """
    return getattr(result, "position_key", None)


def _result_source_move(result: MoveQualityResult) -> Optional[int]:
    """从 MoveQualityResult 取对应原实战手数（训练记录里自定义属性）。"""
    return getattr(result, "source_move_no", None)


def _align_pairs(original_moves, training_moves):
    """把训练手与原实战手两两对齐，返回 [(orig_or_None, train, match_type), ...]。

    对齐顺序（§26.3）：
      1. 精确局面匹配：position_key_before 相同 + 执棋方相同。
      2. 同步对齐：按训练手在训练序列中的次序与原实战阶段内同色手次序对齐
         （分支尚未偏离时等价于「第 N 手」对齐）。
      3. 无法对齐 → match_type = MATCH_NONE。
    """
    # 1) 收集原实战按 position_key 的索引（仅 color + key 都匹配才算精确）
    orig_by_key = {}
    for om in original_moves:
        key = _result_position_key(om)
        if key is None:
            continue
        orig_by_key.setdefault((om.color, key), []).append(om)

    used_orig_ids = set()  # 用 id() 标记已被精确匹配消耗的原实战手

    # 先把训练手两两配对
    raw_pairs = []
    for tm in training_moves:
        key = _result_position_key(tm)
        orig = None
        match_type = MATCH_NONE
        if key is not None:
            bucket = orig_by_key.get((tm.color, key), [])
            for cand in bucket:
                if id(cand) in used_orig_ids:
                    continue
                orig = cand
                used_orig_ids.add(id(cand))
                match_type = MATCH_EXACT_POSITION
                break
        raw_pairs.append([orig, tm, match_type])

    # 2) 同步/阶段模式对齐：有 position_key 但不相同表示分支已偏离，
    # 只能做阶段问题模式比较，不能伪装成“同一手”。
    remaining_orig = [om for om in original_moves if id(om) not in used_orig_ids]
    orig_by_color = {"B": [], "W": []}
    for om in remaining_orig:
        if om.color in orig_by_color:
            orig_by_color[om.color].append(om)

    color_cursor = {"B": 0, "W": 0}
    for pair in raw_pairs:
        if pair[2] != MATCH_NONE:
            continue
        tm = pair[1]
        bucket = orig_by_color.get(tm.color, [])
        idx = color_cursor.get(tm.color, 0)
        if idx < len(bucket):
            pair[0] = bucket[idx]
            orig_key = _result_position_key(bucket[idx])
            train_key = _result_position_key(tm)
            pair[2] = (
                MATCH_STAGE_PATTERN
                if orig_key is not None and train_key is not None
                and orig_key != train_key
                else MATCH_SAME_STEP)
            color_cursor[tm.color] = idx + 1
    return raw_pairs


# ===================== 数据结构 =====================
@dataclass
class MoveComparison:
    """单手训练对齐结果。"""
    move_no: int                                # 训练手手数（result.move_no）
    color: str
    played_move: str
    original_quality: Optional[str] = None      # 原实战同位置 quality key
    training_quality: Optional[str] = None      # 训练 quality key
    match_type: str = MATCH_NONE
    score_loss_improvement: Optional[float] = None    # 原目损 - 训练目损（>0 改善）
    winrate_drop_improvement: Optional[float] = None  # 原胜率损失 - 训练胜率损失
    training_score_loss: Optional[float] = None       # 训练手目损（用于排序复盘位置）
    category: str = "neutral"                    # repeated_error / new_error / improved / neutral
    problem_tags: list[str] = field(default_factory=list)
    original_move_no: Optional[int] = None       # 原实战对应手数（错题本回写/复盘定位用）

    def to_dict(self) -> dict:
        return {
            "move_no": self.move_no,
            "color": self.color,
            "played_move": self.played_move,
            "original_quality": self.original_quality,
            "training_quality": self.training_quality,
            "match_type": self.match_type,
            "score_loss_improvement": self.score_loss_improvement,
            "winrate_drop_improvement": self.winrate_drop_improvement,
            "training_score_loss": self.training_score_loss,
            "category": self.category,
            "problem_tags": list(self.problem_tags or []),
            "original_move_no": self.original_move_no,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MoveComparison":
        raw = data if isinstance(data, dict) else {}
        return cls(
            move_no=int(raw.get("move_no", 0) or 0),
            color=raw.get("color", ""),
            played_move=raw.get("played_move", ""),
            original_quality=raw.get("original_quality"),
            training_quality=raw.get("training_quality"),
            match_type=raw.get("match_type", MATCH_NONE),
            score_loss_improvement=raw.get("score_loss_improvement"),
            winrate_drop_improvement=raw.get("winrate_drop_improvement"),
            training_score_loss=raw.get("training_score_loss"),
            category=raw.get("category", "neutral"),
            problem_tags=list(raw.get("problem_tags") or []),
            original_move_no=raw.get("original_move_no"),
        )


@dataclass
class TrainingAnalysis:
    """训练后分析结果。

    兼容 当前任务.md §11.3 的 TrainingAnalysisResult 字段，同时保留请求中
    要求的入口签名（original_moves / training_moves / phase）。
    """
    # ---- 概览 ----
    phase: str
    phase_label: str
    task_id: Optional[str] = None
    source_game_id: Optional[str] = None

    # ---- 样本量 ----
    original_move_count: int = 0                 # 原实战该阶段有效评价手数
    training_move_count: int = 0                 # 训练有效评价手数
    effective_move_count: int = 0                # 训练有效样本（剔除 unknown）
    sample_insufficient: bool = False

    # ---- 平均目损 ----
    original_avg_score_loss: Optional[float] = None
    training_avg_score_loss: Optional[float] = None
    improvement_score_loss: Optional[float] = None   # original - training（>0 改善）

    # ---- 错误计数 ----
    original_blunder_count: int = 0
    training_blunder_count: int = 0
    original_inaccuracy_count: int = 0
    training_inaccuracy_count: int = 0

    # ---- 逐手对比 ----
    comparisons: list[MoveComparison] = field(default_factory=list)
    repeated_errors: list[MoveComparison] = field(default_factory=list)
    new_errors: list[MoveComparison] = field(default_factory=list)
    improved_moves: list[MoveComparison] = field(default_factory=list)

    # ---- 标签变化 ----
    original_problem_tag_counts: dict = field(default_factory=dict)
    training_problem_tag_counts: dict = field(default_factory=dict)
    problem_tag_changes: dict = field(default_factory=dict)   # {tag: (orig_n, train_n, delta)}

    # ---- 训练评分 ----
    hint_used_count: int = 0
    retry_count: int = 0
    training_score: int = 0
    training_label: str = "样本不足"

    # ---- 复习建议 ----
    review_recommendations: list[str] = field(default_factory=list)
    should_schedule_review: bool = False
    suggested_review_after_days: int = 0

    # ---- 推荐复盘位置（按 score_loss 降序的训练问题手）----
    recommended_review_positions: list[MoveComparison] = field(default_factory=list)

    version: int = VERSION

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "phase_label": self.phase_label,
            "task_id": self.task_id,
            "source_game_id": self.source_game_id,
            "original_move_count": self.original_move_count,
            "training_move_count": self.training_move_count,
            "effective_move_count": self.effective_move_count,
            "sample_insufficient": self.sample_insufficient,
            "original_avg_score_loss": self.original_avg_score_loss,
            "training_avg_score_loss": self.training_avg_score_loss,
            "improvement_score_loss": self.improvement_score_loss,
            "original_blunder_count": self.original_blunder_count,
            "training_blunder_count": self.training_blunder_count,
            "original_inaccuracy_count": self.original_inaccuracy_count,
            "training_inaccuracy_count": self.training_inaccuracy_count,
            "comparisons": [item.to_dict() for item in self.comparisons],
            "repeated_errors": [item.to_dict() for item in self.repeated_errors],
            "new_errors": [item.to_dict() for item in self.new_errors],
            "improved_moves": [item.to_dict() for item in self.improved_moves],
            "original_problem_tag_counts": dict(self.original_problem_tag_counts),
            "training_problem_tag_counts": dict(self.training_problem_tag_counts),
            "problem_tag_changes": {
                key: list(value) for key, value in self.problem_tag_changes.items()
            },
            "hint_used_count": self.hint_used_count,
            "retry_count": self.retry_count,
            "training_score": self.training_score,
            "training_label": self.training_label,
            "review_recommendations": list(self.review_recommendations),
            "should_schedule_review": self.should_schedule_review,
            "suggested_review_after_days": self.suggested_review_after_days,
            "recommended_review_positions": [
                item.to_dict() for item in self.recommended_review_positions
            ],
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TrainingAnalysis":
        raw = data if isinstance(data, dict) else {}

        def comparisons(key):
            return [
                MoveComparison.from_dict(item)
                for item in (raw.get(key) or [])
                if isinstance(item, dict)
            ]

        return cls(
            phase=raw.get("phase", "all"),
            phase_label=raw.get("phase_label", "全盘"),
            task_id=raw.get("task_id"),
            source_game_id=raw.get("source_game_id"),
            original_move_count=int(raw.get("original_move_count", 0) or 0),
            training_move_count=int(raw.get("training_move_count", 0) or 0),
            effective_move_count=int(raw.get("effective_move_count", 0) or 0),
            sample_insufficient=bool(raw.get("sample_insufficient", False)),
            original_avg_score_loss=raw.get("original_avg_score_loss"),
            training_avg_score_loss=raw.get("training_avg_score_loss"),
            improvement_score_loss=raw.get("improvement_score_loss"),
            original_blunder_count=int(raw.get("original_blunder_count", 0) or 0),
            training_blunder_count=int(raw.get("training_blunder_count", 0) or 0),
            original_inaccuracy_count=int(
                raw.get("original_inaccuracy_count", 0) or 0),
            training_inaccuracy_count=int(
                raw.get("training_inaccuracy_count", 0) or 0),
            comparisons=comparisons("comparisons"),
            repeated_errors=comparisons("repeated_errors"),
            new_errors=comparisons("new_errors"),
            improved_moves=comparisons("improved_moves"),
            original_problem_tag_counts=dict(
                raw.get("original_problem_tag_counts") or {}),
            training_problem_tag_counts=dict(
                raw.get("training_problem_tag_counts") or {}),
            problem_tag_changes={
                key: tuple(value) for key, value in (
                    raw.get("problem_tag_changes") or {}).items()
            },
            hint_used_count=int(raw.get("hint_used_count", 0) or 0),
            retry_count=int(raw.get("retry_count", 0) or 0),
            training_score=int(raw.get("training_score", 0) or 0),
            training_label=raw.get("training_label", "样本不足"),
            review_recommendations=list(
                raw.get("review_recommendations") or []),
            should_schedule_review=bool(
                raw.get("should_schedule_review", False)),
            suggested_review_after_days=int(
                raw.get("suggested_review_after_days", 0) or 0),
            recommended_review_positions=comparisons(
                "recommended_review_positions"),
            version=int(raw.get("version", VERSION) or VERSION),
        )


# ===================== 聚合辅助 =====================
def _avg(values) -> Optional[float]:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _count_quality(results, quality_key) -> int:
    return sum(1 for r in results if r.quality_key == quality_key)


def _tag_counts(results) -> dict:
    counts: dict[str, int] = {}
    for r in results:
        for tag in getattr(r, "problem_tags", []) or []:
            counts[tag] = counts.get(tag, 0) + 1
    return counts


def _phase_results(results, phase: Optional[str]) -> list:
    """按 stage 过滤；phase=None 表示不过滤。容错读取 result.stage。"""
    if phase is None:
        return list(results)
    out = []
    for r in results:
        st = getattr(r, "stage", None)
        if st is None:
            out.append(r)        # 缺失 stage 默认保留
        elif st == phase:
            out.append(r)
    return out


# ===================== 分类（§26.4 / §11.5-11.7）=====================
def _classify(orig: Optional[MoveQualityResult], train: MoveQualityResult,
              match_type: str) -> str:
    """返回单手分类：repeated_error / new_error / improved / neutral。"""
    # 训练手不是问题手且原实战也不是 → 中性
    train_problem = _is_problem(train)

    # 无法对齐到原实战：不标 new_error，避免错误归因（§26.4）。
    # 无论训练手是否问题手都判 neutral——new_error 仅在对齐可信时
    # （MATCH_EXACT_POSITION / MATCH_SAME_STEP）才在下文判断。
    if orig is None or orig.quality_key == QUALITY_UNKNOWN:
        return "neutral"

    orig_problem = _is_problem(orig)

    if match_type == MATCH_STAGE_PATTERN:
        shared_tags = set(getattr(orig, "problem_tags", []) or []) & set(
            getattr(train, "problem_tags", []) or [])
        if orig_problem and train_problem and shared_tags:
            return "repeated_error"
        return "neutral"

    # 改善：原问题手 → 训练提升到一般及以上
    if orig_problem and _is_improved_quality(train.quality_key):
        return "improved"

    # 重复错误：原问题手且训练仍是问题手
    if orig_problem and train_problem:
        return "repeated_error"

    # 新错误：仅在对齐可信（精确局面或同步对齐）时判断
    if (not orig_problem) and train_problem and match_type in (MATCH_EXACT_POSITION, MATCH_SAME_STEP):
        return "new_error"

    return "neutral"


def _build_comparison(orig: Optional[MoveQualityResult],
                      train: MoveQualityResult,
                      match_type: str) -> MoveComparison:
    sl_imp = None
    wd_imp = None
    if orig is not None:
        if orig.score_loss is not None and train.score_loss is not None:
            sl_imp = round(orig.score_loss - train.score_loss, 3)
        if orig.winrate_drop is not None and train.winrate_drop is not None:
            wd_imp = round(orig.winrate_drop - train.winrate_drop, 3)

    # 问题标签取训练手 + 原实战手的并集，便于复盘
    tags = []
    seen = set()
    for tag in ((getattr(train, "problem_tags", []) or [])
                + (getattr(orig, "problem_tags", []) if orig is not None else [])):
        if tag not in seen:
            seen.add(tag)
            tags.append(tag)

    category = _classify(orig, train, match_type)
    return MoveComparison(
        move_no=train.move_no,
        color=train.color,
        played_move=train.played_move,
        original_quality=(orig.quality_key if orig is not None else None),
        training_quality=train.quality_key,
        match_type=match_type,
        score_loss_improvement=sl_imp,
        winrate_drop_improvement=wd_imp,
        training_score_loss=train.score_loss,
        category=category,
        problem_tags=tags,
        original_move_no=(
            getattr(orig, "source_move_no", None)
            if orig is not None else None),
    )


# ===================== 训练评分（§11.4 / §26.5）=====================
def compute_training_score(training_avg_score_loss: Optional[float],
                           training_blunder_count: int,
                           training_inaccuracy_count: int,
                           improvement_score_loss: Optional[float],
                           hint_used_count: int = 0,
                           retry_count: int = 0) -> int:
    """训练评分（0-100）。原始公式见 §11.4。"""
    score = 100
    if training_avg_score_loss is not None:
        score -= training_avg_score_loss * 8
    score -= training_blunder_count * 15
    score -= training_inaccuracy_count * 6
    score -= hint_used_count * 3
    score -= retry_count * 2
    if improvement_score_loss is not None and improvement_score_loss > 0:
        score += improvement_score_loss * 5
    return max(0, min(100, round(score)))


def _apply_score_caps(score: int, hint_used_count: int, effective_moves: int,
                      training_moves, repeated_errors) -> tuple[int, str]:
    """应用 §26.5 的额外限制，返回 (score, label)。"""
    # 1) 有效用户手少于 4 → 样本不足
    if effective_moves < MIN_EFFECTIVE_MOVES:
        return (score, "样本不足")

    # 2) 提示比例 > 50% → 最高 85
    hint_ratio = (hint_used_count / effective_moves) if effective_moves else 0
    if hint_ratio > HINT_RATIO_CAP and score > HINT_CAP_SCORE:
        score = HINT_CAP_SCORE

    # 3) 存在重复恶手 → 最高标签不超过「基本合格」（即 score 上限 74）
    has_repeated_blunder = any(
        c.original_quality == QUALITY_BLUNDER and c.training_quality == QUALITY_BLUNDER
        for c in repeated_errors
    )
    if has_repeated_blunder and score > 74:
        score = 74

    return (score, _label_for_score(score))


# ===================== 复习计划（§26.6）=====================
def _plan_review(score: int, label: str, repeated_errors,
                 sample_insufficient: bool) -> tuple[bool, int]:
    """返回 (should_schedule_review, suggested_review_after_days)。"""
    if sample_insufficient:
        # 样本不足也建议近期复习，确认掌握
        return (True, 1)

    has_repeated_blunder = any(
        c.original_quality == QUALITY_BLUNDER and c.training_quality == QUALITY_BLUNDER
        for c in repeated_errors
    )
    has_repeated = len(repeated_errors) > 0
    if has_repeated_blunder:
        return (True, 1)
    if has_repeated or score < 60:
        return (True, 3)
    if 60 <= score <= 74:
        return (True, 5)
    if 75 <= score <= 89:
        return (True, 7)
    # score >= 90 且无重复错误
    return (True, 14)


def _build_recommendations(analysis: TrainingAnalysis) -> list[str]:
    """生成中文复习建议文案（§12 示例文案风格）。"""
    recs: list[str] = []
    if analysis.sample_insufficient:
        recs.append("本次训练有效手数不足 %d 手，结果仅供参考，建议再完整重下一次。"
                    % MIN_EFFECTIVE_MOVES)
        return recs

    recs.append("本次训练评分：%d，%s。" % (analysis.training_score, analysis.training_label))

    if (analysis.original_avg_score_loss is not None
            and analysis.training_avg_score_loss is not None
            and analysis.improvement_score_loss is not None):
        if analysis.improvement_score_loss > 0:
            recs.append("原阶段平均目损 %.1f，本次训练平均目损 %.1f，平均改善 %.1f 目。"
                        % (analysis.original_avg_score_loss,
                           analysis.training_avg_score_loss,
                           analysis.improvement_score_loss))
        elif analysis.improvement_score_loss < 0:
            recs.append("原阶段平均目损 %.1f，本次训练平均目损 %.1f，平均退步 %.1f 目。"
                        % (analysis.original_avg_score_loss,
                           analysis.training_avg_score_loss,
                           abs(analysis.improvement_score_loss)))
        else:
            recs.append("原阶段平均目损 %.1f，本次训练平均目损 %.1f，基本持平。"
                        % (analysis.original_avg_score_loss,
                           analysis.training_avg_score_loss))
    elif analysis.training_avg_score_loss is not None:
        recs.append("本次训练平均目损 %.1f（原阶段数据不足，不输出改善幅度）。"
                    % analysis.training_avg_score_loss)

    if analysis.improved_moves:
        recs.append("修正了 %d 个原实战问题手。" % len(analysis.improved_moves))
    if analysis.repeated_errors:
        # 用 problem_tags 描述重复出现的问题模式
        tags = []
        for c in analysis.repeated_errors:
            tags.extend(c.problem_tags)
        if tags:
            from move_quality import PROBLEM_TAGS
            tag_names = [PROBLEM_TAGS.get(t, t) for t in dict.fromkeys(tags)]
            recs.append("仍有 %d 处问题重复出现（%s）。"
                        % (len(analysis.repeated_errors), "、".join(tag_names)))
        else:
            recs.append("仍有 %d 处问题重复出现。" % len(analysis.repeated_errors))
    if analysis.new_errors:
        recs.append("新出现 %d 处问题，建议对照 AI 首选复盘。" % len(analysis.new_errors))

    if analysis.should_schedule_review:
        recs.append("建议 %d 天后复习本阶段。" % analysis.suggested_review_after_days)
    return recs


# ===================== 主入口 =====================
def analyze_training(original_moves,
                     training_moves,
                     phase: Optional[str] = "middle",
                     *,
                     task_id: Optional[str] = None,
                     source_game_id: Optional[str] = None,
                     hint_used_count: int = 0,
                     retry_count: int = 0) -> TrainingAnalysis:
    """训练后分析主入口。

    参数：
      original_moves: 原实战该阶段用户执棋方的 MoveQualityResult 列表。
      training_moves: 训练中用户执棋方的 MoveQualityResult 列表。
      phase: "opening" / "middle" / "endgame"，或 None 表示不过滤。
              original_moves / training_moves 会按 result.stage 过滤到该阶段。
      task_id / source_game_id: 可选标识，原样写入结果。
      hint_used_count: 本次训练使用提示的用户回合数（计入评分扣分）。
      retry_count: 本次训练的重试次数（计入评分扣分）。

    返回 TrainingAnalysis。边界情况：
      * 无训练数据 / 无原实战数据 / 空阶段 → 返回带 sample_insufficient 或空统计
        的 TrainingAnalysis，不抛异常。
    """
    # 阶段过滤
    orig_phase = _phase_results(original_moves or [], phase)
    train_phase = _phase_results(training_moves or [], phase)

    from review import normalize_phase, PHASE_LABELS
    norm_phase = normalize_phase(phase) if phase is not None else None
    phase_label = PHASE_LABELS.get(norm_phase, "全盘") if norm_phase else "全盘"

    analysis = TrainingAnalysis(
        phase=norm_phase or "all",
        phase_label=phase_label,
        task_id=task_id,
        source_game_id=source_game_id,
        original_move_count=len(orig_phase),
        training_move_count=len(train_phase),
        hint_used_count=hint_used_count,
        retry_count=retry_count,
    )

    # ---- 原实战聚合 ----
    orig_effective = [r for r in orig_phase if r.quality_key != QUALITY_UNKNOWN]
    analysis.original_avg_score_loss = _avg([r.score_loss for r in orig_effective])
    analysis.original_blunder_count = _count_quality(orig_effective, QUALITY_BLUNDER)
    analysis.original_inaccuracy_count = _count_quality(orig_effective, QUALITY_INACCURACY)
    analysis.original_problem_tag_counts = _tag_counts(orig_effective)

    # ---- 训练聚合 ----
    train_effective = [r for r in train_phase if r.quality_key != QUALITY_UNKNOWN]
    analysis.training_avg_score_loss = _avg([r.score_loss for r in train_effective])
    analysis.training_blunder_count = _count_quality(train_effective, QUALITY_BLUNDER)
    analysis.training_inaccuracy_count = _count_quality(train_effective, QUALITY_INACCURACY)
    analysis.training_problem_tag_counts = _tag_counts(train_effective)
    analysis.effective_move_count = len(train_effective)

    # ---- 改善幅度（仅当原实战有数据时输出）----
    if (analysis.original_avg_score_loss is not None
            and analysis.training_avg_score_loss is not None):
        analysis.improvement_score_loss = round(
            analysis.original_avg_score_loss - analysis.training_avg_score_loss, 3)

    # ---- 样本不足判定 ----
    analysis.sample_insufficient = analysis.effective_move_count < MIN_EFFECTIVE_MOVES

    # ---- 逐手对齐 + 分类 ----
    pairs = _align_pairs(orig_phase, train_phase)
    for orig, train, match_type in pairs:
        comp = _build_comparison(orig, train, match_type)
        analysis.comparisons.append(comp)
        if comp.category == "repeated_error":
            analysis.repeated_errors.append(comp)
        elif comp.category == "new_error":
            analysis.new_errors.append(comp)
        elif comp.category == "improved":
            analysis.improved_moves.append(comp)

    # ---- 问题标签变化 ----
    all_tags = set(analysis.original_problem_tag_counts) | set(analysis.training_problem_tag_counts)
    changes = {}
    for tag in all_tags:
        o = analysis.original_problem_tag_counts.get(tag, 0)
        t = analysis.training_problem_tag_counts.get(tag, 0)
        changes[tag] = (o, t, t - o)
    analysis.problem_tag_changes = changes

    # ---- 训练评分 ----
    raw_score = compute_training_score(
        analysis.training_avg_score_loss,
        analysis.training_blunder_count,
        analysis.training_inaccuracy_count,
        analysis.improvement_score_loss,
        hint_used_count=hint_used_count,
        retry_count=retry_count,
    )
    score, label = _apply_score_caps(
        raw_score, hint_used_count, analysis.effective_move_count,
        train_effective, analysis.repeated_errors)
    analysis.training_score = score
    analysis.training_label = label

    # ---- 推荐复盘位置：训练问题手按 score_loss 降序 ----
    review_pool = [c for c in analysis.comparisons
                   if c.training_quality in PROBLEM_QUALITIES]
    review_pool.sort(
        key=lambda c: _train_score_loss_of(c), reverse=True)
    analysis.recommended_review_positions = review_pool[:6]

    # ---- 复习计划 ----
    should, days = _plan_review(
        score, label, analysis.repeated_errors, analysis.sample_insufficient)
    analysis.should_schedule_review = should
    analysis.suggested_review_after_days = days

    # ---- 文案 ----
    analysis.review_recommendations = _build_recommendations(analysis)
    return analysis


def _train_score_loss_of(comp: MoveComparison) -> float:
    """从 MoveComparison 取训练目损用于排序；缺失返回 -1。"""
    val = comp.training_score_loss
    if val is None:
        return -1.0
    return val
