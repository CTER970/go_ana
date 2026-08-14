"""test_profile_report —— 个人画像 Markdown 报告测试。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from player_profile import GameBenchmark, GameTrendPoint, PlayerProfile, ProfileStats
from profile_report import generate_profile_markdown


def check(name, cond, extra=""):
    print("[CHECK] %-34s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        raise AssertionError(name)


def run():
    stats = ProfileStats(
        games=3, moves=60, avg_score_loss=2.25,
        blunder_rate=5.0, inaccuracy_rate=10.0, top3_match_rate=45.0)
    profile = PlayerProfile(
        games_count=3, evaluated_moves_count=60,
        overall=stats, black=stats, white=ProfileStats(),
        opening=stats, middle=stats, endgame=ProfileStats(),
        problem_tag_distribution={"opening_direction": 4},
        weaknesses=["中盘平均目损偏高。"],
        recommendations=["优先复习布局方向问题。"],
        trend_points=[
            GameTrendPoint(
                game_id="g1", order=0, evaluated_moves=20,
                avg_score_loss=3.0, blunder_rate=10.0, top3_match_rate=40.0),
        ],
    )
    benchmark = GameBenchmark(
        status="better", confidence="medium", prior_games=3,
        current_avg_loss=1.5, baseline_avg_loss=2.5, loss_improvement=1.0,
        evidence=["本局优于基线。"])
    report = generate_profile_markdown(
        profile,
        records=[{"id": "g1", "name": "第一盘.sgf"}],
        benchmark=benchmark,
        mistakes=[{
            "isDue": True, "gameName": "第一盘.sgf", "moveNo": 33,
            "playedMove": "D4", "bestMove": "Q16", "scoreLoss": 6.5,
        }],
        generated_at="2026-06-30 12:00")
    check("报告含核心指标", "## 核心指标" in report and "2.25" in report)
    check("报告含个人基线", "优于个人基线" in report and "历史 3 盘" in report)
    check("报告含逐盘趋势", "第一盘.sgf" in report and "40.0%" in report)
    check("报告含问题与建议", "布局方向：4 次" in report and "中盘平均目损偏高" in report)
    check("报告含优先训练", "## 优先训练" in report and "跨局出现 4 次" in report)
    check("优先训练含近期趋势边界", "趋势样本不足" in report)
    check("报告含到期错题", "第33手" in report and "D4 → AI Q16" in report)
    check("报告含口径说明", "仅统计已识别为本人执棋方" in report)


if __name__ == "__main__":
    run()
    print("test_profile_report: PASS")
