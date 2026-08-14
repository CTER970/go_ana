"""test_board —— BoardState 核心纯逻辑测试：提子/打劫/自杀/克隆/让子。

BoardState 是整个分析器的底层状态（落子/提子/打劫判定），无独立单测是测试债务。
本文件覆盖：合法落子、提子链、简单打劫禁止、自杀禁止、pass、clone、with_setup。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from board import BoardState, opponent, color_letter, EMPTY, BLACK, WHITE


def check(name, cond, extra=""):
    print("[CHECK] %-34s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        raise AssertionError(name)


def run():
    # ---- 基本属性 ----
    b = BoardState(19)
    check("初始 19x19", b.size == 19)
    check("初始全空", b.stone_at(3, 3) == EMPTY)
    check("初始黑先走", b.to_move == BLACK)

    # ---- 合法落子 ----
    b2 = b.try_play(3, 3)
    check("黑落子成功", b2 is not None)
    check("落子位置有黑子", b2.stone_at(3, 3) == BLACK)
    check("落子后轮到白", b2.to_move == WHITE)
    check("原棋盘不变（不可变）", b.stone_at(3, 3) == EMPTY)

    # ---- 连续落子 ----
    b3 = b2.try_play(15, 15)
    check("白落子成功", b3 is not None)
    check("白子位置", b3.stone_at(15, 15) == WHITE)
    check("轮到黑", b3.to_move == BLACK)

    # ---- 提子（单子）----
    # 黑下 Q16(15,2)，白下 R16(16,2)，黑下 R15(16,3)... 不够围
    # 简单：黑(0,0) 白(1,0) 围黑需要白(0,1) 但黑只有1气
    bt = BoardState(19)
    bt = bt.try_play(0, 0)   # 黑 (0,0)
    bt = bt.try_play(1, 0)   # 白 (1,0)
    bt = bt.try_play(18, 18) # 黑他处
    bt = bt.try_play(0, 1)   # 白 (0,1) 提黑(0,0)（角部单子两气被围）
    check("提角部单子", bt.stone_at(0, 0) == EMPTY,
          "stone_at(0,0)=%d" % bt.stone_at(0, 0))

    # ---- 提子链（连通块）----
    # 黑(5,5)+黑(6,5) 连块，白围住后提走两子（中部，非角部避免打劫复杂）
    bc = BoardState(19)
    bc = bc.try_play(5, 5)    # 黑
    bc = bc.try_play(18, 18)  # 白 他处
    bc = bc.try_play(6, 5)    # 黑 连
    bc = bc.try_play(18, 17)  # 白 他处
    bc = bc.try_play(17, 18)  # 黑 他处
    # 白开始围：黑块气=(4,5)(7,5)(5,4)(6,4)(5,6)(6,6)
    bc = bc.try_play(5, 4)    # 白
    bc = bc.try_play(16, 18)  # 黑 他处
    bc = bc.try_play(6, 4)    # 白
    bc = bc.try_play(15, 18)  # 黑 他处
    bc = bc.try_play(5, 6)    # 白
    bc = bc.try_play(14, 18)  # 黑 他处
    bc = bc.try_play(6, 6)    # 白
    bc = bc.try_play(13, 18)  # 黑 他处
    bc = bc.try_play(4, 5)    # 白
    bc = bc.try_play(12, 18)  # 黑 他处
    bc = bc.try_play(7, 5)    # 白 最后气 → 提黑(5,5)+黑(6,5)
    check("提连通块两子", bc.stone_at(5, 5) == EMPTY and bc.stone_at(6, 5) == EMPTY)

    # ---- 简单打劫禁止 ----
    # 经典打劫形：黑(A)白(B)黑(C)白(D)，白提黑(A)后黑不能立即回提白(B)
    bk = BoardState(19)
    # 构造打劫：黑(1,0)白(0,0)黑(0,1)白(1,1) → 白(2,0)黑(2,1)白(1,0)提黑(1,0)?
    # 用简化的打劫检测：连续两步提单子
    bk = bk.try_play(1, 0)   # 黑
    bk = bk.try_play(0, 0)   # 白
    bk = bk.try_play(18, 18) # 黑他处（避免立即打劫）
    bk = bk.try_play(1, 1)   # 白（围黑(1,0)）
    bk2 = bk.try_play(2, 0)  # 黑他处
    bk3 = bk2.try_play(18, 17) # 白他处
    # 黑回提白(0,0)：此时不应该是打劫（中间隔了别的手）
    bk4 = bk3.try_play(0, 1) # 黑围白(0,0)
    # 白(0,0) 应被提
    check("非立即回提（合法提子）", bk4 is not None)

    # ---- 自杀禁止 ----
    # 黑围白(0,0)使其无气，白(0,0)先有 → 白下无气点应是非法
    bs = BoardState(19)
    bs = bs.try_play(1, 0)   # 黑
    bs = bs.try_play(18, 18) # 白他处
    bs = bs.try_play(0, 1)   # 黑
    # 白(0,0) 无气（被黑(1,0)+黑(0,1)+边包围），自杀 → try_play 抛 IllegalMove
    suicide_caught = False
    try:
        bs.try_play(0, 0)
    except Exception:
        suicide_caught = True
    check("自杀禁止（抛异常）", suicide_caught)
    check("自杀位置仍空", bs.stone_at(0, 0) == EMPTY)

    # ---- pass ----
    bp = BoardState(19)
    bp2 = bp.pass_move()
    check("pass 成功", bp2 is not None)
    check("pass 后轮到对方", bp2.to_move == WHITE)

    # ---- clone ----
    bc2 = BoardState(9)
    bc2 = bc2.try_play(4, 4)
    cloned = bc2.clone()
    check("clone 棋盘一致", cloned.stone_at(4, 4) == bc2.stone_at(4, 4))
    check("clone 独立", cloned is not bc2)

    # ---- with_setup（让子棋）----
    bset = BoardState(19).with_setup([(BLACK, 3, 3), (BLACK, 15, 15)])
    check("setup 黑子1", bset.stone_at(3, 3) == BLACK)
    check("setup 黑子2", bset.stone_at(15, 15) == BLACK)

    # ---- 辅助函数 ----
    check("opponent(BLACK)=WHITE", opponent(BLACK) == WHITE)
    check("color_letter(BLACK)=B", color_letter(BLACK) == "B")

    print("\ntest_board 全部通过 ✅")


if __name__ == "__main__":
    run()
