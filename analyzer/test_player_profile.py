"""test_player_profile —— 长期个人画像纯逻辑测试（不依赖 tkinter）。

覆盖 §17.2 与 §31.1：
  1. 多盘摘要可聚合
  2. 黑白统计分离正确
  3. 阶段统计正确
  4. Top 问题标签排序正确
  5. 最近趋势计算正确
  6. 空数据不崩溃
  + 聚合按有效手数加权（不是平均数的平均数）
  + 身份未知 / side 过滤
  + 少于最小样本不下强结论
  + 建议文案带证据、不输出段位
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from move_quality import (
    MoveQualityInput, MoveQualityResult, evaluate_move,
    QUALITY_BEST, QUALITY_GOOD, QUALITY_NORMAL, QUALITY_INACCURACY,
    QUALITY_BLUNDER, QUALITY_UNKNOWN,
)
from player_profile import (
    GameProfileSummary, GameBenchmark, ProfileStats, PlayerProfile,
    build_game_profile_summary, build_profile, compare_game_to_baseline, profile_trend,
    analysis_signatures_compatible, prioritize_weaknesses, weakness_trends,
    phase_weaknesses, build_profile_insights,
    is_primary_sample,
    SIDE_BLACK, SIDE_WHITE, SIDE_BOTH, SIDE_UNKNOWN,
    PHASES,
    MIN_MOVES_PHASE_CONCLUSION, MIN_TAG_COUNT_FOR_ADVICE,
    RECENT_TREND_WINDOW, MIN_GAMES_STRONG_TREND,
    TREND_LOSS_DELTA, TREND_BLUNDER_DELTA, TREND_TOP3_DELTA,
)


def check(name, cond, extra=""):
    print(("[CHECK] %-40s %s %s" % (name, "OK" if cond else "FAIL", extra)))
    if not cond:
        raise AssertionError(name)


def _result(**kw) -> MoveQualityResult:
    """快捷构造一个有效（high 置信 + meaningful）的 MoveQualityResult。"""
    defaults = dict(
        move_no=1, color="B", played_move="Q16", best_move="Q16",
        quality_key=QUALITY_GOOD, quality_label="好手", quality_score=85,
        score_loss=1.0, winrate_drop=1.0, ai_rank=0,
        top1_match=True, top3_match=True, top5_match=True,
        stage="opening", is_meaningful_position=True,
        confidence="high", problem_tags=[], reasons=[],
    )
    defaults.update(kw)
    return MoveQualityResult(**defaults)


def _make_game(game_id, color="B", stage_losses=None, blunders=0, inaccuracies=0,
               tags=None, side=None):
    """构造一盘棋的 MoveQualityResult 列表。

    stage_losses: {stage: [loss, loss, ...]} 控制各阶段有效手目损。
    其余手额外补成好手以保证样本量。
    """
    tags = tags or []
    results = []
    move_no = 1
    for stage, losses in (stage_losses or {}).items():
        for loss in losses:
            qk = QUALITY_GOOD
            qscore = 85
            if loss >= 7.0:
                qk = QUALITY_BLUNDER
                qscore = 20
            elif loss >= 3.0:
                qk = QUALITY_INACCURACY
                qscore = 50
            elif loss >= 1.5:
                qk = QUALITY_NORMAL
                qscore = 70
            results.append(_result(
                move_no=move_no, color=color, stage=stage,
                score_loss=loss, winrate_drop=min(loss * 1.5, 20.0),
                quality_key=qk, quality_score=qscore,
                problem_tags=list(tags) if qk in (QUALITY_BLUNDER, QUALITY_INACCURACY) else [],
            ))
            move_no += 1
    # 补恶手 / 不佳手（计入指定阶段）
    for _ in range(blunders):
        results.append(_result(move_no=move_no, color=color, stage="middle",
                               score_loss=10.0, winrate_drop=18.0,
                               quality_key=QUALITY_BLUNDER, quality_score=15,
                               problem_tags=list(tags)))
        move_no += 1
    for _ in range(inaccuracies):
        results.append(_result(move_no=move_no, color=color, stage="endgame",
                               score_loss=4.0, winrate_drop=6.0,
                               quality_key=QUALITY_INACCURACY, quality_score=45,
                               problem_tags=list(tags)))
        move_no += 1
    s = build_game_profile_summary(results, game_id=game_id, profile_side=side or color)
    return {"summary": s, "quality_results": results, "game_id": game_id}


# ===================== 测试 1：空数据不崩溃 =====================
def test_empty():
    """无棋局 → 默认画像，不抛异常。"""
    p = build_profile([])
    check("空数据 games_count==0", p.games_count == 0, str(p.games_count))
    check("空数据 evaluated_moves==0", p.evaluated_moves_count == 0)
    check("空 overall.moves==0", p.overall.moves == 0)
    check("空 avg_score_loss is None", p.overall.avg_score_loss is None)
    check("空 quality_distribution 有全部 key",
          set(p.quality_distribution.keys()) >=
          {QUALITY_BEST, QUALITY_GOOD, QUALITY_NORMAL, QUALITY_INACCURACY,
           QUALITY_BLUNDER, QUALITY_UNKNOWN})
    check("空趋势 insufficient", p.recent_trend.direction == "insufficient",
          p.recent_trend.direction)
    check("空画像可 to_dict", isinstance(p.to_dict(), dict))


# ===================== 测试 2：单局 =====================
def test_single_game():
    """单盘摘要生成正确，avg_score_loss 按有效手加权。"""
    results = [
        _result(move_no=1, color="B", stage="opening", score_loss=0.5, quality_key=QUALITY_BEST),
        _result(move_no=2, color="B", stage="opening", score_loss=2.0, quality_key=QUALITY_NORMAL),
        _result(move_no=3, color="W", stage="middle", score_loss=4.0, quality_key=QUALITY_INACCURACY),
    ]
    s = build_game_profile_summary(results, game_id="g1", profile_side="both")
    check("evaluated_moves==3", s.evaluated_moves == 3, str(s.evaluated_moves))
    # 加权：(0.5 + 2.0 + 4.0) / 3
    expected = (0.5 + 2.0 + 4.0) / 3
    check("单局加权 avg_score_loss", s.avg_score_loss is not None
          and abs(s.avg_score_loss - expected) < 1e-6, str(s.avg_score_loss))
    check("quality_counts 计数", s.quality_counts[QUALITY_BEST] == 1
          and s.quality_counts[QUALITY_NORMAL] == 1
          and s.quality_counts[QUALITY_INACCURACY] == 1)
    # 阶段统计存在
    check("stage_stats 含三阶段", all(p in s.stage_stats for p in PHASES))
    check("opening 阶段 moves==2", s.stage_stats["opening"]["moves"] == 2)
    # 聚合到画像
    p = build_profile([{"summary": s}], user_side="both")
    check("单盘画像 games_count==1", p.games_count == 1)
    check("单盘 overall.avg 与单局一致",
          p.overall.avg_score_loss is not None
          and abs(p.overall.avg_score_loss - expected) < 1e-6,
          str(p.overall.avg_score_loss))


# ===================== 测试 3：多盘聚合（按有效手数加权，非均值平均）=====================
def test_multi_game_weighted():
    """两盘有效手数不同 → 加权平均 ≠ 各盘均值的平均（§27.2）。"""
    # 盘 A：10 手，每手 1 目 → mean 1.0
    gA = _make_game("A", stage_losses={"opening": [1.0] * 10})
    # 盘 B：2 手，每手 5 目 → mean 5.0
    gB = _make_game("B", stage_losses={"opening": [5.0, 5.0]})
    p = build_profile([gA, gB], user_side="both")
    # 加权正确：(10*1 + 2*5) / 12 = 20/12
    weighted = (10 * 1.0 + 2 * 5.0) / 12
    check("多盘加权 avg_score_loss",
          p.overall.avg_score_loss is not None
          and abs(p.overall.avg_score_loss - weighted) < 1e-6,
          "%s vs %s" % (p.overall.avg_score_loss, weighted))
    # 错误方式（均值平均）会是 (1.0 + 5.0)/2 = 3.0
    wrong = (1.0 + 5.0) / 2
    check("非平均数的平均数", abs(p.overall.avg_score_loss - wrong) > 0.01)
    check("overall.moves==12", p.overall.moves == 12, str(p.overall.moves))


# ===================== 测试 4：黑白统计分离 =====================
def test_color_separation():
    """user_side=B 只聚合黑棋盘；black/white 维度正确分离。"""
    games = [
        _make_game("g1", color="B", stage_losses={"opening": [1.0, 1.0]}, side="B"),
        _make_game("g2", color="W", stage_losses={"opening": [3.0, 3.0]}, side="W"),
    ]
    # only B
    pB = build_profile(games, user_side="B")
    check("user_side=B 只含 1 盘", pB.games_count == 1, str(pB.games_count))
    check("B 画像 black.moves==2", pB.black.moves == 2, str(pB.black.moves))
    check("B 画像 white.moves==0", pB.white.moves == 0)
    # both
    pBoth = build_profile(games, user_side="both")
    check("both 含 2 盘", pBoth.games_count == 2)
    check("both black.moves==2", pBoth.black.moves == 2)
    check("both white.moves==2", pBoth.white.moves == 2)
    # black avg=1.0, white avg=3.0
    check("black avg==1.0", abs(pBoth.black.avg_score_loss - 1.0) < 1e-6,
          str(pBoth.black.avg_score_loss))
    check("white avg==3.0", abs(pBoth.white.avg_score_loss - 3.0) < 1e-6,
          str(pBoth.white.avg_score_loss))


# ===================== 测试 5：阶段弱点 + 阶段统计 =====================
def test_phase_breakdown():
    """中盘目损明显高于布局/官子 → phase_weaknesses 排第一。"""
    # 每阶段给足 ≥ MIN_MOVES_PHASE_CONCLUSION 手
    game = _make_game("g1", stage_losses={
        "opening": [0.5] * MIN_MOVES_PHASE_CONCLUSION,    # 平均 0.5
        "middle": [6.0] * MIN_MOVES_PHASE_CONCLUSION,     # 平均 6.0（最弱）
        "endgame": [1.0] * MIN_MOVES_PHASE_CONCLUSION,    # 平均 1.0
    })
    p = build_profile([game], user_side="B")
    check("三阶段 moves 均 ≥ 阈值",
          p.opening.moves >= MIN_MOVES_PHASE_CONCLUSION
          and p.middle.moves >= MIN_MOVES_PHASE_CONCLUSION
          and p.endgame.moves >= MIN_MOVES_PHASE_CONCLUSION)
    check("middle avg > opening avg",
          p.middle.avg_score_loss > p.opening.avg_score_loss)
    weak = phase_weaknesses(p)
    check("phase_weaknesses 非空", len(weak) > 0)
    check("最弱阶段是 middle", weak[0][0] == "middle", weak[0][0])
    # 文案中提到中盘
    check("弱点文案提到中盘",
          any("中盘" in w for w in p.weaknesses), str(p.weaknesses))


# ===================== 测试 6：阶段样本不足不下结论 =====================
def test_insufficient_phase():
    """阶段 < MIN_MOVES_PHASE_CONCLUSION 手 → 不进入 phase_weaknesses。"""
    game = _make_game("g1", stage_losses={
        "opening": [1.0],   # 仅 1 手，不足
        "middle": [5.0] * MIN_MOVES_PHASE_CONCLUSION,
    })
    p = build_profile([game], user_side="B")
    weak = phase_weaknesses(p)
    phases_in = [w[0] for w in weak]
    check("样本不足的 opening 不进入结论", "opening" not in phases_in, str(phases_in))
    check("样本足够的 middle 进入结论", "middle" in phases_in)


# ===================== 测试 7：Top 问题标签排序 =====================
def test_problem_tag_ranking():
    """高频问题标签按次数降序进入建议，且 ≥ MIN_TAG_COUNT_FOR_ADVICE 次。"""
    # 制造 4 次 overplay 标签的恶手 + 1 次 slack（不够门槛）
    games = []
    for i in range(4):
        games.append(_make_game("g%d" % i, color="B",
                                stage_losses={"middle": [10.0]}, tags=["overplay"]))
    games.append(_make_game("gX", color="B",
                            stage_losses={"middle": [2.0]}, tags=["slack_move"]))
    p = build_profile(games, user_side="B")
    check("overplay 累计 4 次",
          p.problem_tag_distribution.get("overplay", 0) == 4,
          str(p.problem_tag_distribution))
    # 建议中应包含 overplay，不含 slack（次数不足）
    advice_text = "；".join(p.recommendations)
    check("建议含 overplay", "过分" in advice_text or "overplay" in advice_text,
          advice_text)
    check("slack 次数 < 门槛",
          p.problem_tag_distribution.get("slack_move", 0) < MIN_TAG_COUNT_FOR_ADVICE)


# ===================== 测试 8：趋势——进步 =====================
def test_trend_improving():
    """最近 5 盘目损明显低于基线 → improving。"""
    # 前 10 盘差（每手 4 目），最近 5 盘好（每手 0.5 目）
    games = []
    for i in range(10):
        games.append(_make_game("old%d" % i, color="B",
                                stage_losses={"opening": [4.0, 4.0, 4.0]}))
    for i in range(5):
        games.append(_make_game("new%d" % i, color="B",
                                stage_losses={"opening": [0.5, 0.5, 0.5]}))
    trend = profile_trend([g["summary"] for g in games])
    check("趋势 improving", trend.direction == "improving", trend.direction)
    check("趋势有证据", len(trend.evidence) > 0)
    check("recent_games==5", trend.recent_games == RECENT_TREND_WINDOW,
          str(trend.recent_games))
    check("recent_avg_loss < baseline",
          trend.recent_avg_loss < trend.baseline_avg_loss)


# ===================== 测试 9：趋势——退步 / 稳定 / 样本不足 =====================
def test_trend_declining_and_stable():
    # 退步
    games = []
    for i in range(10):
        games.append(_make_game("old%d" % i, color="B",
                                stage_losses={"opening": [0.5, 0.5]}))
    for i in range(5):
        games.append(_make_game("new%d" % i, color="B",
                                stage_losses={"opening": [5.0, 5.0]}))
    trend = profile_trend([g["summary"] for g in games])
    check("趋势 declining", trend.direction == "declining", trend.direction)

    # 稳定（目损几乎不变）
    games2 = []
    for i in range(15):
        games2.append(_make_game("s%d" % i, color="B",
                                 stage_losses={"opening": [2.0, 2.0]}))
    trend2 = profile_trend([g["summary"] for g in games2])
    check("趋势 stable", trend2.direction == "stable", trend2.direction)

    # 样本不足（< 10 盘）
    few = [_make_game("f%d" % i, color="B", stage_losses={"opening": [1.0]})["summary"]
           for i in range(7)]
    trend3 = profile_trend(few)
    check("趋势 insufficient (7<10)", trend3.direction == "insufficient",
          trend3.direction)

    # 极少（< 5 盘）
    trend4 = profile_trend([_make_game("x", color="B",
                                       stage_losses={"opening": [1.0]})["summary"]])
    check("趋势 insufficient (1<5)", trend4.direction == "insufficient")


# ===================== 测试 10：主要样本筛选（胜负已定 / unknown 不计入）=====================
def test_primary_sample_filter():
    """confidence=unknown 或 胜负已定 的手不计入主要样本。"""
    good = _result(move_no=1, color="B", stage="opening", score_loss=1.0)
    settled = _result(move_no=2, color="B", stage="endgame", score_loss=20.0,
                      is_meaningful_position=False, confidence="high",
                      quality_key=QUALITY_BLUNDER)
    unknown = _result(move_no=3, color="B", stage="middle",
                      confidence="unknown", score_loss=None,
                      quality_key=QUALITY_UNKNOWN)
    check("good 是主要样本", is_primary_sample(good))
    check("胜负已定非主要样本", not is_primary_sample(settled))
    check("unknown 非主要样本", not is_primary_sample(unknown))

    s = build_game_profile_summary([good, settled, unknown], game_id="g1")
    check("evaluated_moves 只算 1（good）", s.evaluated_moves == 1, str(s.evaluated_moves))
    check("avg_score_loss 只含 good", abs(s.avg_score_loss - 1.0) < 1e-6,
          str(s.avg_score_loss))
    check("settled_moves 计 1", s.settled_moves == 1, str(s.settled_moves))
    # total_moves 仍含全部
    check("total_moves==3", s.total_moves == 3)


# ===================== 测试 11：建议文案带证据、不输出段位 =====================
def test_recommendations_have_evidence_no_rank():
    """建议条目包含样本数/数值证据，且不含"段/级"字样。"""
    games = [_make_game("g%d" % i, color="B",
                        stage_losses={"opening": [3.0] * MIN_MOVES_PHASE_CONCLUSION},
                        tags=["overplay"])
             for i in range(4)]
    p = build_profile(games, user_side="B")
    text = "；".join(p.recommendations) + "；".join(p.weaknesses) + "；".join(p.strengths)
    # §27.5：不输出虚构段位。"段"单独出现是合法的（如"阶段"），
    # 但不应出现"X段/X级表现/棋力/级位"这类段位断言。
    rank_phrases = ["级表现", "段表现", "棋力", "级位", "段位", "业余"]
    check("文案不含段位断言", not any(w in text for w in rank_phrases), text)
    # 至少有一条提到数值或样本
    has_evidence = any(any(c.isdigit() for c in line) for line in p.recommendations)
    check("建议含数值证据", has_evidence, str(p.recommendations))


# ===================== 测试 12：最近窗口与基线不重叠 + to_dict 往返 =====================
def test_trend_no_overlap_and_roundtrip():
    """最近 5 盘与基线窗口不重叠；to_dict 结构完整。"""
    games = []
    for i in range(15):
        games.append(_make_game("g%d" % i, color="B",
                                stage_losses={"opening": [2.0, 2.0]}))
    p = build_profile(games, user_side="B")
    t = p.recent_trend
    check("recent + baseline ≤ 总盘数",
          t.recent_games + t.baseline_games <= p.games_count,
          "%d + %d vs %d" % (t.recent_games, t.baseline_games, p.games_count))
    # to_dict 完整
    d = p.to_dict()
    for key in ("overall", "black", "white", "opening", "middle", "endgame",
                "recent_trend", "trend_points", "quality_distribution",
                "problem_tag_distribution", "strengths", "weaknesses",
                "recommendations"):
        check("to_dict 含 %s" % key, key in d, key)
    check("trend_points 数量 == games_count",
          len(p.trend_points) == p.games_count, str(len(p.trend_points)))


# ===================== 测试 13：window_games 截断 =====================
def test_window_games():
    games = [_make_game("g%d" % i, color="B", stage_losses={"opening": [1.0]})
             for i in range(10)]
    p = build_profile(games, user_side="B", window_games=3)
    check("window_games=3 → 只 3 盘", p.games_count == 3, str(p.games_count))


def test_summary_roundtrip_and_signature_isolation():
    s1 = _make_game(
        "g1", color="B", stage_losses={"opening": [1.0, 2.0]})["summary"]
    s1.analysis_signature = {"model": "m1", "visits": 200}
    restored = GameProfileSummary.from_dict(s1.to_dict())
    check("GameProfileSummary 往返",
          restored.to_dict() == s1.to_dict())

    s2 = _make_game(
        "g2", color="B", stage_losses={"opening": [9.0, 9.0]})["summary"]
    s2.analysis_signature = {"model": "m2", "visits": 200}
    profile = build_profile([s1, s2], user_side="B")
    check("不同分析签名不静默混合", profile.games_count == 1, str(profile.games_count))
    check("记录排除的不兼容棋局", profile.excluded_incompatible_games == 1)
    restored_profile = PlayerProfile.from_dict(profile.to_dict())
    check("PlayerProfile 嵌套往返",
          restored_profile.overall.moves == profile.overall.moves)


def test_top_problems_follow_profile_side():
    """先按画像方过滤再取 Top N，避免对手恶手挤掉用户错题。"""
    results = []
    for i in range(1, 8):
        results.append(_result(
            move_no=i * 2 - 1, color="B", score_loss=float(10 - i),
            quality_key=QUALITY_BLUNDER, played_move="D4", best_move="Q16"))
        results.append(_result(
            move_no=i * 2, color="W", score_loss=float(20 - i),
            quality_key=QUALITY_BLUNDER, played_move="Q4", best_move="D16"))
    summary = build_game_profile_summary(
        results, game_id="side-filter", profile_side="B", top_problem_limit=5)
    check("Top5 先按画像方过滤",
          len(summary.top_problem_moves) == 5
          and all(item["color"] == "B" for item in summary.top_problem_moves))
    check("单局平均值只统计画像方",
          summary.evaluated_moves == 7
          and summary.color_stats["W"]["moves"] == 0,
          "%d / %s" % (summary.evaluated_moves, summary.color_stats["W"]))


def test_game_benchmark():
    prior = [
        _make_game("p%d" % i, color="B",
                   stage_losses={"opening": [4.0, 4.0], "middle": [5.0, 5.0]})["summary"]
        for i in range(3)
    ]
    current = _make_game(
        "current", color="B",
        stage_losses={"opening": [2.0, 2.0], "middle": [3.0, 3.0]})["summary"]
    result = compare_game_to_baseline(current, prior)
    check("单局基线判定更好", result.status == "better", result.to_dict())
    check("单局基线置信度中", result.confidence == "medium")
    check("单局基线按手数加权",
          abs(result.baseline_avg_loss - 4.5) < 1e-9
          and abs(result.current_avg_loss - 2.5) < 1e-9)
    check("阶段基线包含布局/中盘",
          result.stage_comparisons["opening"]["loss_improvement"] == 2.0
          and result.stage_comparisons["middle"]["loss_improvement"] == 2.0)
    check("GameBenchmark 往返",
          GameBenchmark.from_dict(result.to_dict()).to_dict() == result.to_dict())

    current.analysis_signature = {"model": "new"}
    incompatible = compare_game_to_baseline(current, prior)
    check("不同分析口径不进入基线",
          incompatible.status == "insufficient" and incompatible.prior_games == 0)
    check("实际 visits 小幅波动可比较",
          analysis_signatures_compatible(
              {"model": "m", "rules": "chinese", "komi": 7.5, "visits": 120,
               "boardSize": 19, "quality_version": 1},
              {"model": "m", "rules": "chinese", "komi": 7.5, "visits": 124,
               "board_size": 19, "qualityVersion": 1}))
    check("visits 档位差异过大不可比较",
          not analysis_signatures_compatible(
              {"model": "m", "rules": "chinese", "komi": 7.5, "visits": 100},
              {"model": "m", "rules": "chinese", "komi": 7.5, "visits": 800}))


def test_weakness_priorities():
    profile = PlayerProfile(
        problem_tag_distribution={"opening_direction": 5, "overplay": 3})
    priorities = prioritize_weaknesses(profile, [
        {"active": True, "mastered": False, "isDue": True,
         "problemTags": ["overplay"], "scoreLoss": 10.0},
        {"active": True, "mastered": False, "isDue": True,
         "problemTags": ["overplay"], "scoreLoss": 8.0},
        {"active": True, "mastered": False, "isDue": False,
         "problemTags": ["opening_direction"], "scoreLoss": 4.0},
    ])
    check("到期错题进入弱点优先级",
          priorities[0]["tag"] == "overplay"
          and priorities[0]["due_mistakes"] == 2,
          str(priorities))
    check("弱点优先级给出证据",
          "跨局出现 3 次" in priorities[0]["reason"]
          and "平均目损 9.0" in priorities[0]["reason"])


def test_weakness_trends():
    games = []
    for i in range(4):
        games.append(_make_game(
            "old-%d" % i, color="B", blunders=2, tags=["overplay"]))
    for i in range(4):
        games.append(_make_game(
            "new-%d" % i, color="B",
            stage_losses={"opening": [1.0, 1.0]}))
    trends = weakness_trends(games, tags=["overplay"])
    item = trends["overplay"]
    check("弱点趋势使用不重叠前后窗口",
          item["recent"]["games"] == 4 and item["baseline"]["games"] == 4,
          str(item))
    check("弱点频率下降判定改善",
          item["status"] == "improving"
          and item["delta_per_100"] < 0,
          str(item))
    check("弱点趋势给出每百手证据",
          "每百手" in item["reason"] and "下降" in item["reason"],
          item["reason"])

    short = weakness_trends(games[:2], tags=["overplay"])
    check("前后不足两盘不制造趋势",
          short["overplay"]["status"] == "insufficient",
          str(short["overplay"]))

    profile = PlayerProfile(problem_tag_distribution={"overplay": 8})
    priorities = prioritize_weaknesses(
        profile, [], trends=trends)
    check("训练优先级带入近期方向证据",
          priorities[0]["trend"]["status"] == "improving"
          and "较此前下降" in priorities[0]["reason"],
          str(priorities[0]))


if __name__ == "__main__":
    print("=" * 60)
    print(" 长期个人画像（player_profile）测试")
    print("=" * 60)
    test_empty(); print()
    test_single_game(); print()
    test_multi_game_weighted(); print()
    test_color_separation(); print()
    test_phase_breakdown(); print()
    test_insufficient_phase(); print()
    test_problem_tag_ranking(); print()
    test_trend_improving(); print()
    test_trend_declining_and_stable(); print()
    test_primary_sample_filter(); print()
    test_recommendations_have_evidence_no_rank(); print()
    test_trend_no_overlap_and_roundtrip(); print()
    test_window_games(); print()
    test_summary_roundtrip_and_signature_isolation(); print()
    test_top_problems_follow_profile_side(); print()
    test_game_benchmark(); print()
    test_weakness_priorities(); print()
    test_weakness_trends(); print()
    print("test_player_profile 全部通过 ✅")
