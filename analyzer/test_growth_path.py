"""成长路线约束测试。"""
from growth_path import build_growth_path
from style_cost import StyleCostResult
from style_profile import StyleProfile


def cost(key, conclusion, priority):
    return StyleCostResult(
        dimension_key=key, dimension_label=key,
        tendency_level="high", cost_level=(
            "low_cost" if conclusion == "keep" else "high_cost"),
        conclusion=conclusion, avg_score_loss=5.0,
        frequency_per_100=10.0, confidence="medium",
        priority=priority, representative_moves=[{
            "game_id": "g", "move_no": int(priority), "score_loss": priority}])


def run():
    profile = StyleProfile(profile_id="p", confidence="medium")
    costs = [
        cost("tenuki_tendency", "fix", 30),
        cost("advantage_pressure", "fix", 25),
        cost("endgame_safety", "fix", 20),
        cost("fighting_preference", "fix", 15),
        cost("territory_preference", "keep", 10),
        cost("stability_preference", "keep", 9),
        cost("influence_preference", "keep", 8),
        cost("comeback_complexity", "insufficient", 2),
    ]
    path = build_growth_path(profile, costs)
    assert len(path.fix_habits) == 3
    assert len(path.keep_styles) == 2
    assert path.fix_habits[0]["key"] == "tenuki_tendency"
    assert "脱先" in path.main_goal
    assert len(path.verification_required) == 3
    forbidden = ("段表现", "你必须", "固定棋风")
    assert not any(word in path.main_goal for word in forbidden)

    insufficient = build_growth_path(
        profile, [cost("x", "insufficient", 99)])
    assert not insufficient.fix_habits
    print("test_growth_path: PASS")


if __name__ == "__main__":
    run()
