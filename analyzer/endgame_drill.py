"""endgame_drill —— 官子收束题生成（纯逻辑，不依赖 tkinter / KataGo 进程）。

从已完成分析的本地棋谱的终局段自动生成官子收束训练题（GAP-3，产品对标 v2）。
星阵 / 涨棋网都有官子专项训练，但题库有版权与联网问题；本项目差异化做法：
题目全部从本地棋谱库的已分析棋局终局段自动派生（无题库版权问题、数据不出本机）。

题源（按"收束价值"排序，价值降序取前 N 题）：
  * loss（目损收束）—— 终局段目损 ≥ 阈值的实战手：练习"此刻该收哪一手官子"。
  * sente（先后手转换）—— AI 一选为先手（PV 里对手应手与落点 Chebyshev 距离
    ≤ SENTE_ADJACENCY 的启发式判定），且错过先手交换相对最近的后手候选
    代价 ≥ 阈值：练习"先手官子先走"。

每题自带：
  * 局面快照 BoardSnapshot（initialStones + 落子序列 + 行棋方 + 棋盘大小），
    让子局也完整可重建；
  * 最佳收束序列（父局面分析缓存主变 PV）与实战着法；
  * 候选对比表（一选 / 二选 / …：目差、目损、计算量、选点概率）；
  * 用户练习起点 = 该手之前的局面（start_move_number）。

边界约定（与 problem_drill 一致的降级风格）：空棋谱 / 未分析 / 棋局太短 /
终局段分析覆盖率不够 / 无达标收束点 → 返回空题集 + reasons 原因列表，
绝不抛异常穿透。判题复用 candidate_assessment（单一判定源，单位：目）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from board import color_letter
from movetree import COLS
from review import ReviewReport
from problem_drill import quality_label_for_loss, EVAL_LABELS   # 一选/二选…标签单一来源

VERSION = 1

# ===================== 常量 =====================
DEFAULT_ENDGAME_WINDOW = 50     # 终局段默认取最后 50 手（实现选择，无文档硬性区间依据）
MIN_ENDGAME_WINDOW = 20         # 窗口下限（再小就谈不上"终局段"）
MIN_TOTAL_MOVES = 30            # 全局短于此手数 → 棋局太短降级
MIN_ANALYZED_ENDGAME = 10       # 终局段至少要有这么多手双方分析齐全才出题
ENDGAME_LOSS_THRESHOLD = 1.5    # 目损收束题：官子阶段损 1.5 目即值得练
SENTE_GAP_THRESHOLD = 1.0       # 先后手转换题：错过先手交换的代价下限（目）
SENTE_ADJACENCY = 2             # PV 对手应手与一选落点 Chebyshev 距离 ≤2 视为局部应答
BLOWOUT_WARNING_LEAD = 40.0     # 终局段黑目差超过此值：胜负悬殊，仅告警不拦截
DEFAULT_MAX_PROBLEMS = 8        # 每局默认最多出几题
DEFAULT_CANDIDATES = 4          # 每题候选对比表行数（一选/二选/三选/四选）

DRILL_KIND_LABELS = {"loss": "目损收束", "sente": "先后手转换"}


# ===================== 小工具 =====================
def _num(mapping, key, default=0.0):
    value = (mapping or {}).get(key, default)
    return default if value is None else float(value)


def _mover_score(black_score: float, color: str) -> float:
    """黑视角目差 → 走子方视角（与 problem_drill 同一换算）。"""
    return float(black_score) if color == "B" else -float(black_score)


def _coord_or_pass(move: str) -> str:
    move = str(move or "")
    return move if move and move.lower() != "pass" else "pass"


def _point_xy(point: str, size: int) -> Optional[tuple]:
    """GTP 点 → (x, y)；pass / 非法输入返回 None。"""
    try:
        p = str(point or "").strip().upper()
        if not p or p == "PASS" or p[0] not in COLS:
            return None
        x = COLS.index(p[0])
        y = size - int(p[1:])
        if not (0 <= x < size and 0 <= y < size):
            return None
        return (x, y)
    except (ValueError, IndexError):
        return None


def _chebyshev(a, b) -> int:
    if a is None or b is None:
        return 10 ** 6      # 任一为 pass/非法 → 视为"不相邻"
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def _is_sente_pv(pv, size: int) -> bool:
    """先手启发式：主变 pv = [一选, 对手应手, …]，应手与一选邻接 → 对手必须
    局部应答 → 一选视为先手。纯启发式，只用于题目标注与选题，不用于判分。"""
    pv = [str(x) for x in (pv or [])]
    if len(pv) < 2:
        return False
    a = _point_xy(pv[0], size)
    b = _point_xy(pv[1], size)
    return a is not None and b is not None and _chebyshev(a, b) <= SENTE_ADJACENCY


# ===================== 数据结构 =====================
@dataclass
class BoardSnapshot:
    """用户练习起点的局面快照（可完整重建：让子 setup + 落子序列）。"""
    board_size: int
    initial_stones: list = field(default_factory=list)   # [["B","D4"], ...]（GTP）
    moves: list = field(default_factory=list)            # [["B","Q16"], ...]（GTP，到起点为止）
    to_move: str = "B"                                   # 起点行棋方

    def to_dict(self) -> dict:
        return {
            "boardSize": self.board_size,
            "initialStones": [list(s) for s in self.initial_stones],
            "moves": [list(m) for m in self.moves],
            "toMove": self.to_move,
        }


@dataclass
class EndgameCandidate:
    """候选对比表中的一行（父局面 moveInfos 的前 N 个非 pass 候选）。"""
    key: str                    # "c0"/"c1"/...
    eval_label: str             # 一选 / 二选 / …
    move: str                   # GTP 坐标
    coord: str
    visits: int
    policy: float               # 选点概率（0-1）
    score_lead: float           # 走子方视角目差（落完此手后的预期）
    score_loss: float           # 相对一选的目损（>=0）
    quality_label: str          # 最佳 / 好手 / 一般 / 欠佳 / 恶手
    is_best: bool = False

    def to_dict(self) -> dict:
        return {
            "key": self.key, "evalLabel": self.eval_label,
            "move": self.move, "coord": self.coord,
            "visits": self.visits, "policy": round(self.policy, 6),
            "scoreLead": round(self.score_lead, 3),
            "scoreLoss": round(self.score_loss, 3),
            "qualityLabel": self.quality_label, "isBest": self.is_best,
        }


@dataclass
class EndgameDrill:
    """一道官子收束训练题。"""
    move_number: int                        # 第几手的收束问题（1-based，主线）
    start_move_number: int                  # 用户练习起点局面 = 第 move_number-1 手后
    color: str                              # 该手行棋方 "B"/"W"（练习方）
    to_play_label: str                      # 黑方 / 白方
    drill_kind: str                         # "loss" / "sente"
    kind_label: str                         # 目损收束 / 先后手转换
    value: float                            # 收束价值（排序键，目；loss=目损，sente=先手代价）
    played_move: str                        # 实战着法（GTP 或 "pass"）
    played_quality: str                     # 实战评级文案
    loss: float                             # 实战目损（走子方视角，>=0）
    best_move: str                          # AI 一选（GTP 或 "pass"）
    best_coord: str
    best_pv: list = field(default_factory=list)      # 最佳收束序列（父局面分析主变）
    played_pv: list = field(default_factory=list)    # 实战延续（候选命中则用其 PV）
    is_sente: bool = False                  # 一选是否先手（启发式）
    sente_gap: float = 0.0                  # 错过先手交换相对最近后手候选的代价（目）
    score_lead_at_start: float = 0.0        # 起点黑视角目差（终局背景）
    candidates: list = field(default_factory=list)   # EndgameCandidate[]
    snapshot: Optional[BoardSnapshot] = None

    def candidate_of(self, move: str) -> Optional[EndgameCandidate]:
        """按 GTP 着法查候选（判题入口）。"""
        target = _coord_or_pass(move)
        for c in self.candidates:
            if c.move.upper() == target.upper():
                return c
        return None

    def to_dict(self) -> dict:
        return {
            "moveNumber": self.move_number,
            "startMoveNumber": self.start_move_number,
            "color": self.color, "toPlayLabel": self.to_play_label,
            "drillKind": self.drill_kind, "kindLabel": self.kind_label,
            "value": round(self.value, 3),
            "playedMove": self.played_move, "playedQuality": self.played_quality,
            "loss": round(self.loss, 3),
            "bestMove": self.best_move, "bestCoord": self.best_coord,
            "bestPv": list(self.best_pv or []),
            "playedPv": list(self.played_pv or []),
            "isSente": self.is_sente, "senteGap": round(self.sente_gap, 3),
            "scoreLeadAtStart": round(self.score_lead_at_start, 3),
            "candidates": [c.to_dict() for c in self.candidates],
            "snapshot": self.snapshot.to_dict() if self.snapshot else None,
        }


@dataclass
class EndgameDrillSet:
    """一局的官子收束题集。problems 为空时 reasons 必非空（降级原因）。"""
    problems: list = field(default_factory=list)       # EndgameDrill[]（按收束价值降序）
    reasons: list = field(default_factory=list)        # 致空原因（空题集时给出）
    warnings: list = field(default_factory=list)       # 非致命提示
    endgame_start: Optional[int] = None                # 终局段起始手数
    endgame_end: Optional[int] = None                  # 终局段结束手数
    analyzed_moves: int = 0                            # 终局段分析齐全的手数
    score_swing: Optional[float] = None                # 终局段黑目差波动幅度（max-min）
    version: int = VERSION

    @property
    def is_empty(self) -> bool:
        return not self.problems

    def to_dict(self) -> dict:
        return {
            "problems": [p.to_dict() for p in self.problems],
            "reasons": list(self.reasons), "warnings": list(self.warnings),
            "endgameStart": self.endgame_start, "endgameEnd": self.endgame_end,
            "analyzedMoves": self.analyzed_moves,
            "scoreSwing": (None if self.score_swing is None
                           else round(self.score_swing, 3)),
            "version": self.version,
        }


# ===================== 单题构建 =====================
def _build_drill(e, parent_node, *, size: int, candidate_count: int,
                 loss_threshold: float, sente_gap_threshold: float,
                 initial_stones: list) -> Optional[EndgameDrill]:
    """把终局段一手棋的评价 + 父局面分析组装成 EndgameDrill。

    initial_stones 为整树的让子 setup（GTP 列表，让子局快照重建用）。
    返回 None 表示该手不足以出题（无候选 / 一选为 pass / 一选缺 scoreLead /
    两类价值都不达标）。候选缺 move 或 scoreLead 的脏行只跳过该行；
    解析失败按"不足以出题"处理，不向上抛异常。
    """
    try:
        mis = ReviewReport._sorted_move_infos(parent_node)
        if not mis:
            return None
        best_info = mis[0]
        best_move = _coord_or_pass(best_info.get("move"))
        if best_move == "pass":
            return None                     # 一选 pass：此处已无收束价值
        if best_info.get("scoreLead") is None:
            return None                     # 一选缺 scoreLead：目损口径无证据，整手不出题
        color = e.color
        best_pv = [str(x) for x in (best_info.get("pv") or [])]
        best_sc = _mover_score(_num(best_info, "scoreLead"), color)
        loss = max(0.0, float(e.loss or 0.0))
        is_sente = _is_sente_pv(best_pv, size)

        # 先后手转换代价：一选为先手时，最近一个"别处后手候选"相对一选的目差
        sente_gap = 0.0
        if is_sente:
            best_xy = _point_xy(best_move, size)
            for m in mis[1:]:
                mv = _coord_or_pass(m.get("move"))
                if mv == "pass":
                    continue
                if m.get("scoreLead") is None:
                    continue                # 缺目差的候选不参与代价比较（不编造 0）
                if _chebyshev(_point_xy(mv, size), best_xy) <= SENTE_ADJACENCY:
                    continue                # 局部续连（同一先手定式的延续）不算代价
                sente_gap = max(0.0, best_sc - _mover_score(_num(m, "scoreLead"), color))
                break

        if loss >= loss_threshold:
            kind, value = "loss", loss
        elif is_sente and sente_gap >= sente_gap_threshold:
            kind, value = "sente", sente_gap
        else:
            return None

        # 候选对比表（前 N 个非 pass 且带 scoreLead 的候选）
        candidates: list[EndgameCandidate] = []
        for m in mis:
            mv = _coord_or_pass(m.get("move"))
            if mv == "pass" or m.get("scoreLead") is None:
                continue                    # 缺 scoreLead → 目损无法计算，不入表（不编造 0）
            sc = _mover_score(_num(m, "scoreLead", 0.0), color)
            s_loss = max(0.0, best_sc - sc)
            candidates.append(EndgameCandidate(
                key="c%d" % len(candidates),
                eval_label=EVAL_LABELS.get(len(candidates),
                                           "第%d选" % (len(candidates) + 1)),
                move=mv, coord=mv,
                visits=int(_num(m, "visits", 0)),
                policy=float(_num(m, "prior", 0.0)),
                score_lead=sc, score_loss=s_loss,
                quality_label=quality_label_for_loss(s_loss,
                                                     is_best=(len(candidates) == 0)),
                is_best=(len(candidates) == 0),
            ))
            if len(candidates) >= candidate_count:
                break
        if not candidates:
            return None

        # 实战延续：候选命中 → 其 PV；否则只有着法本身
        played_move = "pass" if e.is_pass else (e.coord or "?")
        played_info = next((m for m in mis
                            if _coord_or_pass(m.get("move")) == played_move), None)
        played_pv = [str(x) for x in ((played_info or {}).get("pv") or [])]
        if not played_pv:
            played_pv = [played_move]

        snapshot = BoardSnapshot(
            board_size=size,
            initial_stones=[list(s) for s in (initial_stones or [])],
            moves=list(parent_node.moves_list()),
            to_move=color_letter(parent_node.board.to_move),
        )
        return EndgameDrill(
            move_number=int(e.move_number),
            start_move_number=int(e.move_number) - 1,
            color=color,
            to_play_label="黑方" if color == "B" else "白方",
            drill_kind=kind, kind_label=DRILL_KIND_LABELS[kind],
            value=value, played_move=played_move,
            played_quality=quality_label_for_loss(loss),
            loss=loss, best_move=best_move, best_coord=best_move,
            best_pv=best_pv, played_pv=played_pv,
            is_sente=is_sente, sente_gap=sente_gap,
            score_lead_at_start=float(e.score_lead_before),
            candidates=candidates, snapshot=snapshot,
        )
    except Exception:                       # 单手数据损坏 → 跳过该手，不穿透
        return None


# ===================== 主入口 =====================
def build_endgame_drills(tree, *, window: int = DEFAULT_ENDGAME_WINDOW,
                         max_problems: int = DEFAULT_MAX_PROBLEMS,
                         loss_threshold: float = ENDGAME_LOSS_THRESHOLD,
                         sente_gap_threshold: float = SENTE_GAP_THRESHOLD,
                         candidate_count: int = DEFAULT_CANDIDATES,
                         user_color: str = "both") -> EndgameDrillSet:
    """从已完成分析的棋局终局段生成官子收束题集。

    参数：
      tree: movetree.MoveTree，主线节点需挂有 KataGo 分析缓存（node.analysis）。
      window: 终局段窗口（默认最后 50 手，下限 MIN_ENDGAME_WINDOW）。
      max_problems: 最多出几题（按收束价值降序取前 N）。
      loss_threshold / sente_gap_threshold: 两类题的价值阈值（单位：目）。
      candidate_count: 每题候选对比表行数。
      user_color: "B"/"W"/"both"——只练该方的收束问题。

    返回 EndgameDrillSet；所有边界（空棋谱/未分析/太短/覆盖率不足/无达标点）
    都返回空 problems + reasons，不抛异常。
    """
    reasons: list[str] = []
    warnings: list[str] = []
    if tree is None or getattr(tree, "root", None) is None:
        return EndgameDrillSet(reasons=["没有可用的棋谱。"])

    try:
        nodes = ReviewReport(tree).mainline_nodes()
    except Exception:
        return EndgameDrillSet(reasons=["棋谱主线读取失败，无法生成官子题。"])
    total = len(nodes) - 1                   # 根=0
    if total <= 0:
        return EndgameDrillSet(reasons=["棋谱为空（没有落子）。"])
    if total < MIN_TOTAL_MOVES:
        return EndgameDrillSet(
            reasons=["棋局太短（%d 手，不足 %d 手），终局段无法生成官子题。"
                     % (total, MIN_TOTAL_MOVES)])

    if not any(getattr(n, "analysis", None) for n in nodes):
        return EndgameDrillSet(
            reasons=["棋谱尚未分析（无 KataGo 分析缓存），请先完成整盘分析。"])

    size = int(getattr(tree, "size", 19) or 19)
    window = max(MIN_ENDGAME_WINDOW, int(window))
    endgame_start = max(1, total - window + 1)
    endgame_end = total

    try:
        evaluations = {e.move_number: e for e in ReviewReport(tree).evaluate()}
    except Exception:
        return EndgameDrillSet(reasons=["棋谱评价计算失败，无法生成官子题。"])

    seg = [m for m in range(endgame_start, endgame_end + 1)
           if evaluations.get(m) is not None and evaluations[m].analyzed]
    if len(seg) < MIN_ANALYZED_ENDGAME:
        return EndgameDrillSet(
            endgame_start=endgame_start, endgame_end=endgame_end,
            analyzed_moves=len(seg),
            reasons=["终局段（第 %d-%d 手）分析齐全的着手仅 %d 手（不足 %d 手），"
                     "无法可靠出题，请先补全终局段分析。"
                     % (endgame_start, endgame_end, len(seg), MIN_ANALYZED_ENDGAME)])

    user_color = (user_color or "both").strip().lower()
    if user_color not in ("b", "w", "both"):
        user_color = "both"

    problems: list[EndgameDrill] = []
    skipped = 0
    loss_hits = 0                # 目损已达标但被放弃的手数（空集原因需区分，不得误称未达标）
    try:
        initial_stones = list(tree.initial_stones_list())
    except Exception:
        initial_stones = []
    for m in seg:
        e = evaluations[m]
        if user_color != "both" and e.color.lower() != user_color:
            continue
        if e.loss is not None and float(e.loss) >= float(loss_threshold):
            loss_hits += 1
        drill = _build_drill(e, nodes[m - 1], size=size,
                             candidate_count=int(candidate_count),
                             loss_threshold=float(loss_threshold),
                             sente_gap_threshold=float(sente_gap_threshold),
                             initial_stones=initial_stones)
        if drill is None:
            skipped += 1
            continue
        problems.append(drill)

    # 终局段黑目差波动（背景信息；胜负悬殊只告警不拦截）
    leads = [_num(ReviewReport._root_info(nodes[m]), "scoreLead")
             for m in seg if ReviewReport._root_info(nodes[m])]
    swing = (max(leads) - min(leads)) if leads else None
    if leads and max(abs(v) for v in leads) > BLOWOUT_WARNING_LEAD:
        warnings.append("终局段目差最大已达 %.1f 目，胜负悬殊，收束价值仅供参考。"
                        % max(abs(v) for v in leads))

    unanalyzed = (endgame_end - endgame_start + 1) - len(seg)
    if unanalyzed > 0:
        warnings.append("终局段有 %d 手缺少分析数据被跳过。" % unanalyzed)
    if skipped:
        warnings.append("终局段有 %d 手无达标收束点或候选数据不足，未出题。" % skipped)

    if not problems:
        color_label = {"b": "黑方", "w": "白方", "both": "双方"}.get(user_color, "双方")
        if loss_hits:
            reasons.append(
                "终局段（第 %d-%d 手）有 %d 手%s目损已达 %.1f 目，"
                "但一选为 pass 或候选数据不足，无法成题；"
                "其余收束点未达阈值（目损 < %.1f 目且无 ≥%.1f 目的先后手转换点）。"
                % (endgame_start, endgame_end, loss_hits, color_label,
                   float(loss_threshold), float(loss_threshold),
                   float(sente_gap_threshold)))
        else:
            reasons.append(
                "终局段（第 %d-%d 手）没有达到收束价值阈值的%s官子问题"
                "（目损 < %.1f 目且无 ≥%.1f 目的先后手转换点）。"
                % (endgame_start, endgame_end, color_label,
                   float(loss_threshold), float(sente_gap_threshold)))
        return EndgameDrillSet(
            endgame_start=endgame_start, endgame_end=endgame_end,
            analyzed_moves=len(seg), score_swing=swing,
            reasons=reasons, warnings=warnings)

    problems.sort(key=lambda d: (-d.value, d.move_number))
    return EndgameDrillSet(
        problems=problems[:max(1, int(max_problems))],
        warnings=warnings, endgame_start=endgame_start, endgame_end=endgame_end,
        analyzed_moves=len(seg), score_swing=swing)


# ===================== 判题（复用单一判定源） =====================
def grade_choice(drill: EndgameDrill, move: str, context=None) -> dict:
    """判定一次自由落子作答（单位：目；与 problem_drill.grade_quiz 同一判定源）。

    只认候选表内的着法；不在候选表内的着法（未在分析候选中返回）判为
    无法评定（assessment None，不计对），与 quiz 非法字母的处理一致。
    """
    from candidate_assessment import assessment_for_loss, ASSESSMENT_LABELS
    ctx = context or {}
    chosen = drill.candidate_of(move)
    level, _ok = (assessment_for_loss(
        chosen.score_loss,
        performance_label=ctx.get("performance_label"),
        complexity=ctx.get("complexity") or 0.0)
        if chosen is not None else (None, False))
    return {
        "move": _coord_or_pass(move),
        "chosenKey": chosen.key if chosen else None,
        "chosenMove": chosen.move if chosen else None,
        "chosenQuality": chosen.quality_label if chosen else None,
        "chosenLoss": float(chosen.score_loss) if chosen is not None else None,
        "assessment": level,
        "assessmentLabel": ASSESSMENT_LABELS.get(level, ASSESSMENT_LABELS["unknown"])
        if level else "—",
        "bestMove": drill.best_move,
        "isCorrect": level in ("best", "excellent", "acceptable"),
        # 与 drill 家族 grade_quiz 的键名对齐（原 "isPlayed"，接力板#11 统一）
        "isActual": bool(chosen and chosen.move == drill.played_move),
    }
