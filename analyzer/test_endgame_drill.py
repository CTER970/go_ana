"""test_endgame_drill —— 官子收束题生成的纯逻辑测试（无 tkinter / 无 KataGo）。

fixture 约定（与 test_review.py 一致）：手动构造 analysis dict 挂到主线节点。
棋盘着法用"散点谱"：x∈{1,4,7,10,13,16}、y=1+i//6，60 手内无重复点、无提子，
黑先（让子局 fixture 白先）。
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from board import BLACK
from movetree import MoveTree
from review import ReviewReport
from endgame_drill import (
    build_endgame_drills, grade_choice,
    DEFAULT_ENDGAME_WINDOW, MIN_ANALYZED_ENDGAME, MIN_TOTAL_MOVES,
    DEFAULT_MAX_PROBLEMS,
)


def check(name, cond, extra=""):
    print("[CHECK] %-44s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        raise AssertionError(name)


# ---- analysis dict 构造助手（字段与 KataGo 分析缓存一致） ----
def _mi(move, sl, order=0, wr=0.5, visits=1000, prior=0.2, pv=None):
    return {"move": move, "order": order, "winrate": wr, "scoreLead": sl,
            "visits": visits, "prior": prior, "pv": pv or [move]}


def _default_mis():
    # 默认候选：一选 0.5 目优势 → 默认每手目损 0.5，低于出题阈值，不产生干扰题
    return [_mi("Q16", 0.5, 0), _mi("D4", -0.2, 1)]


def _analysis(sl, mis=None):
    return {"rootInfo": {"scoreLead": sl, "winrate": 0.5},
            "moveInfos": _default_mis() if mis is None else mis}


def _play_spread(t, n):
    """散点谱：60 手内坐标唯一、无提子、全部合法。"""
    for i in range(n):
        t.play(1 + (i % 6) * 3, 1 + i // 6)


def _mk_game(n_moves=60, overrides=None, default=True):
    """造一棵带分析缓存的主线棋谱。overrides: {节点序号(=手数): analysis 或 None}。"""
    t = MoveTree(19)
    _play_spread(t, n_moves)
    line = ReviewReport(t).mainline_nodes()
    for idx, node in enumerate(line):
        if overrides and idx in overrides:
            node.analysis = overrides[idx]
        elif default:
            node.analysis = _analysis(0.0)
    return t


# ---- 正常终局 fixture：4 个收束点（3 目损 + 1 先后手转换） ----
def _normal_overrides():
    return {
        # 第 50 手（白）：实战后黑反超 → 白目损 3.0（D4 黑视角 0 目，比一选差 1 目）
        49: _analysis(0.0, [_mi("Q2", -1.0, 0, pv=["Q2", "C16"]),
                            _mi("D4", 0.0, 1)]),
        50: _analysis(2.0),
        # 第 55 手（黑）：一选 C2（后手），实战目损 5.0
        54: _analysis(0.0, [_mi("C2", 2.0, 0, pv=["C2", "Q16", "C2"]),
                            _mi("Q16", 0.5, 1), _mi("A1", -4.0, 2)]),
        55: _analysis(-3.0),
        # 第 57 手（黑）：一选 C2 是先手（C3 局部应答），错过代价 2.0 目；
        # 其节点缓存同时是第 58 手的父局面（mis 为白视角最优 Q2/-4.5）
        56: _analysis(0.0, [_mi("C2", 2.0, 0, pv=["C2", "C3", "C4"]),
                            _mi("Q16", 0.0, 1, pv=["Q16"])]),
        57: _analysis(1.8, [_mi("Q2", -4.5, 0, pv=["Q2", "C16"]),
                            _mi("D4", -5.0, 1)]),
        # 第 58 手（白）：实战目损 2.5（白视角：sl_after − best_sl = −2.0−(−4.5)）
        58: _analysis(-2.0),
    }


def test_normal_endgame():
    t = _mk_game(60, _normal_overrides())
    s = build_endgame_drills(t)
    check("正常终局非空", not s.is_empty, str(s.reasons))
    check("出 4 题", len(s.problems) == 4,
          str([(p.move_number, p.value) for p in s.problems]))
    check("终局段起点=11", s.endgame_start == 60 - DEFAULT_ENDGAME_WINDOW + 1,
          str(s.endgame_start))
    check("终局段终点=60", s.endgame_end == 60, str(s.endgame_end))
    check("分析齐全 50 手", s.analyzed_moves == 50, str(s.analyzed_moves))
    check("题量不超过默认上限", len(s.problems) <= DEFAULT_MAX_PROBLEMS,
          str(len(s.problems)))
    check("无降级原因", not s.reasons, str(s.reasons))
    check("无缺分析警告", not any("缺少分析" in w for w in s.warnings))

    nums = [p.move_number for p in s.problems]
    vals = [p.value for p in s.problems]
    check("按收束价值降序", nums == [55, 50, 58, 57], str(nums))
    check("价值单调不增", all(vals[i] >= vals[i + 1]
                              for i in range(len(vals) - 1)), str(vals))
    check("目差波动=5.0", abs((s.score_swing or 0) - 5.0) < 1e-6, str(s.score_swing))

    d55 = s.problems[0]
    check("55=目损收束题", d55.drill_kind == "loss" and d55.kind_label == "目损收束")
    check("55 目损 5.0", abs(d55.loss - 5.0) < 1e-6, str(d55.loss))
    check("55 实战着法 B9", d55.played_move == "B9", d55.played_move)
    check("55 一选 C2", d55.best_move == "C2", d55.best_move)
    check("55 主变来自父缓存", d55.best_pv[:2] == ["C2", "Q16"], str(d55.best_pv))
    check("55 实战评级 欠佳", d55.played_quality == "欠佳", d55.played_quality)
    check("55 练习起点=54", d55.start_move_number == 54)
    check("55 快照 54 手", len(d55.snapshot.moves) == 54,
          str(len(d55.snapshot.moves)))
    check("55 快照轮黑", d55.snapshot.to_move == "B" and d55.color == "B")
    check("55 快照棋盘 19", d55.snapshot.board_size == 19)
    check("55 快照无让子", d55.snapshot.initial_stones == [])
    check("55 候选 3 行", len(d55.candidates) == 3, str(len(d55.candidates)))
    check("55 一选标记 is_best", d55.candidates[0].is_best
          and d55.candidates[0].move == "C2")
    check("55 三选目损 6.0", abs(d55.candidates[2].score_loss - 6.0) < 1e-6,
          str(d55.candidates[2].score_loss))
    check("55 非先手标注", d55.is_sente is False)

    d50 = s.problems[1]
    check("50=白方题", d50.color == "W" and d50.to_play_label == "白方")
    check("50 目损 3.0（走子方视角）", abs(d50.loss - 3.0) < 1e-6, str(d50.loss))
    check("50 二选目损 1.0", abs(d50.candidates[1].score_loss - 1.0) < 1e-6,
          str(d50.candidates[1].score_loss))

    d57 = s.problems[3]
    check("57=先后手转换题", d57.drill_kind == "sente"
          and d57.kind_label == "先后手转换")
    check("57 先手启发式命中", d57.is_sente is True)
    check("57 先手代价 2.0", abs(d57.sente_gap - 2.0) < 1e-6, str(d57.sente_gap))
    check("57 实战目损仅 0.2", abs(d57.loss - 0.2) < 1e-6, str(d57.loss))
    check("57 快照 56 手", len(d57.snapshot.moves) == 56)

    d58 = s.problems[2]
    check("58=目损收束题", d58.drill_kind == "loss")
    check("58 目损 2.5", abs(d58.loss - 2.5) < 1e-6, str(d58.loss))


def test_user_color_and_cap():
    t = _mk_game(60, _normal_overrides())
    sw = build_endgame_drills(t, user_color="W")
    check("只练白方 → 50/58 两题",
          [p.move_number for p in sw.problems] == [50, 58],
          str([p.move_number for p in sw.problems]))
    sb = build_endgame_drills(t, user_color="B")
    check("只练黑方 → 55/57",
          [p.move_number for p in sb.problems] == [55, 57],
          str([p.move_number for p in sb.problems]))
    s2 = build_endgame_drills(t, user_color="x")   # 非法 → 双方
    check("非法方 → 按双方", len(s2.problems) == 4)
    s3 = build_endgame_drills(t, max_problems=2)
    check("max_problems 截断", len(s3.problems) == 2
          and [p.move_number for p in s3.problems] == [55, 50])


def test_window_scope():
    # 第 30 手造一个目损 3.0（白）：默认窗口(50)含它，窗口 20 不含
    ov = {29: _analysis(0.0, [_mi("Q2", -1.0, 0), _mi("D4", 0.0, 1)]),
          30: _analysis(2.0),
          54: _normal_overrides()[54], 55: _normal_overrides()[55]}
    t = _mk_game(60, ov)
    s_default = build_endgame_drills(t)
    check("默认窗口含第 30 手",
          any(p.move_number == 30 for p in s_default.problems),
          str([p.move_number for p in s_default.problems]))
    s20 = build_endgame_drills(t, window=20)
    check("窗口 20 起点为 41", s20.endgame_start == 41, str(s20.endgame_start))
    check("窗口 20 排除第 30 手",
          not any(p.move_number == 30 for p in s20.problems))
    check("窗口 20 仍含第 55 手",
          any(p.move_number == 55 for p in s20.problems))


def test_boundaries():
    # None 棋谱 / 空棋谱
    s = build_endgame_drills(None)
    check("None → 空+原因", s.is_empty and s.reasons, str(s.reasons))
    s = build_endgame_drills(MoveTree(19))
    check("空棋谱 → 空+原因", s.is_empty and any("空" in r for r in s.reasons))

    # 短局
    s = build_endgame_drills(_mk_game(MIN_TOTAL_MOVES - 1))
    check("短局 → 空+原因", s.is_empty and any("太短" in r for r in s.reasons),
          str(s.reasons))

    # 无分析
    t = MoveTree(19)
    _play_spread(t, 60)
    s = build_endgame_drills(t)
    check("无分析 → 空+原因", s.is_empty
          and any("尚未分析" in r for r in s.reasons), str(s.reasons))

    # 终局段分析不足（只有前 11 个节点有分析，终局段 0 手齐全）
    t = _mk_game(60, overrides={}, default=False)
    line = ReviewReport(t).mainline_nodes()
    for node in line[:11]:
        node.analysis = _analysis(0.0)
    s = build_endgame_drills(t)
    check("终局段分析不足 → 空+原因", s.is_empty
          and any("分析齐全" in r for r in s.reasons), str(s.reasons))
    check("原因含最低手数", any(str(MIN_ANALYZED_ENDGAME) in r for r in s.reasons),
          str(s.reasons))
    check("降级时给出分析手数", s.analyzed_moves == 0, str(s.analyzed_moves))

    # 全部低于阈值（默认散点谱目损 0.5）
    s = build_endgame_drills(_mk_game(60))
    check("无达标收束点 → 空+原因", s.is_empty
          and any("阈值" in r for r in s.reasons), str(s.reasons))


def test_partial_analysis_and_malformed():
    ov = dict(_normal_overrides())
    ov[44] = None        # 中段一手无分析 → 44/45 两手被跳过 + warning
    # 第 55 手父候选混入脏数据（缺 move / scoreLead=None）
    ov[54] = _analysis(0.0, [_mi("C2", 2.0, 0, pv=["C2", "Q16"]),
                             {"move": None, "order": 1},
                             {"order": 2},
                             {"move": "A1", "scoreLead": None, "order": 3}])
    t = _mk_game(60, ov)
    s = build_endgame_drills(t)
    check("脏数据不崩溃且仍出题", not s.is_empty, str(s.reasons))
    check("缺分析警告 2 手", any("2 手缺少分析" in w for w in s.warnings),
          str(s.warnings))
    check("分析齐全 48 手", s.analyzed_moves == 48, str(s.analyzed_moves))
    d55 = next((p for p in s.problems if p.move_number == 55), None)
    check("第 55 手照常出题", d55 is not None)
    if d55 is not None:
        check("脏候选被跳过", all(c.move for c in d55.candidates)
              and len(d55.candidates) == 1, str([c.move for c in d55.candidates]))
        check("缺 scoreLead 候选不入表（不编造目数）",
              all(c.move != "A1" for c in d55.candidates),
              str([(c.move, c.score_loss) for c in d55.candidates]))


def test_dirty_best_scorelead_skips_hand():
    # 一选缺 scoreLead：目损口径无证据 → 整手不出题（回归：旧版编造 0.0，
    # 全部候选目损 0 全判"最佳"，任意作答都判对）
    ov = {54: {"rootInfo": {"scoreLead": 0.0, "winrate": 0.5},
               "moveInfos": [{"move": "C2", "order": 0},   # 一选缺 scoreLead
                             _mi("Q16", 0.5, 1)]},
          55: _analysis(-3.0)}
    t = _mk_game(60, ov)
    s = build_endgame_drills(t)
    check("一选缺 scoreLead → 该手不出题", s.is_empty
          and not any(p.move_number == 55 for p in s.problems),
          str([p.move_number for p in s.problems]))
    check("空题原因说明数据不足而非未达标",
          any("候选数据不足" in r for r in s.reasons)
          and any("已达" in r for r in s.reasons), str(s.reasons))


def test_pass_best_skipped():
    # 一选为 pass：即使按目损本该出题（3.0），也不从"该收官"处出题
    ov = {56: _analysis(0.0, [_mi("pass", 0.0, 0), _mi("D4", -0.5, 1)]),
          57: _analysis(-3.0)}
    t = _mk_game(60, ov)
    s = build_endgame_drills(t)
    check("一选 pass → 无题", s.is_empty, str([p.move_number for p in s.problems]))
    check("一选 pass → 跳过警告",
          any("无达标收束点" in w for w in s.warnings), str(s.warnings))
    # 回归（文案诚实）：存在 3.0 目损手时，空题原因不得断言"目损 < 1.5 目"
    check("空题原因指出达标手无法成题", any("一选为 pass" in r for r in s.reasons),
          str(s.reasons))


def test_handicap_snapshot():
    t = MoveTree(19)
    t.set_initial_stones([(BLACK, 3, 3), (BLACK, 15, 3), (BLACK, 3, 15),
                          (BLACK, 15, 15), (BLACK, 9, 9)])
    _play_spread(t, 40)     # 让子局白先：偶数手 = 黑
    line = ReviewReport(t).mainline_nodes()
    for idx, node in enumerate(line):
        node.analysis = _analysis(0.0)
    # 第 38 手（黑）目损 3.0
    line[37].analysis = _analysis(0.0, [_mi("C2", 2.0, 0, pv=["C2", "Q16"]),
                                        _mi("Q16", 0.5, 1)])
    line[38].analysis = _analysis(-1.0)
    s = build_endgame_drills(t)
    check("让子局出题", not s.is_empty, str(s.reasons))
    d = next((p for p in s.problems if p.move_number == 38), None)
    check("第 38 手（黑）在题中", d is not None)
    if d is not None:
        check("快照带让子 setup", d.snapshot.initial_stones ==
              t.initial_stones_list(), str(d.snapshot.initial_stones))
        check("让子 setup 非空", len(d.snapshot.initial_stones) == 5)
        check("快照含 D16", ["B", "D16"] in d.snapshot.initial_stones)
        check("练习方=黑", d.color == "B" and d.snapshot.to_move == "B")
        check("快照落子 37 手", len(d.snapshot.moves) == 37)


def test_grade_choice():
    t = _mk_game(60, _normal_overrides())
    s = build_endgame_drills(t)
    d55 = s.problems[0]     # 候选 C2(0目) / Q16(1.5目) / A1(6.0目)
    g_best = grade_choice(d55, "C2")
    check("选一选 → 正确", g_best["isCorrect"] and g_best["assessment"] == "best",
          str(g_best))
    g_lower = grade_choice(d55, "c2")
    check("小写作法同样命中", g_lower["isCorrect"], str(g_lower))
    g_bad = grade_choice(d55, "A1")
    check("选 6 目损候选 → 不正确", not g_bad["isCorrect"]
          and g_bad["assessment"] in ("questionable", "bad"), str(g_bad))
    g_none = grade_choice(d55, "Z99")
    check("候选外着法 → 无法评定", not g_none["isCorrect"]
          and g_none["chosenKey"] is None and g_none["assessment"] is None,
          str(g_none))
    # 接力板#11 回归锚：键名与 drill 家族 grade_quiz 统一（原 "isPlayed"）
    check("判分键统一 isActual（无 isPlayed 残留）",
          "isActual" in g_best and "isPlayed" not in g_best, str(g_best))
    check("一选/劣选均非实战着法",
          g_best["isActual"] is False and g_bad["isActual"] is False,
          str(g_best))


def test_to_dict_shape():
    t = _mk_game(60, _normal_overrides())
    data = build_endgame_drills(t).to_dict()
    check("题集字段齐全", all(k in data for k in (
        "problems", "reasons", "warnings", "endgameStart", "endgameEnd",
        "analyzedMoves", "scoreSwing", "version")))
    p0 = data["problems"][0]
    check("题目字段齐全", all(k in p0 for k in (
        "moveNumber", "startMoveNumber", "drillKind", "kindLabel", "value",
        "playedMove", "bestMove", "bestPv", "candidates", "snapshot")))
    snap = p0["snapshot"]
    check("快照字段齐全", all(k in snap for k in (
        "boardSize", "initialStones", "moves", "toMove")))
    check("候选字段齐全", all(k in p0["candidates"][0] for k in (
        "key", "evalLabel", "move", "visits", "policy", "scoreLead",
        "scoreLoss", "qualityLabel")))


def main():
    test_normal_endgame()
    test_user_color_and_cap()
    test_window_scope()
    test_boundaries()
    test_partial_analysis_and_malformed()
    test_dirty_best_scorelead_skips_hand()
    test_pass_best_skipped()
    test_handicap_snapshot()
    test_grade_choice()
    test_to_dict_shape()
    print("test_endgame_drill: PASS")


if __name__ == "__main__":
    main()
