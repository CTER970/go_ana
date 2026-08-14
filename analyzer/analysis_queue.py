"""棋谱库整盘分析的持久化、可暂停队列。"""
from __future__ import annotations

import json
import os
from datetime import datetime


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class AnalysisQueue:
    def __init__(self, path):
        self.path = os.path.abspath(path)
        self.data = self._load()
        changed = False
        for task in self.data["tasks"]:
            if task.get("status") == "running":
                task["status"] = "queued"
                task["message"] = "上次运行中断，已安全恢复到队列"
                changed = True
        if changed:
            self._save()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict) and isinstance(raw.get("tasks"), list):
                raw.setdefault("version", 1)
                raw.setdefault("paused", False)
                return raw
        except Exception:
            pass
        return {"version": 1, "paused": False, "tasks": []}

    def _save(self):
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def tasks(self):
        return [dict(item) for item in self.data["tasks"]]

    def is_paused(self):
        return bool(self.data.get("paused"))

    def enqueue(self, record_id, name="", total=0):
        existing = next((t for t in self.data["tasks"] if t.get("recordId") == record_id), None)
        if existing:
            return dict(existing), False
        task = {
            "id": str(record_id), "recordId": str(record_id), "name": name or str(record_id),
            "status": "queued", "done": 0, "total": int(total or 0), "attempts": 0,
            "message": "等待分析", "createdAt": _now(), "updatedAt": _now(),
        }
        self.data["tasks"].append(task)
        self._save()
        return dict(task), True

    def claim_next(self):
        if self.is_paused():
            return None
        for task in self.data["tasks"]:
            if task.get("status") == "queued":
                task["status"] = "running"
                task["attempts"] = int(task.get("attempts") or 0) + 1
                task["message"] = "正在分析"
                task["updatedAt"] = _now()
                self._save()
                return dict(task)
        return None

    def update(self, task_id, done=None, total=None, message=None):
        task = self._find(task_id)
        if not task:
            return None
        if done is not None:
            task["done"] = int(done)
        if total is not None:
            task["total"] = int(total)
        if message is not None:
            task["message"] = str(message)
        task["updatedAt"] = _now()
        self._save()
        return dict(task)

    def finish(self, task_id):
        return self._status(task_id, "completed", "分析完成")

    def fail(self, task_id, message):
        return self._status(task_id, "failed", message or "分析失败")

    def release(self, task_id, message="等待继续"):
        return self._status(task_id, "queued", message)

    def pause(self):
        self.data["paused"] = True
        for task in self.data["tasks"]:
            if task.get("status") == "running":
                task["status"] = "paused"
                task["message"] = "已暂停；当前请求返回后保存进度"
        self._save()

    def resume(self):
        self.data["paused"] = False
        for task in self.data["tasks"]:
            if task.get("status") == "paused":
                task["status"] = "queued"
                task["message"] = "等待继续"
        self._save()

    def retry_failed(self):
        count = 0
        for task in self.data["tasks"]:
            if task.get("status") == "failed":
                task["status"] = "queued"
                task["message"] = "等待重试"
                task["updatedAt"] = _now()
                count += 1
        if count:
            self._save()
        return count

    def _find(self, task_id):
        return next((t for t in self.data["tasks"] if t.get("id") == str(task_id)), None)

    def _status(self, task_id, status, message):
        task = self._find(task_id)
        if not task:
            return None
        task["status"] = status
        task["message"] = str(message)
        task["updatedAt"] = _now()
        self._save()
        return dict(task)
