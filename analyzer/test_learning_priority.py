"""test_learning_priority —— 优先级公式、复发统计、选题多样性与训练排序整合测试。"""
import os
import sys
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from learning_priority import (
    PRIORITY_VERSION, WEIGHTS, build_recurrence_index,
    compute_learning_priority, game_importance_of, learnability_of,
    level_gap_of, recurrence_of, select_learning_problems, severity_of,
)
from problem_drill import DEFAULT_LEARNING_MOVES, build_problem_drill


def check(name, cond, extra=""):
    print("[CHECK] %-40s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        raise AssertionError(name)


def _mis(gaps, color="B"):
    """gaps = 各候选相对一选目损，构造 moveInfos（黑视角）。"""
    base = 1.0
    out = []
    for i, gap in enumerate(gaps):
        out.append({"move": "A%d" % i, "order": i,
                    "scoreLead": base - gap if color == "B" else -(base - gap),
                    "prior": 0.5 - 0.1 * i, "winrate": 0.5, "visits": 50})
    return out


def run():
    # 分量函数
    check("severity 8目封顶", severity_of(6.0) == 0.75 and severity_of(99) == 1.0)
    check("recurrence 阶梯",
          recurrence_of(0) == 0.0 and recurrence_of(1) == 0.25
          and recurrence_of(2) == 0.40 and recurrence_of(3) == 0.65
          and recurrence_of(5) == 1.0)
    check("无 Human SL 时 level_gap=0",
          level_gap_of(None, None) == 0.0)
    check("level_gap 正差值", abs(level_gap_of(0.4, 0.1) - 0.3) < 1e-9)
    check("game_importance 权重表",
          game_importance_of("formal") == 1.0
          and game_importance_of("unknown_type") == 0.5)

    learn_simple, _ = learnability_of(_mis([0.0, 0.3, 0.8, 1.2, 1.4]), "B", 0.4)
    learn_unique, _ = learnability_of(_mis([0.0, 5.5, 7.0]), "B", 0.4)
    learn_super, _ = learnability_of(_mis([0.0, 5.5]), "B", 0.03)
    check("多合理候选 > 唯一好手 > 超纲神之一手",
          learn_simple > learn_unique > learn_super,
          "%.2f/%.2f/%.2f" % (learn_simple, learn_unique, learn_super))

    # 完整公式
    pri = compute_learning_priority(
        score_loss=4.0, recurrence_count=2, move_infos=_mis([0.0, 0.5, 1.0]),
        color="B", best_prior=0.4, game_type="formal", mastery_state="new")
    check("分量齐全且版本化",
          set(WEIGHTS) <= set(pri["components"]) and pri["version"] == PRIORITY_VERSION)
    check("最终分在 0-1", 0.0 <= pri["final_score"] <= 1.0)
    repeated = compute_learning_priority(
        score_loss=4.0, recurrence_count=9, move_infos=_mis([0.0, 0.5, 1.0]),
        color="B", game_type="formal", mastery_state="unstable")
    check("复发+不稳定 > 首次新发现", repeated["final_score"] > pri["final_score"])
    transferred = compute_learning_priority(
        score_loss=4.0, recurrence_count=9, move_infos=_mis([0.0, 0.5, 1.0]),
        color="B", game_type="formal", mastery_state="transferred")
    check("已实战迁移大幅降权", transferred["final_score"] < 0.1)

    # 大纲 §9 场景：简单+高频 vs 复杂+偶发
    simple_freq = compute_learning_priority(
        score_loss=4.0, recurrence_count=5, move_infos=_mis([0.0, 0.5, 1.0, 1.2]))
    hard_once = compute_learning_priority(
        score_loss=12.0, recurrence_count=0, move_infos=_mis([0.0, 6.0, 8.0]),
        best_prior=0.02)
    check("简单高频问题优先于复杂偶发",
          simple_freq["final_score"] > hard_once["final_score"],
          "%.3f vs %.3f" % (simple_freq["final_score"], hard_once["final_score"]))

    # 复发索引：按唯一 game_id 去重（一盘 3 个同类错误只算 1 盘）
    events = [SimpleNamespace(game_id="g1", primary_category="weak_groups"),
              SimpleNamespace(game_id="g1", primary_category="weak_groups"),
              SimpleNamespace(game_id="g1", primary_category="weak_groups"),
              SimpleNamespace(game_id="g2", primary_category="weak_groups"),
              SimpleNamespace(game_id="g3", primary_category="weak_groups"),
              SimpleNamespace(game_id="g3", primary_category="endgame"),
              SimpleNamespace(game_id="g1", primary_category="")]
    index = build_recurrence_index(events, exclude_game_id="g1")
    check("跨盘复发按盘计数（同盘多事件不重复计）",
          index == {"weak_groups": 2, "endgame": 1}, str(index))
    check("不排除时 g1 也计 1 盘",
          build_recurrence_index(events) == {"weak_groups": 3, "endgame": 1})

    # 选题多样性：同簇最多 2 个
    problems = [
        {"move_no": 83, "priority": 0.95}, {"move_no": 85, "priority": 0.9},
        {"move_no": 87, "priority": 0.85}, {"move_no": 89, "priority": 0.8},
        {"move_no": 91, "priority": 0.75},
        {"move_no": 51, "priority": 0.7}, {"move_no": 201, "priority": 0.6},
    ]
    picked = select_learning_problems(problems, limit=5, per_cluster_cap=2)
    check("同簇封顶后引入远端问题",
          [p["move_no"] for p in picked][:3] == [83, 85, 51],
          str([p["move_no"] for p in picked]))

    # problem_drill 学习排序整合
    def _ev(move_no, loss, color="B"):
        return SimpleNamespace(
            move_number=move_no, color=color, coord="R%d" % move_no,
            is_pass=False, analyzed=True, loss=loss,
            winrate_before=0.5, winrate_after=0.4,
            score_lead_before=1.0, score_lead_after=-1.0,
            best_move="P%d" % move_no, ai_rank=5)

    evs = [_ev(83, 12.0), _ev(85, 11.0), _ev(87, 10.0), _ev(51, 4.0)]
    infos = {
        83: _mis([0.0, 5.5, 7.0]),   # 神之一手
        85: _mis([0.0, 6.0, 8.0]),
        87: _mis([0.0, 7.0]),
        51: _mis([0.0, 0.5, 1.0, 1.2]),  # 简单多解
    }
    drill = build_problem_drill(
        evs, infos, user_color="both", ranking="learning",
        priority_context={"recurrence_by_move": {51: 5}})
    check("学习模式默认 5 题", len(drill.moves) <= DEFAULT_LEARNING_MOVES)
    check("简单高频第51手排到第一",
          drill.moves[0].move_number == 51,
          str([m.move_number for m in drill.moves]))
    check("优先级与分量落到题目",
          drill.moves[0].learning_priority > 0
          and drill.moves[0].priority_version == PRIORITY_VERSION
          and "severity" in drill.moves[0].priority_components)
    check("落选题进入 other_problems",
          len(drill.other_problems) == len(evs) - len(drill.moves))
    legacy = build_problem_drill(evs, infos, user_color="both")
    check("默认 loss 排序不变（83 目损最大在首位）",
          legacy.moves[0].move_number == 83 and len(legacy.moves) == 4)

    # Human SL 主链（反馈修复3）：双档概率注入 → level_gap 抬升本人档特有问题
    drill_h = build_problem_drill(
        evs, infos, user_color="both", ranking="learning",
        priority_context={
            "recurrence_by_move": {51: 2},
            # 83 手实战：本人档常下(0.35)、高档明显少下(0.02) → level_gap 高
            "human_priors_by_move": {83: {"current": 0.35, "stronger": 0.02}},
        })
    check("Human SL level_gap 抬升本人档特有问题",
          drill_h.moves[0].move_number == 83,
          str([(m.move_number, round(m.learning_priority, 3)) for m in drill_h.moves]))
    no_h = build_problem_drill(
        evs, infos, user_color="both", ranking="learning",
        priority_context={"recurrence_by_move": {51: 2}})
    p_with = [m for m in drill_h.moves if m.move_number == 83][0]
    p_without = [m for m in no_h.moves if m.move_number == 83][0]
    check("level_gap 带来可量化优先级增量",
          p_with.learning_priority > p_without.learning_priority
          and p_with.priority_components["level_gap"] > 0
          and p_without.priority_components["level_gap"] == 0)

    print("test_learning_priority: 全部通过")


if __name__ == "__main__":
    run()
