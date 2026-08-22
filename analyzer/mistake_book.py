"""mistake_book —— 从已分析棋局生成可重复练习的本地错题本。

模块只负责持久化和间隔复习调度，不依赖 tkinter 或 KataGo 进程。
错题来源是 ``profileSummary.top_problem_moves``；同一盘同一手使用稳定 id，
重复分析只更新题面，不会清空用户的复习进度。
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
_FILE_NAME = "mistake_book.json"

# ---- 默认路径调用时解析（usage_log.set_path 同款约定）----
# 历史教训（W29 审查；W16 曾发生真实错题泄入测试）：def f(path=DEFAULT_PATH)
# 在导入期把路径固化进默认参数，重定向对"走默认值"的调用无效，
# 数据可能写错位置。
_state = {"path": None}


def default_path():
    """内置默认路径（不受 set_path 重定向影响）。"""
    return os.path.join(HERE, "game_library", _FILE_NAME)


# 兼容引用：运行期生效的默认以 get_path() 为准。
DEFAULT_PATH = default_path()
BOOK_VERSION = 1


def get_path():
    """当前生效的默认存储路径：set_path 重定向 > game_library.LIBRARY_DIR 派生。"""
    if _state["path"]:
        return _state["path"]
    try:
        import game_library as _gl
        return os.path.join(_gl.LIBRARY_DIR, _FILE_NAME)
    except Exception:
        return default_path()


def set_path(path):
    """重定向默认存储路径（测试用）；None 恢复默认。调用时解析，立即生效。"""
    _state["path"] = path or None


def _resolve_path(path):
    return path or get_path()


def _today(value=None):
    if value is None:
        return date.today()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _item_id(game_id, move_no, color):
    raw = "%s:%s:%s" % (game_id, int(move_no), color)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def _empty_book():
    return {"version": BOOK_VERSION, "updatedAt": _now(), "items": []}


def load_book(path=None):
    path = _resolve_path(path)
    if not os.path.exists(path):
        return _empty_book()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            return _empty_book()
        data.setdefault("version", BOOK_VERSION)
        return data
    except (OSError, ValueError, TypeError):
        return _empty_book()


def save_book(book, path=None):
    path = _resolve_path(path)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    payload = dict(book or {})
    payload["version"] = BOOK_VERSION
    payload["updatedAt"] = _now()
    payload["items"] = list(payload.get("items") or [])
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return payload


def sync_profile_summary(record, summary=None, path=None, today=None):
    """把单局画像中的高价值问题手增量同步到错题本。

    ``profileSide`` 为 B/W 时只收录对应一方；``both`` 收录双方；
    身份未知时不创建题目。同一棋局重新分析后，旧题的调度进度会保留。
    """
    path = _resolve_path(path)
    record = dict(record or {})
    summary = dict(summary or record.get("profileSummary") or {})
    game_id = str(record.get("id") or summary.get("game_id") or "")
    if not game_id:
        return 0
    side = str(
        record.get("profileSide")
        or summary.get("user_side")
        or "unknown"
    )
    book = load_book(path)
    items = list(book.get("items") or [])
    by_id = {str(item.get("id")): item for item in items}

    # 身份切换后，先让这盘旧题退出队列，再重新启用当前身份对应的题。
    for item in items:
        if str(item.get("gameId")) == game_id:
            item["active"] = False
    if side not in ("B", "W", "both"):
        save_book(book, path)
        return 0

    day = _today(today).isoformat()
    changed = 0
    for problem in summary.get("top_problem_moves") or []:
        color = str(problem.get("color") or "").upper()
        if color not in ("B", "W") or (side != "both" and color != side):
            continue
        move_no = int(problem.get("move_no") or 0)
        played = str(problem.get("played_move") or "")
        best = str(problem.get("best_move") or "")
        if move_no <= 0 or not best or played.lower() == best.lower():
            continue
        iid = _item_id(game_id, move_no, color)
        item = by_id.get(iid)
        if item is None:
            item = {
                "id": iid,
                "gameId": game_id,
                "createdAt": _now(),
                "dueDate": day,
                "intervalDays": 0,
                "repetitions": 0,
                "lapses": 0,
                "lastReviewedAt": "",
                "lastResult": "",
                "mastered": False,
            }
            items.append(item)
            by_id[iid] = item
        item.update({
            "active": True,
            "gameName": record.get("name") or summary.get("game_name") or "",
            "projectPath": record.get("projectPath") or "",
            "moveNo": move_no,
            "color": color,
            "playedMove": played,
            "bestMove": best,
            "qualityKey": problem.get("quality_key") or "inaccuracy",
            "scoreLoss": problem.get("score_loss"),
            "winrateDrop": problem.get("winrate_drop"),
            "stage": problem.get("stage") or "middle",
            "problemTags": list(problem.get("problem_tags") or []),
            "sourceUpdatedAt": summary.get("updatedAt") or record.get("updatedAt") or "",
        })
        changed += 1
    book["items"] = items
    save_book(book, path)
    return changed


def _overlay_schedule(items, book_path=None):
    """调度/掌握状态从 LearningEvent 只读投影（P0-4 单一事实源）。"""
    book_path = _resolve_path(book_path)
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


def _overlay_attempts(items, book_path=None):
    """从 LearningEvent 投影作答历史到书侧条目（只读，P0-3 单一事实源）。

    事件侧 attempts 为 snake_case；此处转成书侧历史字段名（playedMove
    等）以兼容既有 UI/统计，不落盘。
    """
    book_path = _resolve_path(book_path)
    try:
        from learning_store import get_events
        events_path = _learning_events_path(book_path)
        by_id = {e.id: e for e in get_events(events_path)}
    except Exception:
        return items
    srs_map = {"best": "good", "excellent": "good", "acceptable": "hard",
               "questionable": "again", "bad": "again", "unknown": "again"}
    for item in items:
        evt = by_id.get(str(item.get("id")))
        item["attempts"] = [{
            "date": a.get("date"),
            "playedMove": a.get("played_move"),
            "scoreLoss": a.get("score_loss"),
            "assessment": a.get("assessment"),
            "aiRank": a.get("ai_rank"),
            # 事件侧不存 SRS 结果，按判定档投影回 good/hard/again
            "result": a.get("result") or srs_map.get(a.get("assessment"), "again"),
            "hintUsed": bool(a.get("hint_used")),
            "thinkingTime": a.get("thinking_time"),
        } for a in (evt.attempts if evt else [])]
    return items


def list_items(path=None, *, due_only=False, include_mastered=False, today=None):
    path = _resolve_path(path)
    day = _today(today)
    out = []
    for raw in load_book(path).get("items") or []:
        item = dict(raw)
        if not item.get("active", True):
            continue
        if item.get("mastered") and not include_mastered:
            continue
        due_text = item.get("dueDate") or "9999-12-31"
        try:
            due = _today(due_text)
        except (TypeError, ValueError):
            due = day
        item["isDue"] = bool(not item.get("mastered") and due <= day)
        if due_only and not item["isDue"]:
            continue
        out.append(item)
    out.sort(key=lambda item: (
        item.get("dueDate") or "9999-12-31",
        -float(item.get("scoreLoss") or 0.0),
        item.get("gameName") or "",
        int(item.get("moveNo") or 0),
    ))
    out = _overlay_attempts(out, path)
    return _overlay_schedule(out, path)


def get_item(item_id, path=None):
    path = _resolve_path(path)
    for item in load_book(path).get("items") or []:
        if str(item.get("id")) == str(item_id):
            return _overlay_schedule(
                _overlay_attempts([dict(item)], path), path)[0]
    return None


def _learning_events_path(book_path):
    return os.path.join(
        os.path.dirname(os.path.abspath(book_path)), "learning_events.json")


def _mirror_mastery_to_events(item, book_path=None):
    """掌握状态镜像：错题本是唯一更新入口，LearningEvent 必须同步（反馈 #6）。

    经 set_event_mastery 直写（绕过 save_event 的进度合并），确保
    "复习会了"在两边同时成立，长期画像读 LearningEvent 不再看到旧状态。
    """
    book_path = _resolve_path(book_path)
    try:
        from learning_event import event_id as _evt_id
        from learning_store import set_event_mastery
        set_event_mastery(
            _evt_id(item.get("gameId"), item.get("moveNo"), item.get("color")),
            str(item.get("masteryState") or "new"),
            path=_learning_events_path(book_path))
    except Exception:
        pass


def _update_item(item_id, updater, path=None):
    path = _resolve_path(path)
    book = load_book(path)
    for item in book.get("items") or []:
        if str(item.get("id")) != str(item_id):
            continue
        updater(item)
        save_book(book, path)
        _mirror_mastery_to_events(item, path)
        return dict(item)
    return None


def _apply_review_result(item, normalized, day, path=None):
    """书侧派生缓存同步（P0-4：调度/掌握的唯一写入者是
    learning_store.apply_review_outcome；本函数只把结果镜像进 item，
    供旧 UI 排序/展示，丢失可随时从事件重建）。"""
    path = _resolve_path(path)
    evt = None
    try:
        from learning_event import event_id as _eid
        from learning_store import apply_review_outcome
        evt = apply_review_outcome(
            _eid(item.get("gameId"), item.get("moveNo"), item.get("color")),
            normalized, today=day, path=_learning_events_path(path))
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
    reps = int(item.get("repetitions") or 0)
    old_interval = int(item.get("intervalDays") or 0)
    if normalized == "again":
        item["lapses"] = int(item.get("lapses") or 0) + 1
        item["repetitions"] = 0
        interval = 1
        # 复习答错 = 连"立即复盘能纠正"都不成立 → 回到 new；
        # unstable（复习会但实战继续犯）只能由实战数据触发
        item["masteryState"] = "new"
    elif normalized == "hard":
        item["repetitions"] = reps + 1
        interval = max(1, round(old_interval * 1.7)) if old_interval else 1
        item["masteryState"] = "understanding"
    else:
        item["repetitions"] = reps + 1
        interval = max(3, round(old_interval * 2.4)) if old_interval else 3
        # 间隔拉到一周量级仍答对 = 已巩固（几天/几周后还能正确）
        item["masteryState"] = "retained" if interval >= 7 else "understanding"
    item["intervalDays"] = min(interval, 365)
    item["dueDate"] = (day + timedelta(days=item["intervalDays"])).isoformat()
    item["lastReviewedAt"] = _now()
    item["lastResult"] = normalized
    item["mastered"] = False


def record_review(item_id, result, path=None, today=None):
    """记录一次作答：again=重做、hard=可行候选、good=命中首选。"""
    path = _resolve_path(path)
    day = _today(today)
    normalized = result if result in ("again", "hard", "good") else "again"

    def update(item):
        _apply_review_result(item, normalized, day, path)

    return _update_item(item_id, update, path)


def record_graded_attempt(item_id, played_move, move_infos=None, color="B",
                          best_move=None, *, forced_score_lead=None,
                          forced_winrate=None, best_score_lead=None,
                          best_winrate=None, performance_label=None,
                          complexity=0.0, hint_used=False, thinking_time=None,
                          path=None, learning_path=None, today=None,
                          assessment=None):
    """按实际目损判分并记录一次作答（项目大纲 §20-23、§39）。

    与旧 grade_attempt 的区别：第 4 选只亏 0.4 目会判 good（不再是 again），
    第 2 选亏 5 目会判 again；榜外手允许传入强制分析结果，绝不直接判错。

    同时：
      - item.attempts[] 追加完整作答历史（每次落子/目损/判定/排名）；
      - 通过 (gameId, moveNo, color) 稳定 id 镜像写入 LearningEvent 的
        attempts[]（learning_store），供学习曲线与画像消费。
    返回 {"assessment": ..., "srs_result": ..., "item": ...}；题目不存在
    返回 None。
    """
    from candidate_assessment import assess_candidate, srs_result
    path = _resolve_path(path)
    item = get_item(item_id, path)
    if item is None:
        return None
    # 审查 P0-1：判分只算一次——调用方（UI 各入口）已算好的 assessment
    # 直接消费；只有旧调用（未传）才在此计算，且用同一上下文参数
    if assessment is None:
        assessment = assess_candidate(
            played_move, move_infos, color,
            forced_score_lead=forced_score_lead, forced_winrate=forced_winrate,
            best_score_lead=best_score_lead, best_winrate=best_winrate,
            performance_label=performance_label, complexity=complexity)
    result = srs_result(assessment["assessment"])
    day = _today(today)

    def update(cur):
        _apply_review_result(cur, result, day, path)
        # 审查 P0-3：attempts 只写 LearningEvent（单一事实源），
        # 书侧不再保存自己的作答历史，读取经 _overlay_attempts 投影。

    updated = _update_item(item_id, update, path)
    if updated is None:
        return None

    # 镜像到 LearningEvent（同一 (game, move, color) 稳定 id，不存在则跳过）
    try:
        from learning_event import event_id as _evt_id
        from learning_store import save_attempt as _save_attempt
        eid = _evt_id(updated.get("gameId"), updated.get("moveNo"),
                      updated.get("color"))
        _save_attempt(
            eid, played_move, score_loss=assessment.get("score_loss"),
            assessment=assessment.get("assessment"),
            ai_rank=assessment.get("ai_rank"), hint_used=hint_used,
            thinking_time=thinking_time,
            path=learning_path or os.path.join(
                os.path.dirname(os.path.abspath(path)), "learning_events.json"))
    except Exception:
        pass
    return {"assessment": assessment, "srs_result": result, "item": updated}


def postpone_item(item_id, days=1, path=None, today=None):
    path = _resolve_path(path)
    day = _today(today)

    def update(item):
        item["dueDate"] = (day + timedelta(days=max(1, int(days)))).isoformat()
        item["mastered"] = False

    return _update_item(item_id, update, path)


def set_mastered(item_id, mastered=True, path=None, today=None):
    """暂不复习（审查 P0-3：只改调度，不碰掌握状态）。

    手动"标记掌握"不再制造 retained——retained 的唯一定义是
    间隔复习后仍能独立解决。本操作只是把到期日推远/拉近，
    mastery_state 保持原样。
    """
    path = _resolve_path(path)
    day = _today(today)

    def update(item):
        item["mastered"] = bool(mastered)
        item["lastReviewedAt"] = _now()
        item["lastResult"] = "snoozed" if mastered else ""
        if mastered:
            item["dueDate"] = (day + timedelta(days=365)).isoformat()

    return _update_item(item_id, update, path)


def apply_training_outcomes(game_id, outcomes, path=None, today=None):
    """阶段训练结果回写（P0-4：委托 learning_store，source=training）。

    书侧只留派生缓存字段（ mastered 等 UI 标志），调度与掌握以事件为准。
    返回实际更新条数。
    """
    path = _resolve_path(path)
    game_id = str(game_id or "")
    if not game_id or not outcomes:
        return 0
    book = load_book(path)
    items = list(book.get("items") or [])
    day = _today(today)
    index = {}
    for item in items:
        try:
            mn = int(item.get("moveNo") or 0)
        except (TypeError, ValueError):
            continue
        index[(str(item.get("gameId")), mn, str(item.get("color")))] = item
    updated = 0
    for move_no, color, result in outcomes:
        try:
            mn = int(move_no)
        except (TypeError, ValueError):
            continue
        item = index.get((game_id, mn, str(color)))
        if item is None:
            continue
        normalized = result if result in ("again", "hard", "good") else "again"
        evt = None
        try:
            from learning_event import event_id as _eid
            from learning_store import apply_review_outcome
            evt = apply_review_outcome(
                _eid(item.get("gameId"), mn, str(color)), normalized,
                today=day, path=_learning_events_path(path), source="training")
        except Exception:
            evt = None
        if evt is not None:
            item["intervalDays"] = evt.review_interval_days
            item["dueDate"] = evt.review_due_date
            item["repetitions"] = evt.review_repetitions
            item["lapses"] = evt.review_lapses
            item["lastReviewedAt"] = evt.last_reviewed_at
            item["lastResult"] = normalized
            item["masteryState"] = evt.mastery_state
            item["mastered"] = normalized != "again"
        else:
            if normalized == "again":
                item["lapses"] = int(item.get("lapses") or 0) + 1
                item["repetitions"] = 0
                item["intervalDays"] = 1
                item["mastered"] = False
                previous = str(item.get("masteryState") or "new")
                item["masteryState"] = (
                    "understanding" if previous in ("understanding", "retained")
                    else previous)
            else:
                item["repetitions"] = int(item.get("repetitions") or 0) + 1
                old_interval = int(item.get("intervalDays") or 0)
                item["intervalDays"] = max(old_interval, 14)
                item["mastered"] = True
                item["masteryState"] = "understanding"
            item["dueDate"] = (day + timedelta(
                days=int(item["intervalDays"]))).isoformat()
            item["lastReviewedAt"] = _now()
            item["lastResult"] = normalized
        item["active"] = True
        updated += 1
    if updated:
        book["items"] = items
        save_book(book, path)
    return updated


def remove_game(game_id, path=None):
    """棋谱从本地库删除时一并移除其错题，避免悬空项目路径。"""
    path = _resolve_path(path)
    book = load_book(path)
    before = len(book.get("items") or [])
    book["items"] = [
        item for item in book.get("items") or []
        if str(item.get("gameId")) != str(game_id)
    ]
    removed = before - len(book["items"])
    if removed:
        save_book(book, path)
    return removed


def book_stats(path=None, today=None):
    path = _resolve_path(path)
    active = list_items(path, include_mastered=True, today=today)
    by_mastery = {}
    for item in active:
        state = str(item.get("masteryState") or "new")
        by_mastery[state] = by_mastery.get(state, 0) + 1
    return {
        "total": len(active),
        "due": sum(1 for item in active if item.get("isDue")),
        "mastered": sum(1 for item in active if item.get("mastered")),
        "reviewed": sum(1 for item in active if item.get("lastReviewedAt")),
        "by_mastery": by_mastery,
        "attempts": sum(len(item.get("attempts") or []) for item in active),
    }
