"""test_pass —— Pass 真实节点测试（board / movetree / sgf + 真实 KataGo 接受 pass）。"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from board import BoardState, BLACK, WHITE
from movetree import MoveTree
from sgf import export_sgf, import_sgf
from katago_client import KataGoAnalysisClient

EXE = r"D:\katago\katago-runtime\katago-eigenavx2.exe"
CFG = os.path.join(HERE, "analysis.cfg")
MODEL = r"D:\katago\katago-runtime\models\kata1-b18c384nbt-s9996604416-d4316597426.bin.gz"


def check(name, cond, extra=""):
    print(("[CHECK] %-32s %s %s" % (name, "OK" if cond else "FAIL", extra)))
    if not cond:
        raise AssertionError(name)


def test_board_pass():
    b = BoardState(19)
    b1 = b.pass_move()
    check("pass 翻转轮次", b1.to_move == WHITE)
    check("pass 盘面不变", b1.grid == b.grid)
    check("pass last_move=None", b1.last_move is None)
    check("pass 原盘不变(轮次仍黑)", b.to_move == BLACK)


def test_tree_pass():
    t = MoveTree(19)
    ok, _ = t.play_pass()
    check("play_pass 成功", ok)
    check("pass 后深度=1", t.current.depth == 1)
    check("moves_list 含 B pass", t.current.moves_list() == [["B", "pass"]], t.current.moves_list())
    t.play_pass()   # W pass
    check("两手 pass 深度=2", t.current.depth == 2)
    check("两手 pass moves", t.current.moves_list() == [["B", "pass"], ["W", "pass"]], t.current.moves_list())
    ok, _ = t.play(3, 3)   # pass 后继续落子（轮到黑）
    check("pass 后落子成功", ok and t.current.depth == 3)
    check("pass 后末手为坐标", t.current.moves_list()[-1] == ["B", "D16"], t.current.moves_list())


def test_sgf_pass():
    t = MoveTree(19)
    t.play_pass()    # B pass
    t.play_pass()    # W pass
    sgf = export_sgf(t, komi=7.5)
    check("sgf 含 ;B[]", ";B[]" in sgf, sgf)
    check("sgf 含 ;W[]", ";W[]" in sgf, sgf)
    t2 = import_sgf(sgf)
    check("sgf 回放深度=2", t2.current.depth == 2, t2.current.depth)
    check("sgf 回放 pass", t2.current.moves_list() == [["B", "pass"], ["W", "pass"]], t2.current.moves_list())


def test_katago_accepts_pass():
    print("\n启动 KataGo 验证 pass 被接受...")
    cli = KataGoAnalysisClient(EXE, CFG, MODEL, cwd=HERE)
    cli.start()
    t0 = time.time()
    while not cli.ready and time.time() - t0 < 90:
        cli.poll()
        time.sleep(0.2)
    check("KataGo 就绪", cli.ready, "\n".join(cli.recent_stderr(15)) if not cli.ready else "")
    qid = cli.analyze({"moves": [["B", "pass"]], "rules": "chinese", "komi": 7.5,
                       "boardXSize": 19, "boardYSize": 19})
    resp = None
    t0 = time.time()
    while time.time() - t0 < 90:
        for rid, r in cli.poll():
            if rid == qid:
                resp = r
                break
        if resp:
            break
        time.sleep(0.1)
    check("pass 查询有响应", resp is not None)
    check("pass 查询无 error", resp is None or "error" not in resp,
          str(resp.get("error") if resp else None))
    if resp and "rootInfo" in resp:
        check("B pass 后轮到白", resp["rootInfo"].get("currentPlayer") == "W",
              resp["rootInfo"].get("currentPlayer"))
    cli.stop()


if __name__ == "__main__":
    print("=" * 60)
    print(" Pass 真实节点测试")
    print("=" * 60)
    test_board_pass(); print()
    test_tree_pass(); print()
    test_sgf_pass(); print()
    test_katago_accepts_pass(); print()
    print("test_pass 全部通过 ✅")
