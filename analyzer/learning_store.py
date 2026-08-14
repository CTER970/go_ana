"""learning_store —— LearningEvent 的 JSON Repository（项目大纲 §51）。

第一版用 JSON 文件实现（原子写，与 mistake_book 同一套防护），接口按
Repository 层设计，未来换 SQLite 实现时上层不需要改动：

    save_event / get_event / get_events / get_events_by_game /
    get_events_by_category / save_attempt / get_due_reviews / remove_game

同一 (game_id, move_no, color) 只保留一条事件：重新分析只更新客观字段与
解释层，用户的作答历史（attempts）、重试结果与掌握状态始终保留。
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime

from learning_event import (
    LEARNING_EVENT_VERSION, MASTERY_NEW, LearningEvent, MASTERY_TRANSFERRED,
    event_id,
)

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(HERE, "game_library", "learning_events.json")
STORE_VERSION = 1

# 进度字段：重新分析入库（from_problem 生成的新事件）不应冲掉用户进度。
# 合并规则 = 新值为默认值时继承旧值（显式设置非默认值可正常更新）。
_PROGRESS_DEFAULTS = {
    "user_retry_move": "", "retry_score_loss": 0.0, "retry_status": "",
    "mastery_state": MASTERY_NEW, "review_due_date": "",
    "recurrence_cluster": "", "recurrence_count": 0,
}
_PROGRESS_LISTS = ("attempts",)


def _today(value=None):
    if value is None:
        return date.today()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def _empty_store():
    return {"version": STORE_VERSION, "updatedAt": "", "events": []}


def load_store(path=DEFAULT_PATH):
    if not os.path.exists(path):
        return _empty_store()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("events"), list):
            return _empty_store()
        data.setdefault("version", STORE_VERSION)
        return data
    except (OSError, ValueError, TypeError):
        return _empty_store()


def save_store(store, path=DEFAULT_PATH):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    payload = dict(store or {})
    payload["version"] = STORE_VERSION
    payload["updatedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload["events"] = list(payload.get("events") or [])
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return payload


def save_event(event, path=DEFAULT_PATH):
    """upsert 一条事件：客观/解释字段以新值为准，进度字段保留旧值。"""
    if not isinstance(event, LearningEvent):
        raise TypeError("save_event 需要 LearningEvent 实例")
    if not event.id:
        event.id = event_id(event.game_id, event.move_no, event.player_color)
    store = load_store(path)
    events = store.get("events") or []
    merged = event.to_dict()
    for old in events:
        if str(old.get("id")) == str(event.id):
            for key, default in _PROGRESS_DEFAULTS.items():
                if merged.get(key) == default and key in old:
                    merged[key] = old[key]
            for key in _PROGRESS_LISTS:
                if not merged.get(key) and key in old:
                    merged[key] = old[key]
            merged["created_at"] = old.get("created_at") or merged.get("created_at")
            events[events.index(old)] = merged
            break
    else:
        events.append(merged)
    events.sort(key=lambda e: (
        str(e.get("game_id")), int(e.get("move_no") or 0), str(e.get("player_color"))))
    store["events"] = events
    save_store(store, path)
    return LearningEvent.from_dict(merged)


def get_event(event_id, path=DEFAULT_PATH):
    for raw in load_store(path).get("events") or []:
        if str(raw.get("id")) == str(event_id):
            return LearningEvent.from_dict(raw)
    return None


def get_events(path=DEFAULT_PATH, *, min_priority=None, mastery=None):
    out = []
    for raw in load_store(path).get("events") or []:
        if min_priority is not None and \
                float(raw.get("learning_priority") or 0.0) < float(min_priority):
            continue
        if mastery is not None and str(raw.get("mastery_state")) != str(mastery):
            continue
        out.append(LearningEvent.from_dict(raw))
    out.sort(key=lambda e: -e.learning_priority)
    return out


def get_events_by_game(game_id, path=DEFAULT_PATH):
    gid = str(game_id or "")
    out = [LearningEvent.from_dict(raw)
           for raw in load_store(path).get("events") or []
           if str(raw.get("game_id")) == gid]
    out.sort(key=lambda e: (-e.learning_priority, e.move_no))
    return out


def get_events_by_category(category, path=DEFAULT_PATH):
    key = str(category or "")
    out = [LearningEvent.from_dict(raw)
           for raw in load_store(path).get("events") or []
           if str(raw.get("primary_category")) == key]
    out.sort(key=lambda e: (-e.learning_priority, e.move_no))
    return out


def save_attempt(event_id, played_move, *, score_loss=None, assessment=None,
                 ai_rank=None, hint_used=False, thinking_time=None,
                 retry_status=None, path=DEFAULT_PATH):
    """记录一次作答：追加 attempts[]，可选同步主动复盘结果。

    返回更新后的 LearningEvent；事件不存在返回 None（不凭空造事件，
    与 mistake_book.apply_training_outcomes 同一原则）。
    """
    store = load_store(path)
    for raw in store.get("events") or []:
        if str(raw.get("id")) != str(event_id):
            continue
        evt = LearningEvent.from_dict(raw)
        evt.add_attempt(played_move, score_loss=score_loss,
                        assessment=assessment, ai_rank=ai_rank,
                        hint_used=hint_used, thinking_time=thinking_time)
        if retry_status:
            evt.record_retry(played_move, score_loss or 0.0, retry_status)
        if evt.review_due_date == "" and retry_status == "repeated":
            evt.review_due_date = _today().isoformat()
        raw.clear()
        raw.update(evt.to_dict())
        save_store(store, path)
        return evt
    return None


def get_due_reviews(today=None, path=DEFAULT_PATH, include_transferred=False):
    """到期需复习的事件（review_due_date <= today，默认排除已实战迁移）。"""
    day = _today(today)
    out = []
    for raw in load_store(path).get("events") or []:
        evt = LearningEvent.from_dict(raw)
        if evt.mastery_state == MASTERY_TRANSFERRED and not include_transferred:
            continue
        due_text = evt.review_due_date or "9999-12-31"
        try:
            due = _today(due_text)
        except (TypeError, ValueError):
            continue
        if due <= day:
            out.append(evt)
    out.sort(key=lambda e: (-e.learning_priority, e.review_due_date))
    return out


def remove_game(game_id, path=DEFAULT_PATH):
    """棋局删除时移除其全部事件（与 mistake_book.remove_game 联动）。"""
    store = load_store(path)
    before = len(store.get("events") or [])
    store["events"] = [
        e for e in (store.get("events") or [])
        if str(e.get("game_id")) != str(game_id)
    ]
    removed = before - len(store["events"])
    if removed:
        save_store(store, path)
    return removed


def store_stats(path=DEFAULT_PATH):
    events = get_events(path)
    by_mastery = {}
    by_category = {}
    for evt in events:
        by_mastery[evt.mastery_state] = by_mastery.get(evt.mastery_state, 0) + 1
        cat = evt.primary_category or "unclassified"
        by_category[cat] = by_category.get(cat, 0) + 1
    return {
        "version": STORE_VERSION,
        "event_version": LEARNING_EVENT_VERSION,
        "total": len(events),
        "games": len({e.game_id for e in events}),
        "attempts": sum(len(e.attempts) for e in events),
        "by_mastery": by_mastery,
        "by_category": by_category,
    }
