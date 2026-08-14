"""无头测试：验证 board 规则、坐标转换、movetree 导航、以及与真实 KataGo 的 analysis 通信闭环。
不依赖 tkinter / 显示器，可在命令行直接跑。
  python test_ipc.py
"""
import os
import sys
import time

# 控制台编码兜底：中文 Windows 默认 GBK，打印 emoji/部分字符（如结尾的 ✅）会抛
# UnicodeEncodeError。提前把标准输出/错误流切到 UTF-8，整个脚本即可放心打印任意字符。
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from board import BoardState, BLACK, WHITE, EMPTY, IllegalMove
from movetree import MoveTree, xy_to_point, point_to_xy, COLS
from katago_client import KataGoAnalysisClient

EXE = r"D:\katago\katago-runtime\katago-eigenavx2.exe"
CFG = os.path.join(HERE, "analysis.cfg")
MODEL = r"D:\katago\katago-runtime\models\kata1-b18c384nbt-s9996604416-d4316597426.bin.gz"


def check(name, cond, extra=""):
    print(("[CHECK] %-28s %s %s" % (name, "OK" if cond else "FAIL", extra)))
    if not cond:
        raise AssertionError(name)


# ---------------- 坐标 ----------------
def test_coords():
    check("xy_to_point(0,0)=A19", xy_to_point(0, 0) == "A19", xy_to_point(0, 0))
    check("xy_to_point(0,18)=A1", xy_to_point(0, 18) == "A1")
    check("xy_to_point(18,0)=T19", xy_to_point(18, 0) == "T19")
    check("xy_to_point(15,3)=Q16", xy_to_point(15, 3) == "Q16")
    check("point_to_xy(Q16)=(15,3)", point_to_xy("Q16") == (15, 3))
    check("point_to_xy(A1)=(0,18)", point_to_xy("A1") == (0, 18))
    check("第9列=J(跳过I)", COLS[8] == "J", COLS[8])


# ---------------- 棋盘规则 ----------------
def test_board():
    b = BoardState(19)
    b1 = b.try_play(3, 3)                       # 黑 D16
    check("落子后该点为黑", b1.stone_at(3, 3) == BLACK)
    check("轮次翻为白", b1.to_move == WHITE)
    check("盘面不可变(原盘空)", b.stone_at(3, 3) == 0)

    # 提子：把白子围死
    s = BoardState(19)
    s = s.try_play(1, 0)   # B
    s = s.try_play(0, 0)   # W 角
    s = s.try_play(0, 1)   # B —— 提掉白角
    check("白角被提(空)", s.stone_at(0, 0) == EMPTY, "got %r" % (s.stone_at(0, 0),))
    check("黑提子计数=1", s.captures[BLACK] == 1, s.captures)

    # 纯自杀禁着：白占角的两个邻，黑下角不提子且自身无气 => 非法
    su = BoardState(19)
    su = su.try_play(5, 5)   # B 随便
    su = su.try_play(1, 0)   # W (x=1,y=0)
    su = su.try_play(6, 6)   # B 随便
    su = su.try_play(0, 1)   # W (x=0,y=1) —— 角被白围
    # 黑下 (0,0)：邻(1,0)=W,(0,1)=W，白串仍有气不提，黑串0气 => 自杀
    try:
        su.try_play(0, 0)
        check("纯自杀被禁", False, "未抛异常")
    except IllegalMove:
        check("纯自杀被禁", True)

    # 打劫：构造劫形，黑提一子后白立即回提应被禁
    ko = BoardState(19)
    ko.grid[0][0] = BLACK; ko.grid[0][2] = BLACK; ko.grid[1][1] = BLACK   # B: (0,0),(2,0),(1,1)
    ko.grid[1][0] = WHITE                                                 # W: (0,1)
    ko.to_move = WHITE
    prev = [r[:] for r in ko.grid]   # 黑提子前的盘面 = 劫的初始形
    prev[0][0] = EMPTY               # (0,0) 原空
    prev[0][1] = WHITE               # (1,0) 原有白子(被黑提)
    ko.prev_grid = prev
    try:
        ko.try_play(1, 0)            # 白回提 => 复现初始形 => 禁
        check("打劫回提被禁", False, "未抛异常")
    except IllegalMove:
        check("打劫回提被禁", True)


# ---------------- MoveTree 导航 ----------------
def test_tree():
    t = MoveTree(19)
    check("初始无父", not t.can_undo())
    check("初始无子", not t.can_redo())
    ok, _ = t.play(3, 3);   check("落子1成功", ok)
    ok, _ = t.play(15, 15); check("落子2成功", ok)
    check("moves_list格式", t.current.moves_list() == [["B", "D16"], ["W", "Q4"]],
          t.current.moves_list())
    check("深度=2", t.current.depth == 2)
    # 撤回/快进
    t.undo(); check("撤回后深度=1", t.current.depth == 1)
    t.undo(); check("撤回到根", t.current is t.root)
    check("根处不可再撤", not t.can_undo())
    t.redo(); check("快进深度=1", t.current.depth == 1)
    # 分支：在根的子节点(第1手)下另走一手形成分支
    t.undo()  # 回根
    t.play(3, 3)   # 复用已有主线第1手 D16
    check("复用既有分支", t.current.depth == 1)
    t.play(15, 3)  # 新第2手 Q16 => 分支
    check("新分支深度=2", t.current.depth == 2)
    t.undo()
    t.play(15, 15)  # 回到主线第2手 Q4
    check("回到主线第2手", t.current.depth == 2 and t.current.move[1] == (15, 15))


# ---------------- SGF 导入/导出 ----------------
def test_sgf():
    from sgf import export_sgf, import_sgf, _sgf_coord, _from_sgf_coord
    check("sgf (15,3)->pd", _sgf_coord(15, 3) == "pd", _sgf_coord(15, 3))
    check("sgf pd->(15,3)", _from_sgf_coord("pd") == (15, 3))
    check("sgf 空串=None(pass)", _from_sgf_coord("") is None)
    check("sgf 单字符=None", _from_sgf_coord("p") is None)
    t_big = import_sgf("(;GM[1]FF[4]SZ[19];B[zz])")
    check("sgf 越界被跳过", t_big.current.depth == 0 and getattr(t_big, "_sgf_skipped", -1) == 1)
    t = MoveTree(19)
    t.play(3, 3)     # B D16 -> sgf dd
    t.play(15, 15)   # W Q4  -> sgf pp
    sgf = export_sgf(t, komi=7.5)
    check("sgf 含 SZ[19]", "SZ[19]" in sgf)
    check("sgf 含 KM[7.5]", "KM[7.5]" in sgf)
    check("sgf 含 ;B[dd]", ";B[dd]" in sgf, sgf)
    check("sgf 含 ;W[pp]", ";W[pp]" in sgf, sgf)
    t2 = import_sgf(sgf)
    check("sgf 回放深度=2", t2.current.depth == 2, t2.current.depth)
    check("sgf 回放末手=(15,15)", t2.current.move[1] == (15, 15), t2.current.move)
    check("sgf 回放盘面一致", t2.current.board.grid == t.current.board.grid)
    check("sgf 跳过=0", getattr(t2, "_sgf_skipped", -1) == 0)


# ---------------- 分支导航 ----------------
def test_branch():
    t = MoveTree(19)
    check("初始 goto_sibling=False", t.goto_sibling(1) is False)
    t.play(3, 3)    # 第1手 D16
    t.play(15, 15)  # 第2手 Q4（主线）
    t.undo()        # 回 D16
    t.play(15, 3)   # 第2手 Q16（分支）=> D16 现有 2 个子
    sibs = t.siblings()
    check("兄弟数=2", len(sibs) == 2, len(sibs))
    check("sibling_index 有效", 0 <= t.sibling_index() < len(sibs))
    first = t.current
    check("goto_sibling(1) 切换", t.goto_sibling(1) and t.current is not first)
    other = t.current
    check("goto_sibling(1) 循环回首个", t.goto_sibling(1) and t.current is first)
    check("goto_sibling(-1) 回另一分支", t.goto_sibling(-1) and t.current is other)
    check("分支节点深度=2", t.current.depth == 2, t.current.depth)


# ---------------- KataGo IPC ----------------
def wait_for(cli, qid, timeout=90):
    t0 = time.time()
    while time.time() - t0 < timeout:
        for rid, resp in cli.poll():
            if rid == qid:
                if "error" in resp:
                    raise AssertionError("KataGo error: %s" % resp.get("error"))
                return resp
        time.sleep(0.1)
    raise AssertionError("超时等待响应 id=%s" % qid)


def test_ipc():
    print("\n启动 KataGo（首次加载模型约数秒）...")
    cli = KataGoAnalysisClient(EXE, CFG, MODEL, cwd=HERE)
    cli.start()
    t0 = time.time()
    while not cli.ready and time.time() - t0 < 90:
        cli.poll()
        time.sleep(0.2)
    check("KataGo 就绪", cli.ready, "\n".join(cli.recent_stderr(15)) if not cli.ready else "")
    print("  version=%s" % (cli.version_info,))

    # 空盘分析
    tree = MoveTree(19)
    qid = cli.analyze({"moves": [], "rules": "chinese", "komi": 7.5,
                       "boardXSize": 19, "boardYSize": 19,
                       "includeOwnership": True, "includePolicy": True})
    resp = wait_for(cli, qid)
    check("空盘返回 moveInfos", "moveInfos" in resp and len(resp["moveInfos"]) > 0)
    check("空盘返回 rootInfo", "rootInfo" in resp)
    check("ownership 长度=361", "ownership" in resp and len(resp["ownership"]) == 361,
          len(resp.get("ownership", [])))
    check("policy 长度=362", "policy" in resp and len(resp["policy"]) == 362,
          len(resp.get("policy", [])))
    omax = max(abs(v) for v in resp["ownership"])
    check("ownership 在 [-1,1]", omax <= 1.0 + 1e-9, omax)
    mi = resp["moveInfos"][0]
    top = mi["move"]
    check("首选点为合法坐标", top != "pass" and top[0] in COLS, top)
    print("  空盘首选: %s  黑胜率=%.3f  目差=%+.2f  pv=%s" % (
        top, mi["winrate"], mi["scoreLead"], " ".join(mi.get("pv", [])[:6])))

    # 落首选子后再分析 —— 验证坐标往返与"引擎确实看到该子"
    tree.play(*point_to_xy(top))
    qid2 = cli.analyze({"moves": tree.current.moves_list(), "rules": "chinese", "komi": 7.5,
                        "boardXSize": 19, "boardYSize": 19})
    resp2 = wait_for(cli, qid2)
    cp = resp2["rootInfo"]["currentPlayer"]
    check("落子后轮到对方", cp == "W", "currentPlayer=%s (top move was %s)" % (cp, top))
    print("  落 %s 后: 轮=%s 黑胜率=%.3f 目差=%+.2f 候选=%d" % (
        top, cp, resp2["rootInfo"]["winrate"], resp2["rootInfo"]["scoreLead"],
        len(resp2["moveInfos"])))

    cli.stop()
    print("  KataGo 已关闭")


if __name__ == "__main__":
    print("=" * 60)
    print(" KataGo 分析器 · 核心闭环无头测试")
    print("=" * 60)
    test_coords()
    print()
    test_board()
    print()
    test_tree()
    print()
    test_sgf()
    print()
    test_branch()
    print()
    test_ipc()
    print("\n全部通过 ✅")
