"""taxonomy —— 错误分类体系（项目大纲 §27-30）。

维度A：技术错误（本模块，v1 九类）。维度B：决策习惯（第二阶段再加）。

设计约束：
- 分类必须保存证据（category_evidence），不允许只落一个 category 字符串；
- 证据不足时明确返回 unclassified（低置信），不猜；
- 输入用现有的 move_quality 标签 + 局面上下文（阶段/目损），完全确定性，
  后续 Human SL / 用户自述接入后在此叠加证据，不推翻本层。
"""
from __future__ import annotations

TAXONOMY_VERSION = "1"

# 维度A：技术错误（v1 九类，项目大纲 §28）
TECHNICAL_CATEGORIES = {
    "reading": "计算",
    "life_death": "死活",
    "shape": "棋形",
    "direction": "方向",
    "weak_groups": "弱棋/轻重",
    "attack_defense": "攻击/防守",
    "sente_tenuki": "先后手/脱先",
    "whole_board": "全局判断",
    "endgame": "官子",
}

CATEGORY_UNCLASSIFIED = "unclassified"
CATEGORY_LABELS = dict(TECHNICAL_CATEGORIES)
CATEGORY_LABELS[CATEGORY_UNCLASSIFIED] = "待分类"

# 第二阶段类别（v1 不启用，占位防魔法字符串）
CATEGORY_JOSEKI = "joseki"
CATEGORY_INVASION = "invasion"
CATEGORY_REDUCTION = "reduction"
CATEGORY_KO = "ko"

# 旧 move_quality 标签 → 新分类（确定性映射；元组按优先级排列）
_TAG_TO_CATEGORY = {
    "opening_direction": ("direction", "whole_board"),
    "endgame_value": ("endgame", "whole_board"),
    "advantage_management": ("whole_board",),
    "comeback_attempt": ("whole_board", "attack_defense"),
    "tenuki_timing": ("sente_tenuki",),
    "overplay": ("attack_defense", "weak_groups"),
    "slack_move": ("sente_tenuki", "whole_board"),
    "life_and_death": ("life_death", "reading"),
}

# 多标签并存时的主类别优先序：越具体越靠前
_TAG_PRIORITY = (
    "life_and_death", "tenuki_timing", "overplay", "opening_direction",
    "endgame_value", "slack_move", "advantage_management", "comeback_attempt",
)

# 意图关键词（用户自述/棋形意图文本 → 分类佐证，§26 用户自述接入点）
_INTENT_KEYWORDS = (
    ("做活", "life_death"), ("死活", "life_death"), ("杀", "life_death"),
    ("攻击", "attack_defense"), ("攻杀", "attack_defense"),
    ("防守", "attack_defense"), ("补棋", "attack_defense"),
    ("弃子", "weak_groups"), ("弱棋", "weak_groups"), ("治孤", "weak_groups"),
    ("脱先", "sente_tenuki"), ("先手", "sente_tenuki"),
    ("围地", "direction"), ("大场", "direction"), ("方向", "direction"),
    ("官子", "endgame"),
)

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"


def classify_problem(problem, intent=None, comparison=None):
    """把一个问题手（top_problem_moves 条目 / LearningEvent dict）分到九类之一。

    返回 dict：primary_category / secondary_categories / category_confidence /
    category_evidence / taxonomy_version。证据不足 → unclassified + low。
    """
    problem = dict(problem or {})
    tags = [t for t in (problem.get("problem_tags") or [])
            if t in _TAG_TO_CATEGORY]
    intent = dict(intent or {})
    intent_text = " ".join(str(x) for x in (
        intent.get("difference"), intent.get("actualIntent"), intent.get("aiIntent"))
        if x)

    evidence = ["deterministic_tag:%s" % t for t in tags]
    if intent_text:
        evidence.append("intent_text:%s" % intent_text[:40])

    # 意图关键词作为佐证（不单独决定主类别，避免文本启发式喧宾夺主）
    intent_categories = []
    for keyword, category in _INTENT_KEYWORDS:
        if keyword in intent_text and category not in intent_categories:
            intent_categories.append(category)
    if intent_categories:
        evidence.append("intent_keywords:%s" % ",".join(intent_categories))

    if not tags:
        # 无旧标签时：意图佐证可以给一个低置信方向，否则明确待分类
        if intent_categories:
            primary = intent_categories[0]
            secondary = intent_categories[1:2]
            confidence = CONFIDENCE_LOW
            evidence.append("no_deterministic_tag")
        else:
            primary = CATEGORY_UNCLASSIFIED
            secondary = []
            confidence = CONFIDENCE_LOW
            evidence = ["no_deterministic_tag"]
    else:
        ordered = sorted(tags, key=lambda t: (
            _TAG_PRIORITY.index(t) if t in _TAG_PRIORITY else len(_TAG_PRIORITY)))
        primary, secondary = None, []
        for tag in ordered:
            cats = _TAG_TO_CATEGORY[tag]
            if primary is None:
                primary = cats[0]
                secondary.extend(c for c in cats[1:] if c != primary)
            else:
                secondary.extend(c for c in cats if c != primary)
        # 意图佐证与主类别一致 → 提升置信度
        consistent = primary in intent_categories
        confidence = (
            CONFIDENCE_MEDIUM if (len(ordered) == 1 and consistent)
            else CONFIDENCE_MEDIUM if len(ordered) == 1 else CONFIDENCE_LOW)
        if consistent and len(ordered) == 1:
            confidence = CONFIDENCE_HIGH
        # 去重保持顺序
        seen = set(primary)
        secondary = [c for c in secondary if not (c in seen or seen.add(c))]

    return {
        "primary_category": primary,
        "secondary_categories": secondary[:2],
        "category_confidence": confidence,
        "category_evidence": evidence,
        "taxonomy_version": TAXONOMY_VERSION,
    }


def category_label(category):
    return CATEGORY_LABELS.get(category, category or CATEGORY_UNCLASSIFIED)
