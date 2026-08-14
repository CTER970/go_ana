"""test_score_estimator —— 终局点目纯逻辑测试（中国规则 / 面积数法）。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from board import BoardState, BLACK, WHITE, EMPTY
from movetree import point_to_xy
from score_estimator import ScoreEstimator, ownership_territory_split


def check(name, cond, extra=""):
    print(("[CHECK] %-34s %s %s" % (name, "OK" if cond else "FAIL", extra)))
    if not cond:
        raise AssertionError(name)


def place(board, color, points):
    for pt in points:
        x, y = point_to_xy(pt, board.size)
        board.grid[y][x] = color


def snap(board):
    return [row[:] for row in board.grid]


def test_empty():
    b = BoardState(19)
    r = ScoreEstimator(b, komi=7.5).compute_chinese_area_score()
    check("空盘 黑活子=0", r.black_stones == 0)
    check("空盘 白方面积=7.5", r.white_area == 7.5, str(r.white_area))
    check("空盘 margin=-7.5", r.margin == -7.5)
    check("空盘 白胜", r.winner == "W")
    check("空盘 result=W+7.5", r.result_text == "W+7.5", r.result_text)


def test_surrounded_territory():
    b = BoardState(19)
    place(b, BLACK, ["C4", "E4", "D3", "D5"])   # 围住 D4
    r = ScoreEstimator(b, komi=0).compute_chinese_area_score()
    check("被围空点 D4 归黑地", "D4" in r.black_territory_points, str(r.black_territory_points[:8]))


def test_dead_stone_removal():
    b = BoardState(19)
    place(b, BLACK, ["C4", "E4", "D3", "D5"])   # 围 D4
    place(b, WHITE, ["D4"])                      # 白死子在黑空内
    se = ScoreEstimator(b, komi=0)
    r0 = se.compute_chinese_area_score()
    check("未标死子前白活子=1", r0.white_stones == 1)
    se.toggle_dead_stone("D4")
    r = se.compute_chinese_area_score()
    check("标死子后白活子=0", r.white_stones == 0)
    check("死白子记录", r.dead_white == ["D4"], str(r.dead_white))
    check("死子点归黑地", "D4" in r.black_territory_points)


def test_toggle_recompute():
    b = BoardState(19)
    place(b, WHITE, ["Q16"])
    se = ScoreEstimator(b, komi=7.5)
    check("未标 白活子=1", se.compute_chinese_area_score().white_stones == 1)
    se.toggle_dead_stone("Q16")
    check("标死 白活子=0", se.compute_chinese_area_score().white_stones == 0)
    se.toggle_dead_stone("Q16")
    check("再取消 白活子=1", se.compute_chinese_area_score().white_stones == 1)


def test_neutral():
    b = BoardState(19)
    place(b, BLACK, ["D4"])    # (3,15)
    place(b, WHITE, ["F4"])    # (5,15)；中间 E4(4,15) 同时邻黑邻白
    r = ScoreEstimator(b, komi=0).compute_chinese_area_score()
    check("E4 为中立", "E4" in r.neutral_points_list, str(r.neutral_points_list[:8]))
    check("中立不计黑地", "E4" not in r.black_territory_points)
    check("中立不计白地", "E4" not in r.white_territory_points)


def test_result_text_draw_and_black_win():
    # 和棋：空盘 komi=0
    r0 = ScoreEstimator(BoardState(19), komi=0).compute_chinese_area_score()
    check("空盘 komi0 和棋", r0.winner == "Draw" and r0.result_text == "0", r0.result_text)
    # 黑胜：仅黑子 komi=0
    b = BoardState(19)
    place(b, BLACK, ["Q16"])
    r1 = ScoreEstimator(b, komi=0).compute_chinese_area_score()
    check("仅黑子 → 黑胜", r1.winner == "B" and r1.result_text.startswith("B+"), r1.result_text)


def test_ownership_suggest():
    # 黑子点 ownership 强偏白 → 建议黑死子；白子点强偏黑 → 建议白死子
    b = BoardState(19)
    place(b, BLACK, ["D4"])     # (3,15) 索引 15*19+3=288
    place(b, WHITE, ["Q16"])    # (15,3) 索引 3*19+15=72
    own = [0.0] * 361
    own[288] = -0.9   # 黑子处强偏白
    own[72] = 0.9     # 白子处强偏黑
    se = ScoreEstimator(b, komi=7.5)
    sug = se.suggest_dead_stones_from_ownership(own, threshold=0.75)
    check("建议黑死子 D4", "D4" in sug, str(sorted(sug)))
    check("建议白死子 Q16", "Q16" in sug, str(sorted(sug)))
    # 阈值之下不建议
    sug2 = se.suggest_dead_stones_from_ownership(own, threshold=0.95)
    check("超阈值不建死子", "D4" not in sug2 and "Q16" not in sug2)


def test_no_mutation():
    b = BoardState(19)
    place(b, BLACK, ["D4"])
    place(b, WHITE, ["Q16"])
    before = snap(b)
    se = ScoreEstimator(b, komi=7.5)
    se.toggle_dead_stone("D4")
    se.compute_chinese_area_score()
    se.suggest_dead_stones_from_ownership([0.0] * 361)
    check("原 BoardState 未被修改", snap(b) == before)


def test_malformed_dead_points_ignored():
    """越界/非法死子点串（'A0' 行0→y=19 越界；'A20' → y=-1 负索引）不崩溃、不误判。"""
    b = BoardState(19)
    place(b, BLACK, ["A1"])          # 左下角 (0,18)
    se = ScoreEstimator(b, komi=7.5)
    se.set_dead_stones({"A0", "A20", "Z9", "A"})
    r = se.compute_chinese_area_score()                       # 不抛 IndexError
    check("越界死子点不崩溃", r.result_text.startswith(("B+", "W+", "0")))
    check("越界点不误判为死子", "A0" not in r.dead_black and "A20" not in r.dead_black,
          str(r.dead_black))
    check("合法黑子仍计为活子（未受越界点影响）", r.black_stones == 1, str(r.black_stones))


def test_short_ownership_safe():
    """ownership 长度不足（异常/截断响应）不抛 IndexError，安全返回。"""
    b = BoardState(19)
    place(b, BLACK, ["D4"])          # 索引 288，远超短数组
    se = ScoreEstimator(b, komi=7.5)
    sug = se.suggest_dead_stones_from_ownership([0.9] * 10, threshold=0.75)   # 不抛
    check("短 ownership 不崩溃", isinstance(sug, set))
    check("短 ownership 不误判", "D4" not in sug, str(sorted(sug)))
    # None / 空也不崩溃
    check("None ownership 安全", se.suggest_dead_stones_from_ownership(None) == set())


def test_ownership_territory_split():
    """形势判断领地拆分：实地(>=0.6) / 倾向(0.05..0.6)，黑正白负。"""
    # 构造 9 路 ownership：左上 3 点强黑、右侧 2 点弱白、其余 0
    size = 9
    own = [0.0] * (size * size)
    own[0 * size + 0] = 0.9    # 强黑 (0,0)
    own[0 * size + 1] = 0.7    # 强黑 (1,0)
    own[0 * size + 2] = 0.2    # 倾向黑 (2,0)
    own[4 * size + 8] = -0.95  # 强白 (8,4)
    own[5 * size + 8] = -0.3   # 倾向白 (8,5)
    own[6 * size + 6] = 0.04   # 中立（<0.05 不计）
    split = ownership_territory_split(own, size=size)
    check("实地黑=2", split["b_strong"] == 2, str(split))
    check("实地白=1", split["w_strong"] == 1, str(split))
    check("倾向黑=1", split["b_lean"] == 1, str(split))
    check("倾向白=1", split["w_lean"] == 1, str(split))
    # None / 空 / 残缺都安全（不抛、不计）
    empty = ownership_territory_split(None, size=9)
    check("None 全 0", empty == {"b_strong": 0, "w_strong": 0, "b_lean": 0, "w_lean": 0})
    short = ownership_territory_split([0.9, -0.9, 0.2], size=19)  # 仅 3 个有效点
    check("残缺 ownership 不越界", short["b_strong"] == 1 and short["w_strong"] == 1
          and short["b_lean"] == 1, str(short))


if __name__ == "__main__":
    print("=" * 60)
    print(" 点目计分（中国规则）测试")
    print("=" * 60)
    test_empty(); print()
    test_surrounded_territory(); print()
    test_dead_stone_removal(); print()
    test_toggle_recompute(); print()
    test_neutral(); print()
    test_result_text_draw_and_black_win(); print()
    test_ownership_suggest(); print()
    test_no_mutation(); print()
    test_malformed_dead_points_ignored(); print()
    test_short_ownership_safe(); print()
    test_ownership_territory_split(); print()
    print("test_score_estimator 全部通过 ✅")
