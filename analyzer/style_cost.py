"""棋风倾向的成本分类：只基于可见统计证据，不贴固定流派标签。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

from style_profile import StyleDimension, StyleProfile

VERSION = 1


@dataclass
class StyleCostResult:
    dimension_key: str
    dimension_label: str
    tendency_level: str = "unknown"
    cost_level: str = "unknown"
    conclusion: str = "insufficient"
    keep_reason: Optional[str] = None
    fix_reason: Optional[str] = None
    avg_score_loss: Optional[float] = None
    frequency_per_100: float = 0.0
    severe_count: int = 0
    confidence: str = "low"
    representative_moves: list = field(default_factory=list)
    suggested_action: str = ""
    recent_trend: str = "insufficient"
    priority: float = 0.0
    version: int = VERSION

    def to_dict(self):
        return asdict(self)


def tendency_level(frequency_per_100):
    value = float(frequency_per_100 or 0.0)
    if value >= 8.0:
        return "high"
    if value >= 3.0:
        return "medium"
    if value > 0:
        return "low"
    return "unknown"


def cost_level(avg_score_loss, blunder_rate, inaccuracy_rate):
    """按平均目损与恶手率/不佳率分级成本。

    单位约定（重要，勿误读为百分比）：
    - ``avg_score_loss``：目（绝对目损，如 5.0 表示 5 目）。
    - ``blunder_rate`` / ``inaccuracy_rate``：**0-1 比例**（来自
      ``style_profile._build_dimension`` 的 ``count/sample_count``），
      展示时需 ``* 100.0`` 转成百分比（见 ``style_report``）。
      因此阈值 ``0.15`` 表示 15%、``0.25`` 表示 25%，比例语义正确。
    """
    if avg_score_loss is None:
        return "unknown"
    if float(avg_score_loss) >= 5.0 or float(blunder_rate or 0.0) >= 0.15:
        return "high_cost"
    if (float(avg_score_loss) >= 2.5
            or float(inaccuracy_rate or 0.0) >= 0.25):
        return "medium_cost"
    return "low_cost"


def _action(key):
    return {
        "territory_preference": "保留低成本的实地确认，同时在布局中比较一次全局大场。",
        "influence_preference": "选择外势时记录后续兑现方式，避免只获得难以利用的厚势。",
        "fighting_preference": "求战前先比较双方薄弱处和最坏应手，再决定是否进入战斗。",
        "stability_preference": "保持低风险候选比较，不必为了接近 AI 而强行改变稳健选择。",
        "tenuki_tendency": "接触战中先确认局部双方棋形安定，再考虑脱先。",
        "advantage_pressure": "领先时优先比较低风险收束候选，再考虑继续施压。",
        "comeback_complexity": "落后时区分必要复杂化与无根据过分，优先寻找有后续的胜负手。",
        "endgame_safety": "官子阶段先比较先后手和逆收价值，减少连续小亏。",
    }.get(key, "复盘代表局面，比较实战与 AI 候选的收益和风险。")


def classify_style_cost(dimension):
    if isinstance(dimension, dict):
        dimension = StyleDimension.from_dict(dimension)
    tendency = tendency_level(dimension.frequency_per_100)
    cost = cost_level(
        dimension.avg_score_loss,
        dimension.blunder_rate,
        dimension.inaccuracy_rate)
    if (dimension.confidence == "low" or dimension.sample_count < 5
            or cost == "unknown"):
        conclusion = "insufficient"
    elif tendency in ("medium", "high") and cost == "low_cost":
        conclusion = "keep"
    elif tendency in ("medium", "high") and cost == "high_cost":
        conclusion = "fix"
    elif cost == "medium_cost":
        conclusion = "observe"
    else:
        conclusion = "observe"
    severe = sum(
        1 for move in dimension.representative_moves
        if move.get("quality_key", move.get("qualityKey")) == "blunder")
    keep_reason = None
    fix_reason = None
    if conclusion == "keep":
        keep_reason = (
            "该倾向每百手出现 %.1f 次，平均目损 %.2f，"
            "当前样本中成本较低，可以保留。"
            % (dimension.frequency_per_100, dimension.avg_score_loss or 0.0))
    elif conclusion == "fix":
        fix_reason = (
            "该倾向每百手出现 %.1f 次，平均目损 %.2f，"
            "在当前样本的特定场景中代价较高。"
            % (dimension.frequency_per_100, dimension.avg_score_loss or 0.0))
    priority = dimension.frequency_per_100
    priority += {"high_cost": 12.0, "medium_cost": 5.0}.get(cost, 0.0)
    if dimension.recent_trend == "worsening":
        priority += 4.0
    return StyleCostResult(
        dimension_key=dimension.key,
        dimension_label=dimension.label,
        tendency_level=tendency,
        cost_level=cost,
        conclusion=conclusion,
        keep_reason=keep_reason,
        fix_reason=fix_reason,
        avg_score_loss=dimension.avg_score_loss,
        frequency_per_100=dimension.frequency_per_100,
        severe_count=severe,
        confidence=dimension.confidence,
        representative_moves=list(dimension.representative_moves),
        suggested_action=_action(dimension.key),
        recent_trend=dimension.recent_trend,
        priority=priority,
    )


def build_style_costs(style_profile):
    if isinstance(style_profile, dict):
        dimensions = [
            StyleDimension.from_dict(item)
            for item in style_profile.get("dimensions") or []]
    else:
        dimensions = style_profile.dimensions
    return [classify_style_cost(item) for item in dimensions]


def attach_style_costs(style_profile, costs):
    """写入成本结论并生成克制的摘要文案。"""
    style_profile.cost_results = list(costs)
    keep = [item for item in costs if item.conclusion == "keep"]
    fix = [item for item in costs if item.conclusion == "fix"]
    uncertain = [
        item for item in costs
        if item.conclusion in ("observe", "insufficient")]
    keep.sort(key=lambda item: item.priority, reverse=True)
    fix.sort(key=lambda item: item.priority, reverse=True)
    style_profile.strengths = [
        item.keep_reason for item in keep[:3] if item.keep_reason]
    style_profile.high_cost_habits = [
        item.fix_reason for item in fix[:3] if item.fix_reason]
    style_profile.uncertain_findings = [
        "%s：%s" % (
            item.dimension_label,
            "样本不足，暂不作为主要结论。"
            if item.conclusion == "insufficient"
            else "当前成本中等，建议继续观察。")
        for item in uncertain[:4]
    ]
    if fix:
        style_profile.style_summary = (
            "从当前样本看，%s是较稳定的低成本倾向；当前更值得关注%s。"
            % (
                "、".join(item.dimension_label for item in keep[:2])
                if keep else "部分选择",
                "、".join(item.dimension_label for item in fix[:2])))
    elif keep:
        style_profile.style_summary = (
            "从当前样本看，%s出现较稳定且平均损失较低，可以作为当前风格保留。"
            % "、".join(item.dimension_label for item in keep[:2]))
    else:
        style_profile.style_summary = (
            "当前样本尚不足以形成稳定棋风结论，先继续积累同口径完整棋局。")
    return style_profile

