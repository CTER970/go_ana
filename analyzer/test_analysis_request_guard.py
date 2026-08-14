"""test_analysis_request_guard —— 异步请求守卫测试（session 隔离 / 过期丢弃）。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from analysis_guard import AnalysisGuard


class FakeNode:
    def __init__(self, nid):
        self.nid = nid


def check(name, cond, extra=""):
    print(("[CHECK] %-34s %s %s" % (name, "OK" if cond else "FAIL", extra)))
    if not cond:
        raise AssertionError(name)


def test_basic():
    g = AnalysisGuard()
    n1, n2 = FakeNode(1), FakeNode(2)
    g.register("q1", n1)
    g.register("q2", n2)
    node, ok = g.take("q1")
    check("take q1 接受", ok and node is n1)
    check("take 后 pending 减少", g.pending_count() == 1)
    node, ok = g.take("q1")
    check("重复 take 拒绝", not ok)


def test_session_invalidate():
    g = AnalysisGuard()
    g.register("q1", FakeNode(1))
    g.new_session()                 # 模拟重启引擎
    node, ok = g.take("q1")
    check("重启后旧请求被丢弃", not ok, str((node, ok)))
    check("new_session 清空挂账", g.pending_count() == 0)
    check("session 自增到 1", g.session == 1)


def test_cross_session():
    g = AnalysisGuard()
    g.new_session()                 # session 1
    g.register("q1", FakeNode(1))   # 属 session 1
    g.new_session()                 # session 2（切换模型重启）
    g.register("q2", FakeNode(2))   # 属 session 2
    node, ok = g.take("q1")
    check("旧 session 结果丢弃", not ok)
    node, ok = g.take("q2")
    check("当前 session 结果接受", ok and node is not None and node.nid == 2)


def test_has_pending():
    g = AnalysisGuard()
    n1 = FakeNode(1)
    g.register("q1", n1)
    check("has_pending 命中", g.has_pending(n1))
    g.take("q1")
    check("take 后 has_pending 失效", not g.has_pending(n1))


def test_invalidate_node():
    g = AnalysisGuard()
    n1, n2 = FakeNode(1), FakeNode(2)
    g.register("q1", n1)
    g.register("q2", n2)
    g.invalidate_node(n1)
    check("invalidate 后该节点不再 pending", not g.has_pending(n1))
    check("invalidate 后 take 拒绝", g.take("q1") == (None, False))
    check("不误伤其它节点", g.has_pending(n2))


if __name__ == "__main__":
    print("=" * 60)
    print(" 异步请求守卫测试")
    print("=" * 60)
    test_basic(); print()
    test_session_invalidate(); print()
    test_cross_session(); print()
    test_has_pending(); print()
    test_invalidate_node(); print()
    print("test_analysis_request_guard 全部通过 ✅")
