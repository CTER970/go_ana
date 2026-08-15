"""learning_priority —— 学习优先级算法（项目大纲 §10-18、§74）。

第一版是可解释、版本化的启发式，不是机器学习模型：

    priority = 0.35×severity + 0.25×recurrence + 0.15×level_gap
             + 0.15×learnability + 0.10×game_importance
    final    = priority × mastery_modifier

所有分量归一化 0-1；权重随 PRIORITY_VERSION 存进每条 LearningEvent，
算法调整后历史数据仍可对比。Human SL 接入前 level_gap 恒为 0（不假装有数据）。
"""
from __future__ import annotations

PRIORITY_VERSION = 1

WEIGHTS = {
    "severity": 0.35,
    "recurrence": 0.25,
    "level_gap": 0.15,
    "learnability": 0.15,
    "game_importance": 0.10,
}

# 对局情境权重（§16：正式比赛错误对用户目标更重要，AI 评价本身不变）
GAME_TYPES = {
    "formal": 1.0,        # 正式比赛
    "dan_match": 0.9,     # 段位赛
    "net_slow": 0.6,      # 网络慢棋
    "net_fast": 0.5,      # 网络快棋
    "training": 0.5,      # 训练棋
    "ai": 0.3,            # AI 对局
}
DEFAULT_GAME_IMPORTANCE = 0.5

# 掌握度调节（§17）：已迁移的错误降权；复习会但实战复发 → 提权
MASTERY_MODIFIERS = {
    "new": 1.0,
    "understanding": 0.7,
    "retained": 0.4,
    "transferred": 0.1,
    "unstable": 1.2,
}

# 复发计数 → 分量（§13 阶梯；以后再改成连续函数）
_RECURRENCE_TABLE = ((0, 0.0), (1, 0.25), (2, 0.40), (3, 0.65), (4, 0.65))

# 可学习度参数（§15）
REASONABLE_LOSS_THRESHOLD = 1.5   # 与一选目差 ≤ 此值 = 合理候选
UNIQUE_BEST_GAP = 5.0             # 一选领先二选 ≥ 此值 = 接近"神之一手"
CLOSE_BEST_GAP = 1.5              # 一二选接近 = 更易学成原则


def severity_of(score_loss):
    """错误严重度：目损主导（§12），8 目封顶。"""
    try:
        return min(max(float(score_loss or 0.0), 0.0) / 8.0, 1.0)
    except (TypeError, ValueError):
        return 0.0


def recurrence_of(count):
    """重复程度阶梯（§13）：过去 N 盘同簇出现次数。"""
    try:
        count = int(count or 0)
    except (TypeError, ValueError):
        count = 0
    for threshold, value in _RECURRENCE_TABLE:
        if count <= threshold:
            return value
    return 1.0


def level_gap_of(prior_current=None, prior_stronger=None):
    """水平差异（§14）：当前段位常下、更高段位明显少下 = 优质学习点。

    Human SL 未接入时两个 prior 均为 None → 返回 0（不编数据）。
    """
    if prior_current is None or prior_stronger is None:
        return 0.0
    try:
        gap = float(prior_current) - float(prior_stronger)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(gap, 1.0))


def learnability_of(move_infos=None, color="B", best_prior=None):
    """可学习程度（§15）：简单错误优先，AI 唯一神之一手降权。

    依据父局面候选：合理候选数量（≤1.5 目）+ 一二选差距；
    一选 prior 极低（人类少选）时再降一档。
    """
    score = 0.5
    notes = []
    losses = _losses_vs_best(move_infos, color)
    if losses:
        reasonable = sum(1 for x in losses if x <= REASONABLE_LOSS_THRESHOLD)
        if reasonable >= 2:
            score += 0.2
            notes.append("reasonable>=2")
        if reasonable >= 4:
            score += 0.1
            notes.append("reasonable>=4")
        gap12 = losses[1] if len(losses) > 1 else None
        if gap12 is not None:
            if gap12 <= CLOSE_BEST_GAP:
                score += 0.2
                notes.append("close12")
            elif gap12 >= UNIQUE_BEST_GAP:
                score -= 0.3
                notes.append("unique_best")
    try:
        prior = float(best_prior) if best_prior is not None else None
    except (TypeError, ValueError):
        prior = None
    if prior is not None and prior < 0.05:
        # 注意：普通 KataGo prior 是引擎 policy 信号（AI 冷门候选），
        # 不是"人类很难想到"——后者应待 humanPrior 缓存后替换本判据
        score -= 0.2
        notes.append("low_ai_prior")
    return max(0.0, min(score, 1.0)), notes


def game_importance_of(game_type=None):
    return GAME_TYPES.get(str(game_type or "").lower(), DEFAULT_GAME_IMPORTANCE)


def mastery_modifier_of(mastery_state=None):
    return MASTERY_MODIFIERS.get(str(mastery_state or "new"), 1.0)


def _losses_vs_best(move_infos, color):
    """父局面各候选相对一选的目损（走子方视角），按 order 排列。"""
    mis = sorted(move_infos or [], key=lambda m: m.get("order", 999))
    if not mis:
        return []
    sign = 1.0 if str(color).upper() == "B" else -1.0

    def _get(m, key):
        v = (m or {}).get(key)
        return None if v is None else float(v)

    best = _get(mis[0], "scoreLead")
    if best is None:
        return []
    out = []
    for m in mis:
        sc = _get(m, "scoreLead")
        if sc is None:
            continue
        out.append(max(0.0, sign * (best - sc)))
    return out


def compute_learning_priority(*, score_loss=None, recurrence_count=0,
                              prior_current=None, prior_stronger=None,
                              move_infos=None, color="B", best_prior=None,
                              game_type=None, mastery_state=None):
    """计算单个问题面的学习优先级，返回含全部分量的 dict。"""
    learnability, learn_notes = learnability_of(move_infos, color, best_prior)
    components = {
        "severity": severity_of(score_loss),
        "recurrence": recurrence_of(recurrence_count),
        "level_gap": level_gap_of(prior_current, prior_stronger),
        "learnability": learnability,
        "game_importance": game_importance_of(game_type),
    }
    if learn_notes:
        components["learnability_notes"] = learn_notes
    base = sum(weight * components[key] for key, weight in WEIGHTS.items())
    modifier = mastery_modifier_of(mastery_state)
    final = max(0.0, min(base * modifier, 1.0))
    return {
        "final_score": round(final, 4),
        "components": {k: (round(v, 4) if isinstance(v, float) else v)
                       for k, v in components.items()},
        "mastery_modifier": modifier,
        "version": PRIORITY_VERSION,
    }


def build_recurrence_index(store_events, exclude_game_id=None):
    """历史 LearningEvent → {primary_category: 出现盘数}（跨局复发统计）。

    按唯一 game_id 去重：一盘棋里同类错误出现 3 次只算 1 盘——
    recurrence 占优先级 25%，把事件数当盘数会系统性放大高频类别。
    """
    games_by_category = {}
    for evt in store_events or []:
        game_id = getattr(evt, "game_id", "") or ""
        if exclude_game_id and game_id == exclude_game_id:
            continue
        category = getattr(evt, "primary_category", "") or ""
        if category:
            games_by_category.setdefault(category, set()).add(game_id)
    return {category: len(games)
            for category, games in games_by_category.items()}


def select_learning_problems(problems, *, limit=5, per_cluster_cap=2,
                             cluster_key=None):
    """按优先级选题 + 多样性约束（§18-19）。

    problems: [{..., "priority": float, "cluster": str}]；cluster 缺省按
    手数邻接（±8 手内视为同一场战斗）自动聚类。同一簇最多保留
    per_cluster_cap 个代表节点，防止 Top5 全来自同一段对杀。
    """
    items = []
    for p in (problems or []):
        item = dict(p)
        item["priority"] = float(item.get("priority") or 0.0)
        items.append(item)
    items.sort(key=lambda p: (-p["priority"], p.get("move_no", 0)))

    def _auto_cluster(p):
        return cluster_key(p) if cluster_key else None

    # 邻接聚类：按手数排序后断簇（优先序与手数序无关，不能边遍历边聚）
    if cluster_key is None:
        bounds = []
        for p in sorted(items, key=lambda p: p.get("move_no", 0)):
            mn = p.get("move_no", 0)
            if not bounds or mn - bounds[-1][0] > 8:
                bounds.append((mn, "c%d" % mn))
            p["cluster"] = bounds[-1][1]
    else:
        for p in items:
            p["cluster"] = _auto_cluster(p) or "default"

    picked, per_cluster = [], {}
    for p in items:
        cluster = p["cluster"]
        if per_cluster.get(cluster, 0) >= per_cluster_cap:
            continue
        per_cluster[cluster] = per_cluster.get(cluster, 0) + 1
        picked.append(p)
        if len(picked) >= limit:
            break
    return picked
