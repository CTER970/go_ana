"""test_problem_drill —— 涨棋网风格问题手训练钻取的纯逻辑测试（无 tkinter / 无 KataGo）。"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from review import MoveEvaluation
from problem_drill import (
    build_problem_drill, build_drill_move, grade_quiz, new_drill_result,
    quality_label_for_loss, QUALITY_BEST, QUALITY_GOOD, QUALITY_INACCURACY,
    OUT_OF_REACH_MAX_POLICY,
)


def _mi(move, order, winrate, score, visits, prior, pv=None):
    return {"move": move, "order": order, "winrate": winrate,
            "scoreLead": score, "visits": visits, "prior": prior,
            "pv": pv or [move]}


def _ev(move_no, color, coord, *, is_pass=False, loss=5.0,
        wr_before=0.55, wr_after=0.40, sl_before=3.0, sl_after=-2.0,
        best_move="Q16", analyzed=True):
    return MoveEvaluation(
        node_nid=move_no, move_number=move_no, color=color, coord=coord,
        is_pass=is_pass, score_lead_before=sl_before, score_lead_after=sl_after,
        winrate_before=wr_before, winrate_after=wr_after, best_move=best_move,
        best_score_lead=sl_before, loss=loss, analyzed=analyzed)


def _infos():
    return [_mi("Q16", 0, 0.55, 3.0, 5000, 0.20, ["Q16", "D4", "R16"]),
            _mi("D4", 1, 0.54, 2.5, 3000, 0.15, ["D4", "Q16"]),
            _mi("R16", 2, 0.53, 2.0, 1500, 0.10, ["R16"])]


def check(name, cond, extra=""):
    print("[CHECK] %-40s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        raise AssertionError(name)


def test_quality_labels():
    check("best loss→最佳", quality_label_for_loss(0.0, is_best=True) == QUALITY_BEST)
    check("0.5→好手", quality_label_for_loss(0.5) == QUALITY_GOOD)
    check("2.0→一般", quality_label_for_loss(2.0) == "一般")
    check("4.0→欠佳", quality_label_for_loss(4.0) == QUALITY_INACCURACY)
    check("7.0→恶手", quality_label_for_loss(7.0) == "恶手")


def test_empty_drill():
    d = build_problem_drill([], {}, user_color="B")
    check("空 evaluations → is_empty", d.is_empty)
    check("空 → 有 warning", len(d.warnings) >= 1)

    # 有评价但都不达阈值
    e = _ev(4, "B", "Q4", loss=0.5)
    d2 = build_problem_drill([e], {4: _infos()}, user_color="B")
    check("未达阈值 → is_empty", d2.is_empty)


def test_build_one_move():
    e = _ev(4, "B", "Q4")
    d = build_problem_drill([e], {4: _infos()}, user_color="B")
    check("构建 1 题", len(d.moves) == 1, str(len(d.moves)))
    m = d.moves[0]
    check("4 个候选(3 AI + 实战)", len(m.candidates) == 4, str(len(m.candidates)))
    check("第 1 候选 key c0", m.candidates[0].key == "c0")
    check("一选 = 最佳", m.candidates[0].quality_label == QUALITY_BEST)
    check("二选目损 0.5", abs(m.candidates[1].score_loss - 0.5) < 1e-6, str(m.candidates[1].score_loss))
    check("二选 = 好手", m.candidates[1].quality_label == QUALITY_GOOD)
    check("实战 key actual", m.actual_candidate is not None)
    check("实战 visits=0(不在候选)", m.actual_candidate.visits == 0)
    check("实战 policy=0", m.actual_candidate.policy == 0.0)
    check("实战目损=eval.loss=5", abs(m.actual_candidate.score_loss - 5.0) < 1e-6)
    check("实战评级=欠佳", m.actual_candidate.quality_label == QUALITY_INACCURACY)
    check("实战胜率损失=15pp", abs(m.actual_candidate.winrate_loss - 15.0) < 1e-6,
          str(m.actual_candidate.winrate_loss))
    check("best_move=Q16", m.best_move == "Q16")
    check("失败图含实战", m.variations["失败图"][0] in ("Q4",))
    check("正解图=一选PV", m.variations["正解图"][0] == "Q16")
    check("二选变化", m.variations["二选"][0] == "D4")
    check("三选变化", m.variations["三选"][0] == "R16")


def test_quiz_letters_cover_all():
    e = _ev(4, "B", "Q4")
    d = build_problem_drill([e], {4: _infos()}, user_color="B")
    m = d.moves[0]
    # quiz_order 覆盖全部 4 个候选 key，且每个候选都有唯一字母
    check("quiz 长度=候选数", len(m.quiz_order) == len(m.candidates))
    keys = set(m.quiz_order)
    check("quiz 覆盖全部 key", keys == {c.key for c in m.candidates}, str(keys))
    # 字母映射：每个 key 都能取到字母，且互不相同
    letters = [m.letter_of(k) for k in m.quiz_order]
    check("字母唯一", len(set(letters)) == len(letters), str(letters))
    check("字母从 A 开始连续", sorted(letters) == list("ABCD"), str(sorted(letters)))
    # letter_of / key_of 互逆
    for k in m.quiz_order:
        check("互逆 %s" % k, m.key_of(m.letter_of(k)) == k)
    # quiz 顺序与逻辑顺序不同（确实乱序了）
    check("确实乱序", m.quiz_order != [c.key for c in m.candidates] or len(m.candidates) <= 2)


def test_grade_quiz():
    e = _ev(4, "B", "Q4")
    d = build_problem_drill([e], {4: _infos()}, user_color="B")
    m = d.moves[0]
    best_letter = m.letter_of("c0")
    actual_letter = m.letter_of("actual")
    check("选一选字母→正确", grade_quiz(m, best_letter)["isCorrect"] is True)
    check("选实战字母→错误", grade_quiz(m, actual_letter)["isCorrect"] is False)
    check("选实战→isActual", grade_quiz(m, actual_letter)["isActual"] is True)
    check("非法字母→错误", grade_quiz(m, "Z")["isCorrect"] is False)
    check("非法字母→chosenKey None", grade_quiz(m, "Z")["chosenKey"] is None)


def test_actual_in_moveinfos():
    # 实战恰好是二选：应拿到真实 visits/policy/pv
    infos = _infos()
    e = _ev(4, "B", "D4", loss=3.0, wr_after=0.54, sl_after=2.5)
    d = build_problem_drill([e], {4: infos}, user_color="B")
    m = d.moves[0]
    ac = m.actual_candidate
    check("实战在候选→visits 真实", ac.visits == 3000, str(ac.visits))
    check("实战在候选→policy 真实", abs(ac.policy - 0.15) < 1e-6, str(ac.policy))
    check("实战在候选→pv 真实", ac.pv[0] == "D4")


def test_pass_actual():
    e = _ev(4, "B", None, is_pass=True, loss=5.0)
    d = build_problem_drill([e], {4: _infos()}, user_color="B")
    m = d.moves[0]
    check("pass 实战 move=pass", m.actual_candidate.move == "pass")
    check("pass 实战 coord=pass", m.actual_candidate.coord == "pass")
    check("pass 失败图=[pass]", m.variations["失败图"] == ["pass"], str(m.variations["失败图"]))


def test_out_of_reach():
    # 一选先验极低 + 大目损 → 超纲
    infos = [_mi("Q16", 0, 0.55, 3.0, 200, 0.02, ["Q16"]),
             _mi("D4", 1, 0.50, 0.0, 100, 0.01, ["D4"])]
    e = _ev(7, "B", "Q4", loss=5.0)
    d = build_problem_drill([e], {7: infos}, user_color="B")
    check("超纲判定", d.moves[0].is_out_of_reach is True)
    check("超纲进入 out_of_reach 列表",
          any(it["move"] == 7 for it in d.out_of_reach))

    # 高先验 → 非超纲
    infos2 = [_mi("Q16", 0, 0.55, 3.0, 5000, 0.20, ["Q16"]), _mi("D4", 1, 0.50, 0.0, 100, 0.10, ["D4"])]
    d2 = build_problem_drill([_ev(7, "B", "Q4", loss=5.0)], {7: infos2}, user_color="B")
    check("高先验→非超纲", d2.moves[0].is_out_of_reach is False)


def test_user_color_filter():
    eb = _ev(4, "B", "Q4", loss=5.0)
    ew = _ev(5, "W", "Q16", loss=5.0, wr_before=0.45, wr_after=0.30, sl_before=-3.0, sl_after=2.0, best_move="Q16")
    infos = {4: _infos(), 5: _infos()}
    db = build_problem_drill([eb, ew], infos, user_color="B")
    check("只取黑方", len(db.moves) == 1 and db.moves[0].color == "B")
    dw = build_problem_drill([eb, ew], infos, user_color="W")
    check("只取白方", len(dw.moves) == 1 and dw.moves[0].color == "W")
    dall = build_problem_drill([eb, ew], infos, user_color="both")
    check("双方→2题", len(dall.moves) == 2)


def test_other_problems_and_max_moves():
    evs = [_ev(i, "B", "Q4", loss=float(i)) for i in range(6, 16)]  # 10 个，目损 6..15
    infos = {i: _infos() for i in range(6, 16)}
    d = build_problem_drill(evs, infos, user_color="B", max_moves=3)
    check("max_moves 截断详解题", len(d.moves) == 3, str(len(d.moves)))
    check("详解题按目损降序", d.moves[0].loss >= d.moves[1].loss >= d.moves[2].loss)
    check("其余进 other_problems", len(d.other_problems) == 7, str(len(d.other_problems)))


def test_drill_result():
    e = _ev(4, "B", "Q4")
    d = build_problem_drill([e], {4: _infos()}, user_color="B")
    res = new_drill_result(d)
    check("total=1", res.total == 1)
    m = d.moves[0]
    g = res.record(m, m.letter_of("c0"))
    check("作答正确", g["isCorrect"] and res.correct == 1)
    check("answered=1", res.answered == 1)
    check("score=100", res.score_pct == 100)
    # 重复作答同一题不重复计数
    res.record(m, m.letter_of("actual"))
    check("重复作答不增 answered", res.answered == 1)
    check("label 优秀", res.label == "优秀")


def test_missing_parent_infos_skipped():
    # 评价存在但父 moveInfos 缺失 → 该手跳过，整体空 + warning
    e = _ev(4, "B", "Q4")
    d = build_problem_drill([e], {}, user_color="B")   # 没有 4 的 infos
    check("缺父候选→空", d.is_empty)
    check("缺父候选→warning", any("候选" in w for w in d.warnings))


def test_phase_label_callback():
    e = _ev(4, "B", "Q4")
    d = build_problem_drill([e], {4: _infos()}, user_color="B",
                            phase_label_of=lambda mn: "布局" if mn < 50 else "官子")
    check("阶段文案传入", d.moves[0].phase_label == "布局")


def main():
    test_quality_labels()
    test_empty_drill()
    test_build_one_move()
    test_quiz_letters_cover_all()
    test_grade_quiz()
    test_actual_in_moveinfos()
    test_pass_actual()
    test_out_of_reach()
    test_user_color_filter()
    test_other_problems_and_max_moves()
    test_drill_result()
    test_missing_parent_infos_skipped()
    test_phase_label_callback()
    print("test_problem_drill: PASS")


if __name__ == "__main__":
    main()
