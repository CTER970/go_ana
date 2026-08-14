"""test_review_report —— Markdown 复盘报告生成测试。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from movetree import MoveTree
from review_report import generate_markdown_report


def check(name, cond, extra=""):
    print(("[CHECK] %-34s %s %s" % (name, "OK" if cond else "FAIL", extra)))
    if not cond:
        raise AssertionError(name)


def analysis(score_lead, winrate, move_infos):
    return {"rootInfo": {"scoreLead": score_lead, "winrate": winrate}, "moveInfos": move_infos}


def mi(move, sl, wr, order=0):
    return {"move": move, "scoreLead": sl, "winrate": wr, "order": order, "visits": 10}


def test_report_markdown():
    t = MoveTree(19)
    t.play(3, 3)      # B D16
    t.play(15, 15)    # W Q4
    t.play(15, 3)     # B Q16
    rr_nodes = []
    n = t.root
    while n is not None:
        rr_nodes.append(n)
        n = n.children[0] if n.children else None

    rr_nodes[0].analysis = analysis(0.0, 0.50, [mi("D16", 0.5, 0.55, 0), mi("Q16", -2.0, 0.45, 1)])
    rr_nodes[1].analysis = analysis(0.5, 0.55, [mi("D4", -1.0, 0.45, 0), mi("Q4", -0.2, 0.50, 1)])
    rr_nodes[2].analysis = analysis(-0.2, 0.50, [
        {"move": "D4", "scoreLead": 8.0, "winrate": 0.80, "order": 0,
         "visits": 10, "pv": ["D4", "Q3", "D3"]},
        mi("Q16", 2.0, 0.62, 1)])
    rr_nodes[3].analysis = analysis(2.0, 0.62, [mi("D4", 2.0, 0.62, 0)])
    t._deep_comparisons = {
        "3": {
            "move": 3,
            "actualMove": "Q16",
            "aiMove": "D4",
            "summary": "AI分支多保留 6.0 目。",
            "diagnosis": "局部战斗次序需要调整。",
            "actual": {"pv": ["Q16", "D4", "Q3"]},
            "ai": {"pv": ["D4", "Q3", "D3"]},
        }
    }

    md = generate_markdown_report(
        t, black_name="Alpha", white_name="Beta",
        komi=7.5, rule="chinese", generated_at="2026-06-25 21:00")
    check("标题存在", "# KataGo 复盘报告" in md)
    check("生成时间稳定", "2026-06-25 21:00" in md)
    check("双方姓名写入", "Alpha" in md and "Beta" in md)
    check("结论摘要存在", "## 结论摘要" in md and "有效问题棋" in md)
    check("三阶段水平存在", "## 三阶段水平分析" in md and "| 布局 |" in md and "| 中盘 |" in md and "| 官子 |" in md)
    check("阶段显示质量而非段位", "阶段表现" in md and "| 优秀 |" in md)
    check("评分表存在", "单局表现估计（非官方段位）" in md)
    check("文字复盘存在", "## 对局文字分析" in md and "关键转折" in md and "复盘重点" in md)
    check("好棋章节存在", "## 下得好的地方" in md and "命中 AI 首选" in md)
    check("问题棋章节存在", "## 需要重点复盘的问题棋" in md and "胜率损失" in md)
    check("问题棋包含第 3 手", "| 3 | 官子 | 黑 | Q16 | 6.0" in md, md)
    check("恶手意图对比存在", "## 恶手意图对比" in md
          and "实战选点可能意图" in md and "AI 推荐选点意图" in md, md)
    check("双分支深度对比存在", "## 问题手双分支深度对比" in md
          and "实战主变：Q16 → D4 → Q3" in md and "AI 主变：D4 → Q3 → D3" in md, md)
    check("不再导出逐手评价", "## 逐手评价" not in md and "首选排名" not in md)
    check("说明存在", "单手最多计 3 目" in md and "不是正式段位认证" in md)

    personal = generate_markdown_report(
        t, black_name="Alpha", white_name="Beta",
        komi=7.5, rule="chinese", generated_at="2026-06-25 21:00",
        focus_color="B")
    check("个人报告标明范围", "- 复盘范围：Alpha（黑）" in personal)
    check("个人报告标明覆盖率", "- 分析覆盖：2/2（100.0%）" in personal)
    check("个人问题表只含本人",
          "| 3 | 官子 | 黑 | Q16 |" in personal
          and "| 白 |" not in personal)
    check("个人表现表不混入对手",
          "| Alpha |" in personal and "| Beta |" not in personal)


if __name__ == "__main__":
    print("=" * 60)
    print(" Markdown 复盘报告测试")
    print("=" * 60)
    test_report_markdown(); print()
    print("test_review_report 全部通过 ✅")
