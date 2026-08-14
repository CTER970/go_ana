"""Tests for actual-vs-AI deep branch comparison."""

from types import SimpleNamespace

from branch_comparison import build_branch_comparison
from movetree import MoveTree
from review import ReviewReport


def response(score, winrate, pv, ownership):
    return {
        "rootInfo": {"scoreLead": score, "winrate": winrate},
        "moveInfos": [{"move": pv[0], "order": 0, "pv": pv}],
        "ownership": ownership,
    }


def test_black_branch_comparison_uses_mover_perspective():
    evaluation = SimpleNamespace(
        color="B", move_number=50, is_pass=False, coord="C11", best_move="K10")
    actual = response(-4.0, 0.34, ["D11", "C10"], [-0.7] * 30 + [0.0] * 331)
    ai = response(2.5, 0.57, ["Q9", "R10"], [0.7] * 45 + [0.0] * 316)
    result = build_branch_comparison(
        evaluation, actual, ai, phase_label="中盘", visits=400)
    assert result["scoreGain"] == 6.5
    assert result["winrateGainPct"] == 23.0
    assert result["actual"]["pv"][0] == "C11"
    assert result["ai"]["pv"][0] == "K10"
    assert result["controlGain"] == 45
    assert "方向选择" in result["diagnosis"]


def test_white_branch_comparison_flips_score_and_winrate():
    evaluation = SimpleNamespace(
        color="W", move_number=120, is_pass=False, coord="D3", best_move="R3")
    actual = response(8.0, 0.72, ["C3"], [0.0] * 361)
    ai = response(3.0, 0.58, ["R4"], [0.0] * 361)
    result = build_branch_comparison(
        evaluation, actual, ai, phase_label="关子", visits=400)
    assert result["scoreGain"] == 5.0
    assert result["winrateGainPct"] == 14.0
    assert "官子价值" in result["diagnosis"]


def test_app_collects_both_async_branches():
    from app import GoAnalyzer

    class Config:
        @staticmethod
        def get(key, default=None):
            return 200 if key == "max_visits" else default

    class Client:
        def __init__(self):
            self.queries = {}

        def analyze(self, query):
            qid = "q%d" % (len(self.queries) + 1)
            self.queries[qid] = query
            return qid

    tree = MoveTree(19)
    tree.root.analysis = {
        "rootInfo": {"scoreLead": 0.0, "winrate": 0.5},
        "moveInfos": [{"move": "D16", "order": 0, "scoreLead": 6.0, "winrate": 0.7}],
    }
    assert tree.play(15, 3)[0]  # B Q16
    tree.current.analysis = {
        "rootInfo": {"scoreLead": 0.0, "winrate": 0.5},
        "moveInfos": [{"move": "D4", "order": 0}],
    }
    evaluation = ReviewReport(tree).evaluate()[0]
    app = GoAnalyzer.__new__(GoAnalyzer)
    app.size = 19
    app.tree = tree
    app.rules = "chinese"
    app.komi = 7.5
    app.model_file = ""
    app.cfg = Config()
    app.client = Client()
    app._problem_compare_pending = {}
    app._selected_problem_eval = evaluation
    app._library_record_id = None
    app._set_msg = lambda _text: None
    app._show_problem_intent = lambda *_args, **_kwargs: None

    assert app._start_problem_comparison(evaluation)
    assert len(app._problem_compare_pending) == 2
    for qid in list(app._problem_compare_pending):
        branch = app._problem_compare_pending[qid]["branch"]
        result = response(
            0.0 if branch == "actual" else 6.0,
            0.5 if branch == "actual" else 0.7,
            ["D4", "Q4"],
            [0.0] * 361,
        )
        app._handle_problem_compare_result(qid, result)
    saved = tree._deep_comparisons["1"]
    assert saved["scoreGain"] == 6.0
    assert saved["actualMove"] == "Q16"
    assert saved["aiMove"] == "D16"


if __name__ == "__main__":
    test_black_branch_comparison_uses_mover_perspective()
    test_white_branch_comparison_flips_score_and_winrate()
    test_app_collects_both_async_branches()
    print("test_branch_comparison all passed")
