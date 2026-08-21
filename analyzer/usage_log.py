"""usage_log —— R0 极简本地使用埋点（减法重构的决策数据源）。

背景（最新改动要求.txt §十八）：删功能之前需要真实使用数据，否则
Keep / Merge / Hide / Delete 只能靠直觉。本模块是"够用就好"的本地
匿名事件日志：

- 单行 JSON 追加写（JSONL），任何 IO 失败静默跳过——埋点绝不影响主流程；
- 超过 2MB 滚动一次（保留一份 .old），磁盘占用有上界；
- 只记事件名 + 少量标量上下文 + 时间戳，不记棋谱内容、路径等隐私；
- summarize() 按 Reach（次数）/ Repeat（活跃天数）/ 最近使用聚合，
  供 R10 删减决策；``py usage_log.py`` 直接打印汇总表。

事件名沿用规划文档的蛇形英文（page_open / review_started / …）。
无头测试环境应调用 set_enabled(False)（adversarial_harness 已统一处理），
避免仿真流量污染真实使用数据。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))

MAX_LOG_BYTES = 2 * 1024 * 1024   # 超过即滚动（保留一份 .old）

_state = {"enabled": True, "path": None}
_lock = threading.Lock()

# 允许写入日志的字段值类型（防误把对象/棋谱内容写进日志）
_SCALARS = (str, int, float, bool)


def default_path():
    return os.path.join(HERE, "game_library", "usage_events.jsonl")


def set_enabled(enabled):
    """总开关（无头测试置 False，见 adversarial_harness.make_headless_app）。"""
    _state["enabled"] = bool(enabled)


def set_path(path):
    """重定向日志文件（测试用）；None 恢复默认路径。"""
    _state["path"] = path


def log_event(event, **fields):
    """追加一条使用事件。失败静默——埋点不允许影响任何主流程。"""
    if not _state["enabled"]:
        return
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": str(event)}
    for key, val in fields.items():
        if isinstance(val, _SCALARS) and val is not None:
            entry[str(key)] = val
    line = json.dumps(entry, ensure_ascii=False)
    path = _state["path"] or default_path()
    try:
        with _lock:
            _rotate_if_needed(path)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except OSError:
        pass


def _rotate_if_needed(path):
    try:
        if os.path.getsize(path) < MAX_LOG_BYTES:
            return
        old = path + ".old"
        if os.path.exists(old):
            os.remove(old)
        os.replace(path, old)
    except OSError:
        pass


def read_events(path=None):
    """读回全部事件（跳过损坏行）；本模块聚合与测试共用。"""
    path = path or _state["path"] or default_path()
    events = []
    if not os.path.exists(path):
        return events
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return events


def summarize(path=None):
    """按事件聚合：次数（Reach）、活跃天数（Repeat）、首次/最近（新陈）。

    返回 {event: {"count", "days", "first", "last"}}，按次数降序排列的
    dict（Python 3.7+ 保持插入序）。
    """
    agg = {}
    for e in read_events(path):
        name = e.get("event")
        if not name:
            continue
        day = str(e.get("ts", ""))[:10]
        slot = agg.setdefault(
            name, {"count": 0, "days": set(), "first": day or "?", "last": ""})
        slot["count"] += 1
        if day:
            slot["days"].add(day)
            if day < slot["first"]:
                slot["first"] = day
            if day > slot["last"]:
                slot["last"] = day
    out = {}
    for name, slot in sorted(agg.items(), key=lambda kv: -kv[1]["count"]):
        out[name] = {"count": slot["count"], "days": len(slot["days"]),
                     "first": slot["first"], "last": slot["last"]}
    return out


def _main():
    """CLI 汇总：``py usage_log.py`` —— R10 删减决策前先看这张表。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    stats = summarize()
    if not stats:
        print("暂无使用事件记录（%s）" % default_path())
        return
    print("%-26s %8s %6s %12s %12s" % ("事件", "次数", "活跃天", "首次", "最近"))
    for name, s in stats.items():
        print("%-26s %8d %6d %12s %12s" % (
            name, s["count"], s["days"], s["first"], s["last"]))


if __name__ == "__main__":
    _main()
