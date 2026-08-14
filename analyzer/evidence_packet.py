"""evidence_packet —— 语言模型教练的唯一事实入口（项目大纲 §32-33）。

所有解释层（确定性讲解、LLM 教练）只能看到本模块产出的 EvidencePacket，
不允许各自从内部状态抓数据。数据包只整理调用方提供的客观证据；缺失的
字段保持缺省值，绝不推断补写——这是数字幻觉归零的第一道闸门。

字段分三类（项目大纲 §53、§90）：
- 事实（fact）：score/winrate/candidates，只来自 KataGo；
- 强推断（inference）：分支对比、意图、复发历史，来自本地分析链路；
- 用户自述（user）：当时想法按钮，用户主动提供。
"""
from __future__ import annotations

from learning_event import position_key_from_board

PACKET_VERSION = 1


def _clean_float(value):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f


def _candidate_list(move_infos, limit=8):
    """整理父局面候选（按 order），只保留 KataGo 原始数值。"""
    out = []
    for info in sorted(move_infos or [], key=lambda m: m.get("order", 999))[:limit]:
        out.append({
            "move": str(info.get("move") or "pass"),
            "order": int(info.get("order", 99)),
            "score_lead": _clean_float(info.get("scoreLead")),
            "winrate": _clean_float(info.get("winrate")),
            "prior": _clean_float(info.get("prior")),
            "visits": info.get("visits"),
            "pv": [str(m) for m in (info.get("pv") or [])[:8]],
        })
    return out


def build_evidence_packet(evaluation, move_infos=None, intent=None,
                          comparison=None, quality=None,
                          recurrence_history=None, human_priors=None,
                          user_intent=None, version_info=None, board=None):
    """从既有客观链路构建 EvidencePacket。

    ``evaluation`` 兼容 review.MoveEvaluation（鸭子类型取属性）。
    其余入参均为可选；没有的证据就是 None/[]，不编造。
    """
    evaluation = evaluation or {}
    g = lambda name, default=None: getattr(evaluation, name, default)
    comparison = comparison or {}
    intent = intent or {}
    quality = quality or None
    human_priors = human_priors or {}
    version_info = version_info or {}

    best_move = g("best_move") or comparison.get("aiMove") or ""
    played = g("coord") or "pass"

    packet = {
        "packet_version": PACKET_VERSION,
        # ---- 局面定位 ----
        "game_id": g("game_id", "") or "",
        "move_no": g("move_number", 0),
        "position_key": position_key_from_board(board),
        "player_color": g("color", "") or "",
        "board_size": g("board_size", 19),
        # ---- 事实： KataGo 客观数值 ----
        "played_move": "pass" if g("is_pass") else str(played or ""),
        "best_move": str(best_move or ""),
        "score_before": _clean_float(g("score_lead_before")),
        "score_after": _clean_float(g("score_lead_after")),
        "score_loss": _clean_float(g("loss")),
        "winrate_before": _clean_float(g("winrate_before")),
        "winrate_after": _clean_float(g("winrate_after")),
        "winrate_drop_pct": None if quality is None else _clean_float(
            getattr(quality, "winrate_drop", None)),
        "ai_rank": g("ai_rank"),
        "candidate_moves": _candidate_list(move_infos),
        "analysis_meta": {
            "katago_version": str(version_info.get("katago_version") or ""),
            "model_hash": str(version_info.get("model_hash") or ""),
            "visits": int(version_info.get("visits") or 0),
        },
        # ---- 强推断：本地分析链路 ----
        "branch_comparison": _branch_summary(comparison),
        "deterministic_tags": list(
            getattr(quality, "problem_tags", None)
            or g("problem_tags", []) or []),
        "human_policy": {
            "profile": str(human_priors.get("profile") or ""),
            "prior_current": _clean_float(human_priors.get("prior_current")),
            "prior_stronger": _clean_float(human_priors.get("prior_stronger")),
            "stronger_profile": str(human_priors.get("stronger_profile") or ""),
        },
        "recurrence_history": [dict(r) for r in (recurrence_history or [])],
        # ---- 用户自述 ----
        "user_intent": str(user_intent or intent.get("userIntent") or ""),
        "played_intent": str(intent.get("actualIntent") or ""),
        "ai_intent": str(intent.get("aiIntent") or ""),
    }
    return packet


def _branch_summary(comparison):
    """双分支对比的压缩视图（无数据时显式 verified=False）。"""
    if not comparison:
        return {"verified": False}
    def _side(side, move_key):
        branch = comparison.get(side) or {}
        return {
            "first_move": str(comparison.get(move_key) or branch.get("move") or ""),
            "winrate": _clean_float(branch.get("winrate")),
            "score_lead": _clean_float(branch.get("score")),
            "pv": [str(m) for m in (branch.get("pv") or [])[:10]],
        }
    return {
        "verified": True,
        "visits": int(comparison.get("visits") or 0),
        "actual": _side("actual", "actualMove"),
        "ai": _side("ai", "aiMove"),
        "score_gain": _clean_float(comparison.get("scoreGain")),
        "winrate_gain_pct": _clean_float(comparison.get("winrateGainPct")),
        "summary": str(comparison.get("summary") or ""),
    }


def packet_facts(packet):
    """数据包里全部可引用数字/选点（LLM 校验器用：回答中的数字必须在此）。"""
    packet = packet or {}
    facts = set()
    for key in ("score_before", "score_after", "score_loss",
                "winrate_before", "winrate_after", "winrate_drop_pct"):
        value = packet.get(key)
        if value is not None:
            facts.add(round(float(value), 1))
            facts.add(round(float(value), 2))
            facts.add(abs(round(float(value), 1)))
            facts.add(abs(round(float(value), 2)))
    moves = {packet.get("best_move", ""), packet.get("played_move", "")}
    moves |= {c.get("move", "") for c in packet.get("candidate_moves") or []}
    branch = packet.get("branch_comparison") or {}
    if branch.get("verified"):
        for side in ("actual", "ai"):
            info = branch.get(side) or {}
            if info.get("first_move"):
                moves.add(info["first_move"])
            for value in (info.get("winrate"), info.get("score_lead")):
                if value is not None:
                    facts.add(round(float(value), 1))
                    facts.add(abs(round(float(value), 1)))
    return {"numbers": facts, "moves": {m for m in moves if m}}
