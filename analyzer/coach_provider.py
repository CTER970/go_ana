"""coach_provider —— 教练解释 Provider 层（项目大纲 §33-37、M8）。

架构：

    KataGo → EvidencePacket → DeterministicCoach（先落地）
                                  ↘ 未来 LLM Provider（可选）
                                       → 校验器 → 用户

铁律：
- 语言模型（未来 Provider）绝不进入客观评价链：目数/胜率/候选必须来自
  EvidencePacket，回答后经 validate_against_packet 程序校验，不一致即
  拒绝并回退确定性解释（数字幻觉基本归零）；
- DeterministicCoach 只复述数据包里的事实，证据不足时明确说不足，不编
  棋理（延续 evidence_explanation.py 的克制原则）；
- 无 API / API 失败 / 输出非法 / 用户关闭联网 → 一律回退确定性解释，
  LLM 增强产品但不成为运行必要条件。
"""
from __future__ import annotations

import re

from coach_schema import (
    SCHEMA_VERSION, empty_explanation, validate_explanation,
)
from evidence_packet import packet_facts

_COORD_RE = re.compile(r"[A-Tt]\d{1,2}")
_IDENT_RE = re.compile(r"rank_\w+")     # 档位名 rank_1d 等是标识符，不是数值断言
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


class CoachProvider:
    """Provider 接口：explain(evidence_packet) → CoachExplanation dict。

    未来实现（OpenAIProvider / ClaudeProvider / LocalModelProvider…）
    只能以 EvidencePacket 为输入，输出必须能通过 coach_schema 校验。
    """

    name = "base"

    def explain(self, evidence_packet):
        raise NotImplementedError

    def available(self):
        return False


class DeterministicCoach(CoachProvider):
    """确定性教练：只整理数据包事实，不调用任何外部模型。"""

    name = "deterministic"

    def available(self):
        return True

    def explain(self, evidence_packet):
        packet = evidence_packet or {}
        out = empty_explanation()
        out["source"] = "deterministic"
        refs = []
        g = packet.get

        # ---- 分类：从确定性标签映射（taxonomy 同源）----
        tags = list(g("deterministic_tags") or [])
        from taxonomy import classify_problem
        classification = classify_problem({"problem_tags": tags})
        out["mistake_category"] = classification["primary_category"]
        out["category_confidence"] = classification["category_confidence"]
        refs.append("deterministic_tags")

        # ---- 摘要 / 事实 ----
        loss = g("score_loss")
        best = g("best_move") or "—"
        played = g("played_move") or "—"
        if loss is not None:
            out["summary"] = "第%s手 %s 相对 AI 首选 %s 损失约 %.1f 目。" % (
                g("move_no", "?"), played, best, float(loss))
            refs.append("score_loss")
        else:
            out["summary"] = "第%s手：%s（目损数据不足）。" % (g("move_no", "?"), played)

        # ---- 发生了什么：双分支 > 候选差 > 数据不足 ----
        branch = g("branch_comparison") or {}
        if branch.get("verified"):
            actual = branch.get("actual") or {}
            ai = branch.get("ai") or {}
            out["what_happened"] = (
                "双分支深算（%d visits）：实战 %s 后局面价值 %s 目、胜率 %s；"
                "AI 首选 %s 后 %s 目、胜率 %s。" % (
                    branch.get("visits", 0),
                    actual.get("first_move") or "—",
                    _fmt(actual.get("score_lead")), _pct(actual.get("winrate")),
                    ai.get("first_move") or "—",
                    _fmt(ai.get("score_lead")), _pct(ai.get("winrate"))))
            refs.append("branch_comparison")
            summary = str(branch.get("summary") or "").strip()
            out["why_problematic"] = summary if summary else \
                "两条分支的数值差可复核（AI 多保留 %.1f 目）。" % float(
                    branch.get("score_gain") or 0.0)
            out["uncertainty"] = "medium"
        elif g("candidate_moves"):
            out["what_happened"] = "已有候选排序，但缺少双分支深算对比。"
            out["why_problematic"] = "现有证据只确认与首选存在数值差距，" \
                                     "暂不足以判断具体棋理根因。"
            out["uncertainty"] = "high"
            refs.append("candidate_moves")
        else:
            out["what_happened"] = "当前局面没有可引用的分析数据。"
            out["why_problematic"] = "证据不足，不做棋理判断。"
            out["uncertainty"] = "high"

        # ---- 可能原因：确定性层面不做心理推断（§34）----
        human = g("human_policy") or {}
        if human.get("prior_current") is not None and \
                human.get("prior_stronger") is not None:
            out["likely_reason"] = (
                "Human SL：本手在 %s 档出现率 %.0f%%，%s 档 %.0f%%"
                "——一种可能的解释是这属于当前棋力常见的选点倾向，"
                "仅供参考。") % (
                human.get("profile") or "?",
                float(human["prior_current"]) * 100,
                human.get("stronger_profile") or "?",
                float(human["prior_stronger"]) * 100)
            refs.append("human_policy")
            out["uncertainty"] = "medium" if out["uncertainty"] == "high" \
                else out["uncertainty"]
        else:
            out["likely_reason"] = "证据不足，无法给出可靠的心理/习惯归因。"

        # ---- 可迁移原则：只对高置信分类给（§36 克制）----
        if classification["category_confidence"] == "high":
            out["transferable_rule"] = _RULE_BY_CATEGORY.get(
                out["mistake_category"], "")
        else:
            out["transferable_rule"] = ""

        # ---- 合理候选：目损 ≤1.5 目的候选（来自数据包）----
        best_score = None
        for cand in g("candidate_moves") or []:
            if cand.get("order") == 0 and cand.get("score_lead") is not None:
                best_score = float(cand["score_lead"])
        if best_score is not None:
            sign = -1.0 if str(g("player_color") or "B").upper() == "W" else 1.0
            reasonable = []
            for cand in g("candidate_moves") or []:
                sc = cand.get("score_lead")
                if sc is None:
                    continue
                if sign * (best_score - float(sc)) <= 1.5:
                    reasonable.append(str(cand.get("move") or ""))
            out["reasonable_moves"] = [m for m in reasonable if m][:6]
            refs.append("candidate_moves")

        # ---- 短变化：AI 首选 PV ----
        for cand in g("candidate_moves") or []:
            if cand.get("order") == 0 and cand.get("pv"):
                out["short_variation"] = [str(m) for m in cand["pv"][:6]]
                break

        out["evidence_refs"] = refs
        ok, errors = validate_explanation(out)
        if not ok:                      # 确定性教练自身输出必须永远合法
            out = empty_explanation()
            out["summary"] = "（内部错误：%s）" % "; ".join(errors[:2])
        out["schema_version"] = SCHEMA_VERSION
        return out


_RULE_BY_CATEGORY = {
    "weak_groups": "攻击之前先比较双方谁更弱：自身未安定时不轻易发动攻击。",
    "attack_defense": "接触战前先确认攻击能否持续，必要时先补强自身。",
    "sente_tenuki": "脱先前先确认当前局部是否必须应答，评估先手价值。",
    "direction": "大场选择时先比较各方向的价值与发展潜力再落子。",
    "life_death": "对杀/死活先数清气与眼位，再决定攻击或做活顺序。",
    "reading": "落子前把关键变化算到底，不依赖感觉定型。",
    "endgame": "官子先手与逆收官子的先后次序按价值大小排序。",
    "whole_board": "局面判断先行：明确优势/劣势后再选择策略。",
    "shape": "避免凝形与重复，优先舒展高效的基本棋形。",
}


def _fmt(value):
    if value is None:
        return "—"
    try:
        return "%+.1f" % float(value)
    except (TypeError, ValueError):
        return "—"


def _pct(value):
    if value is None:
        return "—"
    try:
        return "%.0f%%" % (float(value) * 100)
    except (TypeError, ValueError):
        return "—"


# ===================== 对数据包的程序校验（§36）=====================

def _text_numbers(text):
    """提取文本中的数字（剔除棋盘坐标 D4/Q16 与档位名 rank_1d 等标识符）。"""
    text = _IDENT_RE.sub(" ", _COORD_RE.sub(" ", str(text or "")))
    out = []
    for token in _NUM_RE.findall(text):
        try:
            out.append(float(token))
        except ValueError:
            continue
    return out


def _fact_numbers(packet):
    facts = packet_facts(packet)
    numbers = set(facts["numbers"])
    # 百分比口径：0.55 的胜率允许写 55%
    normalized = set()
    for n in numbers:
        normalized.add(round(n * 100, 1))
        normalized.add(round(abs(n) * 100, 1))
    return numbers | normalized


def validate_against_packet(explanation, packet):
    """教练输出 × 数据包 事实核对（数字/选点/证据引用）。

    返回 (ok, issues)。任何幻觉数字、未经分析的推荐手、失实的证据引用
    都判不通过，由调用方回退确定性解释。
    """
    issues = []
    ok_schema, schema_errors = validate_explanation(explanation)
    if not ok_schema:
        return False, schema_errors
    explanation = explanation or {}
    facts = packet_facts(packet)
    known_moves = facts["moves"]
    known_numbers = _fact_numbers(packet)

    # 1) Move Hallucination：推荐手必须经过 KataGo 分析（§70）
    for move in explanation.get("reasonable_moves") or []:
        move = str(move or "").strip()
        if move and move not in known_moves:
            issues.append("推荐了未经分析的选点：%s" % move)
    for move in explanation.get("short_variation") or []:
        move = str(move or "").strip()
        if move and move not in known_moves:
            issues.append("变化图包含未经验证的选点：%s" % move)

    # 2) Numeric Hallucination：叙述里的数字必须能在数据包找到（§70）
    for field in ("summary", "what_happened", "why_problematic", "likely_reason"):
        for num in _text_numbers(explanation.get(field)):
            if round(num, 1) not in known_numbers and \
                    round(abs(num), 1) not in known_numbers:
                issues.append("%s 出现数据包没有的数字：%.1f" % (field, num))

    return (not issues), issues


def get_coach_explanation(packet, provider=None):
    """统一入口：Provider 优先 → 校验 → 失败回退确定性解释（§37）。"""
    if provider is not None and provider.available():
        try:
            raw = provider.explain(packet)
            ok, issues = validate_against_packet(raw, packet)
            if ok:
                raw.setdefault("schema_version", SCHEMA_VERSION)
                raw.setdefault("source", provider.name)
                return raw
        except Exception:
            pass
    return DeterministicCoach().explain(packet)
