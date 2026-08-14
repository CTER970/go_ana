"""test_training_analysis —— 训练后分析测试（纯逻辑，不依赖 tkinter）。

覆盖：
  1. 空输入（无训练数据 / 无原实战数据 / 空阶段）。
  2. 训练与原实战完全一致 → 全部「改善」类为 0、评分为基线。
  3. 训练全部更差 → 大量重复 / 新错误、改善为 0。
  4. 混合：部分改善、部分重复、部分新错误。
  5. 阶段过滤：仅统计 stage 匹配的手。
  6. 样本不足：训练有效手 < 4 → 标签「样本不足」。
  7. 提示 / 重试扣分；重复恶手封顶「基本合格」。
  8. 复习计划天数规则（§26.6）。
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
    MoveQualityResult,
    QUALITY_BEST, QUALITY_GOOD, QUALITY_NORMAL,
    QUALITY_INACCURACY, QUALITY_BLUNDER, QUALITY_UNKNOWN,
)
from training_analysis import (
    analyze_training,
    TrainingAnalysis, MoveComparison,
    compute_training_score,
    MATCH_EXACT_POSITION, MATCH_SAME_STEP, MATCH_STAGE_PATTERN, MATCH_NONE,
    _classify, _label_for_score,
    MIN_EFFECTIVE_MOVES, HINT_CAP_SCORE,
)


def check(name, cond, extra=""):
    print(("[CHECK] %-44s %s %s" % (name, "OK" if cond else "FAIL", extra)))
    if not cond:
        raise AssertionError(name)


def _res(move_no, color="B", played_move="Q16", quality=QUALITY_NORMAL,
         score_loss=2.0, winrate_drop=3.0, stage="middle",
         problem_tags=None, position_key=None, source_move_no=None):
    """快捷构造 MoveQualityResult（带训练对齐用的旁路属性）。"""
    r = MoveQualityResult(
        move_no=move_no, color=color, played_move=played_move,
        quality_key=quality, quality_label=quality,
        quality_score=70,
        score_loss=score_loss, winrate_drop=winrate_drop,
        stage=stage, problem_tags=list(problem_tags or []),
    )
    # 训练记录里的对齐字段挂在实例上（move_quality 不强制该字段）
    r.position_key = position_key
    r.source_move_no = source_move_no
    return r


# ===================== 1. 空输入 =====================
def test_empty_no_training():
    """无训练数据 → 样本不足，无错误统计。"""
    orig = [_res(10, quality=QUALITY_INACCURACY, score_loss=5.0),
            _res(12, quality=QUALITY_BLUNDER, score_loss=12.0)]
    a = analyze_training(orig, [], phase="middle")
    check("无训练数据 training_move_count=0", a.training_move_count == 0)
    check("effective_move_count=0", a.effective_move_count == 0)
    check("sample_insufficient=True", a.sample_insufficient is True)
    check("标签=样本不足", a.training_label == "样本不足")
    check("repeated_errors 空", len(a.repeated_errors) == 0)
    check("new_errors 空", len(a.new_errors) == 0)
    check("原实战统计仍计算", a.original_blunder_count == 1
          and a.original_inaccuracy_count == 1)


def test_empty_no_original():
    """无原实战数据 → 仍能评价训练，但不输出改善幅度。"""
    train = [_res(10, quality=QUALITY_GOOD, score_loss=0.8),
             _res(12, quality=QUALITY_GOOD, score_loss=1.0),
             _res(14, quality=QUALITY_NORMAL, score_loss=2.0),
             _res(16, quality=QUALITY_GOOD, score_loss=1.2)]
    a = analyze_training([], train, phase="middle")
    check("training_avg_score_loss 非空", a.training_avg_score_loss is not None)
    check("无原实战 → improvement_score_loss=None",
          a.improvement_score_loss is None)
    check("无原实战 → original_avg_score_loss=None",
          a.original_avg_score_loss is None)
    check("不应标 new_error（无原实战对齐）", len(a.new_errors) == 0)
    check("sample_insufficient=False（4 手）", a.sample_insufficient is False)


def test_empty_phase():
    """空阶段（phase 过滤后两手都没了）。"""
    orig = [_res(10, stage="opening")]
    train = [_res(10, stage="opening")]
    a = analyze_training(orig, train, phase="endgame")
    check("endgame 过滤后 original_move_count=0", a.original_move_count == 0)
    check("endgame 过滤后 training_move_count=0", a.training_move_count == 0)
    check("sample_insufficient=True", a.sample_insufficient is True)


# ===================== 2. 完全一致（全改善）=====================
def test_identical_full_improvement():
    """训练把所有原实战问题手都修正为最佳/好手 → 全改善。"""
    orig = [
        _res(10, position_key="p1", quality=QUALITY_INACCURACY, score_loss=5.0),
        _res(12, position_key="p2", quality=QUALITY_BLUNDER, score_loss=12.0),
        _res(14, position_key="p3", quality=QUALITY_INACCURACY, score_loss=4.0),
    ]
    train = [
        _res(10, position_key="p1", quality=QUALITY_BEST, score_loss=0.1),
        _res(12, position_key="p2", quality=QUALITY_GOOD, score_loss=0.8),
        _res(14, position_key="p3", quality=QUALITY_BEST, score_loss=0.2),
    ]
    a = analyze_training(orig, train, phase="middle")
    check("全部精确对齐", all(c.match_type == MATCH_EXACT_POSITION
          for c in a.comparisons))
    check("3 个改善", len(a.improved_moves) == 3)
    check("0 重复错误", len(a.repeated_errors) == 0)
    check("0 新错误", len(a.new_errors) == 0)
    check("training_blunder_count=0", a.training_blunder_count == 0)
    check("improvement_score_loss>0",
          a.improvement_score_loss is not None and a.improvement_score_loss > 0)
    check("分数较高（>=75 明显改善/优秀）", a.training_score >= 75,
          str(a.training_score))
    # 修正了原实战问题手 → 不会触发重复恶手封顶


# ===================== 3. 全部更差 =====================
def test_all_worse():
    """训练把所有位置都下成问题手 → 重复错误 + 新错误。"""
    orig = [
        _res(10, position_key="p1", quality=QUALITY_INACCURACY, score_loss=4.0),
        _res(12, position_key="p2", quality=QUALITY_GOOD, score_loss=1.0),
        _res(14, position_key="p3", quality=QUALITY_NORMAL, score_loss=2.0),
        _res(16, position_key="p4", quality=QUALITY_BEST, score_loss=0.5),
    ]
    train = [
        _res(10, position_key="p1", quality=QUALITY_BLUNDER, score_loss=12.0),
        _res(12, position_key="p2", quality=QUALITY_INACCURACY, score_loss=6.0),
        _res(14, position_key="p3", quality=QUALITY_BLUNDER, score_loss=10.0),
        _res(16, position_key="p4", quality=QUALITY_INACCURACY, score_loss=5.0),
    ]
    a = analyze_training(orig, train, phase="middle")
    # 原问题手训练仍问题 → 重复
    check("1 个重复错误（p1）", len(a.repeated_errors) == 1,
          str([(c.move_no, c.category) for c in a.comparisons]))
    # 原非问题手训练变问题 → 新错误
    check("3 个新错误（p2/p3/p4）", len(a.new_errors) == 3,
          str([(c.move_no, c.category) for c in a.comparisons]))
    check("0 改善", len(a.improved_moves) == 0)
    check("training_blunder_count=2", a.training_blunder_count == 2)
    check("improvement_score_loss<0（退步）",
          a.improvement_score_loss is not None and a.improvement_score_loss < 0)
    check("评分低（<=59 仍需复习/重练）", a.training_score <= 59,
          str(a.training_score))


# ===================== 4. 混合 =====================
def test_mixed():
    """混合：1 改善 + 1 重复 + 1 新错误 + 1 中性。"""
    orig = [
        _res(10, position_key="p1", quality=QUALITY_INACCURACY, score_loss=5.0,
             problem_tags=["overplay"]),
        _res(12, position_key="p2", quality=QUALITY_BLUNDER, score_loss=11.0,
             problem_tags=["overplay"]),
        _res(14, position_key="p3", quality=QUALITY_GOOD, score_loss=0.8),
        _res(16, position_key="p4", quality=QUALITY_NORMAL, score_loss=2.0),
    ]
    train = [
        # p1: 原不佳 → 训练好手（改善）
        _res(10, position_key="p1", quality=QUALITY_GOOD, score_loss=1.0),
        # p2: 原恶手 → 训练仍是恶手（重复错误）
        _res(12, position_key="p2", quality=QUALITY_BLUNDER, score_loss=12.0,
             problem_tags=["overplay"]),
        # p3: 原好手 → 训练不佳（新错误）
        _res(14, position_key="p3", quality=QUALITY_INACCURACY, score_loss=5.0),
        # p4: 原一般 → 训练一般（中性）
        _res(16, position_key="p4", quality=QUALITY_NORMAL, score_loss=1.5),
    ]
    a = analyze_training(orig, train, phase="middle")
    cats = {c.move_no: c.category for c in a.comparisons}
    check("p1=improved", cats.get(10) == "improved", str(cats))
    check("p2=repeated_error", cats.get(12) == "repeated_error", str(cats))
    check("p3=new_error", cats.get(14) == "new_error", str(cats))
    check("p4=neutral", cats.get(16) == "neutral", str(cats))
    check("improved_moves=1", len(a.improved_moves) == 1)
    check("repeated_errors=1", len(a.repeated_errors) == 1)
    check("new_errors=1", len(a.new_errors) == 1)
    # 改善幅度记录
    imp = [c for c in a.improved_moves if c.move_no == 10][0]
    check("p1 score_loss_improvement>0",
          imp.score_loss_improvement is not None and imp.score_loss_improvement > 0)
    # 问题标签变化
    check("problem_tag_changes 含 overplay",
          "overplay" in a.problem_tag_changes)
    o_over, t_over, delta_over = a.problem_tag_changes["overplay"]
    check("overplay 原实战出现 >=2", o_over >= 2, str((o_over, t_over)))


# ===================== 5. 阶段过滤 =====================
def test_phase_filtering():
    """phase 过滤：只统计 stage 匹配的手。"""
    orig = [
        _res(5, stage="opening", position_key="o1", quality=QUALITY_BLUNDER,
             score_loss=10.0),
        _res(80, stage="middle", position_key="m1", quality=QUALITY_INACCURACY,
             score_loss=4.0),
        _res(200, stage="endgame", position_key="e1", quality=QUALITY_BLUNDER,
             score_loss=8.0),
    ]
    train = [
        _res(5, stage="opening", position_key="o1", quality=QUALITY_GOOD,
             score_loss=1.0),
        _res(80, stage="middle", position_key="m1", quality=QUALITY_GOOD,
             score_loss=0.8),
        _res(200, stage="endgame", position_key="e1", quality=QUALITY_BLUNDER,
             score_loss=9.0),
    ]
    # 只看中盘
    a_mid = analyze_training(orig, train, phase="middle")
    check("中盘只 1 手原实战", a_mid.original_move_count == 1)
    check("中盘只 1 手训练", a_mid.training_move_count == 1)
    check("中盘 p80=improved",
          len(a_mid.improved_moves) == 1 and a_mid.improved_moves[0].move_no == 80)
    check("中盘不含官子手", all(c.move_no != 200 for c in a_mid.comparisons))

    # 只看官子
    a_end = analyze_training(orig, train, phase="endgame")
    check("官子 1 个重复恶手", len(a_end.repeated_errors) == 1
          and a_end.repeated_errors[0].move_no == 200)


# ===================== 6. 样本不足 =====================
def test_sample_insufficient():
    """有效训练手 < 4 → 标签「样本不足」。"""
    orig = [_res(10, quality=QUALITY_BLUNDER, score_loss=10.0)] * 5
    train = [_res(10, quality=QUALITY_BEST, score_loss=0.1),
             _res(12, quality=QUALITY_BEST, score_loss=0.2),
             _res(14, quality=QUALITY_BEST, score_loss=0.1)]
    a = analyze_training(orig, train, phase="middle")
    check("3 手 → sample_insufficient", a.sample_insufficient is True)
    check("标签=样本不足", a.training_label == "样本不足")


# ===================== 7. 提示 / 重试扣分 + 重复恶手封顶 =====================
def test_hint_and_retry_deduction():
    """提示与重试正确扣分。"""
    orig = [_res(10, position_key="p1", quality=QUALITY_GOOD, score_loss=1.0)
            for _ in range(4)]
    train = [_res(10 + i, position_key="p%d" % (i + 1),
                  quality=QUALITY_BEST, score_loss=0.1) for i in range(4)]
    a_no_hint = analyze_training(orig, train, phase="middle",
                                 hint_used_count=0, retry_count=0)
    a_hint = analyze_training(orig, train, phase="middle",
                              hint_used_count=3, retry_count=2)
    check("无提示分 >= 有提示分",
          a_no_hint.training_score >= a_hint.training_score,
          "%d vs %d" % (a_no_hint.training_score, a_hint.training_score))
    # 提示比例 3/4 = 75% > 50% → 封顶 85
    check("提示比例>50% → 封顶 85", a_hint.training_score <= HINT_CAP_SCORE,
          str(a_hint.training_score))
    # 提示计数回写
    check("hint_used_count 回写", a_hint.hint_used_count == 3)
    check("retry_count 回写", a_hint.retry_count == 2)


def test_repeated_blunder_caps_label():
    """存在重复恶手 → 最高标签不超过「基本合格」（score<=74）。"""
    orig = [_res(10, position_key="p1", quality=QUALITY_BLUNDER, score_loss=11.0),
            _res(12, position_key="p2", quality=QUALITY_BLUNDER, score_loss=11.0),
            _res(14, position_key="p3", quality=QUALITY_GOOD, score_loss=1.0),
            _res(16, position_key="p4", quality=QUALITY_GOOD, score_loss=1.0)]
    train = [_res(10, position_key="p1", quality=QUALITY_BLUNDER, score_loss=12.0),
             _res(12, position_key="p2", quality=QUALITY_GOOD, score_loss=0.9),
             _res(14, position_key="p3", quality=QUALITY_GOOD, score_loss=1.0),
             _res(16, position_key="p4", quality=QUALITY_GOOD, score_loss=1.0)]
    a = analyze_training(orig, train, phase="middle")
    check("1 个重复恶手（p1）",
          sum(1 for c in a.repeated_errors
              if c.original_quality == QUALITY_BLUNDER
              and c.training_quality == QUALITY_BLUNDER) == 1)
    check("重复恶手封顶 score<=74", a.training_score <= 74,
          str(a.training_score))
    check("标签<=基本合格",
          _label_for_score(a.training_score) in ("基本合格", "仍需复习", "建议重练")
          or a.training_score <= 74)


# ===================== 8. 复习计划 + 推荐复盘位置 + 文案 =====================
def test_review_plan_rules():
    """§26.6 复习天数规则。"""
    # 重复恶手 → 1 天
    orig = [_res(10, position_key="p1", quality=QUALITY_BLUNDER, score_loss=11.0)]
    train = [_res(10, position_key="p1", quality=QUALITY_BLUNDER, score_loss=12.0),
             _res(11, position_key="p2", quality=QUALITY_GOOD, score_loss=1.0),
             _res(12, position_key="p3", quality=QUALITY_GOOD, score_loss=1.0),
             _res(13, position_key="p4", quality=QUALITY_GOOD, score_loss=1.0)]
    a = analyze_training(orig, train, phase="middle")
    check("重复恶手 → 1 天", a.suggested_review_after_days == 1,
          str(a.suggested_review_after_days))

    # 高分无重复 → 14 天
    orig2 = [_res(10, position_key="p1", quality=QUALITY_INACCURACY, score_loss=4.0)]
    train2 = [_res(10, position_key="p1", quality=QUALITY_BEST, score_loss=0.1),
              _res(11, position_key="p2", quality=QUALITY_BEST, score_loss=0.1),
              _res(12, position_key="p3", quality=QUALITY_BEST, score_loss=0.1),
              _res(13, position_key="p4", quality=QUALITY_BEST, score_loss=0.1)]
    a2 = analyze_training(orig2, train2, phase="middle")
    check("高分无重复 → 14 天", a2.suggested_review_after_days == 14,
          str(a2.suggested_review_after_days))


def test_recommendations_and_review_positions():
    """推荐复盘位置按目损降序；文案非空。"""
    orig = [_res(10, position_key="p1", quality=QUALITY_NORMAL, score_loss=2.0),
            _res(12, position_key="p2", quality=QUALITY_NORMAL, score_loss=2.0),
            _res(14, position_key="p3", quality=QUALITY_NORMAL, score_loss=2.0),
            _res(16, position_key="p4", quality=QUALITY_NORMAL, score_loss=2.0)]
    train = [_res(10, position_key="p1", quality=QUALITY_INACCURACY, score_loss=5.0),
             _res(12, position_key="p2", quality=QUALITY_BLUNDER, score_loss=12.0),
             _res(14, position_key="p3", quality=QUALITY_GOOD, score_loss=0.8),
             _res(16, position_key="p4", quality=QUALITY_INACCURACY, score_loss=6.0)]
    a = analyze_training(orig, train, phase="middle")
    check("推荐复盘位置只含训练问题手",
          all(c.training_quality in (QUALITY_INACCURACY, QUALITY_BLUNDER)
              for c in a.recommended_review_positions))
    check("推荐位置按目损降序（12 在 10/16 之前）",
          [c.move_no for c in a.recommended_review_positions][:1] == [12],
          str([c.move_no for c in a.recommended_review_positions]))
    check("review_recommendations 非空", len(a.review_recommendations) >= 1)
    check("文案含评分", any("评分" in r for r in a.review_recommendations),
          str(a.review_recommendations))


# ===================== 9. 同步对齐（无 position_key）=====================
def test_same_step_alignment():
    """无 position_key 时按同色次序对齐（same_step）。"""
    orig = [_res(10, quality=QUALITY_INACCURACY, score_loss=5.0),
            _res(12, quality=QUALITY_GOOD, score_loss=0.8)]
    train = [_res(20, quality=QUALITY_GOOD, score_loss=0.9),
             _res(22, quality=QUALITY_INACCURACY, score_loss=6.0)]
    a = analyze_training(orig, train, phase="middle")
    check("无 key → same_step 对齐",
          all(c.match_type == MATCH_SAME_STEP for c in a.comparisons))
    # 第 1 手：原不佳 → 训练好手（改善）
    # 第 2 手：原好手 → 训练不佳（新错误）
    check("1 改善", len(a.improved_moves) == 1)
    check("1 新错误", len(a.new_errors) == 1)


# ===================== 10. 单手分类单元 =====================
def test_classify_unit():
    """_classify 各分支。"""
    o_inc = _res(1, quality=QUALITY_INACCURACY)
    o_blund = _res(1, quality=QUALITY_BLUNDER)
    o_good = _res(1, quality=QUALITY_GOOD)
    t_good = _res(1, quality=QUALITY_GOOD)
    t_inc = _res(1, quality=QUALITY_INACCURACY)
    t_blund = _res(1, quality=QUALITY_BLUNDER)
    t_norm = _res(1, quality=QUALITY_NORMAL)

    check("原不佳→好手 = improved",
          _classify(o_inc, t_good, MATCH_EXACT_POSITION) == "improved")
    check("原恶手→一般 = improved",
          _classify(o_blund, t_norm, MATCH_EXACT_POSITION) == "improved")
    check("原不佳→不佳 = repeated_error",
          _classify(o_inc, t_inc, MATCH_EXACT_POSITION) == "repeated_error")
    check("原恶手→恶手 = repeated_error",
          _classify(o_blund, t_blund, MATCH_EXACT_POSITION) == "repeated_error")
    check("原好手→不佳（exact）= new_error",
          _classify(o_good, t_inc, MATCH_EXACT_POSITION) == "new_error")
    check("原好手→不佳（stage_pattern）= neutral（不轻标新错误）",
          _classify(o_good, t_inc, MATCH_STAGE_PATTERN) == "neutral")
    check("原好手→好手 = neutral",
          _classify(o_good, t_good, MATCH_EXACT_POSITION) == "neutral")
    check("无原实战 → neutral",
          _classify(None, t_blund, MATCH_NONE) == "neutral")


def test_compute_training_score_formula():
    """评分公式（§11.4）。"""
    # 全部为 0 损失、无提示 → 100
    s = compute_training_score(0.0, 0, 0, 0.0, hint_used_count=0, retry_count=0)
    check("零损失 → 100", s == 100, str(s))
    # 平均目损 5 + 1 恶手 + 1 不佳 + 改善 0
    s2 = compute_training_score(5.0, 1, 1, 0.0, hint_used_count=1, retry_count=1)
    # 100 - 40 - 15 - 6 - 3 - 2 + 0 = 34
    check("公式扣分 → 34", s2 == 34, str(s2))
    # 改善加分
    s3 = compute_training_score(1.0, 0, 0, 3.0)
    # 100 - 8 + 15 = 107 → 封顶 100
    check("改善加分封顶 100", s3 == 100, str(s3))
    # 下限 0
    s4 = compute_training_score(50.0, 10, 10, 0.0)
    check("下限 0", s4 == 0, str(s4))


def test_branch_pattern_and_roundtrip():
    orig = [
        _res(10 + i, position_key="original-%d" % i,
             quality=QUALITY_INACCURACY, score_loss=4.0,
             problem_tags=["overplay"])
        for i in range(4)
    ]
    train = [
        _res(20 + i, position_key="branch-%d" % i,
             quality=QUALITY_INACCURACY, score_loss=4.5,
             problem_tags=["overplay"])
        for i in range(4)
    ]
    result = analyze_training(orig, train, phase="middle")
    check("分支不同标记 stage_pattern",
          all(item.match_type == MATCH_STAGE_PATTERN
              for item in result.comparisons))
    check("相同问题标签可识别模式重复",
          len(result.repeated_errors) == 4)
    restored = TrainingAnalysis.from_dict(result.to_dict())
    check("TrainingAnalysis 往返",
          restored.to_dict() == result.to_dict())


def test_original_move_no_passthrough():
    """MoveComparison 携带原实战手数 original_move_no（错题本回写/复盘定位用）。"""
    orig = [_res(10, color="B", quality=QUALITY_BLUNDER, score_loss=8.0,
                source_move_no=10)]
    train = [_res(20, color="B", played_move="Q16", quality=QUALITY_GOOD,
                 score_loss=1.0, source_move_no=10)]
    a = analyze_training(orig, train, phase="middle")
    comp = a.comparisons[0]
    check("comparison 带 original_move_no(=原实战 10)",
          comp.original_move_no == 10, str(comp.original_move_no))
    check("原恶手→训练好手 判为 improved", comp.category == "improved", comp.category)
    rt = MoveComparison.from_dict(comp.to_dict())
    check("original_move_no to_dict/from_dict 往返",
          rt.original_move_no == 10)
    a2 = analyze_training([], train, phase="middle")
    check("无原实战对齐时 original_move_no=None",
          a2.comparisons[0].original_move_no is None)


if __name__ == "__main__":
    print("=" * 60)
    print(" 训练后分析（training_analysis）测试")
    print("=" * 60)
    test_empty_no_training(); print()
    test_empty_no_original(); print()
    test_empty_phase(); print()
    test_identical_full_improvement(); print()
    test_all_worse(); print()
    test_mixed(); print()
    test_phase_filtering(); print()
    test_sample_insufficient(); print()
    test_hint_and_retry_deduction(); print()
    test_repeated_blunder_caps_label(); print()
    test_review_plan_rules(); print()
    test_recommendations_and_review_positions(); print()
    test_same_step_alignment(); print()
    test_classify_unit(); print()
    test_compute_training_score_formula(); print()
    test_branch_pattern_and_roundtrip(); print()
    test_original_move_no_passthrough(); print()
    print("test_training_analysis 全部通过 ✅")
