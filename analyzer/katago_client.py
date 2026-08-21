"""KataGoAnalysisClient —— 与 `katago analysis` 进程通信的封装。

协议（见 KataGo/docs/Analysis_Engine.md）：
  * 启动: katago analysis -config <cfg> -model <net>
  * stdin  写单行 JSON 查询；stdout 读单行 JSON 响应（异步、按 id 匹配）
  * 启动后发 query_version 探测就绪

线程模型：
  * 一个后台读线程持续读 stdout，按 id 把响应塞进队列
  * UI 主线程通过 poll() 取队列并更新界面（tkinter 非线程安全，只在主线程动 UI）
  * 另一线程排空 stderr，防止缓冲区塞满导致进程阻塞
"""
from __future__ import annotations

import json
import queue
import subprocess
import threading
from collections import deque


class KataGoAnalysisClient:
    def __init__(self, exe_path: str, config_path: str, model_path: str,
                 cwd: str = None, human_model_path: str = None):
        self.exe_path = exe_path
        self.config_path = config_path
        self.model_path = model_path
        self.cwd = cwd
        # Human SL 模型（大纲 §6）：启动时文件不存在则自动丢弃，回退普通 KataGo
        self.human_model_path = human_model_path
        self.human_model_active = False
        self.proc = None
        self._counter = 0
        self._send_lock = threading.Lock()
        self._results = queue.Queue()           # (id, resp)
        self._stderr_lines = deque(maxlen=300)
        self.ready = False
        self.started = False
        self.version_info = None

    # ---- 生命周期 ----
    def human_model_usable(self) -> bool:
        """Human SL 模型是否会被实际启用：路径非空且文件存在。

        无须启动进程即可查询（与 command_args 的 -human-model 判定同源），
        供 UI/诊断显示 Human SL 可用性；启动后的实际状态看
        human_model_active（start() 时按本函数置位）。
        """
        import os
        return bool(self.human_model_path and os.path.exists(self.human_model_path))

    def command_args(self):
        """构造命令行：analysis -config … -model … [-human-model …]。"""
        args = [self.exe_path, "analysis",
                "-config", self.config_path, "-model", self.model_path]
        if self.human_model_usable():
            args += ["-human-model", self.human_model_path]
        return args

    def start(self):
        if self.started:
            return
        self.started = True
        self.human_model_active = self.human_model_usable()
        self.proc = subprocess.Popen(
            self.command_args(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.cwd,
        )
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        # 探测就绪
        self._send_raw({"id": "__version__", "action": "query_version"})

    def _read_stdout(self):
        while True:
            line = self.proc.stdout.readline()
            if not line:
                break
            try:
                resp = json.loads(line.decode("utf-8", "replace").strip())
            except Exception:
                continue
            rid = resp.get("id")
            if rid == "__version__" and "version" in resp:
                self.ready = True
                self.version_info = resp
            self._results.put((rid, resp))

    def _read_stderr(self):
        while self.proc.poll() is None:
            line = self.proc.stderr.readline()
            if not line:
                break
            self._stderr_lines.append(line.decode("utf-8", "replace").rstrip())

    def _send_raw(self, obj: dict):
        data = (json.dumps(obj) + "\n").encode("utf-8")
        with self._send_lock:
            if self.proc and self.proc.stdin:
                self.proc.stdin.write(data)
                self.proc.stdin.flush()

    # ---- 业务 ----
    def analyze(self, query: dict) -> str:
        """提交一个分析查询，返回本次 id。结果稍后经 poll() 取回。"""
        with self._send_lock:
            self._counter += 1
            qid = "q%d" % self._counter
        q = dict(query)
        q["id"] = qid
        self._send_raw(q)
        return qid

    def poll(self):
        """取回所有已就绪的 (id, resp)。"""
        out = []
        while True:
            try:
                out.append(self._results.get_nowait())
            except queue.Empty:
                break
        return out

    def recent_stderr(self, n: int = 30):
        return list(self._stderr_lines)[-n:]

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        try:
            if self.proc and self.proc.stdin:
                self.proc.stdin.close()
        except Exception:
            pass
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
        except Exception:
            pass
