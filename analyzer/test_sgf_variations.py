"""test_sgf_variations —— SGF 变化图 + 注释 + AI 摘要 + 让子棋导入导出测试。"""
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

from board import BLACK, WHITE, BoardState
from movetree import MoveTree
from sgf import export_sgf, import_sgf, parse_sgf_tree
from katago_client import KataGoAnalysisClient
from score_estimator import ScoreEstimator

EXE = r"D:\katago\katago-runtime\katago-eigenavx2.exe"
CFG = os.path.join(HERE, "analysis.cfg")
MODEL = r"D:\katago\katago-runtime\models\kata1-b18c384nbt-s9996604416-d4316597426.bin.gz"


def check(name, cond, extra=""):
    print(("[CHECK] %-34s %s %s" % (name, "OK" if cond else "FAIL", extra)))
    if not cond:
        raise AssertionError(name)


# 样本：B[dd] -> W[pp] -> 两个变着 (;B[qd];W[dp]) 与 (;B[pd]C[注释含\]括号])
SAMPLE = ("(;GM[1]FF[4]SZ[19]KM[7.5]PB[Black]PW[White]"
          ";B[dd];W[pp](;B[qd];W[dp])(;B[pd]C[注释含\\]括号])")


def test_parse():
    nodes = parse_sgf_tree(SAMPLE)
    check("解析到顶层节点链", len(nodes) >= 1, str(len(nodes)))


def test_import_branches():
    t = import_sgf(SAMPLE)
    check("导入黑白姓名", t._sgf_pb == "Black" and t._sgf_pw == "White",
          "%s/%s" % (t._sgf_pb, t._sgf_pw))
    check("主线深度=4", t.current.depth == 4, t.current.depth)   # dd,pp,qd,dp
    wpp = t.root.children[0].children[0]   # W[pp]
    check("W[pp] 有 2 个分支", len(wpp.children) == 2, str(len(wpp.children)))
    br2 = wpp.children[1]                  # (;B[pd]C[..])
    check("分支2 是 B[pd]", br2.move == ("B", (15, 3)), str(br2.move))
    check("分支2 注释保留", br2.comment == "注释含]括号", br2.comment)


def test_export_branches():
    t = MoveTree(19)
    t.play(3, 3)     # B D16 -> dd
    t.play(15, 15)   # W Q4  -> pp
    t.undo()         # 回 B[dd]
    t.play(15, 3)    # W Q16 -> pd （=> B[dd] 现有 2 子）
    sgf = export_sgf(t, komi=7.5)
    check("导出含主线 ;B[dd]", ";B[dd]" in sgf, sgf)
    check("导出含变着括号", sgf.count("(") >= 2 and sgf.count(")") >= 2, sgf)
    t2 = import_sgf(sgf)
    bd = t2.root.children[0]               # B[dd]
    check("回导后 B[dd] 有 2 分支", len(bd.children) == 2, str(len(bd.children)))


def test_comment_escape():
    t = MoveTree(19)
    t.play(3, 3)
    t.current.comment = "有]括号[和\\反斜杠"
    sgf = export_sgf(t, komi=7.5)
    check("转义 ]", "\\]" in sgf, sgf)
    t2 = import_sgf(sgf)
    check("回导注释还原", t2.current.comment == "有]括号[和\\反斜杠", t2.current.comment)


def test_ai_comment():
    t = MoveTree(19)
    t.play(3, 3)
    t.current.analysis = {
        "rootInfo": {"winrate": 0.532, "scoreLead": 1.8},
        "moveInfos": [
            {"order": 0, "move": "Q16", "winrate": 0.532, "scoreLead": 1.8, "visits": 80, "pv": ["Q16", "D4"]},
            {"order": 1, "move": "D4", "winrate": 0.521, "scoreLead": 1.2, "visits": 45, "pv": ["D4"]},
        ],
    }
    sgf = export_sgf(t, komi=7.5)
    check("AI 摘要写入 C[]", "C[" in sgf and "首选:Q16" in sgf, sgf)
    check("AI 含黑胜率", "黑胜率:53.2" in sgf, sgf)


def test_pass_in_branch():
    t = MoveTree(19)
    t.play(3, 3)     # B dd
    t.play(15, 15)   # W pp
    t.undo()         # 回 B dd
    t.play_pass()    # 变着：W pass => B[dd] 2 子
    sgf = export_sgf(t, komi=7.5)
    check("变着含 ;W[]", ";W[]" in sgf, sgf)
    t2 = import_sgf(sgf)
    bd = t2.root.children[0]
    check("pass 分支保留", any(c.move[1] is None for c in bd.children), str([c.move for c in bd.children]))


def test_header_preserved():
    t = MoveTree(19)
    t.play(3, 3)
    sgf = export_sgf(t, komi=6.5, rule="japanese")
    check("KM 保留", "KM[6.5]" in sgf, sgf)
    check("RU 保留", "RU[japanese]" in sgf, sgf)


# ===================== 点目结果 RE[] / C[] =====================
def _draw_result(komi=0.0):
    """空盘 ScoreResult（komi=0 → 和棋 RE[0]；komi=7.5 → 白胜）。"""
    return ScoreEstimator(BoardState(19), komi=komi).compute_chinese_area_score()


def test_scoring_re_and_comment():
    """确认点目结果后导出：根属性 RE[] 写入 + 根 C[] 追加点目摘要。"""
    t = MoveTree(19)
    t.play(3, 3)
    t.score_result = _draw_result(komi=0)     # 和棋 RE[0]
    sgf = export_sgf(t, komi=0)
    check("确认结果写 RE[0]", "RE[0]" in sgf, sgf)
    check("点目摘要进 C[]", "终局点目" in sgf and "面积数法" in sgf, sgf)
    check("点目摘要含规则", "中国规则" in sgf, sgf)
    # 白胜场景
    t.score_result = _draw_result(komi=7.5)
    sgf2 = export_sgf(t, komi=7.5)
    check("白胜写 RE[W+7.5]", "RE[W+7.5]" in sgf2, sgf2)


def test_preserve_re_without_confirm():
    """未确认结果但导入的 SGF 已有 RE[]，导出时保留原 RE[]。"""
    src = "(;GM[1]FF[4]SZ[19]KM[7.5]RE[B+2.5];B[dd];W[pp])"
    t = import_sgf(src)
    check("导入捕获原 RE", getattr(t, "_sgf_re", None) == "B+2.5",
          str(getattr(t, "_sgf_re", None)))
    check("tree.score_result 初始为 None", getattr(t, "score_result", "X") is None)
    out = export_sgf(t, komi=7.5)            # 未设置 score_result
    check("未确认时保留原 RE", "RE[B+2.5]" in out, out)
    check("未确认不写点目摘要", "终局点目" not in out, out[:120])


def test_confirm_overwrites_re():
    """确认新结果覆盖原 RE[]。"""
    src = "(;GM[1]FF[4]SZ[19]KM[7.5]RE[W+9.0];B[dd];W[pp])"
    t = import_sgf(src)
    t.score_result = _draw_result(komi=0)     # 和棋
    out = export_sgf(t, komi=0)
    check("确认覆盖原 RE → RE[0]", "RE[0]" in out and "RE[W+9.0]" not in out, out)


def test_root_comment_roundtrip():
    """根节点 C[]（含点目摘要、换行）可回导，且不被既有逻辑覆盖。"""
    src = "(;GM[1]FF[4]SZ[19]KM[7.5]RE[B+3.5]C[终局点目：\n规则：中国规则\n结果：黑胜 3.5 目];B[dd];W[pp])"
    t = import_sgf(src)
    check("根注释回导", bool(t.root.comment) and "终局点目" in t.root.comment, str(t.root.comment))
    out = export_sgf(t, komi=7.5)            # 未确认 → 保留原 RE + 原根注释
    check("根 C[] 写出含摘要", "终局点目" in out, out[:200])
    check("回导保留原 RE", "RE[B+3.5]" in out, out)


def test_handicap():
    sgf = "(;GM[1]FF[4]SZ[19]HA[2]KM[0.5]AB[dd][pp];W[qd];B[pd])"
    t = import_sgf(sgf)
    n_black = sum(row.count(BLACK) for row in t.root.board.grid)
    check("让子盘面 2 黑子", n_black == 2, str(n_black))
    check("initial_stones=2", len(t.initial_stones) == 2, str(t.initial_stones))
    check("让子后白先", t.root.board.to_move == WHITE, str(t.root.board.to_move))
    isl = t.initial_stones_list()
    check("initialStones 全黑共 2", all(s[0] == "B" for s in isl) and len(isl) == 2, str(isl))
    check("moves=2（不含 setup）", len(t.current.moves_list()) == 2, str(t.current.moves_list()))
    out = export_sgf(t, komi=0.5)
    check("导出含 HA[2]", "HA[2]" in out, out)
    check("导出含 AB[dd]", "AB[dd]" in out, out)
    # 真实引擎接受 initialStones
    print("\n  启动 KataGo 验证 initialStones...")
    cli = KataGoAnalysisClient(EXE, CFG, MODEL, cwd=HERE)
    cli.start()
    t0 = time.time()
    while not cli.ready and time.time() - t0 < 90:
        cli.poll(); time.sleep(0.2)
    check("KataGo 就绪", cli.ready)
    qid = cli.analyze({"moves": [], "initialStones": [["B", "D4"], ["B", "Q4"]],
                       "rules": "chinese", "komi": 0.5, "boardXSize": 19, "boardYSize": 19})
    resp = None
    t0 = time.time()
    while time.time() - t0 < 90:
        for rid, r in cli.poll():
            if rid == qid:
                resp = r; break
        if resp:
            break
        time.sleep(0.1)
    check("initialStones 查询无 error", resp is None or "error" not in resp,
          str(resp.get("error") if resp else None))
    cli.stop()


if __name__ == "__main__":
    print("=" * 60)
    print(" SGF 变化图/注释/让子/点目结果测试")
    print("=" * 60)
    test_parse(); print()
    test_import_branches(); print()
    test_export_branches(); print()
    test_comment_escape(); print()
    test_ai_comment(); print()
    test_pass_in_branch(); print()
    test_header_preserved(); print()
    test_scoring_re_and_comment(); print()
    test_preserve_re_without_confirm(); print()
    test_confirm_overwrites_re(); print()
    test_root_comment_roundtrip(); print()
    test_handicap(); print()
    print("test_sgf_variations 全部通过 ✅")
