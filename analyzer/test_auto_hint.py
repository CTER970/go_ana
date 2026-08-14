"""Headless tests for the automatic AI first-choice hint (全模式 + 全自动).

Covers `_auto_hint_context_allowed` / `_apply_auto_hint`:
- normal / review / browse modes → auto-mark best move
- scoring mode, drill-quiz (not revealed), training user-turn → suppressed by default
- drill revealed, training AI-turn, or cfg auto_hint_training=True → allowed
- pass best-move / illegal candidates handled
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

from app import GoAnalyzer
from movetree import MoveTree, point_to_xy


class FakeCfg:
    def __init__(self, data=None):
        self.data = data or {}
        self.updated = {}

    def get(self, key, fallback=None):
        return self.data.get(key, fallback)

    def update(self, **kwargs):
        self.updated.update(kwargs)


def make_app():
    app = GoAnalyzer.__new__(GoAnalyzer)
    app.size = 19
    app.tree = MoveTree(19)
    app.scoring_mode = False
    app._training = None
    app._mistake_review = None
    app._drill_win = None
    app._drill_revealed = False
    app._hint_point = None
    app._hint_auto = False
    app._auto_hint = True
    app.cfg = FakeCfg()
    return app


def _best_move_response(move="Q16", order=0):
    return {"moveInfos": [{"move": move, "order": order, "winrate": 0.5}]}


def check(name, cond, extra=""):
    print(("[CHECK] %-46s %s %s" % (name, "OK" if cond else "FAIL", extra)))
    if not cond:
        raise AssertionError(name)


def test_auto_hint_marks_best_move_in_normal_mode():
    app = make_app()
    app._apply_auto_hint(_best_move_response("Q16"))
    check("普通模式自动标首选", app._hint_point == point_to_xy("Q16", 19), str(app._hint_point))
    check("标记来自自动(_hint_auto)", app._hint_auto is True)


def test_auto_hint_off_clears():
    app = make_app()
    app._hint_point = (3, 3)
    app._hint_auto = True
    app._auto_hint = False
    app._apply_auto_hint(_best_move_response("Q16"))
    check("自动关→清空标记", app._hint_point is None)
    check("自动关→_hint_auto=False", app._hint_auto is False)


def test_auto_hint_scoring_suppressed():
    app = make_app()
    app.scoring_mode = True
    app._apply_auto_hint(_best_move_response("Q16"))
    check("点目模式不揭示下一手", app._hint_point is None)


def test_auto_hint_drill_quiz_suppressed():
    app = make_app()
    app._drill_active = lambda: True     # quiz 进行中
    app._drill_revealed = False
    app._apply_auto_hint(_best_move_response("Q16"))
    check("问题手 quiz 作答阶段不揭示", app._hint_point is None)


def test_auto_hint_drill_revealed_allowed():
    app = make_app()
    app._drill_active = lambda: True
    app._drill_revealed = True            # 已揭示 → 可继续标首选
    app._apply_auto_hint(_best_move_response("Q16"))
    check("quiz 揭示后允许标首选", app._hint_point == point_to_xy("Q16", 19), str(app._hint_point))


def _training(user_turn=True, ai_playing=False):
    return {"active": True, "finished": False, "ai_playing": ai_playing,
            "user_color": "B", "awaiting": "user" if user_turn else "ai"}


def test_auto_hint_training_user_turn_suppressed_by_default():
    app = make_app()
    app._training = _training(user_turn=True)
    app.cfg = FakeCfg({"auto_hint_training": False})
    app._apply_auto_hint(_best_move_response("Q16"))
    check("训练用户回合默认不揭示", app._hint_point is None)


def test_auto_hint_training_user_turn_override():
    app = make_app()
    app._training = _training(user_turn=True)
    app.cfg = FakeCfg({"auto_hint_training": True})
    app._apply_auto_hint(_best_move_response("Q16"))
    check("设置开启→训练用户回合也揭示", app._hint_point == point_to_xy("Q16", 19), str(app._hint_point))


def test_auto_hint_training_ai_turn_allowed():
    app = make_app()
    app._training = _training(ai_playing=True)
    app.cfg = FakeCfg({"auto_hint_training": False})
    app._apply_auto_hint(_best_move_response("Q16"))
    check("训练 AI 回合允许标首选", app._hint_point == point_to_xy("Q16", 19), str(app._hint_point))


def test_auto_hint_pass_clears():
    app = make_app()
    app._apply_auto_hint({"moveInfos": [{"move": "pass", "order": 0}]})
    check("首选为 pass 不标点", app._hint_point is None and app._hint_auto is False)


def test_auto_hint_empty_moveinfos_clears():
    app = make_app()
    app._apply_auto_hint({"moveInfos": []})
    check("无候选不标点", app._hint_point is None)


def test_auto_hint_skips_illegal_to_second_choice():
    app = make_app()
    assert app.tree.play(*point_to_xy("D16", 19))[0]   # D16 已占
    resp = {"moveInfos": [
        {"move": "D16", "order": 0},   # 非法（已占）
        {"move": "Q4", "order": 1},    # 合法
    ]}
    app._apply_auto_hint(resp)
    check("跳过非法候选取次选", app._hint_point == point_to_xy("Q4", 19), str(app._hint_point))


def test_auto_hint_mistake_review_suppressed():
    """错题测验为盲下：自动首选不得揭示答案（F1 仍可主动请求）。"""
    app = make_app()
    app._mistake_review = {"active": True}
    app._apply_auto_hint(_best_move_response("Q16"))
    check("错题测验盲下不揭示首选", app._hint_point is None)
    check("错题测验不设 _hint_auto", app._hint_auto is False)


def test_manual_hint_preserved_when_auto_off():
    """自动关时，同节点重绘不清掉用户 F1 手动提示；自动首选标记仍清。"""
    app = make_app()
    app._auto_hint = False
    app._hint_point = (15, 3)
    app._hint_auto = False            # 手动提示
    app._apply_auto_hint(_best_move_response("Q4"))
    check("自动关→手动提示保留", app._hint_point == (15, 3), str(app._hint_point))
    app._hint_auto = True             # 自动首选标记 → 应被清
    app._apply_auto_hint(_best_move_response("Q4"))
    check("自动关→自动首选标记清空", app._hint_point is None)


def test_toggle_auto_hint_persists_and_applies():
    app = make_app()
    app.redraw = lambda: None
    app._set_msg = lambda text: None
    app.tree.current.analysis = _best_move_response("Q4")
    assert app._auto_hint is True
    app.toggle_auto_hint()
    check("toggle 后 _auto_hint=False", app._auto_hint is False)
    check("toggle 持久化写 cfg", app.cfg.updated.get("auto_hint") is False)
    check("关闭后清空标记", app._hint_point is None)
    # 再开 → 立即按当前分析标首选
    app.toggle_auto_hint()
    check("再开 _auto_hint=True", app._auto_hint is True)
    check("再开立即标首选", app._hint_point == point_to_xy("Q4", 19), str(app._hint_point))
    check("再开持久化写 cfg", app.cfg.updated.get("auto_hint") is True)


if __name__ == "__main__":
    print("=" * 60)
    print(" 自动 AI 首选（全模式 + 全自动）测试")
    print("=" * 60)
    test_auto_hint_marks_best_move_in_normal_mode()
    test_auto_hint_off_clears()
    test_auto_hint_scoring_suppressed()
    test_auto_hint_drill_quiz_suppressed()
    test_auto_hint_drill_revealed_allowed()
    test_auto_hint_training_user_turn_suppressed_by_default()
    test_auto_hint_training_user_turn_override()
    test_auto_hint_training_ai_turn_allowed()
    test_auto_hint_pass_clears()
    test_auto_hint_empty_moveinfos_clears()
    test_auto_hint_skips_illegal_to_second_choice()
    test_auto_hint_mistake_review_suppressed()
    test_manual_hint_preserved_when_auto_off()
    test_toggle_auto_hint_persists_and_applies()
    print("test_auto_hint 全部通过 ✅")
