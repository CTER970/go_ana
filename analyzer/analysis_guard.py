"""AnalysisGuard —— 分析请求守卫，防止异步结果污染（跨引擎实例的过期结果丢弃）。

纯 Python（不依赖 tkinter / KataGo），可被 test_analysis_request_guard.py 无头测试。
规则：
  * new_session()：每次（重新）启动引擎时调用，session +1 并清空挂账；
  * register(qid, node)：记下请求所属 session；
  * take(qid)：返回 (node, accepted)；若 qid 属于已失效的旧 session，accepted=False（丢弃）；
  * 只有 node is current_node 时 UI 层才刷新显示（在 app._poll_loop 里判定）。
"""
from __future__ import annotations


class AnalysisGuard:
    def __init__(self):
        self.session = 0
        self._pending = {}    # qid -> (node, session)

    def new_session(self):
        """开启新引擎会话：session+1、清空所有挂账（旧请求一律作废）。"""
        self.session += 1
        self._pending.clear()

    def clear(self):
        self._pending.clear()

    def register(self, qid, node):
        self._pending[qid] = (node, self.session)

    def take(self, qid):
        """取回结果。返回 (node, accepted)；不存在或来自旧 session 则 (None, False)。"""
        ent = self._pending.pop(qid, None)
        if ent is None:
            return (None, False)
        node, sess = ent
        if sess != self.session:
            return (None, False)        # 过期：来自上一个引擎实例（如切换模型前的残留）
        return (node, True)

    def has_pending(self, node):
        return any(n is node for (n, _s) in self._pending.values())

    def invalidate_node(self, node):
        """失效某节点的全部 pending（重复请求同一节点前调用，避免旧延迟响应覆盖新结果）。"""
        stale = [qid for qid, (n, _s) in self._pending.items() if n is node]
        for qid in stale:
            del self._pending[qid]

    def pending_count(self):
        return len(self._pending)
