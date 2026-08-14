"""把棋风成本结论压缩成一个可执行的下一阶段成长路线。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime

from style_cost import StyleCostResult

VERSION = 1


@dataclass
class GrowthPath:
    profile_id: str = "default"
    generated_at: str = ""
    stage_title: str = "当前成长阶段"
    main_goal: str = ""
    keep_styles: list = field(default_factory=list)
    fix_habits: list = field(default_factory=list)
    watch_items: list = field(default_factory=list)
    next_review_focus: list = field(default_factory=list)
    recommended_positions: list = field(default_factory=list)
    verification_required: list = field(default_factory=list)
    confidence: str = "low"
    warnings: list = field(default_factory=list)
    version: int = VERSION

    def to_dict(self):
        return asdict(self)


def _goal_for(item):
    return {
        "advantage_pressure": "领先时先比较低风险收束候选，再决定是否继续施压。",
        "tenuki_tendency": "接触战中先确认局部安定，再考虑脱先转向大场。",
        "endgame_safety": "减少官子阶段的连续小亏，先判断先后手和逆收价值。",
        "fighting_preference": "主动求战前增加一次最坏应手检查，降低高成本战斗。",
        "comeback_complexity": "落后时寻找有后续的胜负手，区分复杂化与无根据过分。",
    }.get(
        item.dimension_key,
        "围绕%s复盘代表局面，降低高成本选择。" % item.dimension_label)


def build_growth_path(style_profile, cost_results=None):
    costs = list(cost_results or getattr(style_profile, "cost_results", []) or [])
    costs = [
        item if isinstance(item, StyleCostResult) else StyleCostResult(**item)
        for item in costs
    ]
    fix = sorted(
        [item for item in costs if item.conclusion == "fix"],
        key=lambda item: item.priority, reverse=True)[:3]
    keep = sorted(
        [item for item in costs if item.conclusion == "keep"],
        key=lambda item: item.priority, reverse=True)[:2]
    watch = sorted(
        [item for item in costs
         if item.conclusion in ("observe", "insufficient")],
        key=lambda item: item.priority, reverse=True)[:3]
    main_goal = (
        _goal_for(fix[0]) if fix
        else "继续积累同口径完整棋局，先不强行改变当前棋风。")
    keep_rows = [{
        "key": item.dimension_key,
        "label": item.dimension_label,
        "evidence": item.keep_reason,
        "action": item.suggested_action,
        "representative_moves": item.representative_moves,
    } for item in keep]
    fix_rows = [{
        "key": item.dimension_key,
        "label": item.dimension_label,
        "evidence": item.fix_reason,
        "action": item.suggested_action,
        "representative_moves": item.representative_moves,
        "priority": item.priority,
    } for item in fix]
    watch_rows = [{
        "key": item.dimension_key,
        "label": item.dimension_label,
        "reason": (
            "样本不足，暂不作为主要结论。"
            if item.conclusion == "insufficient"
            else "当前成本中等，继续观察。"),
    } for item in watch]
    positions = []
    for item in fix_rows + keep_rows:
        for move in item.get("representative_moves") or []:
            row = dict(move)
            row["conclusion_key"] = item["key"]
            row["conclusion_label"] = item["label"]
            positions.append(row)
    positions.sort(
        key=lambda move: float(
            move.get("score_loss", move.get("scoreLoss", 0.0)) or 0.0),
        reverse=True)
    verification = [{
        "key": item["key"],
        "label": item["label"],
        "representative_moves": item["representative_moves"],
    } for item in fix_rows if item.get("representative_moves")]
    warnings = list(getattr(style_profile, "warnings", []) or [])
    if not fix:
        warnings.append("当前没有达到样本与成本门槛的主修正项。")
    return GrowthPath(
        profile_id=getattr(style_profile, "profile_id", "default"),
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        stage_title="保留低成本倾向，优先修正一个高成本习惯",
        main_goal=main_goal,
        keep_styles=keep_rows,
        fix_habits=fix_rows,
        watch_items=watch_rows,
        next_review_focus=[
            item["label"] for item in fix_rows[:3]]
            or ["继续积累完整分析棋局"],
        recommended_positions=positions[:10],
        verification_required=verification,
        confidence=getattr(style_profile, "confidence", "low"),
        warnings=warnings,
    )


def apply_verified_findings(growth_path, findings):
    """不稳定结论降级为观察项，并从主修正路线移除。"""
    unstable = {
        item.get("conclusion_key")
        for item in findings or []
        if item.get("stability") == "unstable"
    }
    if not unstable:
        return growth_path
    removed = [
        item for item in growth_path.fix_habits if item.get("key") in unstable]
    growth_path.fix_habits = [
        item for item in growth_path.fix_habits if item.get("key") not in unstable]
    for item in removed:
        growth_path.watch_items.insert(0, {
            "key": item.get("key"),
            "label": item.get("label"),
            "reason": "高强度复核后代表样本不稳定，降为继续观察。",
        })
    if growth_path.fix_habits:
        first = growth_path.fix_habits[0]
        growth_path.main_goal = (
            "围绕%s复盘代表局面，降低高成本选择。" % first.get("label"))
    else:
        growth_path.main_goal = (
            "关键修正项复核后证据不足，继续积累同口径棋局并观察。")
        growth_path.confidence = "low"
    return growth_path

