"""review —— 复盘评价纯逻辑（不依赖 tkinter / KataGo 进程）。

吃一棵已带 analysis 缓存的 MoveTree，产出每一手相对最佳的「目损」(loss) 与
胜率/目差序列，供 app.py 绘制失误标注与胜率曲线。

视角约定（analysis.cfg 的 reportAnalysisWinratesAs = BLACK）：
  rootInfo.scoreLead / winrate、moveInfos[].scoreLead / winrate 均为【黑方视角】。
loss 是【走子方】视角的损失，按走子颜色翻一次符号：
  黑走：loss = S_best - S_actual
  白走：loss = S_actual - S_best
  loss = max(0, loss)        （≥0；大=这手相对最佳明显变差）

只读：不修改传入的 tree / analysis dict。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from board import EMPTY
from movetree import point_to_xy, xy_to_point

LOSS_DEFAULT_THRESHOLD = 2.0   # 目损阈值（≥此值视为明显失误，画红圈/进 Top）
TOP_N_DEFAULT = 8
PHASES = ("opening", "middle", "endgame")
PHASE_LABELS = {"opening": "布局", "middle": "中盘", "endgame": "官子"}
PHASE_ALIASES = {
    "opening": "opening", "layout": "opening", "布局": "opening", "序盘": "opening",
    "middle": "middle", "midgame": "middle", "中盘": "middle",
    "endgame": "endgame", "end": "endgame", "关子": "endgame", "官子": "endgame",
}

# AI 评价分级阈值（目损，单位「目」）
GRADE_GOOD  = 1.0    # <  此值 = 好
GRADE_DOUBT = 3.0    # <  此值 = 普通，≥ = 疑问
GRADE_BAD   = 6.0    # ≥ 此值 = 恶手


def grade_of(loss):
    """目损 → 等级标签：'好' / '普通' / '疑问' / '恶'；None/未分析 → '—'。"""
    if loss is None:
        return "—"
    if loss < GRADE_GOOD:
        return "好"
    if loss < GRADE_DOUBT:
        return "普通"
    if loss < GRADE_BAD:
        return "疑问"
    return "恶"


# 参考棋力档：平均目损 → 业余段/级位（经验参考，无客观标准）。
# v2 校准（2026-07-04）：与主流平台（涨棋网/弈城）对齐，整体放宽容 ~1–2 档，
# 旧的 7段+ 阈值 <0.5 目接近完美不现实；现按「吻合度为主、目损为辅」重新标定。
RANK_BANDS = [
    (0.20, "强 AI 级", "AI"),
    (0.30, "AI 级", "AI"),
    (0.40, "职业级", "职业"),
    (0.6, "业余强段（6段+）", "强段"),
    (1.0, "业余高段（4-5段）", "高段"),
    (1.7, "业余初段（1-3段）", "初段"),
    (2.6, "高级位（1-3级）", "高级位"),
    (4.0, "中级位（4-6级）", "中级位"),
    (6.5, "入门（7-12级）", "入门"),
]

# 单局棋力不能直接用全盘平均目损：胜负已定后 KataGo 会优先守胜率，
# 此时不同候选的 scoreLead 容易大幅摆动。以下映射使用仍有胜负悬念的
# 局面，并把单手计入的目损限制为 3 目，减少一次战术崩盘支配整局结论。
PERFORMANCE_LOSS_CAP = 3.0
PERFORMANCE_WINRATE_FLOOR = 0.02
PERFORMANCE_MIN_MOVES = 8

# 目损→段位（已放宽，仅作辅助/兜底；主信号见 AGREEMENT_RANK_BANDS）。
# index 与 AGREEMENT_RANK_BANDS 对齐：0=7段+，递增为弱。
PERFORMANCE_RANK_BANDS = [
    (0.20, "强AI级表现", "AI"),
    (0.30, "AI级表现", "AI"),
    (0.40, "职业级表现", "职业"),
    (0.60, "业余7段+表现", "7段+"),
    (0.90, "业余6段表现", "6段"),
    (1.30, "业余5段表现", "5段"),
    (1.75, "业余4段表现", "4段"),
    (2.25, "业余3段表现", "3段"),
    (2.80, "业余2段表现", "2段"),
    (3.50, "业余1段表现", "1段"),
    (4.50, "业余1-3级表现", "1-3级"),
    (6.00, "业余4-6级表现", "4-6级"),
    (8.00, "业余7-10级表现", "7-10级"),
    (10.0, "业余11-15级表现", "11-15级"),
]

# 主信号：AI 选点吻合度（top-1，胜负悬念局面）→ 段位，按主流平台标定。
# 吻合度 ≥ 阈值 → 至少该档；index 与 PERFORMANCE_RANK_BANDS 对齐。
AGREEMENT_RANK_BANDS = [
    (0.95, "强AI级表现", "AI"),
    (0.80, "AI级表现", "AI"),
    (0.62, "职业级表现", "职业"),
    (0.50, "业余7段+表现", "7段+"),
    (0.45, "业余6段表现", "6段"),
    (0.40, "业余5段表现", "5段"),
    (0.35, "业余4段表现", "4段"),
    (0.30, "业余3段表现", "3段"),
    (0.26, "业余2段表现", "2段"),
    (0.22, "业余1段表现", "1段"),
    (0.18, "业余1-3级表现", "1-3级"),
    (0.14, "业余4-6级表现", "4-6级"),
    (0.10, "业余7-10级表现", "7-10级"),
    (0.07, "业余11-15级表现", "11-15级"),
]
RATING_BANDS_COUNT = len(AGREEMENT_RANK_BANDS)   # = len(PERFORMANCE_RANK_BANDS)

# 等价 Elo 区间（参考估算，非真实 Elo）：与 AGREEMENT/PERFORMANCE_RANK_BANDS 的 idx 对齐。
# 围棋通行刻度：业余 1500-2000 / 职业 ~2700 / 顶尖人类 ~3600 / 最强 AI(b28c512) 4000+。
# 真实 Elo 需对弈胜负数据；此处用「吻合度+目损→实力参考」让 AI 评到 3500+，不被封顶人类段位。
ELO_BANDS = [
    (3800, 4200, "强 AI 级"),          # idx 0
    (3400, 3800, "AI 级"),             # idx 1
    (2900, 3400, "职业级"),            # idx 2
    (2500, 2900, "业余顶尖 / 职业边缘"),  # idx 3 (7段+)
    (2200, 2500, "业余强段"),          # idx 4 (6段)
    (2000, 2200, "业余高段"),          # idx 5 (5段)
    (1800, 2000, "业余中段"),          # idx 6 (4段)
    (1600, 1800, "业余初段"),          # idx 7 (3段)
    (1400, 1600, "业余初段"),          # idx 8 (2段)
    (1200, 1400, "业余初段"),          # idx 9 (1段)
    (1000, 1200, "高级位"),            # idx 10 (1-3级)
    (800, 1000, "中级位"),             # idx 11 (4-6级)
    (600, 800, "初级位"),              # idx 12 (7-10级)
    (400, 600, "入门"),               # idx 13 (11-15级)
]


def rank_of(avg_loss):
    """平均目损 → 参考棋力档（全称）；None → '—'。"""
    if avg_loss is None:
        return "—"
    for hi, label, _short in RANK_BANDS:
        if avg_loss < hi:
            return label
    return "新手（13级以下）"


def rank_short(avg_loss):
    """平均目损 → 档位简称（用于分阶段紧凑显示）；None → '—'。"""
    if avg_loss is None:
        return "—"
    for hi, _label, short in RANK_BANDS:
        if avg_loss < hi:
            return short
    return "新手"


def performance_rank_of(performance_loss):
    """稳健目损 → 单局表现档；这不是账号段位或长期棋力认证。"""
    if performance_loss is None:
        return "—"
    for hi, label, _short in PERFORMANCE_RANK_BANDS:
        if performance_loss < hi:
            return label
    return "业余16级以下表现"


def performance_rank_short(performance_loss):
    if performance_loss is None:
        return "—"
    for hi, _label, short in PERFORMANCE_RANK_BANDS:
        if performance_loss < hi:
            return short
    return "16级以下"


def _performance_rank_index(performance_loss):
    if performance_loss is None:
        return None
    for i, (hi, _label, _short) in enumerate(PERFORMANCE_RANK_BANDS):
        if performance_loss < hi:
            return i
    return len(PERFORMANCE_RANK_BANDS)


def _performance_short_at(index):
    if index < len(PERFORMANCE_RANK_BANDS):
        return PERFORMANCE_RANK_BANDS[index][2]
    return "16级以下"


def agreement_rank_index(agree1):
    """top-1 吻合度(0–1) → 档位 index（0=7段+，递增为弱）；None → 最弱档外。"""
    if agree1 is None:
        return RATING_BANDS_COUNT
    for i, (thr, _label, _short) in enumerate(AGREEMENT_RANK_BANDS):
        if agree1 >= thr:
            return i
    return RATING_BANDS_COUNT


def _rating_short_at(index):
    """段位 index（0=7段+，与 AGREEMENT/PERFORMANCE_RANK_BANDS 对齐）→ 简称。"""
    if 0 <= index < RATING_BANDS_COUNT:
        return AGREEMENT_RANK_BANDS[index][2]
    return "16级以下"


def performance_rating(agree1, robust_loss, rated_moves):
    """单局棋力档：吻合度为主、稳健目损为辅。

    返回 (index, label, short)，index=0 最强；样本不足或无信号 → None。
    主流平台以 AI 选点吻合度评定段位，故以吻合度定档；仅当目损与吻合度
    相差 ≥3 档（一次严重战术崩盘）时才按目损微调 ±1 档，避免偶发大失误
    单独压垮整局吻合度结论。
    """
    if rated_moves is not None and rated_moves < PERFORMANCE_MIN_MOVES:
        return None
    if agree1 is None and robust_loss is None:
        return None
    a_idx = agreement_rank_index(agree1)
    l_idx = (_performance_rank_index(robust_loss)
             if robust_loss is not None else RATING_BANDS_COUNT)
    final = a_idx
    if l_idx >= a_idx + 3:          # 目损远差于吻合度 → 下调 1 档
        final = a_idx + 1
    elif l_idx <= a_idx - 3:        # 目损远好 → 上调 1 档
        final = a_idx - 1
    # 无吻合度（如分析残缺）→ 退回目损定档
    if agree1 is None:
        final = l_idx
    final = max(0, min(final, RATING_BANDS_COUNT - 1))
    bands = AGREEMENT_RANK_BANDS
    if 0 <= final < len(bands):
        return final, bands[final][1], bands[final][2]
    return final, "级位以下表现", "级位以下"


def elo_estimate(agree1, robust_loss, rated_moves):
    """等价 Elo 区间（参考估算）：基于吻合度(主) + 稳健目损(辅)。

    返回 (elo_lo, elo_hi, label) 或 None（样本不足/无信号）。非真实 Elo——
    真实 Elo 需对弈胜负数据；此处让 AI（自吻合度高）评到 3500+ 区间，
    不被封顶在人类段位体系内。idx 来自 performance_rating（含目损微调）。
    """
    rating = performance_rating(agree1, robust_loss, rated_moves)
    if rating is None:
        return None
    idx = rating[0]
    if 0 <= idx < len(ELO_BANDS):
        lo, hi, label = ELO_BANDS[idx]
        return lo, hi, label
    return None


def ai_likeness_hint(agree1, robust_loss):
    """高吻合度 + 极低目损 → 该方表现接近分析引擎（可能为 AI）。

    返回提示文案或 None。在人类段位体系之外额外标注"AI 级"实力。
    阈值：吻合度 ≥ 0.85 且 稳健目损 ≤ 0.30（与 AGREEMENT_RANK_BANDS 的 AI 级档对齐）。
    """
    if (agree1 is not None and agree1 >= 0.85
            and robust_loss is not None and robust_loss <= 0.30):
        return "该方选点高度吻合分析引擎且目损极低，表现接近 AI 水平（远超人类段位体系）"
    return None


def difficulty_of(best_prior):
    """AI 一选 prior → 「难度」(0-1，越高越难；业余棋手下对概率的补)。

    近似 difficulty ≈ 1 - best_prior：prior 高=AI 明显偏好=人类也常选=易；
    prior 低=AI 冷门=难。诚实：prior 不等于人类下对概率（涨棋网难度基于业余对局
    统计），仅作"冷门程度"代理。
    """
    if best_prior is None:
        return None
    try:
        return max(0.0, min(1.0, 1.0 - float(best_prior)))
    except (TypeError, ValueError):
        return None


def highlight_intervals(evaluations, color=None, min_run=5,
                        good_loss=1.0, ai_loss=0.5):
    """从 MoveEvaluation 序列找「高光时刻」与「疑似AI」区间（对标涨棋网 P3-4）。

    高光(highlight)：连续 ≥min_run 手该方 loss<good_loss（好手及以上）。
    疑似AI(ai_suspect)：连续 ≥min_run 手 agreement_rank==0（AI一选）且 loss≤ai_loss。
    返回 [{"start","end","kind":"highlight"/"ai_suspect"}, ...]（move_no 升序）。
    """
    seq = [e for e in (evaluations or []) if color is None or e.color == color]
    if not seq:
        return []
    seq.sort(key=lambda e: e.move_number)

    def _runs(predicate):
        runs, start, prev = [], None, None
        for e in seq:
            if predicate(e):
                if start is None:
                    start = e.move_number
                prev = e.move_number
            else:
                if start is not None and prev - start + 1 >= min_run:
                    runs.append((start, prev))
                start = prev = None
        if start is not None and prev - start + 1 >= min_run:
            runs.append((start, prev))
        return runs

    out = []
    for s, e in _runs(lambda e: e.analyzed and e.loss is not None and e.loss < good_loss):
        out.append({"start": s, "end": e, "kind": "highlight"})
    for s, e in _runs(lambda e: e.analyzed and e.agreement_rank == 0
                      and e.loss is not None and e.loss <= ai_loss):
        out.append({"start": s, "end": e, "kind": "ai_suspect"})
    out.sort(key=lambda d: d["start"])
    return out


def quality_distribution(move_quality_results, color=None):
    """六级评价计数（对标涨棋网 P5 发挥水准分布）。

    返回 {"best","good","normal","inaccuracy","blunder","unknown"} 计数。
    move_quality_results 为 MoveQualityResult 列表（读 quality_key/color）。
    """
    counts = {"best": 0, "good": 0, "normal": 0,
              "inaccuracy": 0, "blunder": 0, "unknown": 0}
    for r in move_quality_results or []:
        if color is not None and getattr(r, "color", None) != color:
            continue
        k = getattr(r, "quality_key", None) or "unknown"
        counts[k] = counts.get(k, 0) + 1
    return counts


def normalize_phase(phase):
    """阶段名归一化；支持 opening/middle/endgame 与 布局/中盘/官子。"""
    if phase is None:
        return None
    return PHASE_ALIASES.get(str(phase).strip().lower(), PHASE_ALIASES.get(str(phase).strip(), phase))


def _num(d, key, default=0.0):
    """从 dict 取数值字段；缺失/None 返回 default（避免 0.0 被 `or` 吞掉）。"""
    if not d:
        return default
    v = d.get(key, default)
    return default if v is None else v


@dataclass
class MoveEvaluation:
    node_nid: int                 # 对应 MoveNode.nid（跳转定位）
    move_number: int              # 第几手（根=0，根的子=1，…）
    color: str                    # "B" / "W"
    coord: Optional[str]          # GTP 坐标；pass 为 None
    is_pass: bool
    score_lead_before: float      # 这手之前 黑视角 scoreLead（=P.rootInfo）
    score_lead_after: float       # 这手之后 黑视角 scoreLead（=N.rootInfo）
    winrate_before: float         # 黑胜率，之前
    winrate_after: float          # 黑胜率，之后
    best_move: str                # P 最佳走法 GTP（pass 候选为 "pass"）
    best_score_lead: float        # best.scoreLead（黑视角）
    loss: Optional[float]         # 目损（走子方视角，≥0）；None=无法评价
    analyzed: bool                # N 与 P 是否都已分析且 P 有 moveInfos
    agreement_rank: Optional[int] = None   # actual 在父 moveInfos 前 5 选的排名（0=首选）；None=不在/未分析
    ai_rank: Optional[int] = None          # actual 在全部返回候选中的排名（1=首选）
    best_prior: Optional[float] = None     # AI 一选的 prior（选点概率 0-1）；用于「难度」近似（越低越难）


class ReviewReport:
    def __init__(self, tree):
        self.tree = tree
        self.size = tree.size

    # ---- 主线 ----
    def mainline_nodes(self):
        """根 → 主线末尾（沿 children[0]）。"""
        nodes = []
        n = self.tree.root
        while n is not None:
            nodes.append(n)
            n = n.children[0] if n.children else None
        return nodes

    def node_at_move(self, move_number):
        """手数 → 节点（曲线点击跳转用）；越界返回 None。"""
        line = self.mainline_nodes()
        if 0 <= move_number < len(line):
            return line[move_number]
        return None

    # ---- analysis 取值 ----
    @staticmethod
    def _root_info(node):
        a = getattr(node, "analysis", None)
        if not a:
            return None
        return a.get("rootInfo") or {}

    @staticmethod
    def _move_infos(node):
        a = getattr(node, "analysis", None)
        if not a:
            return []
        mis = a.get("moveInfos") or []
        return sorted(mis, key=lambda m: m.get("order", 99))

    def _coord_of(self, node):
        if node.move is None:
            return None
        _cl, coord = node.move
        if coord is None:
            return None            # pass
        return xy_to_point(coord[0], coord[1], self.size)

    # ---- 单手评价 ----
    def _eval(self, node, move_number):
        parent = node.parent
        p_root = self._root_info(parent) if parent is not None else None
        n_root = self._root_info(node)
        mis = self._move_infos(parent) if parent is not None else []

        cl = node.move[0] if node.move else "?"
        coord = node.move[1] if node.move else None
        is_pass = coord is None
        coord_str = self._coord_of(node)

        sl_before = _num(p_root, "scoreLead")
        sl_after = _num(n_root, "scoreLead")
        wr_before = _num(p_root, "winrate")
        wr_after = _num(n_root, "winrate")

        analyzed = bool(p_root and n_root and mis)
        loss = None
        best_move = "pass"
        best_sl = 0.0
        best_prior = None
        agreement_rank = None
        ai_rank = None
        if analyzed:
            best = mis[0]
            best_move = best.get("move") or "pass"
            best_sl = _num(best, "scoreLead")
            best_prior = _num(best, "policy")
            if cl == "B":
                loss = best_sl - sl_after
            else:                                  # 白走：走子方视角翻号
                loss = sl_after - best_sl
            if loss < 0:
                loss = 0.0
            # ai_rank 扫描全部返回候选并对外使用 1-based；旧 agreement_rank
            # 仍只表示前五的 0-based 排名，保持历史调用兼容。
            actual_gtp = "pass" if is_pass else (coord_str or "")
            for ri, m in enumerate(mis):
                if (m.get("move") or "") == actual_gtp:
                    ai_rank = ri + 1
                    if ri < 5:
                        agreement_rank = ri
                    break

        return MoveEvaluation(
            node_nid=node.nid,
            move_number=move_number,
            color=cl,
            coord=coord_str,
            is_pass=is_pass,
            score_lead_before=sl_before,
            score_lead_after=sl_after,
            winrate_before=wr_before,
            winrate_after=wr_after,
            best_move=best_move,
            best_score_lead=best_sl,
            loss=loss,
            analyzed=analyzed,
            agreement_rank=agreement_rank,
            ai_rank=ai_rank,
            best_prior=best_prior,
        )

    def evaluate(self):
        """主线每手一个 MoveEvaluation（不含根）。"""
        out = []
        line = self.mainline_nodes()
        for i, n in enumerate(line):
            if i == 0:
                continue                          # 根无「上一手」
            out.append(self._eval(n, i))
        return out

    def eval_node(self, node):
        """单个节点的评价（move_number 取其在主线的下标，不在主线则用 depth）。"""
        mn = None
        for i, nd in enumerate(self.mainline_nodes()):
            if nd is node:
                mn = i
                break
        if mn is None:
            mn = getattr(node, "depth", 0)
        return self._eval(node, mn)

    def move_quality_results(self, visits=None, include_unknown=True):
        """把主线评价统一转换为 MoveQualityResult。

        阶段只使用 ReviewReport.phase_of_move()；AI 排名使用完整 1-based
        ``ai_rank``；胜率损失保存为走子方损失的百分点。
        """
        from move_quality import MoveQualityInput, evaluate_move, QUALITY_UNKNOWN

        results = []
        line = self.mainline_nodes()
        total = max(0, len(line) - 1)
        for index, node in enumerate(line[1:], start=1):
            evaluation = self._eval(node, index)
            parent_root = self._root_info(node.parent) or {}
            root_visits = parent_root.get("visits")
            effective_visits = root_visits if root_visits is not None else visits
            candidate_count = len(self._move_infos(node.parent))
            wr_drop = None
            if evaluation.analyzed:
                wr_drop = self.winrate_loss_pct(evaluation)
            result = evaluate_move(MoveQualityInput(
                move_no=evaluation.move_number,
                color=evaluation.color,
                played_move=("pass" if evaluation.is_pass else (evaluation.coord or "")),
                best_move=evaluation.best_move,
                ai_rank=evaluation.ai_rank,
                score_loss=evaluation.loss,
                winrate_drop=wr_drop,
                parent_winrate=(evaluation.winrate_before if evaluation.analyzed else None),
                parent_score_lead=(
                    evaluation.score_lead_before if evaluation.analyzed else None),
                visits=effective_visits,
                analysis_available=evaluation.analyzed,
                candidate_count=candidate_count,
                stage=self.phase_of_move(evaluation.move_number, total),
                board_size=self.size,
            ))
            if include_unknown or result.quality_key != QUALITY_UNKNOWN:
                results.append(result)
        return results

    def move_quality_for_node(self, node, stage=None, visits=None):
        """评价主线或训练分支上的单个节点。"""
        from move_quality import MoveQualityInput, evaluate_move

        evaluation = self.eval_node(node)
        parent_root = self._root_info(node.parent) or {}
        root_visits = parent_root.get("visits")
        effective_visits = root_visits if root_visits is not None else visits
        candidate_count = len(self._move_infos(node.parent))
        total = max(0, len(self.mainline_nodes()) - 1)
        phase = stage or self.phase_of_move(evaluation.move_number, total)
        return evaluate_move(MoveQualityInput(
            move_no=evaluation.move_number,
            color=evaluation.color,
            played_move=("pass" if evaluation.is_pass else (evaluation.coord or "")),
            best_move=evaluation.best_move,
            ai_rank=evaluation.ai_rank,
            score_loss=evaluation.loss,
            winrate_drop=(
                self.winrate_loss_pct(evaluation)
                if evaluation.analyzed else None),
            parent_winrate=(
                evaluation.winrate_before if evaluation.analyzed else None),
            parent_score_lead=(
                evaluation.score_lead_before if evaluation.analyzed else None),
            visits=effective_visits,
            analysis_available=evaluation.analyzed,
            candidate_count=candidate_count,
            stage=phase,
            board_size=self.size,
        ))

    def review_summary_v2(self, visits=None, analysis_signature=None):
        """生成项目文件可保存的轻量精细评价摘要。"""
        from move_quality import VERSION as QUALITY_VERSION
        return {
            "version": 1,
            "qualityVersion": QUALITY_VERSION,
            "analysisSignature": dict(analysis_signature or {}),
            "moveQuality": [
                result.to_dict()
                for result in self.move_quality_results(
                    visits=visits, include_unknown=True)
            ],
        }

    # ---- 聚合 ----
    def top_losses(self, n=TOP_N_DEFAULT, min_loss=LOSS_DEFAULT_THRESHOLD):
        """按 loss 降序，取 loss≥min_loss 的前 n 个。"""
        evs = [e for e in self.evaluate()
               if e.analyzed and e.loss is not None and e.loss >= min_loss]
        evs.sort(key=lambda e: e.loss, reverse=True)
        return evs[:n]

    def meaningful_problems(self, n=TOP_N_DEFAULT, min_loss=LOSS_DEFAULT_THRESHOLD,
                            min_winrate_loss=0.03, color=None):
        """返回仍有胜负意义的问题手，按胜率影响优先排序。

        胜负已定局面不进入列表；目损达到阈值，或单手胜率损失达到阈值，
        即视为值得复盘的问题棋。
        """
        out = []
        for e in self.evaluate():
            if color in ("B", "W") and e.color != color:
                continue
            if not e.analyzed or e.loss is None:
                continue
            mover_winrate = e.winrate_before if e.color == "B" else 1.0 - e.winrate_before
            if not PERFORMANCE_WINRATE_FLOOR <= mover_winrate <= 1.0 - PERFORMANCE_WINRATE_FLOOR:
                continue
            winrate_loss = self._winrate_loss(e)
            if e.loss >= min_loss or (e.loss >= 0.5 and winrate_loss >= min_winrate_loss):
                out.append(e)
        out.sort(key=lambda e: (self._winrate_loss(e), min(e.loss, PERFORMANCE_LOSS_CAP)),
                 reverse=True)
        return out[:n]

    @staticmethod
    def _region_name(x, y, size):
        near_x = x <= 5 or x >= size - 6
        near_y = y <= 5 or y >= size - 6
        if near_x and near_y:
            return "角部"
        if min(x, y, size - 1 - x, size - 1 - y) <= 3:
            return "边上"
        return "中央"

    @staticmethod
    def _group_liberties(grid, x, y, size):
        color = grid[y][x]
        seen = {(x, y)}
        stack = [(x, y)]
        liberties = set()
        while stack:
            cx, cy = stack.pop()
            for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
                if not (0 <= nx < size and 0 <= ny < size):
                    continue
                value = grid[ny][nx]
                if value == EMPTY:
                    liberties.add((nx, ny))
                elif value == color and (nx, ny) not in seen:
                    seen.add((nx, ny))
                    stack.append((nx, ny))
        return len(liberties)

    def _move_intent(self, board, move):
        """从局部棋形推断一个选点可能追求的目标。"""
        if not move or str(move).lower() == "pass":
            return {
                "move": "pass", "region": "全局", "captured": 0, "liberties": None,
                "text": "可能认为当前没有更紧迫的局部，准备把行棋权交给对方。"
            }
        try:
            x, y = point_to_xy(move, self.size)
            trial = board.try_play(x, y)
        except Exception:
            return {
                "move": move, "region": "未知", "captured": 0, "liberties": None,
                "text": "该选点无法从当前盘面可靠模拟，暂不能判断意图。"
            }

        color = board.to_move
        opponent = 3 - color
        own_adj = 0
        opp_adj = 0
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if not (0 <= nx < self.size and 0 <= ny < self.size):
                continue
            value = board.grid[ny][nx]
            own_adj += value == color
            opp_adj += value == opponent
        captured = trial.captures[color] - board.captures[color]
        liberties = self._group_liberties(trial.grid, x, y, self.size)
        region = self._region_name(x, y, self.size)

        ideas = []
        if captured > 0:
            ideas.append("立即提掉%d颗对方棋子、兑现局部收益" % captured)
        if own_adj >= 2:
            ideas.append("连接或补强相邻己方棋块")
        elif own_adj and opp_adj:
            ideas.append("在接触战中稳住己棋并限制对方")
        if opp_adj >= 2:
            ideas.append("冲击对方连接，尝试制造切断")
        elif opp_adj and not own_adj:
            ideas.append("贴近对方棋子发起局部接触")
        if region == "角部":
            ideas.append("争取角部实地或攻防要点")
        elif region == "边上":
            ideas.append("扩张边地并压缩对方空间")
        else:
            ideas.append("争夺中央方向和全局主动")
        if liberties <= 2:
            ideas.append("但落子后自身气较紧，战术负担偏大")
        return {
            "move": move,
            "region": region,
            "captured": captured,
            "liberties": liberties,
            "own_adjacent": own_adj,
            "opponent_adjacent": opp_adj,
            "text": "从棋形推测，可能想%s。" % "；".join(ideas[:3]),
        }

    def bad_move_intent(self, evaluation):
        """为一手恶手生成实战意图、AI 意图和方向差异；非恶手返回 None。"""
        if evaluation is None or evaluation.loss is None or evaluation.loss < GRADE_BAD:
            return None
        node = self.node_at_move(evaluation.move_number)
        if node is None or node.parent is None:
            return None
        board = node.parent.board
        actual_move = "pass" if evaluation.is_pass else evaluation.coord
        actual = self._move_intent(board, actual_move)
        ai = self._move_intent(board, evaluation.best_move)
        move_infos = self._move_infos(node.parent)
        pv = (move_infos[0].get("pv") or [])[:5] if move_infos else []

        differences = []
        if actual["region"] != ai["region"]:
            differences.append("实战把手段投向%s，AI则优先处理%s" % (
                actual["region"], ai["region"]))
        if ai["captured"] > actual["captured"]:
            differences.append("AI方案能更直接兑现提子收益")
        actual_libs = actual.get("liberties")
        ai_libs = ai.get("liberties")
        if actual_libs is not None and ai_libs is not None and ai_libs >= actual_libs + 2:
            differences.append("AI落子后的棋形更舒展，气数压力更小")
        if not differences:
            differences.append("两者局部方向接近，但AI次序更准确、后续效率更高")
        differences.append("实战相对AI首选损失%.1f目、胜率下降%.1f个百分点" % (
            evaluation.loss, self.winrate_loss_pct(evaluation)))

        ai_text = ai["text"]
        if pv:
            ai_text += " 参考主变：" + " → ".join(pv)
        return {
            "move": evaluation.move_number,
            "color": evaluation.color,
            "actualMove": actual_move or "pass",
            "aiMove": evaluation.best_move,
            "actualIntent": actual["text"],
            "aiIntent": ai_text,
            "difference": "；".join(differences) + "。",
        }

    def bad_move_intents(self, limit=6, color=None):
        out = []
        for e in self.meaningful_problems(n=99, color=color):
            item = self.bad_move_intent(e)
            if item:
                out.append((e, item))
            if len(out) >= limit:
                break
        return out

    def score_lead_series(self):
        """[(move_number, score_lead), ...] 主线已分析节点（黑视角）。"""
        out = []
        for i, node in enumerate(self.mainline_nodes()):
            ri = self._root_info(node)
            if ri and "scoreLead" in ri:
                out.append((i, ri["scoreLead"]))
        return out

    def winrate_series(self):
        """[(move_number, winrate), ...] 主线已分析节点（黑胜率）。"""
        out = []
        for i, node in enumerate(self.mainline_nodes()):
            ri = self._root_info(node)
            if ri and "winrate" in ri:
                out.append((i, ri["winrate"]))
        return out

    def analyze_progress(self):
        """(已分析节点数, 主线总节点数)。"""
        line = self.mainline_nodes()
        total = len(line)
        done = sum(1 for nd in line if getattr(nd, "analysis", None))
        return done, total

    def grade_summary(self):
        """主线已分析手的等级统计：{'好':x,'普通':y,'疑问':z,'恶':w,'总':n}。"""
        s = {"好": 0, "普通": 0, "疑问": 0, "恶": 0, "总": 0}
        for e in self.evaluate():
            if not e.analyzed or e.loss is None:
                continue
            s[grade_of(e.loss)] += 1
            s["总"] += 1
        return s

    @staticmethod
    def _phase_bounds(total):
        """返回 (布局结束手, 中盘结束手)。短局按三等分，长局限制布局不拖太长。"""
        total = max(0, int(total))
        if total <= 0:
            return (0, 0)
        if total < 60:
            opening_end = max(1, total // 3)
            middle_end = max(opening_end, total * 2 // 3)
        else:
            opening_end = min(50, max(20, int(total * 0.28)))
            middle_end = max(opening_end, int(total * 0.72))
        return (opening_end, middle_end)

    @classmethod
    def phase_of_move(cls, move_number, total):
        """第 N 手属于 布局 / 中盘 / 官子。"""
        if move_number <= 0 or total <= 0:
            return "opening"
        opening_end, middle_end = cls._phase_bounds(total)
        if move_number <= opening_end:
            return "opening"
        if move_number <= middle_end:
            return "middle"
        return "endgame"

    @classmethod
    def phase_label(cls, phase):
        return PHASE_LABELS.get(normalize_phase(phase), str(phase))

    @classmethod
    def _phase_range(cls, phase, total):
        """三阶段手数范围。"""
        phase = normalize_phase(phase)
        if total <= 0:
            return (1, 0)
        opening_end, middle_end = cls._phase_bounds(total)
        if phase == "opening":
            return (1, opening_end)
        if phase == "middle":
            return (opening_end + 1, middle_end)
        if phase == "endgame":
            return (middle_end + 1, total)
        return (1, total)

    def player_stats(self, color, phase=None):
        """某方（'B'/'W'）已分析手聚合：{'moves','avg_loss','agree1','agree3'}；无则 None。

        phase: 'opening'/'middle'/'endgame' 或 布局/中盘/官子；None 为全盘。
        """
        evs = [e for e in self.evaluate()
               if e.color == color and e.analyzed and e.loss is not None]
        if phase:
            total = len(self.mainline_nodes()) - 1
            lo, hi = self._phase_range(normalize_phase(phase), total)
            evs = [e for e in evs if lo <= e.move_number <= hi]
        if not evs:
            return None
        losses = [e.loss for e in evs]
        avg = sum(losses) / len(losses)
        n = len(evs)
        agree1 = sum(1 for e in evs if e.agreement_rank == 0) / n * 100
        agree3 = sum(1 for e in evs if e.agreement_rank is not None and e.agreement_rank < 3) / n * 100
        return {"moves": n, "avg_loss": avg, "agree1": agree1, "agree3": agree3}

    def analysis_coverage(self, color=None):
        """返回复盘范围内的分析覆盖与有效评价数量。"""
        evs = self.evaluate()
        if color in ("B", "W"):
            evs = [e for e in evs if e.color == color]
        total = len(evs)
        analyzed = sum(1 for e in evs if e.analyzed and e.loss is not None)
        meaningful = 0
        for e in evs:
            if not e.analyzed or e.loss is None:
                continue
            mover_wr = e.winrate_before if e.color == "B" else 1.0 - e.winrate_before
            if PERFORMANCE_WINRATE_FLOOR <= mover_wr <= 1.0 - PERFORMANCE_WINRATE_FLOOR:
                meaningful += 1
        return {
            "color": color if color in ("B", "W") else "both",
            "total": total,
            "analyzed": analyzed,
            "meaningful": meaningful,
            "missing": max(0, total - analyzed),
            "percent": (analyzed / total * 100.0) if total else 0.0,
            "complete": bool(total > 0 and analyzed == total),
        }

    def player_performance(self, color):
        """估计某方在本局的表现档，返回稳健目损、有效样本和估计区间。

        仅使用落子前该方胜率在 2%-98% 的局面；单手损失最多计 3 目。
        样本不足 8 手时不输出段位，避免短局或分析未完成时制造精确幻觉。
        """
        all_evs = [e for e in self.evaluate()
                   if e.color == color and e.analyzed and e.loss is not None]
        if not all_evs:
            return None
        rated = []
        for e in all_evs:
            mover_winrate = e.winrate_before if color == "B" else 1.0 - e.winrate_before
            if PERFORMANCE_WINRATE_FLOOR <= mover_winrate <= 1.0 - PERFORMANCE_WINRATE_FLOOR:
                rated.append(e)
        robust_loss = None
        if rated:
            robust_loss = sum(min(e.loss, PERFORMANCE_LOSS_CAP) for e in rated) / len(rated)
        # AI 选点吻合度（top-1 / top-3，与稳健目损同一胜负悬念样本）
        agree1 = agree3 = None
        if rated:
            n = len(rated)
            agree1 = sum(1 for e in rated if e.agreement_rank == 0) / n
            agree3 = sum(1 for e in rated
                         if e.agreement_rank is not None and e.agreement_rank < 3) / n
        enough = len(rated) >= PERFORMANCE_MIN_MOVES
        rating = performance_rating(agree1, robust_loss, len(rated)) if enough else None
        if rating is not None:
            idx, rank, rank_short = rating
        else:
            idx, rank, rank_short = None, "—", "—"
        rank_range = "样本不足"
        confidence = "样本不足"
        if enough and idx is not None:
            spread = 1 if len(rated) >= 20 else 2
            strong = max(0, idx - spread)
            weak = min(RATING_BANDS_COUNT, idx + spread)
            rank_range = "%s-%s" % (_rating_short_at(weak), _rating_short_at(strong))
            confidence = "中等" if len(rated) >= 40 else "有限" if len(rated) >= 20 else "较低"
        elo = elo_estimate(agree1, robust_loss, len(rated)) if enough else None
        ai_hint = ai_likeness_hint(agree1, robust_loss)
        ds = [difficulty_of(e.best_prior) for e in rated]
        ds = [d for d in ds if d is not None]
        avg_difficulty = sum(ds) / len(ds) if ds else None
        highlights = highlight_intervals(all_evs, color)
        return {
            "moves": len(all_evs),
            "rated_moves": len(rated),
            "settled_ignored": len(all_evs) - len(rated),
            "raw_avg_loss": sum(e.loss for e in all_evs) / len(all_evs),
            "performance_loss": robust_loss,
            "agree1": agree1,
            "agree3": agree3,
            "rank": rank,
            "rank_short": rank_short,
            "rank_range": rank_range,
            "confidence": confidence,
            "elo": elo,
            "ai_hint": ai_hint,
            "avg_difficulty": avg_difficulty,
            "highlights": highlights,
        }

    def player_rank(self, color):
        """某方单局表现档；无数据或有效样本不足 → '—'。"""
        s = self.player_performance(color)
        return "—" if s is None else s["rank"]

    def phase_stats(self, phase, color=None):
        """某阶段整体水平与问题统计。

        返回 dict：phase/label/range/moves/avg_loss/rank/good/normal/doubt/bad/problem_count/
        top_problem/best_move；该阶段无已分析手时 avg_loss=None、rank='—'。
        """
        phase = normalize_phase(phase)
        total = len(self.mainline_nodes()) - 1
        lo, hi = self._phase_range(phase, total)
        evs = [e for e in self.evaluate()
               if e.analyzed and e.loss is not None and lo <= e.move_number <= hi]
        if color in ("B", "W"):
            evs = [e for e in evs if e.color == color]
        out = {
            "phase": phase,
            "label": self.phase_label(phase),
            "range": (lo, hi),
            "moves": len(evs),
            "avg_loss": None,
            "rank": "—",
            "rank_short": "—",
            "quality": "暂无数据",
            "good": 0,
            "normal": 0,
            "doubt": 0,
            "bad": 0,
            "problem_count": 0,
            "top_problem": None,
            "best_move": None,
        }
        if not evs:
            return out
        avg = sum(e.loss for e in evs) / len(evs)
        out["avg_loss"] = avg
        out["rank"] = rank_of(avg)
        out["rank_short"] = rank_short(avg)
        if avg < 0.75:
            out["quality"] = "优秀"
        elif avg < 1.5:
            out["quality"] = "稳定"
        elif avg < 3.0:
            out["quality"] = "有波动"
        else:
            out["quality"] = "问题较多"
        for e in evs:
            g = grade_of(e.loss)
            if g == "好":
                out["good"] += 1
            elif g == "普通":
                out["normal"] += 1
            elif g == "疑问":
                out["doubt"] += 1
            elif g == "恶":
                out["bad"] += 1
        problems = [e for e in evs if e.loss >= LOSS_DEFAULT_THRESHOLD]
        problems.sort(key=lambda e: e.loss, reverse=True)
        out["problem_count"] = len(problems)
        out["top_problem"] = problems[0] if problems else None
        good = [e for e in evs if e.loss < GRADE_GOOD or e.agreement_rank == 0]
        good.sort(key=lambda e: (e.loss, 9 if e.agreement_rank is None else e.agreement_rank, e.move_number))
        out["best_move"] = good[0] if good else None
        return out

    def phase_summary(self, color=None):
        """布局/中盘/官子三阶段整体统计。"""
        return [self.phase_stats(p, color=color) for p in PHASES]

    def phase_bar_segments(self, total=None, color=None,
                           good_qualities=("优秀", "稳定")):
        """三阶段摊成「棋力评估进度条」的段，供 UI 标亮下得好的阶段。

        每段：{phase,label,range,frac:(lo_frac,hi_frac),quality,avg_loss,rank,is_good,
        moves,good,doubt,bad}。frac 按主线总手数归一（与 color 无关，段位置稳定）。
        is_good = quality 命中 good_qualities 且该阶段有已分析手（即「下得不错」→ 标亮）。
        """
        if total is None:
            total = max(0, len(self.mainline_nodes()) - 1)
        segs = []
        for st in self.phase_summary(color=color):
            lo, hi = st["range"]
            lo_f = (lo - 1) / total if total > 0 else 0.0
            hi_f = hi / total if total > 0 else 1.0
            segs.append({
                "phase": st["phase"],
                "label": st["label"],
                "range": (lo, hi),
                "frac": (max(0.0, lo_f), min(1.0, hi_f)),
                "quality": st["quality"],
                "avg_loss": st["avg_loss"],
                "rank": st["rank_short"],
                "is_good": bool(st["moves"] > 0 and st["quality"] in good_qualities),
                "moves": st["moves"],
                "good": st["good"],
                "doubt": st["doubt"],
                "bad": st["bad"],
            })
        return segs

    @staticmethod
    def _position_text(score_lead):
        if abs(score_lead) < 1.5:
            return "形势接近"
        return "%s方约领先 %.1f 目" % ("黑" if score_lead > 0 else "白", abs(score_lead))

    @staticmethod
    def _winrate_loss(e):
        if e.color == "B":
            return max(0.0, e.winrate_before - e.winrate_after)
        return max(0.0, e.winrate_after - e.winrate_before)

    def winrate_loss_pct(self, e):
        return self._winrate_loss(e) * 100.0

    def game_commentary(self, black_name="黑方", white_name="白方",
                        focus_color=None):
        """根据形势曲线和逐手目损生成可直接显示的中文对局解说。"""
        evs = [e for e in self.evaluate() if e.analyzed and e.loss is not None]
        if focus_color in ("B", "W"):
            evs = [e for e in evs if e.color == focus_color]
        if len(evs) < 2:
            return "完成整盘分析后，这里会自动生成布局、中盘、关键转折和收束阶段的文字复盘。"

        total = len(self.mainline_nodes()) - 1
        names = {"B": black_name or "黑方", "W": white_name or "白方"}
        result = str(getattr(self.tree, "_sgf_re", "") or "")
        if result.upper().startswith("B+"):
            result_text = "%s胜出（%s）" % (names["B"], result)
        elif result.upper().startswith("W+"):
            result_text = "%s胜出（%s）" % (names["W"], result)
        else:
            last = evs[-1]
            result_text = "终局形势为%s" % self._position_text(last.score_lead_after)

        bp = self.player_performance("B")
        wp = self.player_performance("W")
        level_bits = []
        for color, stats in (("B", bp), ("W", wp)):
            if focus_color in ("B", "W") and color != focus_color:
                continue
            if stats and stats["rank"] != "—":
                level_bits.append("%s为%s" % (names[color], stats["rank"]))
        opening = ("全局：共 %d 手，%s。单局表现估计%s。"
                   % (total, result_text, "；".join(level_bits) if level_bits else "尚缺有效样本"))

        phase_parts = []
        line = self.mainline_nodes()
        for phase in PHASES:
            lo, hi = self._phase_range(phase, total)
            phase_evs = [e for e in evs if lo <= e.move_number <= hi]
            if not phase_evs:
                continue
            end_node = line[min(hi, len(line) - 1)]
            end_info = self._root_info(end_node) or {}
            end_score = _num(end_info, "scoreLead")
            bs = [e.loss for e in phase_evs if e.color == "B"]
            ws = [e.loss for e in phase_evs if e.color == "W"]
            quality = []
            if bs:
                quality.append("%s平均目损 %.1f" % (names["B"], sum(bs) / len(bs)))
            if ws:
                quality.append("%s平均目损 %.1f" % (names["W"], sum(ws) / len(ws)))
            worst = max(phase_evs, key=lambda e: e.loss)
            phase_start = phase_evs[0]
            start_wr = phase_start.winrate_before
            settled_note = ""
            if start_wr < PERFORMANCE_WINRATE_FLOOR or start_wr > 1.0 - PERFORMANCE_WINRATE_FLOOR:
                settled_note = " 本阶段开始时胜负已基本确定，目损只用于复盘，不参与棋力估计。"
            phase_parts.append(
                "%s（%d-%d手）：阶段结束时%s；%s。最大目损出现在第%d手 %s（%s），"
                "实战%s，AI首选%s，损失%.1f目。%s"
                % (self.phase_label(phase), lo, hi, self._position_text(end_score),
                   "，".join(quality), worst.move_number, names[worst.color],
                   _side_word(worst.color), "pass" if worst.is_pass else (worst.coord or "?"),
                   worst.best_move, worst.loss, settled_note))

        pivotal = []
        for e in evs:
            mover_wr = e.winrate_before if e.color == "B" else 1.0 - e.winrate_before
            wr_loss = self._winrate_loss(e)
            if PERFORMANCE_WINRATE_FLOOR <= mover_wr <= 1.0 - PERFORMANCE_WINRATE_FLOOR and wr_loss >= 0.03:
                pivotal.append((wr_loss, e))
        pivotal.sort(key=lambda item: item[0], reverse=True)
        turns = []
        for wr_loss, e in pivotal[:3]:
            turns.append("第%d手 %s %s使该方胜率下降%.1f个百分点（黑胜率%d%%→%d%%）" % (
                e.move_number, names[e.color], "pass" if e.is_pass else (e.coord or "?"),
                wr_loss * 100, int(e.winrate_before * 100 + 0.5),
                int(e.winrate_after * 100 + 0.5)))
        turning_text = "关键转折：" + ("；".join(turns) + "。" if turns else "整盘没有超过3个百分点的单手波动。")

        meaningful = []
        for e in evs:
            mover_wr = e.winrate_before if e.color == "B" else 1.0 - e.winrate_before
            if PERFORMANCE_WINRATE_FLOOR <= mover_wr <= 1.0 - PERFORMANCE_WINRATE_FLOOR:
                meaningful.append(e)
        problems = sorted(meaningful, key=lambda e: e.loss, reverse=True)[:3]
        focus = "复盘重点：" + "；".join(
            "第%d手 %s %s，应优先比较AI首选%s" % (
                e.move_number, names[e.color], "pass" if e.is_pass else (e.coord or "?"), e.best_move)
            for e in problems) + ("。" if problems else "暂无足够有效样本。")
        return "\n\n".join([opening] + phase_parts + [turning_text, focus])


def _side_word(color):
    return "黑" if color == "B" else "白"
