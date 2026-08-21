"""doc_sync_hook —— ZCode Stop 事件 hook 包装器。

由 D:\\katago\\.zcode\\config.json 的 hooks.events.Stop 调起（process 型）。
行为：
  1. mtime 门控：analyzer/ 下 .py 或根目录 *.md 自上次检查后有改动才跑
     完整检查（避免纯问答会话每次停止都白跑）。
  2. 运行 doc_sync.py --check；检测到漂移（输出含 ⚠ 或非零退出）时，
     以 {"decision":"block","reason":...} 请求会话继续修复文档；
     干净时静默退出 0。
  3. 无论何种内部故障都退出 0——hook 基础设施问题绝不阻断会话。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STAMP = os.path.join(HERE, "..", ".zcode", "doc-sync-stamp")
WATCHED_DIRS = [HERE]
WATCHED_GLOBS = ["*.py"]


def _newest_change(stamp_mtime: float) -> bool:
    """analyzer/*.py 或项目根 *.md 是否比 stamp 新。"""
    roots = [(HERE, ".py")]
    proj_root = os.path.join(HERE, "..")
    if os.path.isdir(proj_root):
        roots.append((proj_root, ".md"))
    for folder, suffix in roots:
        try:
            for name in os.listdir(folder):
                if name.endswith(suffix):
                    if os.path.getmtime(os.path.join(folder, name)) > stamp_mtime:
                        return True
        except OSError:
            continue
    return False


def _touch_stamp() -> None:
    try:
        os.makedirs(os.path.dirname(os.path.abspath(STAMP)), exist_ok=True)
        with open(STAMP, "a", encoding="utf-8"):
            os.utime(STAMP, None)
    except OSError:
        pass


def main() -> None:
    try:
        stamp_mtime = os.path.getmtime(STAMP) if os.path.exists(STAMP) else 0.0
        # 30 秒缓冲：避免本 hook 自身运行期间外部脚本触碰文件造成误判
        if not _newest_change(stamp_mtime - 30):
            _touch_stamp()
            return
        r = subprocess.run(
            [sys.executable, os.path.join(HERE, "doc_sync.py"), "--check"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            cwd=HERE,
        )
        out = (r.stdout or "") + (r.stderr or "")
        # ⚠ = 需手动补充（新模块/配置/UI）；📝 = 可自动更新（版本/计数漂移）；两者都算漂移
        drifted = ("⚠" in out) or ("📝" in out) or (r.returncode != 0)
        _touch_stamp()
        if drifted:
            tail = "\n".join(out.strip().splitlines()[-12:])
            print(
                json.dumps(
                    {
                        "decision": "block",
                        "reason": (
                            "文档漂移：doc_sync --check 检测到代码与文档不一致。"
                            "请在 analyzer/ 目录运行 `python doc_sync.py` 自动同步"
                            "（📝 项自动修复；⚠ 项需按提示手动补充文档），然后结束。报告节选：\n" + tail
                        ),
                    },
                    ensure_ascii=False,
                )
            )
    except Exception as exc:  # 任何包装器故障都不阻断会话
        print(json.dumps({"additionalContext": "doc_sync_hook 内部故障（已忽略）: %s" % exc}, ensure_ascii=False))
    finally:
        sys.exit(0)


if __name__ == "__main__":
    main()
