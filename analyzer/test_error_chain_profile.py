"""test_error_chain + test_learning_profile —— 问题簇/错误链与学习画像聚合测试。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from error_chain import build_problem_clusters, chain_summary
from learning_event import (
    MASTERY_NEW, MASTERY_TRANSFERRED, MASTERY_UNSTABLE, LearningEvent,
)
from learning_profile import format_learning_summary, summarize_learning


def check(name, cond, extra=""):
    print("[CHECK] %-46s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        raise AssertionError(name)


def _problem(move_no, loss, color="B", category="weak_groups"):
    return {"move_no": move_no, "color": color, "score_loss": loss,
            "primary_category": category}


def run_error_chain():
    # 大纲 §48 原型：63 留弱棋 → 一连串后果 → 151 崩盘
    problems = [
        _problem(63, 1.8, category="weak_groups"),
        _problem(84, 2.5, category="weak_groups"),
        _problem(107, 3.0, category="weak_groups"),
        _problem(129, 2.2, category="weak_groups"),
        _problem(151, 9.5, category="attack_defense"),
        _problem(201, 5.0, category="endgame"),      # 间隔大，另起一簇
    ]
    clusters = build_problem_clusters(problems)
    check("聚成两簇", len(clusters) == 2, str([c["move_nos"] for c in clusters]))
    main = clusters[0]
    check("链上 5 手归一簇", main["move_nos"] == [63, 84, 107, 129, 151])
    check("根源=最早一手", main["root"]["move_no"] == 63)
    check("最大损失=151", main["largest_loss"]["move_no"] == 151)
    check("代表学习节点=根源手（根源优先于爆炸点）",
          main["representative"]["move_no"] == 63)
    check("簇总损失合计", abs(main["total_loss"] - 19.0) < 1e-6)
    summaries = chain_summary(clusters)
    check("摘要指出根源与爆发点",
          "第63手" in summaries[0]["text"] and "第151手" in summaries[0]["text"])

    # 根源损失很小：代表节点仍是根源，爆炸点经 largest_loss 提供
    c2 = build_problem_clusters([
        _problem(30, 0.4), _problem(45, 8.0), _problem(52, 1.0)])
    check("根源过小代表节点仍=根源",
          c2[0]["representative"]["move_no"] == 30
          and c2[0]["largest_loss"]["move_no"] == 45)

    # 异色不轻易并簇；间隔超限但同类弱并入
    c3 = build_problem_clusters([
        _problem(10, 3.0, color="B", category="direction"),
        _problem(13, 2.0, color="W", category="direction"),   # 同类近距 → 并
        _problem(70, 4.0, color="B", category="endgame"),     # 新簇
        _problem(95, 3.5, color="B", category="endgame"),     # 超间隔但同类 → 并
    ])
    check("异色同类近距并入 / 超间隔同类弱并 / 异类新簇",
          len(c3) == 2 and c3[0]["move_nos"] == [10, 13]
          and c3[1]["move_nos"] == [70, 95], str([c["move_nos"] for c in c3]))
    check("空输入安全", build_problem_clusters([]) == [])

    print("error_chain: 全部通过")


def _evt(game, move_no, category, *, loss=5.0, priority=0.6, retry="",
         attempts=None, mastery=MASTERY_NEW):
    evt = LearningEvent.from_problem(game, {
        "move_no": move_no, "color": "B", "played_move": "R10",
        "best_move": "P9", "quality_key": "blunder", "score_loss": loss})
    evt.primary_category = category
    evt.learning_priority = priority
    evt.retry_status = retry
    evt.mastery_state = mastery
    evt.attempts = list(attempts or [])
    return evt


def run_learning_profile():
    # 4 盘：g1/g2 历史（弱棋类复发），g3/g4 近窗口
    events = [
        _evt("g1", 20, "weak_groups", loss=6.0, priority=0.7),
        _evt("g1", 90, "endgame", loss=2.5, priority=0.3),
        _evt("g2", 40, "weak_groups", loss=5.5, priority=0.75,
             mastery=MASTERY_UNSTABLE),
        _evt("g3", 55, "weak_groups", loss=6.5, priority=0.8,
             retry="corrected", mastery=MASTERY_UNSTABLE,
             attempts=[{"assessment": "bad"}, {"assessment": "excellent"}]),
        _evt("g3", 110, "direction", loss=4.0, priority=0.5, retry="repeated"),
        _evt("g4", 30, "weak_groups", loss=5.0, priority=0.65, retry="improved",
             attempts=[{"assessment": "bad"}, {"assessment": "acceptable"}]),
        _evt("g4", 80, "endgame", loss=3.0, priority=0.4,
             mastery=MASTERY_TRANSFERRED),
    ]
    s = summarize_learning(events, recent_games=2)
    check("盘数与事件数", s["games_total"] == 4 and s["recent_games"] == 2
          and s["events_total"] == 7 and s["recent_events"] == 4)
    check("重复错误率（近2盘：弱棋×2+官子 均在历史出现过 → 3/4）",
          s["repeat_error_rate"] == 75.0, str(s["repeat_error_rate"]))
    check("主动纠正率（corrected+improved / 3 次重选）",
          s["correction_rate"] == 66.7, str(s["correction_rate"]))
    check("延迟保留率（两次作答最新一次合理）",
          s["retention_rate"] == 100.0, str(s["retention_rate"]))
    check("分类分布近窗口",
          s["category_distribution"]["weak_groups"]["count"] == 2
          and s["category_distribution"]["weak_groups"]["pct"] == 50.0)
    check("复发维度过去vs最近",
          s["recurrence_by_category"]["weak_groups"] == {"earlier": 2, "recent": 2})
    check("掌握分布含 unstable/transferred",
          s["mastery_distribution"].get(MASTERY_UNSTABLE) == 2
          and s["mastery_distribution"].get(MASTERY_TRANSFERRED) == 1)
    theme = s["top_training_theme"]
    check("第一训练主题=弱棋且只给一个",
          theme is not None and theme["category"] == "weak_groups"
          and theme["count"] == 2 and theme["avg_loss"] > 5.0,
          str(theme))

    text = format_learning_summary(s)
    check("摘要文本含五指标与主题",
          "重复错误率" in text and "主动纠正率" in text and "延迟保留率" in text
          and "第一训练主题" in text and "unstable" in text, )
    empty = summarize_learning([])
    check("空数据安全", empty["repeat_error_rate"] is None
          and empty["top_training_theme"] is None
          and "—" in format_learning_summary(empty))

    print("learning_profile: 全部通过")


if __name__ == "__main__":
    run_error_chain()
    run_learning_profile()
    print("test_error_chain_profile: 全部通过")
