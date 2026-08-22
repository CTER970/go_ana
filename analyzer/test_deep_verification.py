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




def run_default_path_redirection():
    """W29 审查回归：默认路径必须调用时解析（set_path/gl 派生生效）。"""
    import hashlib
    import game_library as gl
    import deep_verification as dv

    def _digest(path):
        if not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    real = dv.default_path()
    before = _digest(real)
    tmp = tempfile.mkdtemp(prefix="dv-redirect-")
    try:
        assert dv.save_store.__defaults__[0] is None
        assert dv.merge_and_save_tasks.__defaults__[0] is None

        redirected = os.path.join(tmp, "deep_verification.json")
        dv.set_path(redirected)
        try:
            task = DeepVerificationTask(
                task_id="redirect-t1", game_id="g1", move_no=3, color="B",
                played_move="Q16", original_quality="blunder",
                original_score_loss=5.0, target_visits=800)
            dv.merge_and_save_tasks([task])
            assert os.path.exists(redirected)
            assert load_store().get("tasks")
            assert _digest(real) == before, "写穿生产 deep_verification.json"
        finally:
            dv.set_path(None)
        assert dv.get_path() == dv.default_path()

        gl_orig = gl.LIBRARY_DIR
        gl.LIBRARY_DIR = tmp
        try:
            assert dv.get_path() == redirected
        finally:
            gl.LIBRARY_DIR = gl_orig
        print("run_default_path_redirection: PASS")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
        dv.set_path(None)


if __name__ == "__main__":
    run()
    run_default_path_redirection()
