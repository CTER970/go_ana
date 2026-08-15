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
DEFAULT_PATH = os.path.join(HERE, "game_library", "mistake_book.json")
BOOK_VERSION = 1


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


def load_book(path=DEFAULT_PATH):
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


def save_book(book, path=DEFAULT_PATH):
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


def sync_profile_summary(record, summary=None, path=DEFAULT_PATH, today=None):
    """把单局画像中的高价值问题手增量同步到错题本。

    ``profileSide`` 为 B/W 时只收录对应一方；``both`` 收录双方；
    身份未知时不创建题目。同一棋局重新分析后，旧题的调度进度会保留。
    """
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


def list_items(path=DEFAULT_PATH, *, due_only=False, include_mastered=False, today=None):
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
    return out


def get_item(item_id, path=DEFAULT_PATH):
    for item in load_book(path).get("items") or []:
        if str(item.get("id")) == str(item_id):
            return dict(item)
    return None


def grade_attempt(best_move, played_move, move_infos, accepted_rank=3):
    """按父局面的候选顺序判定一次测验落子，返回 ``(result, rank)``。

    .. deprecated:: 学习系统改造后排名判分已被实际目损判分取代
       （candidate_assessment.assess_candidate）；本函数仅为旧调用兼容保留。
    """
    best = str(best_move or "").lower()
    played = str(played_move or "").lower()
    rank = None
    ordered = sorted(
        move_infos or [], key=lambda value: value.get("order", 999))
    for idx, info in enumerate(ordered):
        if str(info.get("move") or "").lower() == played:
            rank = int(info.get("order", idx)) + 1
            break
    if played and played == best:
        return "good", rank or 1
    if rank is not None and rank <= max(1, int(accepted_rank)):
        return "hard", rank
    return "again", rank


def _learning_events_path(book_path):
    return os.path.join(
        os.path.dirname(os.path.abspath(book_path)), "learning_events.json")


def _mirror_mastery_to_events(item, book_path=DEFAULT_PATH):
    """掌握状态镜像：错题本是唯一更新入口，LearningEvent 必须同步（反馈 #6）。

    经 set_event_mastery 直写（绕过 save_event 的进度合并），确保
    "复习会了"在两边同时成立，长期画像读 LearningEvent 不再看到旧状态。
    """
    try:
        from learning_event import event_id as _evt_id
        from learning_store import set_event_mastery
        set_event_mastery(
            _evt_id(item.get("gameId"), item.get("moveNo"), item.get("color")),
            str(item.get("masteryState") or "new"),
            path=_learning_events_path(book_path))
    except Exception:
        pass


def _update_item(item_id, updater, path=DEFAULT_PATH):
    book = load_book(path)
    for item in book.get("items") or []:
        if str(item.get("id")) != str(item_id):
            continue
        updater(item)
        save_book(book, path)
        _mirror_mastery_to_events(item, path)
        return dict(item)
    return None


def _apply_review_result(item, normalized, day):
    """单条 item 的间隔复习调度 + 掌握状态流转（保留原有间隔算法）。"""
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


def record_review(item_id, result, path=DEFAULT_PATH, today=None):
    """记录一次作答：again=重做、hard=可行候选、good=命中首选。"""
    day = _today(today)
    normalized = result if result in ("again", "hard", "good") else "again"

    def update(item):
        _apply_review_result(item, normalized, day)

    return _update_item(item_id, update, path)


def record_graded_attempt(item_id, played_move, move_infos=None, color="B",
                          best_move=None, *, forced_score_lead=None,
                          forced_winrate=None, best_score_lead=None,
                          best_winrate=None, performance_label=None,
                          complexity=0.0, hint_used=False, thinking_time=None,
                          path=DEFAULT_PATH, learning_path=None, today=None):
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
    item = get_item(item_id, path)
    if item is None:
        return None
    assessment = assess_candidate(
        played_move, move_infos, color,
        forced_score_lead=forced_score_lead, forced_winrate=forced_winrate,
        best_score_lead=best_score_lead, best_winrate=best_winrate,
        performance_label=performance_label, complexity=complexity)
    result = srs_result(assessment["assessment"])
    day = _today(today)

    def update(cur):
        _apply_review_result(cur, result, day)
        cur.setdefault("attempts", []).append({
            "date": _now(),
            "playedMove": str(played_move or ""),
            "scoreLoss": assessment.get("score_loss"),
            "assessment": assessment.get("assessment"),
            "assessmentLabel": assessment.get("assessment_label"),
            "aiRank": assessment.get("ai_rank"),
            "result": result,
            "hintUsed": bool(hint_used),
            "thinkingTime": thinking_time,
        })

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


def postpone_item(item_id, days=1, path=DEFAULT_PATH, today=None):
    day = _today(today)

    def update(item):
        item["dueDate"] = (day + timedelta(days=max(1, int(days)))).isoformat()
        item["mastered"] = False

    return _update_item(item_id, update, path)


def set_mastered(item_id, mastered=True, path=DEFAULT_PATH):
    def update(item):
        item["mastered"] = bool(mastered)
        item["lastReviewedAt"] = _now()
        item["lastResult"] = "mastered" if mastered else ""
        # 手动标记掌握 = 已巩固；"transferred" 只能由实战数据判定，不在此设置
        item["masteryState"] = "retained" if mastered else "understanding"

    return _update_item(item_id, update, path)


def apply_training_outcomes(game_id, outcomes, path=DEFAULT_PATH, today=None):
    """把一次阶段训练的结果回写到错题本间隔复习调度。

    ``outcomes`` 为 ``[(move_no, color, result), ...]``，其中 ``move_no``/``color``
    为【原实战】手数与执棋方（与错题本 item 的 moveNo/color 对齐），``result`` ∈
    ``{"again", "good"}``：``again`` 表示训练中重复犯错（重置间隔到 1 天、计一次 lapse），
    ``good`` 表示训练中已改善（标记掌握、间隔至少 14 天）。

    只更新【已存在】于错题本且 (gameId, moveNo, color) 三元组匹配的 item，找不到则跳过
    （不凭空造题——错题本入库仍由 sync_profile_summary 统一负责）。返回实际更新条数。
    """
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
        if normalized == "again":
            item["lapses"] = int(item.get("lapses") or 0) + 1
            item["repetitions"] = 0
            item["intervalDays"] = 1
            item["mastered"] = False
            # 训练/复习复发（review recurrence）≠ 实战复发（real-game
            # recurrence）：这里只降档巩固状态，不设 unstable——
            # unstable 只能由真实棋局再次出现同类错误触发
            # （learning_store.sync_profile_summary 的实战迁移逻辑）。
            previous = str(item.get("masteryState") or "new")
            item["masteryState"] = (
                "understanding" if previous == "retained" else previous)
        else:  # good —— 训练中已改善
            item["repetitions"] = int(item.get("repetitions") or 0) + 1
            old_interval = int(item.get("intervalDays") or 0)
            item["intervalDays"] = max(old_interval, 14)
            item["mastered"] = True
            item["masteryState"] = "retained"
        item["dueDate"] = (day + timedelta(days=int(item["intervalDays"]))).isoformat()
        item["lastReviewedAt"] = _now()
        item["lastResult"] = normalized
        item["active"] = True
        updated += 1
    if updated:
        book["items"] = items
        save_book(book, path)
    return updated


def remove_game(game_id, path=DEFAULT_PATH):
    """棋谱从本地库删除时一并移除其错题，避免悬空项目路径。"""
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


def book_stats(path=DEFAULT_PATH, today=None):
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
