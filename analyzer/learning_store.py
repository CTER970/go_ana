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
    LEARNING_EVENT_VERSION, MASTERY_NEW, MASTERY_TRANSFERRED,
    MASTERY_UNSTABLE, MASTERY_RETAINED, MASTERY_UNDERSTANDING, LearningEvent,
    event_id,
)

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(HERE, "game_library", "learning_events.json")
STORE_VERSION = 1

# 进度字段：重新分析入库（from_problem 生成的新事件）不应冲掉用户进度。
# 合并规则 = 新值为默认值时继承旧值（显式设置非默认值可正常更新）。
# 注意 recurrence_count / recurrence_cluster / learning_priority / 分类 /
# human_prior 都是【派生统计】——每次重新计算后必须覆盖，绝不继承旧值，
# 否则删除历史棋谱后旧计数残留成"幽灵复发"。
_PROGRESS_DEFAULTS = {
    "user_retry_move": "", "retry_score_loss": 0.0, "retry_status": "",
    "mastery_state": MASTERY_NEW, "review_due_date": "",
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


def set_event_mastery(event_id, mastery_state, path=DEFAULT_PATH):
    """直写事件的掌握状态（不经 save_event 合并——镜像同步必须精确覆盖）。

    错题本调度（mistake_book._update_item）是掌握状态的唯一更新入口，
    本函数保证 LearningEvent 与其保持一致（反馈 #6：单一事实源）。
    """
    store = load_store(path)
    for raw in store.get("events") or []:
        if str(raw.get("id")) == str(event_id):
            raw["mastery_state"] = str(mastery_state)
            raw["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            save_store(store, path)
            return True
    return False


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


def sync_profile_summary(record, summary=None, path=DEFAULT_PATH):
    """把单局画像问题手增量同步为 LearningEvent（项目大纲 M2）。

    数据源用 problem_moves_all（全部 ≥2 目/恶手问题）而不是 top_problem_moves：
    长期复发统计必须看到全部问题，"亏 4 目但反复犯"的题不能因单盘目损
    排不进 Top5 而从学习数据库消失。旧记录无该字段时回退 Top5。

    与 mistake_book.sync_profile_summary 同源同策略：身份过滤、稳定 id、
    重新分析只更新客观字段（进度保留）。在此基础上叠加：
      - taxonomy 分类（primary_category + 证据）；
      - learning_priority v1（不含 moveInfos，learnability 取默认 0.5；
        精确优先级由问题手训练入口用父局面候选现算）；
      - recurrence_count = 历史同类错误出现的盘数（唯一 game_id，不含本盘）。
    """
    record = dict(record or {})
    summary = dict(summary or record.get("profileSummary") or {})
    game_id = str(record.get("id") or summary.get("game_id") or "")
    if not game_id:
        return 0
    side = str(record.get("profileSide") or summary.get("user_side") or "unknown")
    if side not in ("B", "W", "both"):
        return 0

    import learning_priority
    import taxonomy

    recurrence_index = learning_priority.build_recurrence_index(
        get_events(path), exclude_game_id=game_id)
    problems = summary.get("problem_moves_all")
    if not problems:
        problems = summary.get("top_problem_moves") or []
    saved = 0
    # 只有实际写入学习库的本人问题，才可以影响“实战复发/迁移”状态。
    # summary 同时包含双方问题时，不能让对手的同类失误误判为本人复发。
    current_categories = set()
    for problem in problems:
        color = str(problem.get("color") or "").upper()
        if color not in ("B", "W") or (side != "both" and color != side):
            continue
        move_no = int(problem.get("move_no") or 0)
        best = str(problem.get("best_move") or "")
        played = str(problem.get("played_move") or "")
        if move_no <= 0 or not best or played.lower() == best.lower():
            continue
        evt = LearningEvent.from_problem(
            game_id, problem, game_name=record.get("name") or "")
        classification = taxonomy.classify_problem(problem)
        evt.primary_category = classification["primary_category"]
        evt.secondary_categories = classification["secondary_categories"]
        evt.category_confidence = classification["category_confidence"]
        evt.category_evidence = classification["category_evidence"]
        evt.taxonomy_version = classification["taxonomy_version"]
        priority = learning_priority.compute_learning_priority(
            score_loss=evt.score_loss,
            recurrence_count=recurrence_index.get(evt.primary_category, 0))
        evt.learning_priority = priority["final_score"]
        evt.priority_components = priority["components"]
        evt.priority_version = str(priority["version"])
        evt.recurrence_count = recurrence_index.get(evt.primary_category, 0)
        save_event(evt, path)
        if evt.primary_category and evt.primary_category != "unclassified":
            current_categories.add(evt.primary_category)
        saved += 1
    if current_categories:
        _apply_real_game_transitions(path, game_id, current_categories)
    return saved


def _apply_real_game_transitions(path, current_game_id, current_categories,
                                 *, observation_window=5):
    """实战维度的掌握状态迁移（大纲 §17/§40/§41，反馈修复5）。

    只有【真实棋局】能触发这两个状态——训练/复习错题（review recurrence）
    走 mistake_book 的调度路径，不允许冒充实战复发：

    - 实战复发：本盘又出现同类错误 → 过去盘里 understanding/retained 的
      同类事件标记 unstable（复习会但实战继续犯）；已 transferred 的类别
      复发同样重新打开为 unstable（问题回来了）；
    - 实战迁移：retained 事件所属类别在最近 observation_window 盘实战中
      都没再出现（且事件本身早于观察窗）→ transferred（真实棋局里这个
      问题已经消失）。
    """
    store = load_store(path)
    events = store.get("events") or []
    if not events:
        return
    games_ordered = []
    seen = set()
    for raw in sorted(events, key=lambda e: str(e.get("created_at") or "")):
        gid = str(raw.get("game_id") or "")
        if gid and gid not in seen:
            seen.add(gid)
            games_ordered.append(gid)
    recent_games = set(games_ordered[-observation_window:])
    recent_categories = {
        str(raw.get("primary_category") or "")
        for raw in events if str(raw.get("game_id")) in recent_games
    } - {"", "unclassified"}
    # 时间方向守卫：实战复发只向后看——只有【早于本盘】的 retained/
    # understanding/transferred 事件才会被本盘的同类错误重新打开，
    # 避免按库顺序同步旧棋谱时把更新棋局的事件误标 unstable
    try:
        current_pos = games_ordered.index(current_game_id)
    except ValueError:
        current_pos = len(games_ordered)
    past_games = set(games_ordered[:current_pos])

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    changed = {}
    for raw in events:
        gid = str(raw.get("game_id") or "")
        if gid == current_game_id or gid not in past_games:
            continue
        state = str(raw.get("mastery_state") or MASTERY_NEW)
        category = str(raw.get("primary_category") or "")
        if category in current_categories and state in (
                MASTERY_UNDERSTANDING, MASTERY_RETAINED, MASTERY_TRANSFERRED):
            raw["mastery_state"] = MASTERY_UNSTABLE
            raw["updated_at"] = now_str
            changed[str(raw.get("id"))] = MASTERY_UNSTABLE
        elif (state == MASTERY_RETAINED and category
              and category not in recent_categories
              and gid not in recent_games):
            raw["mastery_state"] = MASTERY_TRANSFERRED
            raw["updated_at"] = now_str
            changed[str(raw.get("id"))] = MASTERY_TRANSFERRED
    if not changed:
        return
    save_store(store, path)
    # 同步镜像到错题本 item（同一稳定 id），保持两处掌握状态一致；
    # mastered 标志同步校正：unstable 重新入队（当日到期复习），
    # transferred 出队——否则"复习会但实战复发"的题会被复习队列隐藏
    today_iso = datetime.now().strftime("%Y-%m-%d")
    try:
        from mistake_book import _update_item as _book_update
        book_path = os.path.join(
            os.path.dirname(os.path.abspath(path)), "mistake_book.json")
        for eid, state in changed.items():
            def _apply(item, s=state, unstable_due=today_iso):
                item["masteryState"] = s
                item["mastered"] = (s == MASTERY_TRANSFERRED)
                if s == MASTERY_UNSTABLE:
                    item["active"] = True
                    item["dueDate"] = min(
                        str(item.get("dueDate") or "9999-12-31"), unstable_due)
            _book_update(eid, _apply, path=book_path)
    except Exception:
        pass


def get_active_learning_events(path=DEFAULT_PATH):
    """复习视图投影：未实战迁移的事件（含 unstable——最该复习）。"""
    return [e for e in get_events(path) if e.mastery_state != MASTERY_TRANSFERRED]


def get_retained_learning_events(path=DEFAULT_PATH):
    """复习视图投影：已巩固（retained）的事件——观察窗后晋级 transferred。"""
    return [e for e in get_events(path) if e.mastery_state == "retained"]


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
