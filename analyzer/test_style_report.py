"""棋风报告测试。"""
from growth_path import build_growth_path
from style_cost import attach_style_costs, build_style_costs
from style_profile import StyleDimension, StyleProfile
from style_report import render_style_report


def run():
    profile = StyleProfile(
        games_count=5, evaluated_moves_count=100, confidence="medium",
        analysis_signature_summary={
            "model": "m", "visits": 200, "rules": "chinese", "komi": 7.5},
        dimensions=[
            StyleDimension(
                key="territory_preference", label="实地确认",
                sample_count=20, evaluated_moves=100, frequency_per_100=20,
                avg_score_loss=1.0, confidence="high"),
            StyleDimension(
                key="tenuki_tendency", label="脱先倾向",
                sample_count=10, evaluated_moves=100, frequency_per_100=10,
                avg_score_loss=6.0, blunder_rate=0.2, confidence="medium",
                representative_moves=[{
                    "game_id": "g", "game_name": "棋局", "move_no": 30,
                    "color": "B", "played_move": "K10", "best_move": "D4",
                    "quality_key": "blunder", "score_loss": 6.0}]),
        ])
    costs = build_style_costs(profile)
    attach_style_costs(profile, costs)
    growth = build_growth_path(profile, costs)
    report = render_style_report(profile, growth, generated_at="2026-06-30")
    for text in ("## 数据范围", "## 棋风摘要", "## 风格维度",
                 "## 下一阶段成长路线", "## 边界说明",
                 "尚未高强度复核"):
        assert text in report
    assert "正式棋力认证" in report
    verified = render_style_report(profile, growth, verification=[{
        "conclusion_label": "脱先倾向", "checked_samples": 3,
        "stable_samples": 2, "stability": "stable", "message": "可信"}])
    assert "stable" in verified and "可信" in verified
    print("test_style_report: PASS")


if __name__ == "__main__":
    run()
