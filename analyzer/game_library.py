"""game_library —— 本地棋谱库。

棋谱库把原始 SGF 和一个可继续复盘的 .kga.json 项目快照保存在
``analyzer/game_library/``，并维护 ``index.json`` 供 UI 列表读取。

自动采集入口：把 SGF 放进 ``game_library/inbox/``，程序启动或打开棋谱库时会
扫描、去重、入库。重复扫描不会覆盖已有项目快照里的分析缓存。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime

from project_store import save_project
from sgf import import_sgf

HERE = os.path.dirname(os.path.abspath(__file__))
LIBRARY_DIR = os.path.join(HERE, "game_library")
INBOX_DIR = os.path.join(LIBRARY_DIR, "inbox")
SGF_DIR = os.path.join(LIBRARY_DIR, "sgf")
PROJECT_DIR = os.path.join(LIBRARY_DIR, "projects")
INDEX_PATH = os.path.join(LIBRARY_DIR, "index.json")
PROFILE_CACHE_PATH = os.path.join(LIBRARY_DIR, "profile_cache.json")
SUPPORTED_EXTENSIONS = (".sgf",)


def _ensure_dirs():
    os.makedirs(INBOX_DIR, exist_ok=True)
    os.makedirs(SGF_DIR, exist_ok=True)
    os.makedirs(PROJECT_DIR, exist_ok=True)
    os.makedirs(os.path.join(LIBRARY_DIR, "training_cache"), exist_ok=True)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _sha1(text):
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()


def _read_text(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _ensure_sgf_text(text):
    if "(;" not in text:
        raise ValueError("不是有效 SGF：缺少 (; 根节点")


def inbox_dir():
    """返回自动采集目录，并确保目录存在。"""
    _ensure_dirs()
    return INBOX_DIR


def _mainline_count(tree):
    n = tree.root
    count = 0
    while n.children:
        count += 1
        n = n.children[0]
    return count


def _analysis_counts(tree):
    """返回 (已分析节点数, 总节点数)，含根节点。"""
    done = 0
    total = 0

    def walk(node):
        nonlocal done, total
        total += 1
        if getattr(node, "analysis", None):
            done += 1
        for ch in node.children:
            walk(ch)

    walk(tree.root)
    return done, total


def _load_index():
    if not os.path.exists(INDEX_PATH):
        return {"version": 1, "records": []}
    try:
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("records"), list):
            return data
    except Exception:
        # 中断写入或手工损坏时尝试最近备份。
        backup = INDEX_PATH + ".bak"
        try:
            with open(backup, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("records"), list):
                return data
        except Exception:
            pass
    return {"version": 1, "records": []}


def _save_index(data):
    _ensure_dirs()
    tmp = INDEX_PATH + ".tmp"
    backup = INDEX_PATH + ".bak"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(INDEX_PATH):
            try:
                shutil.copy2(INDEX_PATH, backup)
            except OSError:
                pass
        os.replace(tmp, INDEX_PATH)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise


def list_records():
    """返回按最近活动时间倒序排列的棋谱库记录。"""
    data = _load_index()
    records = list(data.get("records") or [])
    records.sort(key=lambda r: (
        r.get("lastOpenedAt") or r.get("updatedAt") or r.get("importedAt", "")
    ), reverse=True)
    return records


def search_records(query):
    """按名称、规则、来源路径简单搜索。空查询返回全部。"""
    q = (query or "").strip().lower()
    records = list_records()
    if not q:
        return records
    out = []
    for rec in records:
        hay = " ".join(str(rec.get(k, "")) for k in ("name", "rules", "sourcePath", "id")).lower()
        if q in hay:
            out.append(rec)
    return out


def get_record(record_id):
    for rec in _load_index().get("records") or []:
        if rec.get("id") == record_id:
            return rec
    return None


def training_cache_path(record_id):
    _ensure_dirs()
    return os.path.join(LIBRARY_DIR, "training_cache", "%s.json" % record_id)


def load_training_cache(record_id):
    """Load a record's prepared training response tree."""
    if not record_id:
        return None
    path = training_cache_path(record_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def save_training_cache(record_id, package):
    """Atomically persist a prepared response tree and update its index status."""
    if not record_id or not isinstance(package, dict):
        return None
    path = training_cache_path(record_id)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(package, f, ensure_ascii=False, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise
    data = _load_index()
    for item in data.get("records", []):
        if item.get("id") == record_id:
            item["trainingCache"] = {
                "status": package.get("status", "ready"),
                "taskId": package.get("taskId"),
                "entries": len(package.get("entries") or {}),
                "rounds": int(package.get("preparedRounds") or 0),
                "updatedAt": package.get("updatedAt") or _now(),
                "signature": package.get("signature") or {},
            }
            item["updatedAt"] = _now()
            _save_index(data)
            return item
    return None


def clear_training_cache(record_id):
    """Remove a prepared response tree and its index metadata."""
    if not record_id:
        return False
    path = training_cache_path(record_id)
    removed = False
    if os.path.exists(path):
        try:
            os.remove(path)
            removed = True
        except OSError:
            pass
    data = _load_index()
    changed = False
    for item in data.get("records", []):
        if item.get("id") == record_id and "trainingCache" in item:
            item.pop("trainingCache", None)
            changed = True
            break
    if changed:
        _save_index(data)
    return removed or changed


def add_sgf_to_library(source_path, sgf_text, tree, rules="chinese", komi=7.5,
                       source_kind="manual", replace_project=True):
    """把 SGF 导入棋谱库，并生成项目快照。

    ``replace_project=False`` 用于自动扫描：若该 SGF 已入库，保留已有项目快照，
    避免把已经分析过的缓存覆盖成一份空快照。
    """
    _ensure_dirs()
    digest = _sha1(sgf_text)
    rec_id = digest[:16]
    name = os.path.basename(source_path) if source_path else ("%s.sgf" % rec_id)
    sgf_path = os.path.join(SGF_DIR, "%s.sgf" % rec_id)
    project_path = os.path.join(PROJECT_DIR, "%s.kga.json" % rec_id)
    abs_source = os.path.abspath(source_path) if source_path else ""
    data = _load_index()
    existing = None
    for rec in data.get("records", []):
        if rec.get("id") == rec_id:
            existing = rec
            break

    if existing and not replace_project:
        changed = False
        if not os.path.exists(sgf_path):
            with open(sgf_path, "w", encoding="utf-8") as f:
                f.write(sgf_text)
        if not existing.get("sgfPath"):
            existing["sgfPath"] = sgf_path; changed = True
        if not existing.get("projectPath"):
            existing["projectPath"] = project_path; changed = True
        if not existing.get("sourcePath") and abs_source:
            existing["sourcePath"] = abs_source; changed = True
        if not existing.get("sourceKind"):
            existing["sourceKind"] = source_kind; changed = True
        if changed:
            _save_index(data)
        return dict(existing)

    with open(sgf_path, "w", encoding="utf-8") as f:
        f.write(sgf_text)
    analyzed, total_nodes = _analysis_counts(tree)
    save_project(project_path, tree, rules=rules, komi=komi, meta={
        "libraryId": rec_id,
        "sourceName": name,
        "sourcePath": abs_source,
    })

    records = [r for r in data.get("records", []) if r.get("id") != rec_id]
    imported_at = (existing or {}).get("importedAt") or _now()
    rec = {
        "id": rec_id,
        "name": name,
        "sourcePath": abs_source,
        "sourceKind": source_kind,
        "sgfPath": sgf_path,
        "projectPath": project_path,
        "importedAt": imported_at,
        "updatedAt": _now() if existing else imported_at,
        "sha1": digest,
        "moves": _mainline_count(tree),
        "analyzed": analyzed,
        "totalNodes": total_nodes,
        "rules": rules,
        "komi": komi,
    }
    # 重复导入并刷新项目快照时保留训练、画像和身份元数据。
    if existing:
        for key in (
                "playerColor", "profileSide", "profileSummary",
                "trainingTask", "trainingSummary", "trainingCache",
                "trainingSessions", "lastTrainingAt", "lastOpenedAt"):
            if key in existing:
                rec[key] = existing[key]
    records.append(rec)
    data["records"] = records
    _save_index(data)
    return rec


def _iter_sgf_files(paths):
    """产出 paths 中的 SGF 文件；目录递归扫描。"""
    seen = set()
    for raw in paths:
        path = os.path.abspath(raw)
        if not os.path.exists(path):
            continue
        if os.path.isdir(path):
            for root, _dirs, files in os.walk(path):
                for name in sorted(files):
                    if name.lower().endswith(SUPPORTED_EXTENSIONS):
                        fp = os.path.join(root, name)
                        if fp not in seen:
                            seen.add(fp)
                            yield fp
        elif path.lower().endswith(SUPPORTED_EXTENSIONS) and path not in seen:
            seen.add(path)
            yield path


def scan_paths(paths, rules="chinese", komi=7.5, source_kind="inbox"):
    """扫描文件/目录并自动入库。

    返回 ``{"imported": [...], "duplicates": [...], "failed": [...]}``。
    已存在的棋谱按 SGF 内容 SHA1 去重，不覆盖已有分析缓存。
    """
    _ensure_dirs()
    imported, duplicates, failed = [], [], []
    known = {rec.get("id") for rec in _load_index().get("records", [])}
    for path in _iter_sgf_files(paths):
        try:
            text = _read_text(path)
            _ensure_sgf_text(text)
            digest = _sha1(text)
            rec_id = digest[:16]
            if rec_id in known:
                rec = add_sgf_to_library(path, text, None, rules=rules, komi=komi,
                                         source_kind=source_kind, replace_project=False)
                duplicates.append(rec)
                continue
            tree = import_sgf(text)
            rec = add_sgf_to_library(path, text, tree, rules=rules, komi=komi,
                                     source_kind=source_kind, replace_project=False)
            known.add(rec.get("id"))
            imported.append(rec)
        except Exception as e:
            failed.append({"path": path, "error": str(e)})
    return {"imported": imported, "duplicates": duplicates, "failed": failed}


def scan_inbox(rules="chinese", komi=7.5):
    """扫描自动采集目录 ``game_library/inbox``。"""
    return scan_paths([inbox_dir()], rules=rules, komi=komi)


def import_sgf_text(text, rules="chinese", komi=7.5, name="粘贴棋谱.sgf"):
    """把粘贴的 SGF 文本安全入库；按内容哈希去重且不覆盖已有分析。"""
    _ensure_sgf_text(text)
    digest = _sha1(text)
    existing = get_record(digest[:16])
    if existing:
        return dict(existing), False
    tree = import_sgf(text)
    rec = add_sgf_to_library(
        None, text, tree, rules=rules, komi=komi,
        source_kind="paste", replace_project=False)
    # 无来源路径时使用可读名称；修改索引不触碰项目缓存。
    data = _load_index()
    for item in data.get("records", []):
        if item.get("id") == rec.get("id"):
            item["name"] = str(name or "粘贴棋谱.sgf")
            rec = dict(item)
            _save_index(data)
            break
    return rec, True


def update_project_snapshot(record_id, tree, rules="chinese", komi=7.5):
    """更新某条记录的项目快照，用于后续把新分析缓存写回库。"""
    rec = get_record(record_id)
    if not rec:
        return None
    project_path = rec.get("projectPath")
    if not project_path:
        project_path = os.path.join(PROJECT_DIR, "%s.kga.json" % record_id)
        rec["projectPath"] = project_path
    analyzed, total_nodes = _analysis_counts(tree)
    profile_summary = _build_profile_summary_for_tree(
        tree, rec, rules=rules, komi=komi)
    save_project(project_path, tree, rules=rules, komi=komi, meta={
        "libraryId": record_id,
        "sourceName": rec.get("name", ""),
        "sourcePath": rec.get("sourcePath", ""),
    })
    data = _load_index()
    for item in data.get("records", []):
        if item.get("id") == record_id:
            item["projectPath"] = project_path
            item["moves"] = _mainline_count(tree)
            item["analyzed"] = analyzed
            item["totalNodes"] = total_nodes
            item["rules"] = rules
            item["komi"] = komi
            if profile_summary is not None:
                item["profileSummary"] = profile_summary
            task = build_training_task(tree, item.get("playerColor"))
            if task:
                item["trainingTask"] = task
                item["trainingSummary"] = task.get("summary", "")
            item["updatedAt"] = _now()
            _save_index(data)
            if profile_summary is not None:
                mark_profile_cache_stale()
                _sync_mistake_book(item, profile_summary)
            return item
    return rec


def _build_profile_summary_for_tree(tree, record, rules="chinese", komi=7.5):
    """主线分析完整时生成 reviewSummaryV2 与单局轻量画像。"""
    try:
        from move_quality import VERSION as QUALITY_VERSION
        from player_profile import build_game_profile_summary
        from review import ReviewReport

        rr = ReviewReport(tree)
        nodes = rr.mainline_nodes()
        if len(nodes) <= 1 or any(getattr(node, "analysis", None) is None for node in nodes):
            return None
        root_info = (nodes[0].analysis or {}).get("rootInfo") or {}
        saved_signature = dict(getattr(tree, "_analysis_signature", {}) or {})
        visits = root_info.get("visits")
        if visits is None:
            visits = saved_signature.get("visits")
        signature = saved_signature or {
            "model": getattr(tree, "_analysis_model", None),
            "rules": rules,
            "komi": komi,
            "visits": visits,
            "quality_version": QUALITY_VERSION,
        }
        review_summary = rr.review_summary_v2(
            visits=visits, analysis_signature=signature)
        tree._review_summary_v2 = review_summary
        side = record.get("profileSide", "unknown")
        tree._profile_side = side
        summary = build_game_profile_summary(
            rr.move_quality_results(visits=visits, include_unknown=True),
            game_id=record.get("id", ""),
            game_name=record.get("name", ""),
            black_player=getattr(tree, "_sgf_pb", None),
            white_player=getattr(tree, "_sgf_pw", None),
            profile_side=side,
            model=getattr(tree, "_analysis_model", None),
            visits=visits,
            analysis_signature=signature,
            analyzed_at=_now(),
        ).to_dict()
        summary["updatedAt"] = _now()
        return summary
    except Exception:
        # 摘要是可重建的可选数据，绝不能阻断项目快照。
        return None


def mark_profile_cache_stale():
    """使长期画像缓存失效；下一次打开画像窗口时会按索引重建。"""
    for path in (PROFILE_CACHE_PATH, PROFILE_CACHE_PATH + ".tmp"):
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


def _sync_mistake_book(record, summary):
    """画像摘要写入后同步复习队列；失败不影响棋谱库主流程。"""
    try:
        from mistake_book import sync_profile_summary
        sync_profile_summary(
            record, summary,
            path=os.path.join(LIBRARY_DIR, "mistake_book.json"))
    except Exception:
        pass


def update_game_profile_summary(record_id, summary):
    """保存单局轻量画像摘要，不复制完整 analysis。"""
    if not record_id or not isinstance(summary, dict):
        return None
    data = _load_index()
    for item in data.get("records", []):
        if item.get("id") != record_id:
            continue
        payload = dict(summary)
        payload.setdefault("version", 1)
        payload["updatedAt"] = _now()
        item["profileSummary"] = payload
        item["updatedAt"] = _now()
        _save_index(data)
        mark_profile_cache_stale()
        _sync_mistake_book(item, payload)
        return item
    return None


def update_profile_side(record_id, side):
    """设置独立于训练 playerColor 的长期画像执棋方。"""
    normalized = side if side in ("B", "W", "both", "unknown") else "unknown"
    data = _load_index()
    for item in data.get("records", []):
        if item.get("id") != record_id:
            continue
        item["profileSide"] = normalized
        item["updatedAt"] = _now()
        summary = item.get("profileSummary")
        if isinstance(summary, dict):
            summary["user_side"] = normalized
            summary["updatedAt"] = _now()
        _save_index(data)
        mark_profile_cache_stale()
        if isinstance(summary, dict):
            _sync_mistake_book(item, summary)
        return item
    return None


def get_recent_profile_summaries(limit=30):
    """返回最近有画像摘要的棋局，按旧到新排列，便于趋势比较。"""
    records = [
        item for item in _load_index().get("records", [])
        if isinstance(item.get("profileSummary"), dict)
    ]
    records.sort(key=lambda item: (
        item.get("lastOpenedAt") or item.get("updatedAt")
        or item.get("importedAt", "")
    ), reverse=True)
    if limit and int(limit) > 0:
        records = records[:int(limit)]
    out = []
    for item in reversed(records):
        out.append({
            "id": item.get("id"),
            "name": item.get("name", ""),
            "profileSide": item.get("profileSide", "unknown"),
            "updatedAt": item.get("updatedAt", ""),
            "profileSummary": dict(item.get("profileSummary") or {}),
        })
    return out


def get_recent_style_records(limit=30):
    """返回棋风分析所需的摘要，按旧到新排列。

    只读取项目顶层的 ``reviewSummaryV2``，不重建棋树；损坏或缺少摘要的
    项目安全降级为空 moveQuality，由棋风模块输出样本不足。
    """
    records = [
        item for item in _load_index().get("records", [])
        if isinstance(item.get("profileSummary"), dict)
    ]
    records.sort(key=lambda item: (
        item.get("lastOpenedAt") or item.get("updatedAt")
        or item.get("importedAt", "")
    ), reverse=True)
    if limit and int(limit) > 0:
        records = records[:int(limit)]
    out = []
    for item in reversed(records):
        review_summary = {}
        project_path = item.get("projectPath")
        if project_path and os.path.exists(project_path):
            try:
                with open(project_path, "r", encoding="utf-8") as f:
                    project = json.load(f)
                if isinstance(project.get("reviewSummaryV2"), dict):
                    review_summary = dict(project["reviewSummaryV2"])
            except (OSError, ValueError, TypeError):
                review_summary = {}
        out.append({
            "id": item.get("id"),
            "name": item.get("name", ""),
            "profileSide": item.get("profileSide", "unknown"),
            "projectPath": project_path,
            "rules": item.get("rules", ""),
            "komi": item.get("komi"),
            "updatedAt": item.get("updatedAt", ""),
            "profileSummary": dict(item.get("profileSummary") or {}),
            "reviewSummaryV2": review_summary,
        })
    return out


def build_training_task(tree, player_color=None):
    """根据已分析主线生成棋局库训练题；分析不足时返回 None。"""
    try:
        from training import generate_training_task
        return generate_training_task(tree, player_color=player_color)
    except Exception:
        return None


def refresh_training_task(record_id, tree):
    """刷新某条记录的最差阶段训练题。"""
    data = _load_index()
    for item in data.get("records", []):
        if item.get("id") == record_id:
            task = build_training_task(tree, item.get("playerColor"))
            if not task:
                return None
            item["trainingTask"] = task
            item["trainingSummary"] = task.get("summary", "")
            item["updatedAt"] = _now()
            _save_index(data)
            return item
    return None


def update_training_settings(record_id, player_color=None):
    """更新训练设置：player_color 为 B/W/both。"""
    if not record_id:
        return None
    try:
        from training import normalize_player_color
        color = normalize_player_color(player_color)
    except Exception:
        color = player_color or "both"
    data = _load_index()
    for item in data.get("records", []):
        if item.get("id") == record_id:
            item["playerColor"] = color
            item.pop("trainingCache", None)
            item["updatedAt"] = _now()
            _save_index(data)
            path = training_cache_path(record_id)
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
            return item
    return None


def append_training_session(record_id, session):
    """把一次训练评价追加到棋局库索引，供下次打开棋局库查看。"""
    if not record_id or not session:
        return None
    data = _load_index()
    for item in data.get("records", []):
        if item.get("id") == record_id:
            sessions = list(item.get("trainingSessions") or [])
            sessions.append(session)
            item["trainingSessions"] = sessions[-20:]
            item["lastTrainingAt"] = session.get("createdAt") or _now()
            item["updatedAt"] = _now()
            _save_index(data)
            return item
    return None


def touch_record(record_id):
    """标记记录最近打开时间。"""
    data = _load_index()
    for item in data.get("records", []):
        if item.get("id") == record_id:
            item["lastOpenedAt"] = _now()
            _save_index(data)
            return item
    return None


def delete_record(record_id, delete_files=True):
    """从棋谱库删除记录；默认删除库内 SGF 与项目快照，不删除原始来源文件。"""
    data = _load_index()
    records = data.get("records", [])
    keep = []
    target = None
    for rec in records:
        if rec.get("id") == record_id:
            target = rec
        else:
            keep.append(rec)
    if target is None:
        return False
    if delete_files:
        for key in ("sgfPath", "projectPath"):
            path = target.get(key)
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
        cache_path = training_cache_path(record_id)
        if os.path.exists(cache_path):
            try:
                os.remove(cache_path)
            except OSError:
                pass
    data["records"] = keep
    _save_index(data)
    try:
        from mistake_book import remove_game
        remove_game(
            record_id, path=os.path.join(LIBRARY_DIR, "mistake_book.json"))
    except Exception:
        pass
    if isinstance(target.get("profileSummary"), dict):
        mark_profile_cache_stale()
    return True
