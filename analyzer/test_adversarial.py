"""test_adversarial —— 对抗检测：确定性全枚举动作序列 × 状态不变式。

设计原理（来自调研）：
- 状态空间小（12 动作），确定性全枚举 pairwise=12×12=144 序列，比 LLM 即兴更可靠
- 每个序列：clean → 动作A（含fixture）→ redraw+update → 查18条不变式
                                    → 动作B → 再查不变式 → 收集违规
- 违规即 AssertionError，接入现有 check()/run() 框架

能抓的问题类型：
- 功能间互斥失效（如 scoring 和 training 同时 active）
- 换棋谱后状态残留（如点目/训练/drill 没清）
- Canvas 图层冲突（如点目时还画候选点）
- 退出后引用未清（如 drill 关了但 _drill_win 残留）
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import adversarial_harness as ah
from actions import ACTIONS, action_names
from invariants import (check_all_unconditional, check_post_game_switch,
                        check_post_close, snapshot_modes, canvas_marker_counts)


def check(name, cond, extra=""):
    status = "OK" if cond else "FAIL"
    print("[CHECK] %-40s %s %s" % (name, status, extra))
    if not cond:
        raise AssertionError(name)


def _run_action_and_check(app, action, step_label):
    """执行单个动作并按其 category 检查对应不变式，返回违规列表。"""
    action.apply(app)
    # redraw + pump 让 canvas 和异步回调反映最新状态
    try:
        app.redraw()
        ah.pump_after_callbacks(app)
    except Exception:
        pass
    # 互斥不变式每步都查
    violations = check_all_unconditional(app)
    # 按 category 追加专项检查
    if action.category == "switch":
        violations += check_post_game_switch(app)
    elif action.category == "close":
        violations += check_post_close(app)
    return violations


def _report_violations(seq_name, step, violations, app):
    """格式化违规报告。"""
    snap = snapshot_modes(app)
    markers = canvas_marker_counts(app)
    lines = ["序列 %s 第 %d 步后违规:" % (seq_name, step)]
    for inv_id, msg in violations:
        lines.append("  [%s] %s" % (inv_id, msg))
    lines.append("  状态快照: %s" % snap)
    if markers:
        lines.append("  canvas markers: %s" % markers)
    return "\n".join(lines)


def run_pairwise():
    """pairwise 全枚举：所有动作两两组合，每步后查不变式。

    12×12=144 序列。每个序列：
      clean → 动作A（含fixture）→ 查不变式 → 动作B → 查不变式
    """
    app = ah.make_headless_app()
    total_seqs = len(ACTIONS) * len(ACTIONS)
    total_violations = 0
    tested_seqs = 0

    print("=" * 60)
    print("对抗检测 pairwise：%d 动作 × %d 动作 = %d 序列" % (
        len(ACTIONS), len(ACTIONS), total_seqs))
    print("=" * 60)

    for i, act_a in enumerate(ACTIONS):
        for j, act_b in enumerate(ACTIONS):
            seq_name = "%s → %s" % (act_a.name, act_b.name)
            ah.clean(app)

            # 第一步：动作 A
            violations_a = _run_action_and_check(app, act_a, "A")
            if violations_a:
                # 第一步就违规——记录但继续测第二步（看是否雪上加霜）
                print("⚠️  " + _report_violations(seq_name, 1, violations_a, app))
                total_violations += len(violations_a)

            # 第二步：动作 B（在同一 app 状态上继续，测"A 后 B"的残留冲突）
            violations_b = _run_action_and_check(app, act_b, "B")
            if violations_b:
                print("⚠️  " + _report_violations(seq_name, 2, violations_b, app))
                total_violations += len(violations_b)

            tested_seqs += 1

    ah.destroy_app()
    print()
    print("=" * 60)
    print("对抗检测完成：%d 序列，发现 %d 处违规" % (tested_seqs, total_violations))
    print("=" * 60)
    return total_violations


def run():
    """主入口（接入现有 run() 约定）。"""
    violations = run_pairwise()
    check("对抗检测无违规", violations == 0,
          "（发现 %d 处违规，详见上方报告）" % violations if violations else "")
    print("\ntest_adversarial: " + ("PASS" if violations == 0 else "FOUND %d VIOLATIONS" % violations))


if __name__ == "__main__":
    run()
