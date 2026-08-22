"""test_learning_event —— LearningEvent 数据结构与生命周期测试。"""
import os
import sys
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from learning_event import (
    LEARNING_EVENT_VERSION, MASTERY_NEW, MASTERY_UNDERSTANDING, MASTERY_UNSTABLE,
    RETRY_REPEATED, RETRY_CORRECTED, RETRY_ALTERNATIVE_CORRECT, RETRY_IMPROVED,
    KIND_ENDGAME_DRILL, KIND_PROBLEM, LearningEvent, endgame_event_id,
    event_id, position_key_from_board,
)
from board import BoardState


def check(name, cond, extra=""):
    print("[CHECK] %-40s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        raise AssertionError(name)


def run():
    problem = {
        "move_no": 82, "color": "B", "played_move": "R10", "best_move": "P9",
        "quality_key": "blunder", "score_loss": 6.3, "winrate_drop": 11.2,
        "stage": "middle", "problem_tags": ["overplay"],
    }
    evt = LearningEvent.from_problem(
        "game-1", problem, game_name="测试局",
        version_info={"katago_version": "1.15.3", "visits": 200})
    check("稳定 id 与手数/颜色绑定",
          evt.id == event_id("game-1", 82, "B"), evt.id)
    check("客观字段完整", evt.score_loss == 6.3 and evt.best_move == "P9"
          and evt.played_move == "R10")
    check("版本信息落盘", evt.katago_version == "1.15.3" and evt.visits == 200)
    check("初始掌握状态", evt.mastery_state == MASTERY_NEW)
    check("无 Human SL 时为空默认",
          evt.human_profile == "" and evt.human_prior_current == 0.0)

    # 序列化往返
    data = evt.to_dict()
    check("to_dict 版本字段", data["version"] == LEARNING_EVENT_VERSION)
    restored = LearningEvent.from_dict(data)
    check("序列化往返无损",
          restored.to_dict() == data and restored.attempts == [])

    # 局面指纹
    b1 = BoardState(9)
    b2 = BoardState(9)
    b3 = BoardState(9)
    try:
        b3 = b3.try_play(2, 2)   # try_play 返回新盘面，不改原对象
    except Exception:
        pass
    check("同局面同指纹", position_key_from_board(b1) == position_key_from_board(b2))
    check("不同局面不同指纹",
          position_key_from_board(b1) != position_key_from_board(b3))
    check("None 盘面返回空串", position_key_from_board(None) == "")

    # 主动复盘结果流转
    evt.record_retry("Q11", 0.7, RETRY_ALTERNATIVE_CORRECT)
    check("合理替代判为已理解",
          evt.retry_status == RETRY_ALTERNATIVE_CORRECT
          and evt.mastery_state == MASTERY_UNDERSTANDING)
    try:
        evt.record_retry("Q11", 0.7, "nonsense")
        check("非法状态被拒绝", False)
    except ValueError:
        check("非法状态被拒绝", True)

    # 重复错误 + 复发历史 → unstable（复习会但实战继续犯）
    evt2 = LearningEvent.from_problem("game-2", dict(problem, move_no=51))
    evt2.recurrence_count = 4
    evt2.record_retry("R10", 7.1, RETRY_REPEATED)
    check("复发+重选重复 → unstable", evt2.mastery_state == MASTERY_UNSTABLE)
    evt3 = LearningEvent.from_problem("game-3", dict(problem, move_no=52))
    evt3.record_retry("R10", 7.1, RETRY_REPEATED)
    check("首次重复停在 understanding",
          evt3.mastery_state == MASTERY_UNDERSTANDING)

    # 作答历史
    evt2.add_attempt("R10", 6.2, assessment="bad", ai_rank=7)
    evt2.add_attempt("Q11", 0.7, assessment="acceptable", ai_rank=2, hint_used=True)
    check("attempts 追加两条", len(evt2.attempts) == 2)
    check("attempt 字段齐全",
          evt2.attempts[0]["played_move"] == "R10"
          and evt2.attempts[1]["hint_used"] is True
          and evt2.attempts[0]["date"])
    roundtrip = LearningEvent.from_dict(evt2.to_dict())
    check("attempts 序列化保留", len(roundtrip.attempts) == 2)

    # ---- 官子训练作答事件（接力板#12）：kind 隔离 + 语义边界 ----
    # 鸭子类型构造：不 import endgame_drill（保持本模块轻依赖），
    # 字段与 EndgameDrill dataclass 同名。
    drill = SimpleNamespace(
        move_number=55, color="B", played_move="B9", best_move="C2",
        loss=5.0, drill_kind="loss", value=5.0)
    eg = LearningEvent.from_endgame_drill("game-1", drill, game_name="测试局")
    check("官子事件独立 id 命名空间",
          eg.id == endgame_event_id("game-1", 55, "B")
          and eg.id != event_id("game-1", 55, "B"))
    check("kind 标记为 endgame_drill", eg.kind == KIND_ENDGAME_DRILL)
    check("题面客观字段对齐（实战/一选/目损/题型/价值）",
          eg.played_move == "B9" and eg.best_move == "C2"
          and eg.score_loss == 5.0 and eg.stage == "endgame"
          and eg.drill_kind == "loss" and eg.drill_value == 5.0)
    check("官子事件不携带问题手语义",
          eg.primary_category == "" and eg.retry_status == ""
          and eg.mastery_state == MASTERY_NEW and eg.review_due_date == ""
          and eg.learning_priority == 0.0)
    eg.add_attempt("C2", score_loss=0.0, assessment="best", ai_rank=1)
    eg_round = LearningEvent.from_dict(eg.to_dict())
    check("官子字段与作答历史序列化往返",
          eg_round.kind == KIND_ENDGAME_DRILL and eg_round.drill_kind == "loss"
          and len(eg_round.attempts) == 1)
    legacy = LearningEvent.from_dict({"game_id": "old", "move_no": 3})
    check("旧数据无 kind 键回退问题手", legacy.kind == KIND_PROBLEM)

    print("test_learning_event: 全部通过")


if __name__ == "__main__":
    run()
