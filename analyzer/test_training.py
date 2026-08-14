"""test_training -- stage training task selection and grading."""

from movetree import MoveTree, point_to_xy, xy_to_point
from review import ReviewReport
from training import generate_training_task, grade_training_session


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, detail)
    if not cond:
        raise AssertionError(name + (" :: " + detail if detail else ""))


def analysis(score_lead, winrate, move_infos):
    return {"rootInfo": {"scoreLead": score_lead, "winrate": winrate}, "moveInfos": move_infos}


def mi(move, sl, wr, order=0):
    return {"move": move, "scoreLead": sl, "winrate": wr, "order": order, "visits": 10}


def make_tree(losses):
    t = MoveTree(19)
    coords = []
    for y in range(3, 16, 3):
        for x in range(3, 16, 3):
            coords.append(xy_to_point(x, y, 19))
    for y in range(19):
        for x in range(19):
            pt = xy_to_point(x, y, 19)
            if pt not in coords:
                coords.append(pt)
    played = []
    for i, _loss in enumerate(losses):
        pt = coords[i]
        played.append(pt)
        x, y = point_to_xy(pt, 19)
        ok, reason = t.play(x, y)
        check("play %s" % pt, ok, reason)
    line = ReviewReport(t).mainline_nodes()
    for i, loss in enumerate(losses, start=1):
        parent = line[i - 1]
        node = line[i]
        color = node.move[0]
        best_sl = loss if color == "B" else -loss
        node_wr = 0.5 - min(loss, 20) / 100.0 if color == "B" else 0.5 + min(loss, 20) / 100.0
        parent.analysis = analysis(0.5, 0.5, [mi("A1", best_sl, 0.5, 0)])
        node.analysis = analysis(0.0, node_wr, [mi("A1", 0.0, node_wr, 0)])
    return t


def test_generate_training_task_selects_worst_stage():
    losses = [0.4] * 12 + [1.0] * 12 + [5.0] * 24 + [0.6] * 12
    t = make_tree(losses)
    task = generate_training_task(t, window=24, step=12)
    check("生成训练题", task is not None, str(task))
    check("选中最差累计阶段", task["startMove"] == 25 and task["endMove"] == 48, str(task))
    check("不是单手题", task["moves"] == 24, str(task))
    check("有阶段摘要", "第 25-48 手" in task["summary"], task["summary"])


def test_generate_training_task_filters_player_color():
    losses = [0.4] * 12 + [1.0] * 12 + [5.0] * 24 + [0.6] * 12
    t = make_tree(losses)
    task = generate_training_task(t, window=24, step=12, player_color="W")
    check("训练题记录用户执棋方", task["playerColor"] == "W", str(task))
    check("训练题按用户方统计手数", task["focusMoves"] < task["analyzedMoves"], str(task))
    check("摘要包含用户方", "白方" in task["summary"], task["summary"])


def test_grade_training_session_counts_user_moves():
    t = make_tree([0.5, 6.0, 0.5, 6.0, 0.5, 6.0, 0.5, 6.0])
    rr = ReviewReport(t)
    evs = rr.evaluate()
    task = {"id": "stage-1-8", "avgLoss": 6.0, "playerColor": "B", "summary": "原阶段"}
    report = grade_training_session(task, evs, "B")
    check("只统计黑方用户手", report["userMoves"] == 4, str(report))
    check("训练评价可生成", report["grade"] in ("A", "B", "C", "D"), str(report))
    check("改善值来自原阶段对比", report["improvement"] > 0, str(report))
    check("保留原阶段摘要", report["originalSummary"] == "原阶段", str(report))


if __name__ == "__main__":
    test_generate_training_task_selects_worst_stage(); print()
    test_generate_training_task_filters_player_color(); print()
    test_grade_training_session_counts_user_moves(); print()
    print("test_training all passed")
