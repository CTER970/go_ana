"""problem_drill —— 涨棋网风格的问题手训练钻取（纯逻辑，不依赖 tkinter / KataGo 进程）。

把一局已分析棋中“用户方”的问题手，组织成可逐题训练的钻取，对标涨棋网大师级
复盘报告里“题目训练的方式每手棋 2 页”的问题手详解：

  * 每题取问题手父局面的 AI 候选（一选 / 二选 / 三选 …）+ 实战，生成“选点对比表”
    （评价 / 评级 / 坐标 / 计算量 / 选点概率 / 胜率 / 领先目 / 胜率损失 / 目数损失）。
  * 把这些选点用乱序字母 A/B/C/D 标记，先隐藏答案，供用户先思考、再作答（quiz）。
  * 附带 4 个变化图（正解图 = 一选 PV / 失败图 = 实战 PV / 二选 / 三选）。
  * 区分“其它问题手”与“超纲问题手”（同水平棋手难以掌握的好手）。

设计原则：
  * 复用 review.MoveEvaluation 与 KataGo moveInfo 原始字段，不重新评价单手。
  * 所有胜率 / 目差数值都转成“走子方视角”，可直接展示，含义与涨棋网一致。
  * 纯逻辑、可被无头测试覆盖；UI 层只负责取数与渲染。
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

from review import GRADE_DOUBT, LOSS_DEFAULT_THRESHOLD
import learning_priority

VERSION = 1

# ===================== 常量 =====================
DEFAULT_MAX_MOVES = 10          # 涨棋网：最多详细讲解 10 手问题手
DEFAULT_LEARNING_MOVES = 5      # 学习排序模式：每盘默认 5 个学习节点（大纲 §19）
DEFAULT_CANDIDATES = 3          # 一选 / 二选 / 三选
LETTERS = "ABCDEFGH"

# 候选评级（按相对一选的目损）：与 move_quality 的 6 档对齐显示文案
QUALITY_BEST = "最佳"
QUALITY_GOOD = "好手"
QUALITY_NORMAL = "一般"
QUALITY_INACCURACY = "欠佳"
QUALITY_BLUNDER = "恶手"

# 超纲启发式：AI 一选的先验（选点概率）很低 + 实战目损明显 → 同水平难以掌握
OUT_OF_REACH_MAX_POLICY = 0.05
OUT_OF_REACH_MIN_LOSS = GRADE_DOUBT      # 3.0 目

EVAL_LABELS = {0: "一选", 1: "二选", 2: "三选", 3: "四选", 4: "五选"}
VARIATION_KEYS = ("正解图", "失败图", "二选", "三选")


def quality_label_for_loss(loss_vs_best: float, *, is_best: bool = False) -> str:
    """按相对一选的目损给候选打评级文案。"""
    if is_best or loss_vs_best <= 0.01:
        return QUALITY_BEST
    if loss_vs_best < 1.5:
        return QUALITY_GOOD
    if loss_vs_best < GRADE_DOUBT:
        return QUALITY_NORMAL
    if loss_vs_best < 6.0:
        return QUALITY_INACCURACY
    return QUALITY_BLUNDER


# ===================== 走子方视角换算 =====================
def _mover_winrate(black_winrate: float, color: str) -> float:
    return float(black_winrate) if color == "B" else 1.0 - float(black_winrate)


def _mover_score(black_score: float, color: str) -> float:
    return float(black_score) if color == "B" else -float(black_score)


def _num(mapping, key, default=0.0):
    value = (mapping or {}).get(key, default)
    return default if value is None else float(value)


def _coord_or_pass(move: str) -> str:
    move = str(move or "")
    return move if move and move.lower() != "pass" else "pass"


# ===================== 数据结构 =====================
@dataclass
class DrillCandidate:
    """选点对比表中的一行（一选 / 二选 / 三选 … 或 实战）。"""
    key: str                    # "c0"/"c1"/.../"actual"
    eval_label: str             # 一选 / 二选 / 三选 / … / 实战
    move: str                   # GTP 坐标或 "pass"
    coord: str                  # 展示坐标或 "pass"
    visits: int                 # 计算量
    policy: float               # 选点概率（0-1）
    winrate: float              # 走子方胜率（0-1）
    score_lead: float           # 走子方领先目
    winrate_loss: float         # 相对一选的胜率损失（百分点，>=0）
    score_loss: float           # 相对一选的目损（>=0）
    quality_label: str          # 最佳 / 好手 / 一般 / 欠佳 / 恶手
    pv: list = field(default_factory=list)   # 该选的变化（首着起）
    is_actual: bool = False

    def to_dict(self) -> dict:
        return {
            "key": self.key, "evalLabel": self.eval_label,
            "move": self.move, "coord": self.coord,
            "visits": self.visits, "policy": round(self.policy, 6),
            "winrate": round(self.winrate, 6), "scoreLead": round(self.score_lead, 3),
            "winrateLoss": round(self.winrate_loss, 3),
            "scoreLoss": round(self.score_loss, 3),
            "qualityLabel": self.quality_label,
            "pv": list(self.pv or []), "isActual": self.is_actual,
        }


@dataclass
class DrillMove:
    """一道问题手训练题。"""
    move_number: int
    color: str                              # "B" / "W"
    phase_label: str                        # 布局 / 中盘 / 关子
    played_move: str                        # 实战 GTP 坐标或 "pass"
    played_quality: str                     # 实战评级文案
    loss: float                             # 实战目损（走子方视角）
    best_move: str                          # 一选 GTP 坐标
    best_coord: str
    candidates: list = field(default_factory=list)   # DrillCandidate[]，顺序：一选/二选/三选/…/实战
    quiz_order: list = field(default_factory=list)   # 候选 key 的乱序（下标 0 = 字母 A）
    variations: dict = field(default_factory=dict)   # {正解图/失败图/二选/三选: [pv]}
    is_out_of_reach: bool = False
    learning_priority: float = 0.0          # 学习排序模式：综合优先级（0-1）
    priority_components: dict = field(default_factory=dict)
    priority_version: int = 0

    def letter_of(self, key: str) -> Optional[str]:
        """候选 key → 乱序字母（None 表示不在 quiz 里）。"""
        try:
            return LETTERS[self.quiz_order.index(key)]
        except (ValueError, IndexError):
            return None

    def key_of(self, letter: str) -> Optional[str]:
        """乱序字母 → 候选 key。"""
        letter = (letter or "").strip().upper()
        if not letter or letter not in LETTERS:
            return None
        idx = LETTERS.index(letter)
        if 0 <= idx < len(self.quiz_order):
            return self.quiz_order[idx]
        return None

    def candidate(self, key: str) -> Optional[DrillCandidate]:
        for c in self.candidates:
            if c.key == key:
                return c
        return None

    @property
    def best_candidate(self) -> Optional[DrillCandidate]:
        return self.candidate("c0")

    @property
    def actual_candidate(self) -> Optional[DrillCandidate]:
        return self.candidate("actual")

    def to_dict(self) -> dict:
        return {
            "moveNumber": self.move_number, "color": self.color,
            "phaseLabel": self.phase_label, "playedMove": self.played_move,
            "playedQuality": self.played_quality, "loss": round(self.loss, 3),
            "bestMove": self.best_move, "bestCoord": self.best_coord,
            "candidates": [c.to_dict() for c in self.candidates],
            "quizOrder": list(self.quiz_order),
            "variations": {k: list(v or []) for k, v in self.variations.items()},
            "isOutOfReach": self.is_out_of_reach,
            "learningPriority": round(self.learning_priority, 4),
            "priorityComponents": dict(self.priority_components),
            "priorityVersion": self.priority_version,
        }


@dataclass
class ProblemDrill:
    """整局问题手训练钻取。"""
    user_color: str                                 # "B" / "W" / "both"
    user_color_label: str
    moves: list = field(default_factory=list)       # DrillMove[]（已按目损降序）
    other_problems: list = field(default_factory=list)   # {move,color,quality}
    out_of_reach: list = field(default_factory=list)     # {move,color,quality}
    warnings: list = field(default_factory=list)
    version: int = VERSION

    @property
    def is_empty(self) -> bool:
        return not self.moves

    def to_dict(self) -> dict:
        return {
            "userColor": self.user_color,
            "userColorLabel": self.user_color_label,
            "moves": [m.to_dict() for m in self.moves],
            "otherProblems": list(self.other_problems),
            "outOfReach": list(self.out_of_reach),
            "warnings": list(self.warnings),
            "version": self.version,
        }


# ===================== 单题构建 =====================
def _eval_label(index: int) -> str:
    return EVAL_LABELS.get(index, "第%d选" % (index + 1))


def _actual_stats(eval_, mis, color):
    """实战行：优先用父 moveInfos 里的真实 visits/policy/pv；胜率目差用实战后续局面。
    返回 (visits, policy, pv, in_infos)。"""
    actual_gtp = "pass" if eval_.is_pass else (eval_.coord or "")
    for m in mis:
        if (m.get("move") or "") == actual_gtp and not eval_.is_pass:
            return (int(_num(m, "visits", 0)), float(_num(m, "prior", 0.0)),
                    list(m.get("pv") or []), True)
    # 不在候选里（常见：pass / 远离 AI 候选的恶手）
    return (0, 0.0, ([actual_gtp] if actual_gtp else []), False)


def build_drill_move(eval_, mis, *, board_size: int = 19,
                     candidate_count: int = DEFAULT_CANDIDATES,
                     phase_label: str = "",
                     played_quality: Optional[str] = None) -> Optional[DrillMove]:
    """把一个 MoveEvaluation + 其父局面 moveInfos 组装成 DrillMove。

    返回 None 表示数据不足以建题（父局面无 moveInfos / 无一选）。
    """
    mis = sorted((mis or []), key=lambda m: m.get("order", 99))
    if not mis:
        return None
    color = eval_.color
    best_info = mis[0]
    best_move = str(best_info.get("move") or "pass")
    best_wr = _mover_winrate(_num(best_info, "winrate", 0.5), color)
    best_sc = _mover_score(_num(best_info, "scoreLead", 0.0), color)
    best_policy = float(_num(best_info, "prior", 0.0))

    candidates: list[DrillCandidate] = []

    # ---- AI 候选（一选 / 二选 / 三选 …）----
    valid_idx = 0
    for m in mis:
        mv = str(m.get("move") or "")
        if not mv or mv.lower() == "pass":
            # pass 一般不作为训练选点展示（涨棋网只标实际落点）；跳过
            continue
        wr = _mover_winrate(_num(m, "winrate", best_wr), color)
        sc = _mover_score(_num(m, "scoreLead", best_sc), color)
        s_loss = max(0.0, best_sc - sc)
        w_loss = max(0.0, (best_wr - wr) * 100.0)
        candidates.append(DrillCandidate(
            key="c%d" % valid_idx,
            eval_label=_eval_label(valid_idx),
            move=mv, coord=_coord_or_pass(mv),
            visits=int(_num(m, "visits", 0)),
            policy=float(_num(m, "prior", 0.0)),
            winrate=wr, score_lead=sc,
            winrate_loss=w_loss, score_loss=s_loss,
            quality_label=quality_label_for_loss(s_loss, is_best=(valid_idx == 0)),
            pv=[str(x) for x in (m.get("pv") or [])],
            is_actual=False,
        ))
        valid_idx += 1
        if valid_idx >= candidate_count:
            break
    if not candidates:
        return None

    # ---- 实战行 ----
    a_visits, a_policy, a_pv, _in = _actual_stats(eval_, mis, color)
    # 实战后续局面的走子方胜率 / 目差（eval_ 的 after 态）
    a_wr = _mover_winrate(eval_.winrate_after, color)
    a_sc = _mover_score(eval_.score_lead_after, color)
    a_s_loss = max(0.0, float(eval_.loss or 0.0))
    # 走子方胜率损失：落子前后走子方胜率之差
    wr_before = _mover_winrate(eval_.winrate_before, color)
    a_w_loss = max(0.0, (wr_before - a_wr) * 100.0)
    if not a_pv:
        a_pv = [eval_.coord or ("pass" if eval_.is_pass else "?")]
    played_q = played_quality or quality_label_for_loss(a_s_loss)
    actual = DrillCandidate(
        key="actual", eval_label="实战",
        move=("pass" if eval_.is_pass else (eval_.coord or "?")),
        coord=("pass" if eval_.is_pass else (eval_.coord or "?")),
        visits=a_visits, policy=a_policy,
        winrate=a_wr, score_lead=a_sc,
        winrate_loss=a_w_loss, score_loss=a_s_loss,
        quality_label=played_q, pv=[str(x) for x in a_pv], is_actual=True,
    )
    candidates.append(actual)

    # ---- quiz 乱序字母（确定性，便于复现）----
    # pass 实战没有棋盘落点，不参与字母标记（但仍出现在对比表里）。
    keys = [c.key for c in candidates if c.key != "actual"]
    if not eval_.is_pass:
        keys.append("actual")
    rng = random.Random(0x9E37 + (int(eval_.move_number) * 2654435761 & 0xFFFFFFFF))
    shuffled = list(keys)
    rng.shuffle(shuffled)
    # 保证不与逻辑顺序完全相同（避免偶尔“没乱序”）
    if len(shuffled) > 2 and shuffled == keys:
        shuffled = shuffled[1:] + shuffled[:1]

    # ---- 变化图 ----
    c1 = candidates[1].pv if len(candidates) > 1 else []
    c2 = candidates[2].pv if len(candidates) > 2 else []
    variations = {
        "正解图": list(candidates[0].pv or []),
        "失败图": list(actual.pv or []),
        "二选": list(c1 or []),
        "三选": list(c2 or []),
    }

    is_oor = (best_policy <= OUT_OF_REACH_MAX_POLICY
              and a_s_loss >= OUT_OF_REACH_MIN_LOSS)

    return DrillMove(
        move_number=int(eval_.move_number),
        color=color, phase_label=phase_label or "全盘",
        played_move=actual.move, played_quality=played_q,
        loss=a_s_loss, best_move=best_move,
        best_coord=_coord_or_pass(best_move),
        candidates=candidates, quiz_order=shuffled,
        variations=variations, is_out_of_reach=is_oor,
    )


def compute_final_priorities(evaluations, parent_move_infos, priority_context):
    """终算优先级（P1-2：一次计算，到处读取的唯一入口）。

    与 build_problem_drill 内部使用完全相同的输入装配；app 侧调用后
    经 learning_store.finalize_priority 持久化，训练/时间轴/画像全部
    消费 LearningEvent.learning_priority 同一份 final 值。
    """
    context = dict(priority_context or {})
    recurrence_by_move = context.get("recurrence_by_move") or {}
    mastery_by_move = context.get("mastery_by_move") or {}
    human_priors_by_move = context.get("human_priors_by_move") or {}
    game_type = context.get("game_type")
    finals = {}
    for e in evaluations or []:
        mis = (parent_move_infos or {}).get(e.move_number) or []
        best_prior = None
        ordered = sorted(mis, key=lambda m: m.get("order", 999))
        if ordered:
            try:
                best_prior = float(ordered[0].get("prior"))
            except (TypeError, ValueError):
                best_prior = None
        priors = human_priors_by_move.get(e.move_number) or {}
        finals[e.move_number] = learning_priority.compute_learning_priority(
            score_loss=float(e.loss or 0.0),
            recurrence_count=recurrence_by_move.get(e.move_number, 0),
            prior_current=priors.get("current"),
            prior_stronger=priors.get("stronger"),
            move_infos=mis, color=e.color,
            best_prior=best_prior, game_type=game_type,
            mastery_state=mastery_by_move.get(e.move_number))
    return finals


# ===================== 主入口 =====================
def build_problem_drill(evaluations, parent_move_infos, *,
                        user_color: str = "both",
                        max_moves: Optional[int] = None,
                        candidate_count: int = DEFAULT_CANDIDATES,
                        loss_threshold: Optional[float] = None,
                        board_size: int = 19,
                        quality_by_move: Optional[dict] = None,
                        phase_label_of=None,
                        ranking: str = "loss",
                        priority_context: Optional[dict] = None,
                        final_priorities: Optional[dict] = None) -> ProblemDrill:
    """构建问题手训练钻取。

    参数：
      evaluations: review.MoveEvaluation 列表（ReviewReport.evaluate() 结果）。
      parent_move_infos: {move_number: [moveInfo, ...]}，每个问题手父局面的候选。
      user_color: "B" / "W" / "both"，只训练该方的问题手。
      max_moves: 最多详细讲解几手（learning 模式默认 5，loss 模式默认 10）。
      candidate_count: 每题展示几个 AI 候选（默认 3 = 一/二/三选）。
      loss_threshold: 计入问题手的最小目损（默认 LOSS_DEFAULT_THRESHOLD=2.0）。
      quality_by_move: {move_number: quality_label}，覆盖实战评级文案（可来自 move_quality）。
      phase_label_of: callable(move_number) -> 阶段文案；缺省 "全盘"。
      ranking: "loss" = 目损降序（旧研究模式）；"learning" = 学习优先级排序
        （severity + recurrence + learnability + 掌握度调节 + 同簇多样性封顶，
        项目大纲 §9-19）。
      priority_context: learning 模式的上下文：
        {"recurrence_by_move": {move_no: count}, "game_type": str,
         "mastery_by_move": {move_no: state},
         "human_priors_by_move": {move_no: {"current": p, "stronger": p}}}
        human_priors 来自 KataGo Human SL（本人档/更高档 humanPolicy，缓存在
        LearningEvent 里），驱动 level_gap 分量：本人档常下、高档明显少下
        的问题优先（大纲 §14）。

    返回 ProblemDrill。边界：无可用问题手时返回空 moves 的 ProblemDrill（不抛异常）。
    """
    user_color = (user_color or "both").strip().lower()
    if user_color not in ("b", "w", "both"):
        user_color = "both"
    color_label = {"b": "黑方", "w": "白方", "both": "双方"}.get(user_color, "双方")
    threshold = LOSS_DEFAULT_THRESHOLD if loss_threshold is None else float(loss_threshold)
    quality_by_move = quality_by_move or {}
    warnings: list[str] = []
    ranking = (ranking or "loss").lower()

    evs = [e for e in (evaluations or [])
           if e.analyzed and e.loss is not None and e.loss >= threshold
           and (user_color == "both" or e.color.lower() == user_color)]
    if not evs:
        return ProblemDrill(user_color=user_color, user_color_label=color_label,
                            warnings=["没有找到达到目损阈值（%.1f 目）的%s问题手。" % (
                                threshold, color_label)])

    if max_moves is None:
        max_moves = DEFAULT_LEARNING_MOVES if ranking == "learning" else DEFAULT_MAX_MOVES

    priorities = {}
    if ranking == "learning":
        context = dict(priority_context or {})
        recurrence_by_move = context.get("recurrence_by_move") or {}
        mastery_by_move = context.get("mastery_by_move") or {}
        human_priors_by_move = context.get("human_priors_by_move") or {}
        game_type = context.get("game_type")
        for e in evs:
            mis = (parent_move_infos or {}).get(e.move_number) or []
            best_prior = None
            if mis:
                ordered = sorted(mis, key=lambda m: m.get("order", 999))
                try:
                    best_prior = float(ordered[0].get("prior"))
                except (TypeError, ValueError):
                    best_prior = None
            final = (final_priorities or {}).get(e.move_number)
            if final is not None:
                # P1-2：直接消费已持久化的 final 值（与时间轴/画像同源）
                priorities[e.move_number] = final
            else:
                priors = human_priors_by_move.get(e.move_number) or {}
                priorities[e.move_number] = learning_priority.compute_learning_priority(
                    score_loss=float(e.loss or 0.0),
                    recurrence_count=recurrence_by_move.get(e.move_number, 0),
                    prior_current=priors.get("current"),
                    prior_stronger=priors.get("stronger"),
                    move_infos=mis, color=e.color,
                    best_prior=best_prior, game_type=game_type,
                    mastery_state=mastery_by_move.get(e.move_number))
        # 同簇多样性封顶（手数邻接 ±8 自动聚簇，每簇最多 2 题）
        ranked = learning_priority.select_learning_problems([
            {"move_no": e.move_number, "eval": e,
             "priority": priorities[e.move_number]["final_score"]}
            for e in evs], limit=max(1, int(max_moves)), per_cluster_cap=2)
        evs_sorted = [item["eval"] for item in ranked]
        # 未入选者按目损降序排入 other_problems
        rest = [e for e in evs if e.move_number not in
                {item["move_no"] for item in ranked}]
        rest.sort(key=lambda e: (float(e.loss or 0.0), e.move_number), reverse=True)
        evs_sorted_rest = rest
    else:
        evs_sorted = sorted(
            evs, key=lambda e: (float(e.loss or 0.0), e.move_number), reverse=True)
        evs_sorted_rest = []

    detailed = evs_sorted[:max(1, int(max_moves))]
    detailed_moves = set(e.move_number for e in detailed)

    moves: list[DrillMove] = []
    skipped = 0
    for e in detailed:
        mis = (parent_move_infos or {}).get(e.move_number) or []
        phase = phase_label_of(e.move_number) if phase_label_of else ""
        dm = build_drill_move(
            e, mis, board_size=board_size,
            candidate_count=int(candidate_count),
            phase_label=phase,
            played_quality=quality_by_move.get(e.move_number))
        if dm is None:
            skipped += 1
            continue
        if ranking == "learning":
            pri = priorities.get(e.move_number) or {}
            dm.learning_priority = pri.get("final_score", 0.0)
            dm.priority_components = pri.get("components", {})
            dm.priority_version = pri.get("version", 0)
        moves.append(dm)

    if not moves:
        warnings.append("问题手父局面缺少 AI 候选数据，无法生成详解（请先对整盘做分析）。")
    elif skipped:
        warnings.append("有 %d 手问题手因父局面缺少候选数据被跳过。" % skipped)

    # 其它问题手（达到阈值但未进详解；loss 模式=目损序，learning 模式=落选题按目损序）
    other_src = evs_sorted_rest + [e for e in (
        evs_sorted if ranking != "learning" else [])
        if e.move_number not in detailed_moves]
    other_problems = [
        {"move": e.move_number, "color": e.color,
         "quality": quality_by_move.get(e.move_number) or quality_label_for_loss(float(e.loss or 0.0))}
        for e in other_src
    ]
    # 超纲问题手（来自所有问题手，不仅是详解题）
    out_of_reach = [
        {"move": e.move_number, "color": e.color,
         "quality": quality_by_move.get(e.move_number) or quality_label_for_loss(float(e.loss or 0.0))}
        for e in evs_sorted if any(m.is_out_of_reach and m.move_number == e.move_number for m in moves)
    ]

    return ProblemDrill(
        user_color=user_color, user_color_label=color_label,
        moves=moves, other_problems=other_problems,
        out_of_reach=out_of_reach, warnings=warnings,
    )


# ===================== 作答评分 =====================
def grade_quiz(drill_move: DrillMove, letter: str, context=None) -> dict:
    """判定一次 quiz 作答（统一按实际目损，大纲 §20-23 / 审查 P0-1）。

    字母只是输入方式之一，判定标准与自由落子作答完全一致：选点相对
    一选的目损 ≤ 动态容差即合理，不再要求命中 AI 第一选。
    context 为 build_assessment_context() 的产物——三条输入路径共用
    同一上下文，同一手棋无论怎么输入判定都相同。
    """
    from candidate_assessment import assessment_for_loss
    ctx = context or {}
    key = drill_move.key_of(letter)
    chosen = drill_move.candidate(key) if key else None
    best = drill_move.best_candidate
    level, _ok = (assessment_for_loss(
        chosen.score_loss,
        performance_label=ctx.get("performance_label"),
        complexity=ctx.get("complexity") or 0.0)
        if chosen is not None else (None, False))
    from candidate_assessment import ASSESSMENT_LABELS
    return {
        "letter": (letter or "").strip().upper(),
        "chosenKey": key,
        "chosenMove": chosen.move if chosen else None,
        "chosenQuality": chosen.quality_label if chosen else None,
        "chosenLoss": float(chosen.score_loss) if chosen is not None else None,
        "assessment": level,
        "assessmentLabel": ASSESSMENT_LABELS.get(level, ASSESSMENT_LABELS["unknown"])
        if level else "—",
        "bestMove": best.move if best else drill_move.best_move,
        "isCorrect": level in ("best", "excellent", "acceptable"),
        "isActual": bool(chosen and chosen.is_actual),
    }


def drill_difficulty_label(drill_move):
    """选点直观度参考（审查 #5 后停用普通 prior 的伪人类难度断言）。

    "什么水平的人想不想得到"只允许 Human SL（humanPrior）回答，
    模型缺失即不判断（fail closed）。保留函数供旧调用兼容，恒返回
    空串——训练窗口表头相应不再展示该段。
    """
    del drill_move
    return ""


@dataclass
class DrillResult:
    """一次完整训练钻取的作答汇总。"""
    total: int = 0
    answered: int = 0
    correct: int = 0
    picked_actual: int = 0          # 把实战当成最佳选了
    answers: dict = field(default_factory=dict)   # {move_number: grade_quiz dict}

    @property
    def score_pct(self) -> int:
        if self.answered == 0:
            return 0
        return int(round(self.correct * 100.0 / self.answered))

    @property
    def label(self) -> str:
        s = self.score_pct
        if self.answered == 0:
            return "未作答"
        if s >= 90:
            return "优秀"
        if s >= 75:
            return "良好"
        if s >= 60:
            return "合格"
        if s >= 40:
            return "仍需复习"
        return "建议重练"

    def record(self, drill_move: DrillMove, letter: str, context=None) -> dict:
        g = grade_quiz(drill_move, letter, context=context)
        if drill_move.move_number not in self.answers:
            self.answered += 1
            if g["isCorrect"]:
                self.correct += 1
            if g["isActual"]:
                self.picked_actual += 1
        self.answers[drill_move.move_number] = g
        return g

    def to_dict(self) -> dict:
        return {
            "total": self.total, "answered": self.answered, "correct": self.correct,
            "pickedActual": self.picked_actual,
            "scorePct": self.score_pct, "label": self.label,
            "answers": dict(self.answers),
        }


def new_drill_result(drill: ProblemDrill) -> DrillResult:
    res = DrillResult(total=len(drill.moves))
    return res
