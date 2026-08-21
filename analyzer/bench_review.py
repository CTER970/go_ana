"""bench_review —— R14 性能分诊：_update_review_state 管线在批量回流下的 O(n²) 实测。

无 Tk，只测 ReviewReport 侧的计算成本（move_quality_results / meaningful_problems /
phase_summary / coverage / player_performance / commentary / winrate_series / eval_node），
即 _update_review_state 每次刷新真正做的重活。批量分析 = 每节点回流跑一次全量。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from movetree import MoveTree
from review import ReviewReport, CachedReviewReport

N = 300          # 主线手数
CANDS = 15       # 每节点 moveInfos 候选数（KataGo 典型量级）


def mi(move, sl, wr, order):
    return {"move": move, "scoreLead": sl, "winrate": wr, "order": order,
            "prior": round(1.0 / (order + 2), 4)}


def build_tree(n):
    t = MoveTree(19)
    sl = 0.5
    played = 0
    while played < n:
        i = played
        x = i % 19
        y = i // 19
        ok, _ = t.play(x, y)
        if not ok:                     # 非法点（占用/自杀）——换下一格
            x, y = (i * 7 + 3) % 19, (i * 11 + 5) % 19
            ok, _ = t.play(x, y)
            if not ok:
                raise RuntimeError("坐标生成器失效 i=%d" % i)
        played += 1
        wr = 0.5 + 0.4 * (sl / max(1.0, abs(sl) + 5.0))
        node = t.current
        cands = []
        for c in range(CANDS):
            csl = sl + (n - c) * 0.01      # 第 0 候选最好
            cands.append(mi("P%s" % c, csl, max(0.02, min(0.98, wr + 0.01)), c))
        node.analysis = {
            "rootInfo": {"scoreLead": sl, "winrate": wr, "visits": 200},
            "moveInfos": cands,
        }
        sl += (i % 13 - 6) * 0.05          # 漂移目差，制造部分问题手
    # 根也挂（首手评价依赖根）
    t.root.analysis = {
        "rootInfo": {"scoreLead": 0.0, "winrate": 0.5, "visits": 200},
        "moveInfos": [mi("P%s" % c, 0.5 + (n - c) * 0.01, 0.51, c)
                      for c in range(CANDS)],
    }
    return t


def pipeline(tree):
    """镜像 _update_review_state 的 ReviewReport 侧全部重活（快照实例）。"""
    rr = CachedReviewReport(tree)
    rr.move_quality_results(visits=200, include_unknown=True)
    rr.meaningful_problems(n=15, min_loss=2.0, min_winrate_loss=0.03, color=None)
    rr.phase_summary(color=None)
    rr.analysis_coverage(None)
    rr.analysis_coverage("B")
    rr.player_performance("B")
    rr.player_performance("W")
    rr.game_commentary("黑方", "白方", focus_color=None)
    rr.winrate_series()
    rr.eval_node(tree.current)


def pipeline_light(tree):
    """镜像 light=True 轻量档：失误榜/覆盖/曲线/当前手 loss（无解说/评分/画像）。"""
    rr = CachedReviewReport(tree)
    rr.move_quality_results(visits=200, include_unknown=True)
    rr.meaningful_problems(n=15, min_loss=2.0, min_winrate_loss=0.03, color=None)
    rr.analysis_coverage(None)
    rr.winrate_series()
    rr.eval_node(tree.current)


def main():
    tree = build_tree(N)
    # 校验管线结果非空（保证测的是真活）
    rr = ReviewReport(tree)
    assert len(rr.move_quality_results(visits=200)) == N
    probs = rr.meaningful_problems(n=15)
    assert probs, "应存在问题手"
    assert rr.game_commentary("黑方", "白方")

    # 单次刷新
    t0 = time.perf_counter()
    pipeline(tree)
    one = time.perf_counter() - t0

    # 轻量档单次
    t0 = time.perf_counter()
    pipeline_light(tree)
    one_light = time.perf_counter() - t0

    # 批量回流：每节点轻量 + 完成时 1 次全量（R14 修复后的生产行为）
    t0 = time.perf_counter()
    for _ in range(N):
        pipeline_light(tree)
    pipeline(tree)
    batch_fixed = time.perf_counter() - t0

    # 旧生产行为对照（每节点全量）
    t0 = time.perf_counter()
    for _ in range(N):
        pipeline(tree)
    batch_old = time.perf_counter() - t0

    print("[BENCH] %d 手 × %d 候选" % (N, CANDS))
    print("[BENCH] 单次全量刷新          %.1f ms（memo 后）" % (one * 1000))
    print("[BENCH] 单次轻量刷新          %.1f ms" % (one_light * 1000))
    print("[BENCH] 整盘批量·旧行为      %.1f s" % (batch_old, ))
    print("[BENCH] 整盘批量·R14 修复后   %.1f s  （%.1f× 加速）" % (
        batch_fixed, batch_old / batch_fixed))


if __name__ == "__main__":
    main()
