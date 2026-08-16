"""test_candidate_assessment —— 实际目损判分、榜外强制分析、四分类边界测试（大纲 §76）。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from candidate_assessment import (
    ASSESSMENT_ACCEPTABLE, ASSESSMENT_BAD, ASSESSMENT_BEST,
    ASSESSMENT_EXCELLENT, ASSESSMENT_QUESTIONABLE, ASSESSMENT_UNKNOWN,
    RETRY_ALTERNATIVE_CORRECT, RETRY_CORRECTED, RETRY_IMPROVED,
    RETRY_REPEATED, assess_candidate, classify_retry, dynamic_tolerance,
    forced_move_query, forced_move_result, srs_result,
)


def check(name, cond, extra=""):
    print("[CHECK] %-44s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        raise AssertionError(name)


def _infos(entries, color="B"):
    """entries = [(move, loss_vs_best)]，构造 moveInfos。"""
    base = 2.0
    out = []
    for i, (mv, gap) in enumerate(entries):
        score = base - gap if color == "B" else -(base - gap)
        out.append({"move": mv, "order": i, "scoreLead": score,
                    "winrate": 0.52 if i == 0 else 0.50, "prior": 0.4,
                    "visits": 100})
    return out


def run():
    # 大纲 §20 的两个经典反例：第4选0.4目 vs 第2选4.8目
    infos = _infos([("A", 0.0), ("B", 4.8), ("C", 6.2), ("D", 0.4), ("E", 1.2)])
    d = assess_candidate("D", infos, "B")
    check("第4选仅亏0.4目 → 优秀", d["assessment"] == ASSESSMENT_EXCELLENT
          and d["ai_rank"] == 4, str(d))
    b = assess_candidate("B", infos, "B")
    check("第2选亏4.8目 → 可疑/问题",
          b["assessment"] in (ASSESSMENT_QUESTIONABLE, ASSESSMENT_BAD)
          and b["ai_rank"] == 2, str(b))

    # 基本档位
    check("AI最佳", assess_candidate("A", infos, "B")["assessment"] == ASSESSMENT_BEST)
    check("合理（1.5目内）",
          assess_candidate("E", infos, "B")["assessment"] == ASSESSMENT_ACCEPTABLE)

    # 动态容差：当前水平可接受（低段位 + 高复杂度放宽）
    low_infos = _infos([("A", 0.0), ("F", 2.2)])
    d = assess_candidate("F", low_infos, "B", performance_label="15级",
                         complexity=0.8)
    check("低段位复杂局面2.2目 → 当前水平可接受",
          d["assessment"] == ASSESSMENT_ACCEPTABLE and d["current_level_ok"],
          str(d))
    d2 = assess_candidate("F", low_infos, "B", performance_label="职业",
                          complexity=0.0)
    check("职业档不给放宽 → 可疑", d2["assessment"] == ASSESSMENT_QUESTIONABLE)
    check("容差单调（复杂度越高越宽）",
          dynamic_tolerance("5级", 1.0) > dynamic_tolerance("5级", 0.0))

    # 榜外手：绝不能直接判错（§23）
    ghost = assess_candidate("P8", infos, "B")   # 不在候选、未强制分析
    check("榜外且无数据 → unknown 不猜", ghost["assessment"] == ASSESSMENT_UNKNOWN
          and ghost["source"] == "insufficient")
    forced = assess_candidate(
        "P8", infos, "B",
        forced_score_lead=2.0 - 0.7,   # 只亏 0.7 目（黑视角）
        forced_winrate=0.515, best_score_lead=2.0, best_winrate=0.52)
    check("榜外但强制分析0.7目 → 优秀",
          forced["assessment"] == ASSESSMENT_EXCELLENT
          and forced["source"] == "forced", str(forced))
    q = forced_move_query({"moves": ["Q16"], "komi": 7.5}, "P8", player="w")
    check("强制分析查询符合 KataGo 协议",
          q["allowMoves"] == [{"player": "W", "moves": ["P8"], "untilDepth": 1}]
          and q["komi"] == 7.5, str(q["allowMoves"]))
    resp = {"moveInfos": [{"move": "P8", "order": 0, "scoreLead": 1.3,
                           "winrate": 0.51}]}
    check("强制分析结果解析",
          forced_move_result(resp, "p8") == (1.3, 0.51, 0)
          and forced_move_result(resp, "Z1") == (None, None, None))

    # 白方视角目差换算
    w_infos = _infos([("A", 0.0), ("B", 1.0)], color="W")
    check("白方视角目损正确",
          abs(assess_candidate("B", w_infos, "W")["score_loss"] - 1.0) < 1e-6)

    # 主动复盘四分类（§25）
    check("重选达优秀 → corrected",
          classify_retry(7.3, ASSESSMENT_EXCELLENT) == RETRY_CORRECTED)
    check("重选合理非最优 → alternative_correct",
          classify_retry(7.3, ASSESSMENT_ACCEPTABLE) == RETRY_ALTERNATIVE_CORRECT)
    check("明显改善但未达标 → improved",
          classify_retry(8.0, ASSESSMENT_QUESTIONABLE, retry_loss=2.5)
          == RETRY_IMPROVED)
    check("再次同类错误 → repeated",
          classify_retry(8.0, ASSESSMENT_BAD, retry_loss=7.1) == RETRY_REPEATED)
    check("无数据保守 repeated",
          classify_retry(8.0, ASSESSMENT_UNKNOWN) == RETRY_REPEATED)

    # SRS 映射
    check("SRS 映射",
          srs_result(ASSESSMENT_BEST) == "good"
          and srs_result(ASSESSMENT_ACCEPTABLE) == "hard"
          and srs_result(ASSESSMENT_BAD) == "again")

    # 审查 P0-1：同一手亏 1.8 目——字母/自由落子/持久化三路判定必须一致
    from candidate_assessment import build_assessment_context
    ctx = build_assessment_context(stable_rank="15级")   # 容差 2.8
    infos2 = _infos([("A", 0.0), ("B", 1.8)])
    free = assess_candidate("B", infos2, "B",
                            performance_label=ctx["performance_label"],
                            complexity=ctx["complexity"])
    from candidate_assessment import assessment_for_loss
    letter_level, _ = assessment_for_loss(
        1.8, performance_label=ctx["performance_label"],
        complexity=ctx["complexity"])
    check("字母与自由落子同容差同判定（1.8目@15级）",
          free["assessment"] == letter_level == "acceptable"
          and free["current_level_ok"] is True,
          "%s vs %s" % (free["assessment"], letter_level))
    # 未设稳定棋力：基础容差（默认 1.8），两条路径同样一致
    # 2.2 目 > 基础容差 → 可疑；1.8 目压线 → 当前水平可接受
    free2 = assess_candidate("B", _infos([("A", 0.0), ("B", 2.2)]), "B",
                             performance_label=None, complexity=0.0)
    letter2, _ = assessment_for_loss(2.2, performance_label=None, complexity=0.0)
    check("未设棋力走基础容差且两路一致",
          free2["assessment"] == letter2 == "questionable")
    edge2, _ = assessment_for_loss(1.8, performance_label=None, complexity=0.0)
    check("基础容差边界（1.8 目压线可接受）", edge2 == "acceptable")
    # 持久化消费已算好的 assessment，不重算（结果漂移归零）
    import tempfile, os as _os, shutil as _sh
    import mistake_book as mb, learning_store as ls, learning_event as le
    tmp = _os.path.join(tempfile.mkdtemp(prefix="p01-"))
    # 用规范文件名（书侧投影按目录推导 learning_events.json）
    book = _os.path.join(tmp, "book.json")
    lp = _os.path.join(tmp, "learning_events.json")
    prob = [{"move_no": 5, "color": "B", "played_move": "R10",
             "best_move": "A", "quality_key": "blunder", "score_loss": 4.0}]
    mb.sync_profile_summary({"id": "gx", "profileSide": "B"},
                            {"top_problem_moves": prob}, book)
    ls.sync_profile_summary({"id": "gx", "profileSide": "B"},
                            {"problem_moves_all": prob}, lp)
    iid = mb.list_items(book)[0]["id"]
    out = mb.record_graded_attempt(
        iid, "B", infos2, "B", "A", assessment=dict(free),
        path=book, learning_path=lp)
    check("持久化直接消费传入判定（good=hard 档）",
          out["srs_result"] == "hard"
          and out["assessment"]["assessment"] == free["assessment"])
    it = mb.get_item(iid, book)
    check("书侧 attempts 为事件投影且判定一致",
          it["attempts"][0]["assessment"] == "acceptable"
          and it["attempts"][0]["result"] == "hard")
    _sh.rmtree(tmp, ignore_errors=True)

    print("test_candidate_assessment: 全部通过")


if __name__ == "__main__":
    run()
