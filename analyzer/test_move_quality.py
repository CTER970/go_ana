"""test_move_quality —— 精细手段评价引擎测试（纯逻辑，不依赖 tkinter）。"""
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
    MoveQualityInput, MoveQualityResult, evaluate_move, compute_quality_score,
    score_to_quality, is_meaningful_position, build_quality_reasons,
    QUALITY_BEST, QUALITY_GOOD, QUALITY_NORMAL, QUALITY_INACCURACY,
    QUALITY_BLUNDER, QUALITY_UNKNOWN,
    CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, CONFIDENCE_LOW, CONFIDENCE_UNKNOWN,
)


def check(name, cond, extra=""):
    print(("[CHECK] %-36s %s %s" % (name, "OK" if cond else "FAIL", extra)))
    if not cond:
        raise AssertionError(name)


def _inp(**kw):
    """快捷构造 MoveQualityInput（默认合理值）。"""
    defaults = dict(move_no=10, color="B", played_move="Q16", best_move="Q16",
                    ai_rank=0, score_loss=0.0, winrate_drop=0.0,
                    parent_winrate=0.5, parent_score_lead=0.0,
                    visits=200, stage="opening")
    defaults.update(kw)
    return MoveQualityInput(**defaults)


def test_best():
    """最佳：首选 + 极小目损。"""
    r = evaluate_move(_inp(ai_rank=0, score_loss=0.1, winrate_drop=0.2))
    check("首选+极小目损 → best", r.quality_key == QUALITY_BEST, r.quality_key)
    check("quality_score >= 95", r.quality_score >= 95, str(r.quality_score))
    check("top1_match", r.top1_match is True)
    check("confidence high (visits=200)", r.confidence == CONFIDENCE_HIGH)


def test_good():
    """好手：前三选 + 小目损。"""
    r = evaluate_move(_inp(ai_rank=2, score_loss=0.8, winrate_drop=1.5))
    check("前三+小目损 → good", r.quality_key == QUALITY_GOOD, r.quality_key)
    check("quality_score 80-94", 80 <= r.quality_score <= 94, str(r.quality_score))
    check("top3_match", r.top3_match is True)
    check("top5_match", r.top5_match is True)


def test_normal():
    """一般：中等目损。"""
    r = evaluate_move(_inp(ai_rank=4, score_loss=2.0, winrate_drop=3.0))
    check("中等目损 → normal/inaccuracy", r.quality_key in (QUALITY_NORMAL, QUALITY_INACCURACY),
          r.quality_key)


def test_inaccuracy():
    """不佳：较大目损。"""
    r = evaluate_move(_inp(ai_rank=6, score_loss=5.0, winrate_drop=8.0))
    check("较大目损 → inaccuracy/blunder", r.quality_key in (QUALITY_INACCURACY, QUALITY_BLUNDER),
          r.quality_key)
    check("quality_score < 60", r.quality_score < 60, str(r.quality_score))


def test_blunder():
    """恶手：极大目损。"""
    r = evaluate_move(_inp(ai_rank=8, score_loss=12.0, winrate_drop=20.0))
    check("极大目损 → blunder", r.quality_key == QUALITY_BLUNDER, r.quality_key)
    check("quality_score <= 29", r.quality_score <= 29, str(r.quality_score))


def test_unknown():
    """未评价：数据不足。"""
    r = evaluate_move(_inp(score_loss=None, ai_rank=None, winrate_drop=None))
    check("score_loss=None → unknown", r.quality_key == QUALITY_UNKNOWN, r.quality_key)
    check("confidence unknown", r.confidence == CONFIDENCE_UNKNOWN)


def test_meaningful_position():
    """胜负已定降级。"""
    check("正常局面 meaningful", is_meaningful_position(0.5, 0.0) is True)
    check("碾压局 not meaningful", is_meaningful_position(0.99, 35.0) is False)
    check("高胜率但目差小 meaningful", is_meaningful_position(0.98, 5.0) is True)
    # 胜负已定时恶手降为不佳
    r = evaluate_move(_inp(score_loss=12.0, winrate_drop=10.0,
                           parent_winrate=0.99, parent_score_lead=35.0))
    check("胜负已定→blunder降为inaccuracy", r.quality_key == QUALITY_INACCURACY,
          r.quality_key)
    check("is_meaningful_position=False", r.is_meaningful_position is False)
    # 胜负已定但 winrate_drop>15 仍是恶手
    r2 = evaluate_move(_inp(score_loss=12.0, winrate_drop=16.0,
                            parent_winrate=0.99, parent_score_lead=35.0))
    check("胜负已定+wr_drop>15→仍blunder", r2.quality_key == QUALITY_BLUNDER, r2.quality_key)


def test_confidence():
    """置信度分级。"""
    check("visits=200 → high", evaluate_move(_inp(visits=200)).confidence == CONFIDENCE_HIGH)
    check("visits=100 → medium", evaluate_move(_inp(visits=100)).confidence == CONFIDENCE_MEDIUM)
    check("visits=50 → low", evaluate_move(_inp(visits=50)).confidence == CONFIDENCE_LOW)


def test_quality_score():
    """评分公式。"""
    s = compute_quality_score(0.0, 0.0, 0, True)
    check("满分首选 score>=100", s >= 95, str(s))
    s2 = compute_quality_score(10.0, 15.0, 8, True)
    check("大目损 score<30", s2 < 30, str(s2))
    s3 = compute_quality_score(5.0, 5.0, 4, False)
    check("胜负已定 score 45-80", 45 <= s3 <= 80, str(s3))


def test_score_to_quality():
    """评分→标签映射。"""
    check("95→best", score_to_quality(95) == QUALITY_BEST)
    check("80→good", score_to_quality(80) == QUALITY_GOOD)
    check("60→normal", score_to_quality(60) == QUALITY_NORMAL)
    check("30→inaccuracy", score_to_quality(30) == QUALITY_INACCURACY)
    check("29→blunder", score_to_quality(29) == QUALITY_BLUNDER)


def test_reasons():
    """原因列表非空 + 含阶段。"""
    r = evaluate_move(_inp(ai_rank=0, score_loss=0.1))
    check("reasons 非空", len(r.reasons) >= 2, str(r.reasons))
    check("reasons 含阶段", any("阶段" in x for x in r.reasons), str(r.reasons))
    check("首选原因", any("第一推荐" in x for x in r.reasons), str(r.reasons))


def test_problem_tags():
    """问题标签。"""
    # 布局方向
    r = evaluate_move(_inp(stage="opening", ai_rank=6, score_loss=4.0))
    check("布局方向 tag", "opening_direction" in r.problem_tags, str(r.problem_tags))
    # 官子大小
    r2 = evaluate_move(_inp(stage="endgame", ai_rank=3, score_loss=3.0, winrate_drop=2.0))
    check("官子大小 tag", "endgame_value" in r2.problem_tags, str(r2.problem_tags))
    # 优势保持
    r3 = evaluate_move(_inp(color="B", parent_winrate=0.7, score_loss=4.0, ai_rank=6))
    check("优势保持 tag", "advantage_management" in r3.problem_tags, str(r3.problem_tags))
    # 无标签（好棋）
    r4 = evaluate_move(_inp(ai_rank=0, score_loss=0.1))
    check("好棋无 problem_tags", len(r4.problem_tags) == 0, str(r4.problem_tags))


def test_white_perspective():
    """白方视角（parent_winrate 翻转用于 tag）。"""
    r = evaluate_move(_inp(color="W", parent_winrate=0.3, score_loss=5.0,
                           ai_rank=6, winrate_drop=3.0))
    # parent_winrate=0.3 黑方 → 白方 player_wr = 0.7（优势）
    check("白方优势→advantage_management", "advantage_management" in r.problem_tags,
          str(r.problem_tags))


def test_rank_contract_and_roundtrip():
    """对外 ai_rank 为 1-based；序列化容忍未来未知字段。"""
    r = evaluate_move(_inp(ai_rank=0, score_loss=0.1, winrate_drop=0.2))
    check("旧 0-based 首选迁移为 ai_rank=1", r.ai_rank == 1, str(r.ai_rank))
    check("1-based 首选 top1", r.top1_match)
    payload = r.to_dict()
    payload["future_field"] = {"ignored": True}
    restored = MoveQualityResult.from_dict(payload)
    check("to_dict/from_dict 往返", restored.to_dict() == r.to_dict())

    low = evaluate_move(_inp(visits=50, ai_rank=None, candidate_count=8))
    check("低置信原因含 visits",
          any("visits" in reason for reason in low.reasons), str(low.reasons))


if __name__ == "__main__":
    print("=" * 60)
    print(" 精细手段评价（move_quality）测试")
    print("=" * 60)
    test_best(); print()
    test_good(); print()
    test_normal(); print()
    test_inaccuracy(); print()
    test_blunder(); print()
    test_unknown(); print()
    test_meaningful_position(); print()
    test_confidence(); print()
    test_quality_score(); print()
    test_score_to_quality(); print()
    test_reasons(); print()
    test_problem_tags(); print()
    test_white_perspective(); print()
    test_rank_contract_and_roundtrip(); print()
    print("test_move_quality 全部通过 ✅")
