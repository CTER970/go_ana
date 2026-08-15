"""test_coach —— Coach schema、确定性教练克制性、程序校验与回退测试。"""
import os
import sys
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from coach_provider import (
    DeterministicCoach, CoachProvider, get_coach_explanation,
    validate_against_packet,
)
from coach_schema import empty_explanation, validate_explanation
from evidence_packet import build_evidence_packet


def check(name, cond, extra=""):
    print("[CHECK] %-46s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        raise AssertionError(name)


def _packet(**kw):
    ev = SimpleNamespace(
        game_id="g1", move_number=82, color="B", coord="R10", is_pass=False,
        score_lead_before=1.2, score_lead_after=-5.1, loss=6.3,
        winrate_before=0.55, winrate_after=0.41, best_move="P9", ai_rank=7,
        board_size=19, **kw)
    return build_evidence_packet(
        ev,
        move_infos=[
            {"move": "P9", "order": 0, "scoreLead": 1.2, "winrate": 0.55,
             "prior": 0.42, "visits": 120, "pv": ["P9", "Q10", "R8"]},
            {"move": "Q11", "order": 1, "scoreLead": 0.4, "winrate": 0.53,
             "prior": 0.21, "visits": 60, "pv": ["Q11"]},
            {"move": "R10", "order": 5, "scoreLead": -5.1, "winrate": 0.41,
             "prior": 0.08, "visits": 30, "pv": ["R10", "S11"]},
        ],
        quality=SimpleNamespace(winrate_drop=14.0,
                                problem_tags=["overplay", "weak_group"]),
        comparison={
            "actualMove": "R10", "aiMove": "P9", "visits": 800,
            "scoreGain": 6.1, "winrateGainPct": 12.5, "summary": "实战攻击落空",
            "actual": {"winrate": 0.41, "score": -5.1, "pv": ["R10", "S11"]},
            "ai": {"winrate": 0.55, "score": 1.2, "pv": ["P9", "Q10"]},
        },
    )


def run():
    packet = _packet()

    # schema
    ok, errors = validate_explanation(empty_explanation())
    check("空模板本身合法", ok, str(errors))
    bad = empty_explanation(); bad.pop("summary"); bad["uncertainty"] = "sure"
    ok2, errors2 = validate_explanation(bad)
    check("缺字段+非法枚举被拒", not ok2 and len(errors2) == 2, str(errors2))

    # 确定性教练：输出合法、事实克制
    coach = DeterministicCoach()
    exp = coach.explain(packet)
    ok3, errors3 = validate_explanation(exp)
    check("确定性教练输出合法", ok3, str(errors3))
    ok4, issues4 = validate_against_packet(exp, packet)
    check("确定性教练零幻觉", ok4, str(issues4))
    check("摘要只复述数据包数字",
          "6.3" in exp["summary"] and "P9" in exp["summary"], exp["summary"])
    check("合理候选只来自候选表",
          set(exp["reasonable_moves"]) <= {"P9", "Q11", "R10"},
          str(exp["reasonable_moves"]))
    check("变化图取自首选 PV", exp["short_variation"] == ["P9", "Q10", "R8"])
    check("分类来自确定性标签",
          exp["mistake_category"] in ("attack_defense", "weak_groups"),
          exp["mistake_category"])
    check("非高置信不给可迁移原则", exp["transferable_rule"] == "")

    # 证据不足的克制：空包不编
    empty_exp = coach.explain(build_evidence_packet(None))
    check("空包明确证据不足",
          "不足" in empty_exp["why_problematic"]
          and empty_exp["uncertainty"] == "high"
          and empty_exp["reasonable_moves"] == [])
    ok5, _ = validate_against_packet(empty_exp, build_evidence_packet(None))
    check("空包解释也零幻觉", ok5)

    # Human SL 佐证：可能原因写成"一种可能的解释"，不写事实断言
    packet_h = _packet()
    packet_h["human_policy"] = {
        "profile": "rank_1d", "prior_current": 0.31,
        "stronger_profile": "rank_3d", "prior_stronger": 0.06}
    exp_h = coach.explain(packet_h)
    check("心理推断只用可能语气",
          "一种可能" in exp_h["likely_reason"], exp_h["likely_reason"])
    ok6, _ = validate_against_packet(exp_h, packet_h)
    check("Human SL 百分比数字可核对", ok6)

    # 程序校验：抓数字幻觉 / 选点幻觉
    liar = dict(empty_explanation())
    liar.update(summary="这手大约亏 9.9 目。", reasonable_moves=["Z99"])
    ok7, issues7 = validate_against_packet(liar, packet)
    check("编造目数被拒", any("数字" in i for i in issues7), str(issues7))
    check("未分析选点被拒", any("Z99" in i for i in issues7))
    # 百分比口径允许（0.55 → 55%）
    pct = dict(empty_explanation())
    pct.update(summary="此前黑方胜率约 55%。")
    ok8, issues8 = validate_against_packet(pct, packet)
    check("百分比口径换算被接受", ok8, str(issues8))
    # 坐标里的数字不算叙述数字
    coord = dict(empty_explanation())
    coord.update(summary="实战下在 D4。")
    ok9, _ = validate_against_packet(coord, packet)
    check("坐标数字不误报", ok9)

    # 回退：Provider 不可用/输出非法 → 确定性
    check("基类 Provider 不可用", not CoachProvider().available())
    class LiarProvider(CoachProvider):
        name = "liar"
        def available(self): return True
        def explain(self, packet): return liar
    fallback = get_coach_explanation(packet, LiarProvider())
    check("非法 Provider 输出回退确定性",
          fallback["source"] == "deterministic", str(fallback["source"]))
    direct = get_coach_explanation(packet)
    check("无 Provider 直接走确定性", direct["source"] == "deterministic")

    print("test_coach: 全部通过")


if __name__ == "__main__":
    run()
