"""高强度复核任务与稳定性测试。"""
import os
import tempfile

from deep_verification import (
    DeepVerificationTask, build_verification_tasks, is_stable,
    load_store, save_store, summarize_verified_findings, update_task_result)
from growth_path import GrowthPath, apply_verified_findings
from style_profile import StyleProfile


def run():
    growth = GrowthPath(verification_required=[{
        "key": "tenuki_tendency", "label": "脱先倾向",
        "representative_moves": [
            {"game_id": "g", "game_name": "棋局", "move_no": i,
             "color": "B", "played_move": "K10", "quality_key": "blunder",
             "score_loss": 8.0, "visits": 100}
            for i in range(1, 7)]
    }])
    tasks = build_verification_tasks(
        StyleProfile(), growth, max_samples_per_finding=3)
    assert len(tasks) == 3
    assert all(item.target_visits == 800 for item in tasks)
    assert is_stable(8.0, 7.0, "blunder", "inaccuracy")
    assert not is_stable(8.0, 2.0, "blunder", "good")

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "verify.json")
        save_store(tasks, path)
        for task in tasks:
            update_task_result(task.task_id, {
                "quality_key": "good", "score_loss": 1.0}, path=path)
        data = load_store(path)
        findings = summarize_verified_findings(data["tasks"])
        assert findings[0].stability == "unstable"
        route = GrowthPath(
            main_goal="原目标",
            fix_habits=[{"key": "tenuki_tendency", "label": "脱先"}])
        apply_verified_findings(route, [
            findings[0].to_dict()])
        assert not route.fix_habits
        assert route.watch_items
    print("test_deep_verification: PASS")


if __name__ == "__main__":
    run()
