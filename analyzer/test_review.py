"""test_review —— 复盘评价纯逻辑测试（不启动 KataGo，手动构造 analysis dict）。"""
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
from review import ReviewReport, MoveEvaluation


def check(name, cond, extra=""):
    print(("[CHECK] %-32s %s %s" % (name, "OK" if cond else "FAIL", extra)))
    if not cond:
        raise AssertionError(name)


# ---- analysis dict 构造助手 ----
def analysis(score_lead, winrate, move_infos):
    return {"rootInfo": {"scoreLead": score_lead, "winrate": winrate},
            "moveInfos": move_infos}


def mi(move, sl, wr, order=0):
    return {"move": move, "scoreLead": sl, "winrate": wr, "order": order}


def _attach_mainline_analysis(tree, per_node):
    """给主线每个节点（含根）按顺序挂 analysis dict（per_node: list，长度=主线节点数）。"""
    rr = ReviewReport(tree)
    line = rr.mainline_nodes()
    assert len(line) == len(per_node), (len(line), len(per_node))
    for node, a in zip(line, per_node):
        node.analysis = a


def test_mainline_only_follows_first_child():
    t = MoveTree(19)
    t.play(3, 3)       # B D16
    t.play(15, 15)     # W Q4   ← 主线第二子
    t.undo()           # 回 B D16
    t.play(15, 3)      # 变着 W Q16（B D16 现有 2 子；此为分支 children[1]）
    rr = ReviewReport(t)
    line = rr.mainline_nodes()
    check("主线含根共 3 节点", len(line) == 3, str(len(line)))      # root + B + 主线 W
    check("主线第一子=首次落子", line[1].move[1] == (3, 3))
    check("主线第二子=首次 W 落子", line[2].move[1] == (15, 15))
    check("分支不计入主线", all(nd.move is None or nd.move[1] != (15, 3) for nd in line))


def test_loss_zero_when_actual_is_best():
    # 黑走 best：P.best=D4 sl=+3，走 D4 后 N.scoreLead=+3 → loss=0
    t = MoveTree(19)
    t.play(3, 15)      # B D4 (3,15)
    _attach_mainline_analysis(t, [
        analysis(0.0, 0.50, [mi("D4", 3.0, 0.52, 0), mi("Q16", 1.0, 0.51, 1)]),
        analysis(3.0, 0.52, [mi("Q16", 3.0, 0.52, 0)]),
    ])
    evs = ReviewReport(t).evaluate()
    check("评价数=1", len(evs) == 1, str(len(evs)))
    e = evs[0]
    check("黑走 best → loss=0", e.analyzed and abs(e.loss - 0.0) < 1e-9, str(e.loss))
    check("color=B", e.color == "B")
    check("coord=D4", e.coord == "D4", str(e.coord))
    check("best_move=D4", e.best_move == "D4")
    check("before=0 after=3", abs(e.score_lead_before - 0.0) < 1e-9 and abs(e.score_lead_after - 3.0) < 1e-9)


def test_loss_black_blunder():
    # 黑走失误：P.best=D4 sl=+3，走 Q16 后 N.scoreLead=+1 → loss = 3-1 = 2
    t = MoveTree(19)
    t.play(15, 3)      # B Q16 (15,3)
    _attach_mainline_analysis(t, [
        analysis(0.0, 0.50, [mi("D4", 3.0, 0.52, 0), mi("Q16", 1.0, 0.51, 1)]),
        analysis(1.0, 0.51, [mi("D4", 1.0, 0.51, 0)]),
    ])
    e = ReviewReport(t).evaluate()[0]
    check("黑走失误 loss=2.0", e.analyzed and abs(e.loss - 2.0) < 1e-9, str(e.loss))


def test_loss_white_view_flip():
    # 白走失误：P(轮白).best=D4 sl=-3，白走 Q16 后 N.scoreLead=-1
    # loss(白) = S_actual - S_best = -1 - (-3) = 2.0
    t = MoveTree(19)
    t.play(3, 3)       # B D16（让轮次到白）
    t.play(15, 3)      # W Q16
    _attach_mainline_analysis(t, [
        analysis(0.0, 0.50, [mi("D4", -3.0, 0.40, 0), mi("Q16", -1.0, 0.49, 1)]),  # 根：轮黑
        analysis(0.0, 0.50, [mi("D4", -3.0, 0.40, 0), mi("Q16", -1.0, 0.49, 1)]),  # B 走后：轮白
        analysis(-1.0, 0.49, [mi("D4", -1.0, 0.49, 0)]),                            # W 走 Q16 后
    ])
    evs = ReviewReport(t).evaluate()
    e_white = evs[1]   # 第二手=白走 Q16
    check("白走 color=W", e_white.color == "W")
    check("白走失误 loss=2.0（视角翻转）", e_white.analyzed and abs(e_white.loss - 2.0) < 1e-9, str(e_white.loss))

    # 白走最佳：白走 D4 → N.scoreLead=-3 → loss = -3-(-3)=0
    t2 = MoveTree(19)
    t2.play(3, 3); t2.play(3, 15)   # B D16, W D4
    _attach_mainline_analysis(t2, [
        analysis(0.0, 0.50, [mi("D4", -3.0, 0.40, 0)]),
        analysis(0.0, 0.50, [mi("D4", -3.0, 0.40, 0)]),
        analysis(-3.0, 0.40, [mi("Q16", -3.0, 0.40, 0)]),
    ])
    e_best = ReviewReport(t2).evaluate()[1]
    check("白走 best loss≈0", e_best.analyzed and abs(e_best.loss - 0.0) < 1e-9, str(e_best.loss))


def test_not_analyzed_cases():
    # P 缺 analysis
    t = MoveTree(19)
    t.play(3, 3)
    t.root.analysis = None
    t.current.analysis = analysis(1.0, 0.5, [mi("Q16", 1.0, 0.5, 0)])
    e = ReviewReport(t).evaluate()[0]
    check("P 缺 analysis → analyzed=False", e.analyzed is False and e.loss is None)

    # N 缺 analysis
    t2 = MoveTree(19)
    t2.play(3, 3)
    t2.root.analysis = analysis(0.0, 0.5, [mi("D4", 3.0, 0.52, 0)])
    t2.current.analysis = None
    e2 = ReviewReport(t2).evaluate()[0]
    check("N 缺 analysis → analyzed=False", e2.analyzed is False and e2.loss is None)

    # P 无 moveInfos
    t3 = MoveTree(19)
    t3.play(3, 3)
    t3.root.analysis = analysis(0.0, 0.5, [])
    t3.current.analysis = analysis(1.0, 0.5, [])
    e3 = ReviewReport(t3).evaluate()[0]
    check("P 无 moveInfos → analyzed=False", e3.analyzed is False and e3.loss is None)


def test_pass_node():
    # pass 节点：coord=None，is_pass=True；best 为 pass 时 loss≈0
    t = MoveTree(19)
    t.play(3, 3)
    t.play_pass()      # W pass
    _attach_mainline_analysis(t, [
        analysis(0.0, 0.50, [mi("D4", 3.0, 0.52, 0)]),
        analysis(0.0, 0.50, [mi("pass", 0.0, 0.50, 0)]),     # 轮白，best=pass
        analysis(0.0, 0.50, [mi("D4", 0.0, 0.50, 0)]),       # W pass 后
    ])
    e = ReviewReport(t).evaluate()[1]
    check("pass coord=None", e.coord is None)
    check("pass is_pass=True", e.is_pass is True)
    check("pass(=best) loss≈0", e.analyzed and abs(e.loss - 0.0) < 1e-9, str(e.loss))
    check("best_move='pass'", e.best_move == "pass", str(e.best_move))


def test_top_losses_order_and_filter():
    t = MoveTree(19)
    # 三手，loss 分别 5、1、3（黑黑黑，简化）
    t.play(15, 3)      # B1 loss 5
    t.play(3, 15)      # B2 loss 1（白这手，但为构造方便给白也 loss；实际白 loss 用 flip）
    t.play(15, 15)     # B3 loss 3
    # 给每个非根节点的父配 analysis
    line = ReviewReport(t).mainline_nodes()
    # root(轮黑) best=某 sl；每手 actual 后 scoreLead 设成制造 loss
    line[0].analysis = analysis(0.0, 0.5, [mi("A1", 5.0, 0.6, 0)])   # best sl=5
    line[1].analysis = analysis(0.0, 0.5, [mi("A1", 0.0, 0.5, 0)])   # B1 走后 sl=0 → loss=5（黑）
    line[2].analysis = analysis(0.0, 0.5, [mi("A1", 1.0, 0.5, 0)])   # 轮白 best sl=1
    # 白走后 sl 设 -4 → 白 loss = S_actual - S_best = -4 - 1 = -5 → max(0)=0？要 loss=1，让 actual=-0?
    # 重新：要白 loss=1，sl_after - best_sl =1 → best_sl=1, sl_after=2? 但白走差应黑视角变大。
    # 简化：本测试只验排序与过滤，loss 值手动给已知。重设白那手。
    line[2].analysis = analysis(2.0, 0.5, [mi("A1", 1.0, 0.5, 0)])   # 轮白 best sl=1, 走后 sl=2 → 白 loss=2-1=1
    line[3].analysis = analysis(0.0, 0.5, [mi("A1", 3.0, 0.5, 0)])
    # 第三手 B3：父=line[2](轮黑) best sl 应为某值，actual 后 line[3].sl
    line[2].analysis = analysis(2.0, 0.5, [mi("A1", 3.0, 0.6, 0)])   # 轮黑 best sl=3
    line[3].analysis = analysis(0.0, 0.5, [mi("A1", 0.0, 0.5, 0)])   # B3 走后 sl=0 → loss=3
    rr = ReviewReport(t)
    evs = rr.evaluate()
    losses = sorted([e.loss for e in evs if e.loss is not None], reverse=True)
    check("三手均有 loss", len(losses) == 3, str(losses))
    top = rr.top_losses(n=8, min_loss=2.0)
    check("min_loss 过滤掉 loss<2", all(e.loss >= 2.0 for e in top), str([e.loss for e in top]))
    check("Top 按降序", [e.loss for e in top] == sorted([e.loss for e in top], reverse=True),
          str([e.loss for e in top]))
    check("Top 含 loss=5 与 loss=3，不含 loss=1", any(abs(e.loss - 5.0) < 1e-9 for e in top)
          and any(abs(e.loss - 3.0) < 1e-9 for e in top) and all(abs(e.loss - 1.0) > 1e-9 for e in top),
          str([e.loss for e in top]))
    # n 上限
    top2 = rr.top_losses(n=1, min_loss=0.0)
    check("n 上限生效", len(top2) == 1, str(len(top2)))


def test_series_and_progress():
    t = MoveTree(19)
    t.play(3, 3)
    t.play(15, 15)
    line = ReviewReport(t).mainline_nodes()
    line[0].analysis = analysis(0.0, 0.50, [])
    line[1].analysis = analysis(1.5, 0.52, [])
    # line[2] 不挂 analysis
    rr = ReviewReport(t)
    sl = rr.score_lead_series()
    check("score_lead_series 含已分析的 2 点", len(sl) == 2 and sl[0] == (0, 0.0) and sl[1] == (1, 1.5),
          str(sl))
    wr = rr.winrate_series()
    check("winrate_series 含已分析的 2 点", len(wr) == 2, str(wr))
    done, total = rr.analyze_progress()
    check("进度 2/3", done == 2 and total == 3, "%d/%d" % (done, total))
    check("node_at_move(1) 正确", rr.node_at_move(1) is line[1])
    check("node_at_move 越界 None", rr.node_at_move(99) is None)


def test_handicap_does_not_affect_move_number():
    # 让子 2 子 setup：root 有 2 黑子，但主线手数从第一手落子起算
    from board import BLACK
    t = MoveTree(19)
    t.set_initial_stones([(BLACK, 3, 3), (BLACK, 15, 15)])
    t.play(3, 15)     # 白第一手（让子后白先）
    line = ReviewReport(t).mainline_nodes()
    check("主线含根+1手", len(line) == 2, str(len(line)))
    evs = ReviewReport(t).evaluate()
    check("让子下第一手 move_number=1", evs[0].move_number == 1, str(evs[0].move_number))
    check("让子下第一手 color=W", evs[0].color == "W")


def test_readonly():
    t = MoveTree(19)
    t.play(3, 3)
    t.play(15, 15)
    line = ReviewReport(t).mainline_nodes()
    line[0].analysis = analysis(0.0, 0.5, [mi("D4", 3.0, 0.52, 0), mi("Q16", 1.0, 0.51, 1)])
    line[1].analysis = analysis(0.0, 0.5, [mi("D4", -3.0, 0.4, 0), mi("Q16", -1.0, 0.49, 1)])
    line[2].analysis = analysis(1.0, 0.51, [mi("D4", 1.0, 0.51, 0)])
    snap_root = dict(line[0].analysis)
    rr = ReviewReport(t)
    rr.evaluate(); rr.top_losses(); rr.score_lead_series(); rr.winrate_series(); rr.analyze_progress()
    rr.grade_summary()
    check("analysis dict 未被修改", line[0].analysis == snap_root)
    check("root move 未变", t.root.move is None)


def test_grade():
    from review import grade_of, GRADE_GOOD, GRADE_DOUBT, GRADE_BAD
    check("None → —", grade_of(None) == "—")
    check("0.0 → 好（best play 典型 loss）", grade_of(0.0) == "好")
    check("0.5 → 好", grade_of(0.5) == "好")
    check("好上界 0.999", grade_of(GRADE_GOOD - 0.001) == "好")
    check("1.0 → 普通（边界）", grade_of(GRADE_GOOD) == "普通")
    check("2.9 → 普通", grade_of(GRADE_DOUBT - 0.1) == "普通")
    check("3.0 → 疑问（边界）", grade_of(GRADE_DOUBT) == "疑问")
    check("5.9 → 疑问", grade_of(GRADE_BAD - 0.1) == "疑问")
    check("6.0 → 恶（边界）", grade_of(GRADE_BAD) == "恶")
    check("10 → 恶", grade_of(10) == "恶")
    # grade_summary：构造 好/疑问/恶 三手
    t = MoveTree(19)
    t.play(15, 3); t.play(3, 15); t.play(15, 15)   # B1 / W2 / B3
    line = ReviewReport(t).mainline_nodes()         # root,B1,W2,B3
    line[0].analysis = analysis(0.0, 0.5, [mi("A1", 0.5, 0.5, 0)])   # root best sl=0.5（B1 loss 用）
    line[1].analysis = analysis(0.0, 0.5, [mi("A1", -2.0, 0.5, 0)])  # B1 sl=0→loss=0.5(好); 轮白 best=-2（W2 loss 用）
    line[2].analysis = analysis(2.0, 0.5, [mi("A1", 7.0, 0.5, 0)])   # W2 sl=2→白loss=4(疑问); 轮黑 best=7（B3 loss 用）
    line[3].analysis = analysis(0.0, 0.5, [mi("A1", 0.0, 0.5, 0)])   # B3 sl=0→loss=7(恶)
    s = ReviewReport(t).grade_summary()
    check("grade_summary 好1 疑问1 恶1 总3",
          s["好"] == 1 and s["疑问"] == 1 and s["恶"] == 1 and s["总"] == 3, str(s))


def test_rating():
    from review import performance_rank_of, rank_of, ReviewReport
    check("rank None → —", rank_of(None) == "—")
    check("rank 0.3 → 职业级（扩档后）", rank_of(0.3) == "职业级")
    check("rank 0.5 边界 → 业余强段", rank_of(0.5) == "业余强段（6段+）")
    check("rank 1.0 边界 → 业余初段", rank_of(1.0) == "业余初段（1-3段）")
    check("rank 1.8 边界 → 高级位", rank_of(1.8) == "高级位（1-3级）")
    check("rank 3.0 边界 → 中级位", rank_of(3.0) == "中级位（4-6级）")
    check("rank 5.0 边界 → 入门", rank_of(5.0) == "入门（7-12级）")
    check("rank 8.0 边界 → 新手", rank_of(8.0) == "新手（13级以下）")
    check("rank 20 → 新手", rank_of(20) == "新手（13级以下）")
    # agreement_rank + player_stats：B 走首选(rank0) loss小；W 走次选(rank1) loss大
    t = MoveTree(19)
    t.play(3, 3)    # B D16（首选）
    t.play(15, 15)  # W Q4（次选）
    line = ReviewReport(t).mainline_nodes()
    line[0].analysis = analysis(0.0, 0.5, [mi("D16", 0.5, 0.55, 0), mi("Q4", 0.3, 0.52, 1)])
    line[1].analysis = analysis(0.5, 0.55, [mi("D4", -1.0, 0.45, 0), mi("Q4", -0.2, 0.5, 1)])
    line[2].analysis = analysis(-0.2, 0.5, [mi("D4", -0.2, 0.5, 0)])
    rr = ReviewReport(t)
    b = [e for e in rr.evaluate() if e.color == "B"][0]
    w = [e for e in rr.evaluate() if e.color == "W"][0]
    check("B agreement_rank=0（首选）", b.agreement_rank == 0, str(b.agreement_rank))
    check("W agreement_rank=1（次选）", w.agreement_rank == 1, str(w.agreement_rank))
    bs = rr.player_stats("B")
    ws = rr.player_stats("W")
    check("B avg_loss≈0", abs(bs["avg_loss"]) < 1e-9, str(bs))
    check("B agree1=100%", abs(bs["agree1"] - 100) < 1e-9, str(bs))
    check("W avg_loss≈0.8", abs(ws["avg_loss"] - 0.8) < 1e-9, str(ws))
    check("W agree1=0%", abs(ws["agree1"]) < 1e-9, str(ws))
    check("W agree3=100%（rank1<3）", abs(ws["agree3"] - 100) < 1e-9, str(ws))
    check("短样本不估段位", rr.player_rank("B") == "—" and rr.player_rank("W") == "—")
    check("稳健目损0.30 → 职业级表现（扩档后）", performance_rank_of(0.30) == "职业级表现")
    check("稳健目损0.58 → 7段+表现（已放宽）", performance_rank_of(0.58) == "业余7段+表现")
    check("稳健目损0.80 → 6段表现", performance_rank_of(0.80) == "业余6段表现")
    check("稳健目损1.20 → 5段表现（已放宽）", performance_rank_of(1.20) == "业余5段表现")
    # 空局
    t2 = MoveTree(19)
    check("空局 player_stats None", ReviewReport(t2).player_stats("B") is None)
    check("空局 player_rank —", ReviewReport(t2).player_rank("B") == "—")


def test_performance_rating_ignores_settled_positions():
    rr = ReviewReport(MoveTree(19))
    evs = []
    for i in range(8):
        evs.append(MoveEvaluation(
            i, i + 1, "W", "D4", False, 0.0, 0.0, 0.5, 0.5,
            "Q16", 0.0, 0.58, True, 0))   # 首选(rank0)，低目损
    for i in range(2):
        evs.append(MoveEvaluation(
            20 + i, 20 + i, "W", "D4", False, 0.0, 20.0, 0.999, 1.0,
            "Q16", 0.0, 20.0, True, None))
    rr.evaluate = lambda: evs
    p = rr.player_performance("W")
    check("胜负已定两手被排除", p["rated_moves"] == 8 and p["settled_ignored"] == 2, str(p))
    check("巨大后盘目损不污染估计", abs(p["performance_loss"] - 0.58) < 1e-9, str(p))
    check("吻合度=100%（首选）", abs(p["agree1"] - 1.0) < 1e-9, str(p))
    check("100%首选+目损0.58 → AI级表现（扩档后，目损非极低下调1档）", p["rank"] == "AI级表现", str(p))
    check("胜负已定大目损不进问题棋", rr.meaningful_problems() == [])


def test_meaningful_problems_prioritize_winrate_impact():
    rr = ReviewReport(MoveTree(19))
    evs = [
        MoveEvaluation(1, 1, "B", "D4", False, 0, 0, 0.60, 0.40,
                       "Q16", 0, 1.0, True, None),
        MoveEvaluation(2, 2, "W", "Q4", False, 0, 0, 0.50, 0.65,
                       "D16", 0, 4.0, True, None),
        MoveEvaluation(3, 3, "B", "C3", False, 0, 0, 0.995, 0.50,
                       "C4", 0, 20.0, True, None),
    ]
    rr.evaluate = lambda: evs
    problems = rr.meaningful_problems(n=10)
    check("仅保留有胜负意义的问题棋", [e.move_number for e in problems] == [1, 2],
          str([e.move_number for e in problems]))
    check("按胜率影响优先", abs(rr.winrate_loss_pct(problems[0]) - 20.0) < 1e-9)
    black_problems = rr.meaningful_problems(n=10, color="B")
    check("个人范围只保留指定颜色问题棋",
          [e.move_number for e in black_problems] == [1])
    coverage = rr.analysis_coverage("B")
    check("个人范围分析覆盖正确",
          coverage["total"] == 2 and coverage["analyzed"] == 2
          and coverage["meaningful"] == 1 and coverage["complete"])


def test_bad_move_intent_analysis():
    t = MoveTree(19)
    t.play(3, 3)  # B D16
    line = ReviewReport(t).mainline_nodes()
    line[0].analysis = analysis(
        0.0, 0.50,
        [{"move": "Q16", "scoreLead": 7.0, "winrate": 0.75,
          "order": 0, "pv": ["Q16", "D4", "Q3"]}])
    line[1].analysis = analysis(0.0, 0.30, [mi("D4", 0.0, 0.30, 0)])
    rr = ReviewReport(t)
    e = rr.evaluate()[0]
    intent = rr.bad_move_intent(e)
    check("恶手生成选点意图", intent is not None, str(intent))
    check("包含实战与AI选点", intent["actualMove"] == "D16"
          and intent["aiMove"] == "Q16", str(intent))
    check("AI意图包含主变", "Q16" in intent["aiIntent"] and "D4" in intent["aiIntent"],
          intent["aiIntent"])
    check("差异包含目损", "7.0目" in intent["difference"], intent["difference"])
    e.loss = 1.0
    check("非恶手不生成意图", rr.bad_move_intent(e) is None)


def test_phase_analysis():
    t = MoveTree(19)
    for x, y in [(3, 3), (15, 15), (3, 15), (15, 3), (9, 9), (10, 9), (9, 10), (10, 10), (4, 4)]:
        ok, reason = t.play(x, y)
        check("构造阶段测试棋局落子", ok, reason)
    rr = ReviewReport(t)
    line = rr.mainline_nodes()
    losses = [0.2, 0.4, 0.6, 2.0, 4.0, 6.0, 1.0, 1.0, 1.0]
    for i in range(len(line)):
        line[i].analysis = analysis(0.0, 0.5, [mi("pass", 0.0, 0.5, 0)])
    for i, loss in enumerate(losses, start=1):
        node = line[i]
        parent = line[i - 1]
        cl, coord = node.move
        actual = "pass" if coord is None else node.moves_list()[-1][1]
        best_sl = loss if cl == "B" else -loss
        parent.analysis = analysis(0.0, 0.5, [mi(actual, best_sl, 0.5, 0)])
        node.analysis = analysis(0.0, 0.5, [mi("pass", 0.0, 0.5, 0)])

    phases = {s["phase"]: s for s in rr.phase_summary()}
    check("布局范围 1-3", phases["opening"]["range"] == (1, 3), str(phases["opening"]["range"]))
    check("中盘范围 4-6", phases["middle"]["range"] == (4, 6), str(phases["middle"]["range"]))
    check("关子范围 7-9", phases["endgame"]["range"] == (7, 9), str(phases["endgame"]["range"]))
    check("布局 avg≈0.4", abs(phases["opening"]["avg_loss"] - 0.4) < 1e-9, str(phases["opening"]))
    check("中盘 avg≈4.0", abs(phases["middle"]["avg_loss"] - 4.0) < 1e-9, str(phases["middle"]))
    check("关子 avg≈1.0", abs(phases["endgame"]["avg_loss"] - 1.0) < 1e-9, str(phases["endgame"]))
    check("中盘问题手 3", phases["middle"]["problem_count"] == 3, str(phases["middle"]))
    check("阶段使用质量评价而非段位", phases["opening"]["quality"] == "优秀"
          and phases["middle"]["quality"] == "问题较多", str(phases))
    check("中文 phase player_stats 可用", rr.player_stats("B", phase="布局") is not None)


def test_full_ai_rank_and_quality_bridge():
    t = MoveTree(19)
    t.play(3, 3)  # B D16
    line = ReviewReport(t).mainline_nodes()
    candidates = [
        mi(move, 7.0 - index, 0.55, index)
        for index, move in enumerate(
            ["Q16", "D4", "Q4", "D3", "Q3", "K10", "D16"])
    ]
    line[0].analysis = analysis(0.0, 0.50, candidates)
    line[0].analysis["rootInfo"]["visits"] = 200
    line[1].analysis = analysis(1.0, 0.45, [mi("D4", 1.0, 0.45, 0)])
    rr = ReviewReport(t)
    evaluation = rr.evaluate()[0]
    check("完整候选排名 ai_rank=7", evaluation.ai_rank == 7, str(evaluation.ai_rank))
    check("旧 agreement_rank 仍只覆盖前五", evaluation.agreement_rank is None)
    quality = rr.move_quality_results(visits=200)[0]
    check("质量桥接使用 1-based ai_rank", quality.ai_rank == 7, str(quality.ai_rank))
    check("质量桥接阶段来自 ReviewReport", quality.stage == "opening", quality.stage)


def test_phase_bar_segments():
    """棋力评估进度条段：布局/关子(下得不错)标亮，中盘(问题较多)不标亮；frac 单调。"""
    t = MoveTree(19)
    for x, y in [(3, 3), (15, 15), (3, 15), (15, 3), (9, 9), (10, 9), (9, 10), (10, 10), (4, 4)]:
        ok, _reason = t.play(x, y)
        check("构造阶段测试棋局落子", ok)
    rr = ReviewReport(t)
    line = rr.mainline_nodes()
    losses = [0.2, 0.4, 0.6, 2.0, 4.0, 6.0, 1.0, 1.0, 1.0]   # 布局优 / 中盘差 / 关子稳
    for i in range(len(line)):
        line[i].analysis = analysis(0.0, 0.5, [mi("pass", 0.0, 0.5, 0)])
    for i, loss in enumerate(losses, start=1):
        node = line[i]
        parent = line[i - 1]
        cl, coord = node.move
        actual = "pass" if coord is None else node.moves_list()[-1][1]
        best_sl = loss if cl == "B" else -loss
        parent.analysis = analysis(0.0, 0.5, [mi(actual, best_sl, 0.5, 0)])
        node.analysis = analysis(0.0, 0.5, [mi("pass", 0.0, 0.5, 0)])
    segs = rr.phase_bar_segments()
    check("三段", len(segs) == 3, str(len(segs)))
    check("段标签", [s["label"] for s in segs] == ["布局", "中盘", "官子"])
    check("布局下得不错→标亮", segs[0]["is_good"] is True and segs[0]["quality"] == "优秀",
          str(segs[0]))
    check("中盘问题多→不标亮", segs[1]["is_good"] is False and segs[1]["quality"] == "问题较多",
          str(segs[1]))
    check("关子稳定→标亮", segs[2]["is_good"] is True and segs[2]["quality"] == "稳定",
          str(segs[2]))
    # frac 单调、覆盖 [0,1]、首段起点≈0、末段终点≈1
    fracs = [s["frac"] for s in segs]
    check("frac 在[0,1]", all(0.0 <= lo <= hi <= 1.0 for lo, hi in fracs), str(fracs))
    check("frac 首起点≈0", fracs[0][0] <= 1e-9, str(fracs[0]))
    check("frac 末终点≈1", abs(fracs[-1][1] - 1.0) < 1e-9, str(fracs[-1]))
    # color 视角过滤：只看黑方时仍返回三段结构（段位置不变）
    segs_b = rr.phase_bar_segments(color="B")
    check("黑方视角三段", len(segs_b) == 3 and [s["label"] for s in segs_b] == ["布局", "中盘", "官子"])
    # 无分析：全不标亮
    empty = ReviewReport(MoveTree(19)).phase_bar_segments()
    check("空盘全不标亮", all(not s["is_good"] and s["moves"] == 0 for s in empty))


def test_strength_calibration():
    """棋力校准（对齐主流平台）：吻合度为主、目损为辅（≥3档差才微调）。"""
    from review import (performance_rating, agreement_rank_index,
                        AGREEMENT_RANK_BANDS, PERFORMANCE_RANK_BANDS)
    # 档位与段位对齐：扩档后 index 0=强AI级，3=7段+（原 0），递增为弱
    check("吻合度段位数=目损段位数（对齐）",
          len(AGREEMENT_RANK_BANDS) == len(PERFORMANCE_RANK_BANDS),
          "%d/%d" % (len(AGREEMENT_RANK_BANDS), len(PERFORMANCE_RANK_BANDS)))
    check("吻合度0.52→7段+(idx3)", agreement_rank_index(0.52) == 3)
    check("吻合度0.42→5段(idx5)", agreement_rank_index(0.42) == 5)
    check("吻合度0.30→3段(idx7)", agreement_rank_index(0.30) == 7)
    check("吻合度0.05→档外", agreement_rank_index(0.05) == len(AGREEMENT_RANK_BANDS))
    # 扩档：高吻合度 → AI/职业级（不再封顶7段+）
    check("吻合0.97→强AI(idx0)", agreement_rank_index(0.97) == 0)
    check("吻合0.85→AI级(idx1)", agreement_rank_index(0.85) == 1)
    check("吻合0.65→职业(idx2)", agreement_rank_index(0.65) == 2)
    # 用户场景：旧目损法判 5段（loss≈1.2），吻合度 0.52 → 现 7段+（对齐涨棋网）
    r = performance_rating(0.52, 1.20, 30)
    check("吻合0.52+loss1.2 → 7段+（不再被目损压到5段）",
          r is not None and r[2] == "7段+", str(r))
    # 吻合度 0.42（5段水平）+ loss1.2 → 5段
    r = performance_rating(0.42, 1.20, 30)
    check("吻合0.42+loss1.2 → 5段", r is not None and r[2] == "5段", str(r))
    # 吻合度高但目损崩盘（差≥3档）→ 下调1档：7段+ → 6段
    r = performance_rating(0.52, 5.00, 30)
    check("吻合0.52+loss5.0崩盘 → 下调到6段", r is not None and r[2] == "6段", str(r))
    # 目损差但<3档差 → 不下调：吻合0.42(5段)+loss2.0(3段,差2) → 仍5段
    r = performance_rating(0.42, 2.00, 30)
    check("差<3档不下调 → 5段", r is not None and r[2] == "5段", str(r))
    # 样本不足 → None
    check("样本不足不估档", performance_rating(0.52, 1.0, 5) is None)
    # 无吻合度（分析残缺）→ 退回目损：loss0.58 → 7段+
    r = performance_rating(None, 0.58, 30)
    check("无吻合度退回目损 → 7段+", r is not None and r[2] == "7段+", str(r))

    # 等价 Elo 估算（围棋通行刻度）：高吻合 → 高 Elo（AI 级 3500+）
    from review import elo_estimate, ai_likeness_hint
    e = elo_estimate(0.97, 0.20, 30)
    check("强AI级 Elo 3800-4200", e is not None and e[0] == 3800 and e[1] == 4200, str(e))
    e2 = elo_estimate(0.52, 1.20, 30)
    check("7段+ Elo 2500-2900", e2 is not None and e2[0] == 2500, str(e2))
    check("样本不足 Elo=None", elo_estimate(0.97, 0.2, 5) is None)
    # AI 识别提示（高吻合 + 极低目损）
    check("AI级有提示", ai_likeness_hint(0.95, 0.20) is not None)
    check("人类水平无提示", ai_likeness_hint(0.50, 1.0) is None)

    # 难度指标（基于 prior）+ 发挥水准分布
    from review import difficulty_of, quality_distribution
    check("难度 prior0.5→0.5", abs(difficulty_of(0.5) - 0.5) < 1e-9)
    check("难度 prior0.1→0.9（难）", abs(difficulty_of(0.1) - 0.9) < 1e-9)
    check("难度 None→None", difficulty_of(None) is None)

    class _QR:
        def __init__(self, k, c="B"):
            self.quality_key = k
            self.color = c
    d = quality_distribution(
        [_QR("best"), _QR("best"), _QR("blunder"), _QR("unknown"), _QR("good", "W")], color="B")
    check("分布 color=B：best=2/blunder=1/unknown=1", d["best"] == 2 and d["blunder"] == 1 and d["unknown"] == 1, str(d))


if __name__ == "__main__":
    print("=" * 60)
    print(" 复盘评价（review）测试")
    print("=" * 60)
    test_mainline_only_follows_first_child(); print()
    test_loss_zero_when_actual_is_best(); print()
    test_loss_black_blunder(); print()
    test_loss_white_view_flip(); print()
    test_not_analyzed_cases(); print()
    test_pass_node(); print()
    test_top_losses_order_and_filter(); print()
    test_series_and_progress(); print()
    test_handicap_does_not_affect_move_number(); print()
    test_readonly(); print()
    test_grade(); print()
    test_rating(); print()
    test_performance_rating_ignores_settled_positions(); print()
    test_meaningful_problems_prioritize_winrate_impact(); print()
    test_bad_move_intent_analysis(); print()
    test_phase_analysis(); print()
    test_full_ai_rank_and_quality_bridge(); print()
    test_phase_bar_segments(); print()
    test_strength_calibration(); print()
    print("test_review 全部通过 ✅")
