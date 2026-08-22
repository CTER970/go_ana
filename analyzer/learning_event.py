"""learning_event —— 学习事件：个人错误模型的核心数据结构（项目大纲 §52-54）。

一条 LearningEvent 对应一个值得学习的关键局面。设计约束：

- 客观事实（KataGo 数值：score_loss / winrate_drop / best_move 等）与解释数据
  （错误分类、学习优先级、重试结果）分层存放，解释层永远不允许覆盖客观字段；
- 携带完整版本信息（引擎/模型/visits/算法版本），半年后仍能解释"同一局面
  为什么以前标 A、现在标 B"；
- 序列化为 snake_case JSON，与 learning_store 持久化格式一致。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime

LEARNING_EVENT_VERSION = 1

# 事件类别（接力板#12）：问题手事件（复盘同步，参与 SRS/掌握/复发统计）与
# 官子训练作答事件（题源语义≠问题手——不入错题本、不进 SRS 调度、掌握状态
# 恒 new、无 review_due_date，只累积 attempts 作答历史供画像汇总）。
KIND_PROBLEM = "problem"
KIND_ENDGAME_DRILL = "endgame_drill"
KINDS = (KIND_PROBLEM, KIND_ENDGAME_DRILL)

# 掌握状态（项目大纲 §40）：new → understanding → retained → transferred；
# unstable = 复习会但实战继续犯（最有诊断价值的一种）。
MASTERY_NEW = "new"
MASTERY_UNDERSTANDING = "understanding"
MASTERY_RETAINED = "retained"
MASTERY_TRANSFERRED = "transferred"
MASTERY_UNSTABLE = "unstable"
MASTERY_STATES = (
    MASTERY_NEW, MASTERY_UNDERSTANDING, MASTERY_RETAINED,
    MASTERY_TRANSFERRED, MASTERY_UNSTABLE,
)

# 主动复盘结果四分类（项目大纲 §25）。
RETRY_CORRECTED = "corrected"
RETRY_IMPROVED = "improved"
RETRY_REPEATED = "repeated"
RETRY_ALTERNATIVE_CORRECT = "alternative_correct"
RETRY_STATUSES = (RETRY_CORRECTED, RETRY_IMPROVED, RETRY_REPEATED,
                  RETRY_ALTERNATIVE_CORRECT)


def _now():
    # 毫秒精度：盘与盘的先后（实战复发的时间方向守卫）依赖 created_at
    # 排序，秒级精度在批量同步时会并列退化成字典序
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def event_id(game_id, move_no, color):
    """同一盘同一手同一方的稳定 id（与 mistake_book._item_id 同源策略）。"""
    raw = "%s:%s:%s" % (game_id, int(move_no), str(color).upper())
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def endgame_event_id(game_id, move_no, color):
    """官子训练作答事件的稳定 id：与问题手 event_id 同源策略但命名空间隔离。

    同一盘同一手可能既有问题手事件又有官子作答事件（官子题正是从实战
    失损手派生的），save_event 按 id upsert——两者 id 必须不同，否则官子
    作答会覆盖问题手事件的进度字段。game_id 字段保持原值不隔离：
    remove_game 按局联动删除时官子作答事件一并清掉。
    """
    return event_id("%s#endgame" % str(game_id), move_no, color)


def position_key_from_board(board):
    """局面指纹：size + 盘面 + 轮走方（不含历史，只标识当前局面）。"""
    if board is None:
        return ""
    try:
        rows = ["".join(str(int(cell)) for cell in row) for row in board.grid]
        digest = hashlib.sha1(
            ("%d|%s|%d" % (board.size, "/".join(rows), board.to_move)
             ).encode("utf-8")).hexdigest()
        return digest[:16]
    except Exception:
        return ""


@dataclass
class LearningEvent:
    version: int = LEARNING_EVENT_VERSION
    id: str = ""
    kind: str = KIND_PROBLEM             # 事件类别：problem / endgame_drill
    game_id: str = ""
    game_name: str = ""
    move_no: int = 0                     # 第几手（与 ReviewReport 口径一致：根=0）
    position_key: str = ""               # 局面指纹（同局面跨棋局识别重复错误）
    player_color: str = ""               # "B" / "W"

    # ---- 客观事实（KataGo，解释层不可改写）----
    played_move: str = ""                # 实战选点 GTP（pass 为 "pass"）
    best_move: str = ""                  # AI 首选 GTP
    score_loss: float = 0.0              # 目损（走子方视角，≥0）
    winrate_drop: float = 0.0            # 胜率损失（百分点）
    quality_key: str = ""                # inaccuracy / mistake / blunder
    stage: str = "middle"                # opening / middle / endgame
    problem_tags: list = field(default_factory=list)   # 旧 move_quality 标签
    ai_rank: int = 0                     # 实战选点在 AI 候选中的排名（0=不在返回中）
    complexity: float = 0.0              # 可学习度分量（阶段2由 learning_priority 填）
    drill_kind: str = ""                 # 官子题类型：loss（目损收束）/ sente（先后手转换）
    drill_value: float = 0.0             # 收束价值（目；loss=目损，sente=先手代价）

    # ---- 分析版本（保证历史数据可解释）----
    katago_version: str = ""
    model_hash: str = ""
    visits: int = 0
    analysis_config_hash: str = ""

    # ---- Human SL（阶段5接入前恒为默认值）----
    human_profile: str = ""              # 如 rank_1d
    human_prior_current: float = 0.0     # 当前棋力下这手的概率
    human_prior_stronger: float = 0.0    # 高段位下这手的概率（level_gap 依据）

    # ---- 错误分类（taxonomy，阶段2填）----
    primary_category: str = ""           # 如 weak_groups
    secondary_categories: list = field(default_factory=list)
    category_confidence: str = ""        # high / medium / low
    category_evidence: list = field(default_factory=list)
    taxonomy_version: str = ""

    # ---- 学习优先级（learning_priority，阶段2填）----
    learning_priority: float = 0.0       # 0-1 综合分
    priority_components: dict = field(default_factory=dict)
    priority_version: str = ""
    priority_status: str = "provisional"  # provisional | final（P1-2：一次终算到处读）

    # ---- SRS 调度状态（P0-4：唯一事实源迁入事件；书侧只读投影）----
    review_interval_days: int = 0
    review_repetitions: int = 0
    review_lapses: int = 0
    last_reviewed_at: str = ""
    last_review_result: str = ""

    # ---- 主动复盘与作答历史（阶段3/4填；save_attempt 追加）----
    user_retry_move: str = ""
    retry_score_loss: float = 0.0
    retry_status: str = ""               # RETRY_STATUSES 之一
    attempts: list = field(default_factory=list)

    # ---- 复发与掌握 ----
    recurrence_cluster: str = ""         # 同类错误簇 id（阶段7 error_chain 填）
    recurrence_count: int = 0            # 过去 N 盘同簇出现次数（含本盘）
    mastery_state: str = MASTERY_NEW
    review_due_date: str = ""            # ISO 日期；与错题本调度联动

    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    # ---- 构造与序列化 ----
    @classmethod
    def from_problem(cls, game_id, problem, game_name="", board=None,
                     version_info=None):
        """从 profileSummary.top_problem_moves 条目构建（现有链路的接入点）。"""
        problem = dict(problem or {})
        color = str(problem.get("color") or "").upper()
        move_no = int(problem.get("move_no") or 0)
        version_info = dict(version_info or {})
        evt = cls(
            id=event_id(game_id, move_no, color),
            game_id=str(game_id or ""),
            game_name=str(game_name or ""),
            move_no=move_no,
            position_key=position_key_from_board(board),
            player_color=color,
            played_move=str(problem.get("played_move") or ""),
            best_move=str(problem.get("best_move") or ""),
            score_loss=float(problem.get("score_loss") or 0.0),
            winrate_drop=float(problem.get("winrate_drop") or 0.0),
            quality_key=str(problem.get("quality_key") or ""),
            stage=str(problem.get("stage") or "middle"),
            problem_tags=list(problem.get("problem_tags") or []),
            katago_version=str(version_info.get("katago_version") or ""),
            model_hash=str(version_info.get("model_hash") or ""),
            visits=int(version_info.get("visits") or 0),
            analysis_config_hash=str(version_info.get("analysis_config_hash") or ""),
        )
        return evt

    @classmethod
    def from_endgame_drill(cls, game_id, drill, game_name=""):
        """从 endgame_drill.EndgameDrill 构建官子训练作答事件（接力板#12）。

        客观字段 = 题面事实（实战着法 / AI 一选 / 实战目损 / 收束价值），
        用户作答走 add_attempt 追加（每次作答一条）。语义边界：
          - primary_category 留空：官子题不参与 taxonomy 分类与复发统计
            （build_recurrence_index / summarize_learning 的类别口径不受污染）；
          - 掌握 / 复习字段保持默认：不进 SRS 调度（D5 同源边界——训练作答
            不证 retention），也不参与实战迁移状态机。
        鸭子类型读取属性，不 import endgame_drill（保持本模块轻依赖）。
        """
        return cls(
            id=endgame_event_id(
                game_id, int(getattr(drill, "move_number", 0) or 0),
                str(getattr(drill, "color", "") or "")),
            kind=KIND_ENDGAME_DRILL,
            game_id=str(game_id or ""),
            game_name=str(game_name or ""),
            move_no=int(getattr(drill, "move_number", 0) or 0),
            player_color=str(getattr(drill, "color", "") or "").upper(),
            played_move=str(getattr(drill, "played_move", "") or ""),
            best_move=str(getattr(drill, "best_move", "") or ""),
            score_loss=float(getattr(drill, "loss", 0.0) or 0.0),
            stage="endgame",
            drill_kind=str(getattr(drill, "drill_kind", "") or ""),
            drill_value=float(getattr(drill, "value", 0.0) or 0.0),
        )

    def to_dict(self):
        return {
            "version": self.version,
            "id": self.id,
            "kind": self.kind,
            "game_id": self.game_id,
            "game_name": self.game_name,
            "move_no": self.move_no,
            "position_key": self.position_key,
            "player_color": self.player_color,
            "played_move": self.played_move,
            "best_move": self.best_move,
            "score_loss": self.score_loss,
            "winrate_drop": self.winrate_drop,
            "quality_key": self.quality_key,
            "stage": self.stage,
            "problem_tags": list(self.problem_tags),
            "ai_rank": self.ai_rank,
            "complexity": self.complexity,
            "drill_kind": self.drill_kind,
            "drill_value": self.drill_value,
            "katago_version": self.katago_version,
            "model_hash": self.model_hash,
            "visits": self.visits,
            "analysis_config_hash": self.analysis_config_hash,
            "human_profile": self.human_profile,
            "human_prior_current": self.human_prior_current,
            "human_prior_stronger": self.human_prior_stronger,
            "primary_category": self.primary_category,
            "secondary_categories": list(self.secondary_categories),
            "category_confidence": self.category_confidence,
            "category_evidence": list(self.category_evidence),
            "taxonomy_version": self.taxonomy_version,
            "learning_priority": self.learning_priority,
            "priority_components": dict(self.priority_components),
            "priority_version": self.priority_version,
            "priority_status": self.priority_status,
            "review_interval_days": self.review_interval_days,
            "review_repetitions": self.review_repetitions,
            "review_lapses": self.review_lapses,
            "last_reviewed_at": self.last_reviewed_at,
            "last_review_result": self.last_review_result,
            "user_retry_move": self.user_retry_move,
            "retry_score_loss": self.retry_score_loss,
            "retry_status": self.retry_status,
            "attempts": [dict(a) for a in self.attempts],
            "recurrence_cluster": self.recurrence_cluster,
            "recurrence_count": self.recurrence_count,
            "mastery_state": self.mastery_state,
            "review_due_date": self.review_due_date,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data):
        data = dict(data or {})
        known = {f for f in cls.__dataclass_fields__}
        kwargs = {k: v for k, v in data.items() if k in known}
        evt = cls(**kwargs)
        evt.version = int(data.get("version") or LEARNING_EVENT_VERSION)
        if not evt.kind:                       # 旧数据无 kind 键/空值 → 问题手
            evt.kind = KIND_PROBLEM
        evt.attempts = [dict(a) for a in (data.get("attempts") or [])]
        evt.problem_tags = list(data.get("problem_tags") or [])
        evt.secondary_categories = list(data.get("secondary_categories") or [])
        evt.category_evidence = list(data.get("category_evidence") or [])
        evt.priority_components = dict(data.get("priority_components") or {})
        return evt

    # ---- 行为 ----
    def record_retry(self, move, score_loss, status):
        """记录一次主动复盘重选（项目大纲 §24-25）；status 必须是四分类之一。"""
        status = str(status or "").lower()
        if status not in RETRY_STATUSES:
            raise ValueError("未知重试状态: %r" % status)
        self.user_retry_move = str(move or "")
        self.retry_score_loss = float(score_loss or 0.0)
        self.retry_status = status
        self.updated_at = _now()
        # 立即复盘能纠正 → 已理解；再次重复同类错误 → 真知识盲区。
        if status == RETRY_REPEATED:
            if self.mastery_state in (MASTERY_NEW, MASTERY_UNDERSTANDING):
                self.mastery_state = MASTERY_UNSTABLE if self.recurrence_count > 1 \
                    else MASTERY_UNDERSTANDING
        elif self.mastery_state == MASTERY_NEW:
            self.mastery_state = MASTERY_UNDERSTANDING

    def add_attempt(self, played_move, score_loss=None, assessment=None,
                    ai_rank=None, hint_used=False, thinking_time=None):
        """追加一次作答记录（完整学习曲线的数据源，项目大纲 §39）。"""
        attempt = {
            "date": _now(),
            "played_move": str(played_move or ""),
            "score_loss": (None if score_loss is None else float(score_loss)),
            "assessment": str(assessment or ""),
            "ai_rank": (None if ai_rank is None else int(ai_rank)),
            "hint_used": bool(hint_used),
            "thinking_time": (None if thinking_time is None
                              else float(thinking_time)),
        }
        self.attempts.append(attempt)
        self.updated_at = _now()
        return attempt
