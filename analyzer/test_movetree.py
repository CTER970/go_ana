"""test_movetree —— MoveTree 核心纯逻辑测试：落子/撤回/快进/分支/兄弟切换/reset。

MoveTree 是分析器的状态管理层（导航/分支/让子），无独立单测是测试债务。
本文件覆盖：play/undo/redo/reset/siblings/goto_sibling/pass/initial_stones。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from movetree import MoveTree, MoveNode, xy_to_point, point_to_xy


def check(name, cond, extra=""):
    print("[CHECK] %-34s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        raise AssertionError(name)


def run():
    # ---- 基本属性 ----
    t = MoveTree(19)
    check("初始 19x19", t.size == 19)
    check("初始 depth=0", t.current.depth == 0)
    check("初始无子节点", t.root.children == [])

    # ---- play / undo / redo ----
    t.play(3, 3)    # 黑 D4
    check("play 后 depth=1", t.current.depth == 1)
    check("play 后有黑子", t.current.board.stone_at(3, 3) != 0)
    check("root 有 1 个子", len(t.root.children) == 1)

    t.play(15, 15)  # 白 Q16
    check("play 后 depth=2", t.current.depth == 2)

    ok = t.undo()
    check("undo 成功", ok)
    check("undo 后 depth=1", t.current.depth == 1)

    ok = t.redo()
    check("redo 成功", ok)
    check("redo 后 depth=2", t.current.depth == 2)

    ok = t.undo()
    check("undo 到 depth=1", t.current.depth == 1)

    # ---- 分支：撤回后另走产生分支 ----
    t.play(3, 15)   # 白 D16（不同于 Q16，产生分支）
    check("分支后 root 子节点 depth=1 仍 1", t.root.children[0].children[0] is not None)
    check("第一手节点有 2 个子（分支）", len(t.root.children[0].children) == 2)

    # ---- siblings / sibling_index / goto_sibling ----
    sibs = t.siblings()
    check("有兄弟分支", len(sibs) == 2)
    check("sibling_index", t.sibling_index() in (0, 1))

    ok = t.goto_sibling(1)
    check("goto_sibling 切换", ok)
    ok = t.goto_sibling(-1)
    check("goto_sibling 切回", ok)

    # ---- reset ----
    t.reset()
    check("reset 后 depth=0", t.current.depth == 0)
    check("reset 后回到 root", t.current is t.root)

    # ---- can_undo / undo 到根 ----
    t.play(3, 3)
    check("can_undo=True", t.can_undo() is True)
    t.undo()
    check("undo 到根后 can_undo=False", t.can_undo() is False)
    ok = t.undo()
    check("根上 undo 返回 False", ok is False)

    # ---- pass ----
    t.play_pass()
    check("pass 后 depth=1", t.current.depth == 1)

    # ---- set_initial_stones（让子棋）----
    t2 = MoveTree(19)
    from board import BLACK, WHITE
    t2.set_initial_stones([(BLACK, 3, 3), (BLACK, 15, 15), (WHITE, 3, 15)])  # 黑2子白1子
    check("initial stones 黑1", t2.root.board.stone_at(3, 3) == BLACK)
    check("initial stones 黑2", t2.root.board.stone_at(15, 15) == BLACK)
    check("initial stones 白", t2.root.board.stone_at(3, 15) == WHITE)

    # ---- initial_stones_list（往返）----
    stones = t2.initial_stones_list()
    check("initial_stones_list 返回 3 子", len(stones) == 3, str(stones))

    # ---- moves_list ----
    t3 = MoveTree(19)
    t3.play(3, 3)
    t3.play(15, 15)
    t3.play(3, 15)
    moves = t3.current.moves_list()
    check("moves_list 返回 3 手", len(moves) == 3, str(moves))

    # ---- xy_to_point / point_to_xy 往返 ----
    pt = xy_to_point(3, 3, 19)
    x, y = point_to_xy(pt, 19)
    check("坐标往返", x == 3 and y == 3, "%s→%d,%d" % (pt, x, y))

    print("\ntest_movetree 全部通过 ✅")


if __name__ == "__main__":
    run()
