"""test_domain_invariants —— 学习领域不变式测试（审查 A2，D1-D10）。

这些不变式保护的是 go_ana 学习系统的核心语义。
违反任何一条 = 学习数据不可信。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from domain_invariants import DOMAIN_INVARIANTS, check_all_domain


def check(name, cond, extra=""):
    print("[CHECK] %-40s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        raise AssertionError(name)


def run():
    # 所有领域不变式通过
    violations = check_all_domain()
    check("全部领域不变式通过（%d 条）" % len(DOMAIN_INVARIANTS),
          not violations, str(violations))

    # 逐条报告
    for inv_id, fn, desc in DOMAIN_INVARIANTS:
        ok, msg = fn()
        check("%s %s" % (inv_id, desc), ok, msg)

    print("\ntest_domain_invariants: 全部通过")


if __name__ == "__main__":
    run()
