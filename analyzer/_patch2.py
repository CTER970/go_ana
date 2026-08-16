# -*- coding: utf-8 -*-
import io
p = "mistake_book.py"
t = io.open(p, encoding="utf-8").read()

old_doc = 'def _apply_review_result(item, normalized, day):\n    """单条 item 的间隔复习调度 + 掌握状态流转（保留原有间隔算法）。\n'
new_doc = '''def _apply_review_result(item, normalized, day):
    """书侧派生缓存同步（P0-4：调度/掌握的唯一写入者是
    learning_store.apply_review_outcome；本函数只把结果镜像进 item，
    供旧 UI 排序/展示，丢失可随时从事件重建）。
"""
    evt = None
    try:
        from learning_event import event_id as _eid
        from learning_store import apply_review_outcome
        evt = apply_review_outcome(
            _eid(item.get("gameId"), item.get("moveNo"), item.get("color")),
            normalized, today=day,
            path=_learning_events_path(DEFAULT_PATH))
    except Exception:
        evt = None
    if evt is not None:
        item["intervalDays"] = evt.review_interval_days
        item["dueDate"] = evt.review_due_date
        item["repetitions"] = evt.review_repetitions
        item["lapses"] = evt.review_lapses
        item["lastReviewedAt"] = evt.last_reviewed_at
        item["lastResult"] = evt.last_review_result
        item["masteryState"] = evt.mastery_state
        item["mastered"] = False
        return
    # 事件不存在（极旧数据未回填）：就地退化为旧算法，保证可用
'''
print("doc block:", t.count(old_doc))
assert t.count(old_doc) == 1
t = t.replace(old_doc, new_doc, 1)

ret = "    return _overlay_attempts(out, path)"
print("ret count:", t.count(ret))
assert t.count(ret) == 1
t = t.replace(ret, "    out = _overlay_attempts(out, path)\n    return _overlay_schedule(out, path)", 1)

anchor = "def _overlay_attempts(items, book_path=DEFAULT_PATH):"
print("anchor:", t.count(anchor))
assert t.count(anchor) == 1
overlay = '''def _overlay_schedule(items, book_path=DEFAULT_PATH):
    """调度/掌握状态从 LearningEvent 只读投影（P0-4 单一事实源）。"""
    try:
        from learning_store import get_events
        by_id = {e.id: e for e in get_events(_learning_events_path(book_path))}
    except Exception:
        return items
    for item in items:
        evt = by_id.get(str(item.get("id")))
        if evt is None:
            continue
        item["intervalDays"] = evt.review_interval_days
        item["repetitions"] = evt.review_repetitions
        item["lapses"] = evt.review_lapses
        item["lastReviewedAt"] = evt.last_reviewed_at
        item["lastResult"] = evt.last_review_result
        item["masteryState"] = evt.mastery_state
        if evt.review_due_date:
            item["dueDate"] = evt.review_due_date
        # 到期判定以事件为准（无 due = 尚未进入复习队列）
        item["isDue"] = bool(
            evt.mastery_state != "transferred" and evt.review_due_date
            and _day_le(evt.review_due_date))
    return items


def _day_le(date_text):
    try:
        from datetime import date as _d
        return _d(int(date_text[:4]), int(date_text[5:7]),
                  int(date_text[8:10])) <= _d.today()
    except Exception:
        return False


'''
t = t.replace(anchor, overlay + anchor, 1)
io.open(p, "w", encoding="utf-8", newline="").write(t)
print("OK")
