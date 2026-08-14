"""test_project_store —— 复盘项目文件保存/打开测试。"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from movetree import MoveTree
from score_estimator import ScoreResult
from project_store import tree_to_project, project_to_tree, save_project, load_project


def check(name, cond, extra=""):
    print(("[CHECK] %-34s %s %s" % (name, "OK" if cond else "FAIL", extra)))
    if not cond:
        raise AssertionError(name)


def analysis(score_lead, winrate, move):
    return {
        "rootInfo": {"scoreLead": score_lead, "winrate": winrate, "currentPlayer": "B"},
        "moveInfos": [{"order": 0, "move": move, "scoreLead": score_lead, "winrate": winrate}],
    }


def score_result():
    return ScoreResult(
        black_stones=1, white_stones=0,
        black_territory=10, white_territory=8, neutral_points=0,
        komi=7.5, black_area=11, white_area=15.5,
        margin=-4.5, winner="W", result_text="W+4.5",
        dead_black=[], dead_white=["D4"],
        black_territory_points=["Q16"], white_territory_points=["D4"],
        neutral_points_list=[],
    )


def sample_tree():
    t = MoveTree(19)
    t.root.comment = "根注释"
    t.root.analysis = analysis(0.0, 0.5, "Q16")
    t.play(15, 3)       # B Q16
    n1 = t.current
    n1.comment = "主线黑棋"
    n1.analysis = analysis(1.0, 0.55, "D4")
    t.play(3, 15)       # W D4
    main_end = t.current
    main_end.analysis = analysis(-0.5, 0.48, "Q4")
    t.undo()
    t.play(15, 15)      # W Q4 branch
    branch = t.current
    branch.comment = "分支白棋"
    branch.analysis = analysis(2.0, 0.62, "D16")
    t.score_result = score_result()
    t._sgf_re = "B+2.5"
    t._sgf_pb = "Tester Black"
    t._sgf_pw = "Tester White"
    t._deep_comparisons = {
        "2": {"move": 2, "actualMove": "D4", "aiMove": "Q4", "summary": "测试分支对比"}
    }
    t._review_summary_v2 = {
        "version": 1,
        "analysisSignature": {
            "model": "test.bin.gz", "rules": "chinese",
            "komi": 7.5, "visits": 120,
        },
        "moveQuality": [{"move_no": 1, "quality_key": "good"}],
    }
    t._profile_side = "B"
    return t, main_end, branch


def test_roundtrip_dict():
    t, _main_end, branch = sample_tree()
    data = tree_to_project(t, rules="chinese", komi=7.5, meta={"name": "demo"})
    check("格式标记", data["format"] == "katago-analyzer-project")
    check("当前路径指向分支", data["currentPath"] == [0, 1], str(data["currentPath"]))
    check("scoreResult 写入", data["scoreResult"]["result_text"] == "W+4.5")
    t2 = project_to_tree(data)
    check("规则外部保存不进 tree", t2.size == 19)
    check("根注释恢复", t2.root.comment == "根注释", str(t2.root.comment))
    check("根 analysis 恢复", t2.root.analysis["moveInfos"][0]["move"] == "Q16")
    check("主线第一手恢复", t2.root.children[0].move[0] == "B")
    check("分支数恢复", len(t2.root.children[0].children) == 2)
    check("当前节点恢复为分支", t2.current.comment == branch.comment, t2.current.comment)
    check("当前节点 moves_list 恢复", t2.current.moves_list() == [["B", "Q16"], ["W", "Q4"]], str(t2.current.moves_list()))
    check("节点 analysis 恢复", t2.current.analysis["rootInfo"]["scoreLead"] == 2.0)
    check("点目结果恢复", t2.score_result.result_text == "W+4.5", str(t2.score_result))
    check("原 SGF RE 恢复", t2._sgf_re == "B+2.5")
    check("黑白姓名恢复", t2._sgf_pb == "Tester Black" and t2._sgf_pw == "Tester White")
    check("双分支深度对比恢复", t2._deep_comparisons["2"]["aiMove"] == "Q4")
    check("精细评价摘要恢复",
          t2._review_summary_v2["moveQuality"][0]["quality_key"] == "good")
    check("分析口径随项目恢复", t2._analysis_signature["visits"] == 120)
    check("画像执棋方恢复", t2._profile_side == "B")

    damaged = dict(data)
    damaged["reviewSummaryV2"] = "broken"
    t3 = project_to_tree(damaged)
    check("损坏摘要不阻断棋树读取", t3._review_summary_v2 is None)

    old_score = dict(data)
    old_score["scoreResult"] = dict(data["scoreResult"])
    old_score["scoreResult"].pop("dead_black", None)
    old_score["scoreResult"]["futureField"] = "ignored"
    t4 = project_to_tree(old_score)
    check("旧版点目缺可选字段仍可读",
          t4.score_result.result_text == "W+4.5" and t4.score_result.dead_black == [])

    broken_score = dict(data)
    broken_score["scoreResult"] = dict(data["scoreResult"])
    broken_score["scoreResult"].pop("black_stones", None)
    t5 = project_to_tree(broken_score)
    check("损坏点目不阻断棋树读取", t5.score_result is None)


def test_roundtrip_file():
    t, _main_end, _branch = sample_tree()
    fd, path = tempfile.mkstemp(suffix=".kga.json")
    os.close(fd)
    try:
        save_project(path, t, rules="chinese", komi=7.5)
        t2, data = load_project(path)
        check("文件格式", data["format"] == "katago-analyzer-project")
        check("文件打开后当前节点", t2.current.moves_list() == [["B", "Q16"], ["W", "Q4"]])
        check("文件打开后分析缓存", t2.current.analysis["moveInfos"][0]["move"] == "D16")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


if __name__ == "__main__":
    print("=" * 60)
    print(" 复盘项目文件测试")
    print("=" * 60)
    test_roundtrip_dict(); print()
    test_roundtrip_file(); print()
    print("test_project_store 全部通过 ✅")
