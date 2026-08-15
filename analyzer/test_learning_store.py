"""test_learning_store —— LearningStore JSON Repository 测试。"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from learning_event import (
    MASTERY_UNDERSTANDING, MASTERY_TRANSFERRED, RETRY_REPEATED,
    RETRY_CORRECTED, LearningEvent,
)
from learning_store import (
    get_due_reviews, get_event, get_events, get_events_by_category,
    get_events_by_game, remove_game, save_attempt, save_event, store_stats,
    sync_profile_summary,
)


def check(name, cond, extra=""):
    print("[CHECK] %-40s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        raise AssertionError(name)


def _problem(move_no, color="B", loss=6.0):
    return {"move_no": move_no, "color": color, "played_move": "R10",
            "best_move": "P9", "quality_key": "blunder", "score_loss": loss,
            "stage": "middle", "problem_tags": ["overplay"]}


def run():
    tmp = tempfile.mkdtemp(prefix="learning-store-")
    path = os.path.join(tmp, "learning_events.json")
    try:
        e1 = LearningEvent.from_problem("g1", _problem(82), game_name="第一局")
        e1.primary_category = "weak_groups"
        e1.learning_priority = 0.8
        e2 = LearningEvent.from_problem("g1", _problem(51, loss=4.0))
        e2.primary_category = "sente_tenuki"
        e2.learning_priority = 0.95
        e3 = LearningEvent.from_problem("g2", _problem(7, color="W"))
        e3.primary_category = "weak_groups"
        e3.learning_priority = 0.4

        save_event(e1, path)
        save_event(e2, path)
        save_event(e3, path)
        check("保存三条", len(get_events(path)) == 3)

        # 重新分析同手：客观字段更新、进度字段保留
        e1_new = LearningEvent.from_problem(
            "g1", _problem(82, loss=7.5), game_name="第一局")
        e1_new.primary_category = "attack_defense"
        e1_new.learning_priority = 0.7
        e1_new.user_retry_move = "Q11"      # 不应经此路径覆盖进度
        save_attempt(e1.id, "Q11", score_loss=0.7, assessment="acceptable",
                     ai_rank=2, retry_status=RETRY_CORRECTED, path=path)
        merged = save_event(e1_new, path)
        check("客观目损已更新", abs(merged.score_loss - 7.5) < 1e-9)
        check("作答历史保留", len(merged.attempts) == 1)
        check("重试结果保留", merged.retry_status == RETRY_CORRECTED
              and merged.retry_score_loss == 0.7)
        check("掌握状态保留", merged.mastery_state == MASTERY_UNDERSTANDING)

        # 查询
        g1 = get_events_by_game("g1", path)
        check("按局查询且按优先级排序",
              len(g1) == 2 and g1[0].move_no == 51)
        cats = get_events_by_category("weak_groups", path)
        check("按分类查询", len(cats) == 1 and cats[0].game_id == "g2")
        top = get_events(path, min_priority=0.7)
        check("优先级过滤", len(top) == 2)
        check("get_event 精确取回", get_event(e3.id, path).move_no == 7)
        check("未知 id 返回 None", get_event("nope", path) is None)

        # 复习到期
        save_attempt(e1.id, "R10", score_loss=6.0, assessment="bad",
                     retry_status=RETRY_REPEATED, path=path)
        due = get_due_reviews(today="2026-08-15", path=path)
        check("重复错误当天到期", len(due) == 1 and due[0].id == e1.id)
        check("未来日期无到期", get_due_reviews(today="2020-01-01", path=path) == [])

        # 实战迁移后退出复习队列
        evt = get_event(e1.id, path)
        evt.mastery_state = MASTERY_TRANSFERRED
        save_event(evt, path)
        check("已迁移不再出现在到期队列",
              get_due_reviews(today="2026-08-15", path=path) == [])

        # 全量问题入库：problem_moves_all 优先于 top_problem_moves 切片
        all_summary = {"problem_moves_all": [
            _problem(m, loss=2.5 + i * 0.5) for i, m in enumerate(
                [120, 121, 122, 123, 124, 125, 126, 127])],
            "top_problem_moves": [_problem(120), _problem(121), _problem(122)]}
        save_event(LearningEvent.from_problem("g9", _problem(1)), path=path)
        n_all = sync_profile_summary(
            {"id": "g9", "profileSide": "B"}, all_summary, path)
        check("全量问题入库（8 条而非 Top3）", n_all == 8, str(n_all))
        g9 = get_events_by_game("g9", path)
        check("小目损问题也进入学习库",
              any(e.move_no == 127 for e in g9), str([e.move_no for e in g9]))
        remove_game("g9", path)

        # 旧记录无 problem_moves_all → 回退 top_problem_moves
        legacy = sync_profile_summary(
            {"id": "g8", "profileSide": "B"},
            {"top_problem_moves": [_problem(10), _problem(11)]}, path)
        check("旧记录回退 Top 切片", legacy == 2, str(legacy))
        remove_game("g8", path)

        # 统计与删除
        stats = store_stats(path)
        check("统计字段齐全",
              stats["total"] == 3 and stats["games"] == 2
              and stats["by_mastery"].get(MASTERY_TRANSFERRED) == 1)
        check("按局删除联动", remove_game("g1", path) == 2
              and len(get_events(path)) == 1)
        check("save_attempt 不凭空造事件",
              save_attempt("ghost", "A1", path=path) is None)

        print("test_learning_store: 全部通过")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    run()
