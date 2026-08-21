"""test_usage_log —— R0 使用埋点测试（临时目录隔离，不碰真实 game_library）。

覆盖：追加与读回、标量字段过滤、IO 失败静默、损坏行容忍、
大小滚动、summarize 聚合、总开关。
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import usage_log as ul


def check(name, cond, extra=""):
    print("[CHECK] %-42s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        raise AssertionError(name)


class _Env:
    """临时路径 + 状态现场保护（退出即恢复，防止泄漏到其他测试）。"""

    def __enter__(self):
        self.tmp = tempfile.mkdtemp(prefix="usage_log_test_")
        self.path = os.path.join(self.tmp, "usage_events.jsonl")
        self.enabled = ul._state["enabled"]
        self.state_path = ul._state["path"]
        ul.set_path(self.path)
        ul.set_enabled(True)
        return self

    def __exit__(self, *exc):
        ul.set_enabled(self.enabled)
        ul.set_path(self.state_path)
        shutil.rmtree(self.tmp, ignore_errors=True)


def test_append_and_readback():
    with _Env():
        ul.log_event("page_open", page="home")
        ul.log_event("review_started", games=1, ratio=0.5, flag=True)
        ul.log_event("page_open", page="library")
        events = ul.read_events()
        check("三条事件读回", len(events) == 3, str(len(events)))
        check("时间戳与事件名写入",
              all("ts" in e and e.get("event") for e in events))
        check("标量字段保留",
              events[1].get("page") is None
              and events[0].get("page") == "home"
              and events[1].get("games") == 1
              and events[1].get("ratio") == 0.5
              and events[1].get("flag") is True)
        # 非标量字段被过滤（防把对象/棋谱内容写进日志）
        ul.log_event("bad_fields", blob={"secret": 1}, lst=[1, 2],
                     good="ok", none_val=None)
        last = ul.read_events()[-1]
        check("非标量字段被过滤",
              "blob" not in last and "lst" not in last
              and "none_val" not in last and last.get("good") == "ok")


def test_silent_io_failure():
    with _Env():
        # 指向多层不存在的目录：open 追加必须失败，且埋点不允许抛出
        bad = os.path.join(
            tempfile.gettempdir(), "usage_log_no_dir", "x", "y.jsonl")
        ul.set_path(bad)
        try:
            ul.log_event("boom")
            check("IO 失败静默", True)
        except OSError as e:
            check("IO 失败静默", False, repr(e))


def test_corrupt_line_tolerated():
    with _Env():
        with open(ul._state["path"], "w", encoding="utf-8") as f:
            f.write("{broken json\n\n")
            f.write(json.dumps({"ts": "2026-08-21T10:00:00",
                                "event": "page_open"}) + "\n")
        events = ul.read_events()
        check("损坏行跳过、有效行保留", len(events) == 1
              and events[0].get("event") == "page_open", str(len(events)))


def test_rotation():
    with _Env():
        old_cap = ul.MAX_LOG_BYTES
        try:
            ul.MAX_LOG_BYTES = 300
            for i in range(20):
                ul.log_event("noise", seq=i, pad="x" * 40)
            old = ul._state["path"] + ".old"
            check("滚动产生 .old", os.path.exists(old))
            check("滚动后新文件低于上限",
                  os.path.getsize(ul._state["path"]) <= 300,
                  str(os.path.getsize(ul._state["path"])))
            check("滚动保住旧事件",
                  ul.read_events(old)[0].get("event") == "noise")
        finally:
            ul.MAX_LOG_BYTES = old_cap


def test_summarize():
    with _Env():
        ul.log_event("page_open", page="home")
        ul.log_event("page_open", page="library")
        ul.log_event("practice_started")
        # 手补两条历史日期，验证跨天聚合与排序
        with open(ul._state["path"], "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": "2026-07-01T09:00:00",
                                "event": "page_open"}) + "\n")
            f.write(json.dumps({"ts": "2026-07-01T21:00:00",
                                "event": "export_used"}) + "\n")
        stats = ul.summarize()
        check("事件按次数降序",
              list(stats) == ["page_open", "practice_started", "export_used"],
              str(list(stats)))
        po = stats["page_open"]
        check("page_open 聚合正确",
              po["count"] == 3 and po["days"] == 2
              and po["first"] == "2026-07-01" and po["last"],
              str(po))


def test_enabled_switch():
    with _Env():
        ul.set_enabled(False)
        ul.log_event("muted")
        check("关闭后不写文件",
              not os.path.exists(ul._state["path"]))
        ul.set_enabled(True)
        ul.log_event("unmuted")
        check("重开后恢复写入", len(ul.read_events()) == 1)


if __name__ == "__main__":
    print("=" * 60)
    print(" usage_log 测试（R0 使用埋点）")
    print("=" * 60)
    test_append_and_readback(); print()
    test_silent_io_failure(); print()
    test_corrupt_line_tolerated(); print()
    test_rotation(); print()
    test_summarize(); print()
    test_enabled_switch(); print()
    print("test_usage_log 全部通过 ✅")
