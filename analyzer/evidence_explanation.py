"""关键问题手的可验证三段式讲解。

本模块只整理本地已有的 KataGo 数值、棋形意图和双分支结果；不会调用
联网模型，也不会在证据缺失时补写棋理断言。
"""
from __future__ import annotations


def _tags(quality):
    raw = list(getattr(quality, "problem_tags", None) or [])
    try:
        from move_quality import PROBLEM_TAGS
        return [PROBLEM_TAGS.get(tag, tag) for tag in raw]
    except Exception:
        return raw


def build_evidence_explanation(evaluation, intent=None, quality=None, comparison=None):
    """返回可序列化的三段式讲解及证据清单。"""
    intent = intent or {}
    comparison = comparison or {}
    loss = getattr(evaluation, "loss", None)
    wr_loss = None
    if comparison:
        wr_loss = comparison.get("winrateGainPct")

    root_bits = []
    if comparison.get("diagnosis"):
        root_bits.append(str(comparison["diagnosis"]).strip())
    elif intent.get("difference"):
        root_bits.append(str(intent["difference"]).strip())
    tag_names = _tags(quality)
    if tag_names:
        root_bits.append("结构化评价标签：%s。" % "、".join(tag_names[:3]))
    if not root_bits:
        root_bits.append("现有证据只确认这手与 AI 首选存在数值差距，暂不足以判断具体棋理根因。")

    ai_purpose = intent.get("aiIntent")
    if not ai_purpose:
        ai_move = getattr(evaluation, "best_move", None) or comparison.get("aiMove") or "pass"
        ai_pv = ((comparison.get("ai") or {}).get("pv") or [])[:6]
        ai_purpose = "AI 首选为 %s。" % ai_move
        if ai_pv:
            ai_purpose += " 可核对变化：%s。" % " → ".join(ai_pv)
        else:
            ai_purpose += "当前没有足够棋形或主变证据解释其具体目的。"

    consequence = []
    if loss is not None:
        consequence.append("实战相对首选损失 %.1f 目" % float(loss))
    if wr_loss is not None:
        consequence.append("双分支深算相差 %.1f 个胜率百分点" % float(wr_loss))
    if comparison.get("controlGain") is not None:
        consequence.append("稳定控制点差 %+d" % int(comparison.get("controlGain") or 0))
    if comparison:
        actual_pv = ((comparison.get("actual") or {}).get("pv") or [])[:6]
        ai_pv = ((comparison.get("ai") or {}).get("pv") or [])[:6]
        if actual_pv and ai_pv:
            consequence.append("可在“实战变化 / AI 变化”中逐手核对两条主变")
    if not consequence:
        consequence.append("尚缺双分支深算，当前只能确认候选排序，不能量化后续差异")

    evidence = []
    if loss is not None:
        evidence.append("基础分析：目损 %.1f" % float(loss))
    quality_loss = getattr(quality, "winrate_drop", None)
    if quality_loss is not None:
        evidence.append("基础分析：胜率损失 %.1f 个百分点" % float(quality_loss))
    if comparison:
        evidence.append("双分支：%d visits" % int(comparison.get("visits") or 0))
        evidence.append("双分支：AI 多保留 %.1f 目" % float(comparison.get("scoreGain") or 0.0))
        evidence.append("双分支：胜率差 %.1f 个百分点" % float(comparison.get("winrateGainPct") or 0.0))
    if intent:
        evidence.append("棋形模拟：实战 %s / AI %s" % (
            intent.get("actualMove", "?"), intent.get("aiMove", "?")))

    return {
        "version": 1,
        "rootCause": "".join(root_bits),
        "aiPurpose": str(ai_purpose),
        "actualConsequence": "；".join(consequence) + "。",
        "evidence": evidence,
        "verified": bool(comparison),
        "disclaimer": (
            "双分支已完成，可按数值与主变复核。" if comparison else
            "当前为基础分析与棋形推断；完成双分支深算后再确认。"),
    }


def format_evidence_explanation(result):
    result = result or {}
    evidence = "；".join(result.get("evidence") or ["暂无可列出的结构化证据"])
    return (
        "【1 · 问题根因】\n%s\n\n"
        "【2 · AI 手目的】\n%s\n\n"
        "【3 · 实战后果 / 核心差异】\n%s\n\n"
        "证据：%s\n说明：%s"
    ) % (
        result.get("rootCause", "—"),
        result.get("aiPurpose", "—"),
        result.get("actualConsequence", "—"),
        evidence,
        result.get("disclaimer", ""),
    )
