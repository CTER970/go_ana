"""棋风关键结论的高 visits 复核队列与稳定性判断。"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional

HERE = os.path.dirname(os.path.abspath(__file__))
_FILE_NAME = "deep_verification.json"

# ---- 默认路径调用时解析（usage_log.set_path 同款约定）----
# 历史教训（W29 审查）：def f(path=DEFAULT_PATH) 在导入期把路径固化进
# 默认参数，重定向对"走默认值"的调用无效，数据可能写错位置。
_state = {"path": None}


def default_path():
    """内置默认路径（不受 set_path 重定向影响）。"""
    return os.path.join(HERE, "game_library", _FILE_NAME)


# 兼容引用：运行期生效的默认以 get_path() 为准。
DEFAULT_PATH = default_path()
VERSION = 1


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


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class DeepVerificationTask:
    task_id: str
    source: str = "style_profile"
    conclusion_key: str = ""
    conclusion_label: str = ""
    game_id: str = ""
    game_name: str = ""
    project_path: str = ""
    move_no: int = 0
    color: str = ""
    played_move: str = ""
    original_quality: str = ""
    original_score_loss: Optional[float] = None
    original_visits: Optional[int] = None
    target_visits: int = 800
    status: str = "pending"
    created_at: str = ""
    finished_at: Optional[str] = None
    result: Optional[dict] = None
    version: int = VERSION

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, raw):
        data = dict(raw or {})
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: data[key] for key in allowed if key in data})


@dataclass
class VerifiedStyleFinding:
    conclusion_key: str
    conclusion_label: str
    checked_samples: int = 0
    stable_samples: int = 0
    changed_samples: int = 0
    original_avg_score_loss: Optional[float] = None
    verified_avg_score_loss: Optional[float] = None
    stability: str = "insufficient"
    confidence: str = "low"
    message: str = ""
    sample_results: list = field(default_factory=list)
    version: int = VERSION

    def to_dict(self):
        return asdict(self)


def _task_id(conclusion_key, game_id, move_no):
    raw = "%s|%s|%s" % (conclusion_key, game_id, move_no)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:18]


def build_verification_tasks(style_profile, growth_path,
                             max_samples_per_finding=3, target_visits=800):
    """只为已进入成长路线修正项的代表局面生成任务。"""
    tasks = []
    for finding in growth_path.verification_required:
        moves = finding.get("representative_moves") or []
        for move in moves[:max(0, int(max_samples_per_finding))]:
            game_id = str(move.get("game_id") or "")
            move_no = int(move.get("move_no", move.get("moveNo", 0)) or 0)
            if not game_id or move_no <= 0:
                continue
            tasks.append(DeepVerificationTask(
                task_id=_task_id(finding.get("key"), game_id, move_no),
                conclusion_key=finding.get("key") or "",
                conclusion_label=finding.get("label") or "",
                game_id=game_id,
                game_name=move.get("game_name") or game_id,
                project_path=move.get("project_path") or "",
                move_no=move_no,
                color=move.get("color") or "",
                played_move=move.get("played_move", move.get("playedMove", "")) or "",
                original_quality=move.get(
                    "quality_key", move.get("qualityKey", "")) or "",
                original_score_loss=move.get(
                    "score_loss", move.get("scoreLoss")),
                original_visits=move.get("visits"),
                target_visits=int(target_visits),
                status="pending",
                created_at=_now(),
            ))
    return tasks


def is_stable(original_loss, verified_loss, original_quality, verified_quality):
    if original_quality == verified_quality:
        return True
    if original_loss is not None and verified_loss is not None:
        return abs(float(original_loss) - float(verified_loss)) <= 1.5
    return False


def result_stability(task, result):
    if is_stable(
            task.original_score_loss, result.get("score_loss"),
            task.original_quality, result.get("quality_key")):
        return "stable"
    original = task.original_quality
    verified = result.get("quality_key")
    if (original, verified) in (
            ("blunder", "inaccuracy"), ("inaccuracy", "blunder")):
        return "partially_changed"
    return "unstable"


def summarize_verified_findings(tasks):
    groups = {}
    for task in tasks:
        if isinstance(task, dict):
            task = DeepVerificationTask.from_dict(task)
        if task.status != "done" or not isinstance(task.result, dict):
            continue
        groups.setdefault(task.conclusion_key, []).append(task)
    findings = []
    for key, items in groups.items():
        statuses = [
            result_stability(item, item.result) for item in items]
        stable = statuses.count("stable")
        changed = len(items) - stable
        if len(items) < 2:
            stability = "insufficient"
        elif stable / len(items) >= 0.7:
            stability = "stable"
        elif stable == 0:
            stability = "unstable"
        else:
            stability = "partially_changed"
        confidence = (
            "high" if len(items) >= 4 and stability == "stable"
            else "medium" if len(items) >= 2 else "low")
        original_losses = [
            float(item.original_score_loss) for item in items
            if item.original_score_loss is not None]
        verified_losses = [
            float(item.result.get("score_loss")) for item in items
            if item.result.get("score_loss") is not None]
        label = items[0].conclusion_label
        message = (
            "“%s”已复核 %d 个样本，其中 %d 个严重度稳定；%s。"
            % (
                label, len(items), stable,
                "结论可信度提升" if stability == "stable"
                else "结论需降级或继续观察"))
        findings.append(VerifiedStyleFinding(
            conclusion_key=key,
            conclusion_label=label,
            checked_samples=len(items),
            stable_samples=stable,
            changed_samples=changed,
            original_avg_score_loss=(
                sum(original_losses) / len(original_losses)
                if original_losses else None),
            verified_avg_score_loss=(
                sum(verified_losses) / len(verified_losses)
                if verified_losses else None),
            stability=stability,
            confidence=confidence,
            message=message,
            sample_results=[
                dict(item.result, task_id=item.task_id, stability=status)
                for item, status in zip(items, statuses)],
        ))
    return findings


def load_store(path=None):
    path = _resolve_path(path)
    if not os.path.exists(path):
        return {"version": VERSION, "tasks": [], "verifiedFindings": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"version": VERSION, "tasks": [], "verifiedFindings": []}
    return data if isinstance(data, dict) else {
        "version": VERSION, "tasks": [], "verifiedFindings": []}


def save_store(tasks, path=None):
    path = _resolve_path(path)
    task_list = [
        item.to_dict() if hasattr(item, "to_dict") else dict(item)
        for item in tasks]
    findings = [
        item.to_dict() for item in summarize_verified_findings(task_list)]
    data = {
        "version": VERSION,
        "tasks": task_list,
        "verifiedFindings": findings,
        "updatedAt": _now(),
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return data


def merge_and_save_tasks(tasks, path=None):
    path = _resolve_path(path)
    existing = {
        item.get("task_id"): item
        for item in load_store(path).get("tasks") or []}
    for task in tasks:
        raw = task.to_dict() if hasattr(task, "to_dict") else dict(task)
        old = existing.get(raw.get("task_id"))
        if old and old.get("status") == "done":
            continue
        existing[raw.get("task_id")] = raw
    return save_store(list(existing.values()), path)


def update_task_result(task_id, result=None, error=None, path=None):
    path = _resolve_path(path)
    tasks = load_store(path).get("tasks") or []
    for item in tasks:
        if item.get("task_id") != task_id:
            continue
        if error:
            item["status"] = "failed"
            item["result"] = {"error": str(error)}
        else:
            item["status"] = "done"
            item["result"] = dict(result or {})
        item["finished_at"] = _now()
        break
    return save_store(tasks, path)


def set_task_status(task_id, status, path=None):
    path = _resolve_path(path)
    tasks = load_store(path).get("tasks") or []
    for item in tasks:
        if item.get("task_id") == task_id:
            item["status"] = status
            break
    return save_store(tasks, path)
