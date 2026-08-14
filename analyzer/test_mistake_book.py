"""test_mistake_book —— 错题同步、身份过滤与间隔复习调度测试。"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from mistake_book import (apply_training_outcomes, book_stats, get_item, grade_attempt,
                          list_items, postpone_item, record_review, set_mastered,
                          sync_profile_summary)


def check(name, cond, extra=""):
    print("[CHECK] %-34s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        raise AssertionError(name)


def run():
    tmp = tempfile.mkdtemp(prefix="mistake-book-")
    path = os.path.join(tmp, "book.json")
    try:
        record = {
            "id": "g1",
            "name": "测试棋局",
            "projectPath": "g1.kga.json",
            "profileSide": "B",
        }
        summary = {
            "top_problem_moves": [
                {"move_no": 31, "color": "B", "played_move": "D4", "best_move": "Q16",
                 "quality_key": "blunder", "score_loss": 8.5,
                 "problem_tags": ["opening_direction"]},
                {"move_no": 32, "color": "W", "played_move": "C3", "best_move": "R17",
                 "quality_key": "inaccuracy", "score_loss": 3.2},
            ]
        }
        check("只同步画像执棋方", sync_profile_summary(
            record, summary, path, today="2026-06-30") == 1)
        items = list_items(path, due_only=True, today="2026-06-30")
        check("新题当天到期", len(items) == 1 and items[0]["moveNo"] == 31)
        iid = items[0]["id"]

        record["profileSide"] = "both"
        sync_profile_summary(record, summary, path, today="2026-06-30")
        check("双方身份同步两题", len(list_items(path, today="2026-06-30")) == 2)

        record_review(iid, "again", path, today="2026-06-30")
        item = get_item(iid, path)
        check("答错次日再练", item["dueDate"] == "2026-07-01" and item["lapses"] == 1)
        record_review(iid, "good", path, today="2026-07-01")
        item = get_item(iid, path)
        check("命中首选间隔三天", item["dueDate"] == "2026-07-04")
        postpone_item(iid, 2, path, today="2026-07-04")
        check("可主动推迟", get_item(iid, path)["dueDate"] == "2026-07-06")
        set_mastered(iid, True, path)
        check("掌握后退出默认队列", all(x["id"] != iid for x in list_items(path)))
        stats = book_stats(path, today="2026-07-06")
        check("统计包含已掌握题", stats["total"] == 2 and stats["mastered"] == 1)

        # 重分析更新题面但保留复习进度。
        record["profileSide"] = "B"
        summary["top_problem_moves"][0]["score_loss"] = 9.5
        sync_profile_summary(record, summary, path, today="2026-07-06")
        item = get_item(iid, path)
        check("重分析保留调度", item["mastered"] and item["scoreLoss"] == 9.5)
        check("身份切换停用另一方", len(list_items(
            path, include_mastered=True, today="2026-07-06")) == 1)
        candidates = [
            {"order": 0, "move": "Q16"},
            {"order": 1, "move": "D16"},
            {"order": 3, "move": "K10"},
        ]
        check("测验首选判定 good",
              grade_attempt("Q16", "Q16", candidates) == ("good", 1))
        check("测验前三选判定 hard",
              grade_attempt("Q16", "D16", candidates) == ("hard", 2))
        check("测验其他手判定 again",
              grade_attempt("Q16", "K10", candidates) == ("again", 4))
    finally:
        shutil.rmtree(tmp)

    test_apply_training_outcomes()


def test_apply_training_outcomes():
    """阶段训练结果回写错题本：again 重置间隔、good 标记掌握；只命中已存在 item。"""
    tmp = tempfile.mkdtemp(prefix="mistake-book-tr-")
    path = os.path.join(tmp, "book.json")
    try:
        record = {"id": "g1", "name": "测试棋局", "projectPath": "g1.kga.json",
                  "profileSide": "B"}
        summary = {"top_problem_moves": [
            {"move_no": 31, "color": "B", "played_move": "D4", "best_move": "Q16",
             "quality_key": "blunder", "score_loss": 8.5}]}
        sync_profile_summary(record, summary, path, today="2026-07-04")
        n = apply_training_outcomes("g1", [(31, "B", "again")], path, today="2026-07-04")
        check("回写命中已存在 item", n == 1, "n=%d" % n)
        items = list_items(path, today="2026-07-04", include_mastered=True)
        it = next(x for x in items if x["moveNo"] == 31)
        check("again 重置间隔 1 天 + lapse+1",
              it["intervalDays"] == 1 and it["lapses"] == 1, str(it))
        check("again 次日到期", it["dueDate"] == "2026-07-05")
        n2 = apply_training_outcomes("g1", [(999, "B", "again")], path, today="2026-07-04")
        check("不存在 item 不凭空造题", n2 == 0, "n2=%d" % n2)
        apply_training_outcomes("g1", [(31, "B", "good")], path, today="2026-07-04")
        it2 = get_item(it["id"], path)
        check("good 标记掌握且间隔>=14",
              it2["mastered"] and it2["intervalDays"] >= 14, str(it2))
    finally:
        shutil.rmtree(tmp)


if __name__ == "__main__":
    run()
    print("test_mistake_book: PASS")
