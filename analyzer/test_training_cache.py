"""Tests for persistent stage-training response cache helpers."""

from training_cache import compact_analysis, package_matches, position_key, put_analysis


def test_position_key_is_stable_and_position_specific():
    initial = [["B", "D16"]]
    moves = [["W", "Q4"], ["B", "Q16"]]
    key1 = position_key(initial, moves, rules="chinese", komi=7.5)
    key2 = position_key(list(initial), list(moves), rules="chinese", komi=7.5)
    changed = position_key(initial, moves + [["W", "D4"]], rules="chinese", komi=7.5)
    assert key1 == key2
    assert key1 != changed


def test_compact_analysis_removes_large_maps_and_limits_candidates():
    response = {
        "rootInfo": {"winrate": 0.6, "scoreLead": 2.5},
        "moveInfos": [
            {"move": "A%d" % (i + 1), "order": i, "winrate": 0.6}
            for i in range(12)
        ],
        "ownership": [0.1] * 361,
        "policy": [0.01] * 362,
    }
    compact = compact_analysis(response)
    assert compact["rootInfo"]["scoreLead"] == 2.5
    assert len(compact["moveInfos"]) == 8
    assert "ownership" not in compact
    assert "policy" not in compact


def test_package_matching_and_insert():
    task = {"id": "stage-20-55", "playerColor": "B"}
    signature = {
        "model": "model.bin.gz:1:2",
        "rules": "chinese",
        "komi": 7.5,
        "visits": 80,
        "boardSize": 19,
    }
    package = {
        "version": 1,
        "taskId": task["id"],
        "taskPlayerColor": "B",
        "signature": dict(signature),
        "entries": {},
    }
    assert package_matches(package, task, signature)
    assert not package_matches(package, dict(task, playerColor="W"), signature)
    assert not package_matches(package, task, dict(signature, visits=140))
    response = {
        "rootInfo": {"winrate": 0.5, "scoreLead": 0.0},
        "moveInfos": [{"move": "Q16", "order": 0}],
    }
    assert put_analysis(package, "position-key", response)
    assert package["entries"]["position-key"]["moveInfos"][0]["move"] == "Q16"
    assert not put_analysis(package, "position-key", response)


def test_background_builder_prepares_three_user_branches_and_ai_replies():
    from app import GoAnalyzer
    from movetree import MoveTree

    class Guard:
        @staticmethod
        def pending_count():
            return 0

    class Client:
        def __init__(self):
            self.next_id = 1

        def analyze(self, _query):
            qid = str(self.next_id)
            self.next_id += 1
            return qid

    analyzer = GoAnalyzer.__new__(GoAnalyzer)
    analyzer.size = 19
    analyzer.guard = Guard()
    analyzer.client = Client()
    analyzer._training_cache_bg_pending = {}
    analyzer._library_bg_should_pause = lambda: False
    analyzer.after = lambda _delay, _callback: None
    analyzer._set_msg = lambda _text: None
    tree = MoveTree(19)
    initial_analysis = {
        "rootInfo": {"winrate": 0.5, "scoreLead": 0.0},
        "moveInfos": [
            {"move": "D16", "order": 0},
            {"move": "Q16", "order": 1},
            {"move": "D4", "order": 2},
        ],
    }
    package = {"entries": {}}
    analyzer._training_cache_bg_current = {
        "record_id": "test",
        "name": "test.sgf",
        "tree": tree,
        "rules": "chinese",
        "komi": 7.5,
        "visits": 80,
        "package": package,
        "current_moves": [],
        "current_analysis": initial_analysis,
        "to_move": "B",
        "user_color": "B",
        "rounds": 0,
        "planned_rounds": 1,
        "jobs": [],
        "branches": {},
        "errors": 0,
    }

    analyzer._advance_training_cache_background()
    while analyzer._training_cache_bg_current["rounds"] == 0:
        if not analyzer._training_cache_bg_pending:
            analyzer._send_next_training_cache_bg_request()
            continue
        qid, pending = next(iter(analyzer._training_cache_bg_pending.items()))
        kind = pending["job"]["kind"]
        reply = {
            "rootInfo": {"winrate": 0.51, "scoreLead": 0.3},
            "moveInfos": [{"move": "Q4" if kind == "user" else "C3", "order": 0}],
        }
        analyzer._handle_training_cache_bg_result(qid, reply)

    assert analyzer._training_cache_bg_current["rounds"] == 1
    assert len(package["entries"]) == 6


if __name__ == "__main__":
    test_position_key_is_stable_and_position_specific()
    test_compact_analysis_removes_large_maps_and_limits_candidates()
    test_package_matching_and_insert()
    test_background_builder_prepares_three_user_branches_and_ai_replies()
    print("test_training_cache all passed")
