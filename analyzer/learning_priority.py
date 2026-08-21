"""learning_priority —— 学习优先级算法（项目大纲 §10-18、§74）。

第一版是可解释、版本化的启发式，不是机器学习模型：

    priority = 0.35×severity + 0.25×recurrence + 0.15×level_gap
             + 0.15×learnability + 0.10×game_importance
    final    = priority × mastery_modifier

所有分量归一化 0-1；权重随 PRIORITY_VERSION 存进每条 LearningEvent，
算法调整后历史数据仍可对比。level_gap 是唯一允许 None 的分量：Human SL
数据缺失（模型未安装/本手无数据）时返回 None 表示"分量不参与"，
归一化时剔除其权重而不是记 0 分（旧行为会把每个问题的分数统一压低
15%，且用户对分量失效无感知——本次治理对象）；"模型在但双档无差异"
仍返回真实的 0.0。

V2 变更：level_gap 缺数据时按剩余权重归一化（加权平均），无 Human SL
的环境下其余分量仍满幅表达，排序不退化、不全部并列。
"""
from __future__ import annotations

PRIORITY_VERSION = 2

WEIGHTS = {
    "severity": 0.35,
    "recurrence": 0.25,
    "level_gap": 0.15,
    "learnability": 0.15,
    "game_importance": 0.10,
}

# 对局情境权重（§16：正式比赛错误对用户目标更重要，AI 评价本身不变）。
# 键同时收英文内部键（历史事件数据）与中文常用名——default_game_type
# 直接存中文（如"网络对局"），用户可读可改；空串/未识别类型回落
# DEFAULT_GAME_IMPORTANCE 基准（老配置兼容路径）。
GAME_TYPES = {
    # 英文键（内部调用 / 历史数据）
    "formal": 1.0,        # 正式比赛
    "dan_match": 0.9,     # 段位赛
    "net_slow": 0.6,      # 网络慢棋
    "net_fast": 0.5,      # 网络快棋
    "training": 0.5,      # 训练棋
    "ai": 0.3,            # AI 对局
    # 中文常用类型（反馈 #13 补全）
    "正式比赛": 1.0,
    "段位赛": 0.9,
    "网络慢棋": 0.6,
    "网络快棋": 0.5,
    "训练赛": 0.5,
    "训练棋": 0.5,
    "网络对局": 0.55,     # 用时制未知的普通网棋，介于快慢棋之间
    "友谊赛": 0.4,
    "AI对局": 0.3,
    "人机对局": 0.3,
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

    返回值三态（治理"模型缺失被静默记 0 分"，与"模型在但无差异"区分）：
    - None：双档 humanPrior 不完整（Human SL 模型未安装 / 本手无数据）
      → 分量"不参与"，compute_learning_priority 归一化时剔除其权重；
    - 0.0：模型在、双档数据齐全但无差异（如 common_both）→ 真实零分；
    - 0-1：本人档概率显著高于更高档 → 分量得分（原始概率差截断）。
    """
    if prior_current is None or prior_stronger is None:
        return None
    try:
        gap = float(prior_current) - float(prior_stronger)
    except (TypeError, ValueError):
        return None
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
    # 审查 #5：删除"普通 KataGo prior 低 = 人类难想到"的惩罚——引擎
    # policy 不是人类心理学。人类难度判断只允许来自 Human SL
    # （humanPrior），缺失即不判断（fail closed）。
    del best_prior
    return max(0.0, min(score, 1.0)), notes


def game_importance_of(game_type=None):
    """对局情境 → 权重（中文/英文键均可，未识别回落 0.5 基准）。"""
    return GAME_TYPES.get(str(game_type or "").strip().lower(),
                          DEFAULT_GAME_IMPORTANCE)


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
    """计算单个问题面的学习优先级，返回含全部分量的 dict。

    level_gap 为 None（无 Human SL 数据）时该分量不参与：base 按
    剩余分量的权重做加权平均（等效于把 0.15 权重按比例摊给其余
    分量），而不是把缺失分量记 0 分——避免无模型环境下全体问题的
    分数被统一压低 15%。其余分量恒有数值，weight_sum 不会为 0。
    """
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
    base, weight_sum = 0.0, 0.0
    for key, weight in WEIGHTS.items():
        value = components.get(key)
        if value is None:
            continue  # 分量不参与（如无 Human SL 数据的 level_gap）
        base += weight * value
        weight_sum += weight
    base = base / weight_sum if weight_sum > 0 else 0.0
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
