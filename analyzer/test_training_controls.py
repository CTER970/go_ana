"""Headless tests for global hint and takeback behavior."""

from app import GoAnalyzer
from movetree import MoveTree, point_to_xy


class FakeGuard:
    def __init__(self):
        self.invalidated = []

    def invalidate_node(self, node):
        self.invalidated.append(node)


def make_app_without_tk():
    app = GoAnalyzer.__new__(GoAnalyzer)
    app.size = 19
    app.tree = MoveTree(19)
    app.scoring_mode = False
    app._training = None
    app._hint_point = None
    app._hint_pending_nid = None
    app._active_training_cache = None
    app._training_deferred_nodes = {}
    app._training_prefetch_pending = {}
    app._training_prefetch_cache = {}
    app._training_prefetch_waiters = {}
    app.guard = FakeGuard()
    app._stop_auto_play = lambda: None
    app._after_navigate = lambda: None
    app._schedule_training_prefetch = lambda delay=120: None
    app._set_msg = lambda text: setattr(app, "_last_message", text)
    return app


def test_training_takeback_removes_user_move_and_ai_reply():
    app = make_app_without_tk()
    played = []
    for point in ("D16", "Q4", "Q16", "D4"):
        x, y = point_to_xy(point, 19)
        assert app.tree.play(x, y)[0]
        played.append(app.tree.current)
    app._training = {
        "active": True,
        "finished": False,
        "user_color": "B",
        "nodes": list(played),
        "awaiting": "user",
        "ai_playing": False,
    }

    assert app._training_takeback()
    assert app.tree.current is played[1]
    assert app._training["nodes"] == played[:2]
    assert app._training["awaiting"] == "user"
    assert app.guard.invalidated == played[2:]
    assert "上一手及 AI 应手" in app._last_message


def test_normal_takeback_exits_scoring_and_undoes_one_move():
    app = make_app_without_tk()
    assert app.tree.play(3, 3)[0]
    app.scoring_mode = True
    app.exit_scoring = lambda: setattr(app, "scoring_mode", False)

    assert app.do_takeback()
    assert app.tree.current.depth == 0
    assert app.scoring_mode is False


def test_hint_skips_stale_illegal_candidate():
    app = make_app_without_tk()
    assert app.tree.play(3, 3)[0]
    response = {
        "moveInfos": [
            {"move": "D16", "order": 0},
            {"move": "Q4", "order": 1},
        ]
    }
    choice = app._hint_move_info(response)
    assert choice["move"] == "Q4"


if __name__ == "__main__":
    test_training_takeback_removes_user_move_and_ai_reply()
    test_normal_takeback_exits_scoring_and_undoes_one_move()
    test_hint_skips_stale_illegal_candidate()
    print("test_training_controls all passed")
