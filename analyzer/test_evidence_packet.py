"""test_evidence_packet —— EvidencePacket 构建、克制性与事实索引测试。"""
import os
import sys
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from evidence_packet import build_evidence_packet, packet_facts


def check(name, cond, extra=""):
    print("[CHECK] %-40s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        raise AssertionError(name)


def run():
    ev = SimpleNamespace(
        game_id="g1", move_number=82, color="B", coord="R10", is_pass=False,
        score_lead_before=1.2, score_lead_after=-5.1, loss=6.3,
        winrate_before=0.55, winrate_after=0.41, best_move="P9", ai_rank=7,
        board_size=19,
    )
    quality = SimpleNamespace(
        winrate_drop=14.0, problem_tags=["overplay", "weak_group"])
    move_infos = [
        {"move": "P9", "order": 0, "scoreLead": 1.2, "winrate": 0.55,
         "prior": 0.42, "visits": 120, "pv": ["P9", "Q10", "R8"]},
        {"move": "Q11", "order": 1, "scoreLead": 0.4, "winrate": 0.53,
         "prior": 0.21, "visits": 60, "pv": ["Q11"]},
    ]
    comparison = {
        "actualMove": "R10", "aiMove": "P9", "visits": 800,
        "scoreGain": 6.1, "winrateGainPct": 12.5,
        "summary": "实战攻击落空",
        "actual": {"winrate": 0.41, "score": -5.1, "pv": ["R10", "S11"]},
        "ai": {"winrate": 0.55, "score": 1.2, "pv": ["P9", "Q10"]},
    }
    packet = build_evidence_packet(
        ev, move_infos=move_infos, quality=quality, comparison=comparison,
        human_priors={"profile": "rank_1d", "prior_current": 0.31,
                      "prior_stronger": 0.06, "stronger_profile": "rank_3d"},
        recurrence_history=[{"game_id": "g0", "move_no": 51}],
        version_info={"katago_version": "1.15.3", "visits": 200},
    )
    check("事实字段逐项落位",
          packet["score_loss"] == 6.3 and packet["best_move"] == "P9"
          and packet["played_move"] == "R10")
    check("候选按 order 整理",
          [c["move"] for c in packet["candidate_moves"]] == ["P9", "Q11"]
          and packet["candidate_moves"][0]["prior"] == 0.42)
    check("双分支 verified",
          packet["branch_comparison"]["verified"] is True
          and packet["branch_comparison"]["score_gain"] == 6.1)
    check("Human SL 概率原样传递",
          packet["human_policy"]["prior_current"] == 0.31
          and packet["human_policy"]["stronger_profile"] == "rank_3d")
    check("确定性标签与复发历史", packet["deterministic_tags"] == [
        "overplay", "weak_group"] and len(packet["recurrence_history"]) == 1)
    check("分析版本随包", packet["analysis_meta"]["katago_version"] == "1.15.3")

    # 克制性：什么都不给时不得编造
    empty = build_evidence_packet(None)
    check("空输入不编造",
          empty["score_loss"] is None and empty["candidate_moves"] == []
          and empty["branch_comparison"] == {"verified": False}
          and empty["human_policy"]["prior_current"] is None)
    check("pass 走子表达", build_evidence_packet(
        SimpleNamespace(is_pass=True, coord=None))["played_move"] == "pass")

    # 事实索引（校验器用）
    facts = packet_facts(packet)
    check("数字进入事实集", 6.3 in facts["numbers"] and 1.2 in facts["numbers"])
    check("选点进入事实集",
          {"P9", "R10", "Q11"} <= facts["moves"])
    empty_facts = packet_facts(None)
    check("空包事实集为空", empty_facts["numbers"] == set())

    print("test_evidence_packet: 全部通过")


if __name__ == "__main__":
    run()
