"""test_heatmap_mapping —— ownership/policy 与棋盘坐标映射正确性（含真实引擎）。"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from heatmap import (ownership_index, ownership_at, ownership_is_black,
                     policy_board_entries, policy_pass_value)
from katago_client import KataGoAnalysisClient

EXE = r"D:\katago\katago-runtime\katago-eigenavx2.exe"
CFG = os.path.join(HERE, "analysis.cfg")
MODEL = r"D:\katago\katago-runtime\models\kata1-b18c384nbt-s9996604416-d4316597426.bin.gz"


def check(name, cond, extra=""):
    print(("[CHECK] %-38s %s %s" % (name, "OK" if cond else "FAIL", extra)))
    if not cond:
        raise AssertionError(name)


def test_index_mapping():
    # y=0 顶部 = A19 行；x=0 左
    check("A19(左上) 索引=0", ownership_index(0, 0) == 0)
    check("T1(右下) 索引=360", ownership_index(18, 18) == 360)
    check("A1(左下) 索引=342", ownership_index(0, 18) == 342)   # 18*19
    check("T19(右上) 索引=18", ownership_index(18, 0) == 18)
    check("Q16(15,3) 索引=72", ownership_index(15, 3) == 72)   # 3*19+15


def test_ownership_sign():
    check("+0.6 判为黑属地", ownership_is_black(0.6))
    check("-0.6 不判为黑", not ownership_is_black(-0.6))
    check("0 中性", not ownership_is_black(0.0))
    check("None 安全", not ownership_is_black(None))


def test_policy_entries():
    pol = [0.0] * 362
    pol[72] = 0.5         # Q16
    pol[0] = -1           # A19 非法
    pol[361] = 0.05       # pass（末位）
    entries = policy_board_entries(pol)
    check("条目数=360（361 棋位点去 1 非法）", len(entries) == 360, str(len(entries)))
    coords = {(x, y) for x, y, v in entries}
    check("含 Q16", (15, 3) in coords)
    check("不含非法 A19", (0, 0) not in coords)
    check("不含 pass（无第 362 项）", all((x, y) != (18, 18) or v == 0.0 for x, y, v in entries))
    check("pass 权重可取", policy_pass_value(pol) == 0.05)


def test_katago_real_mapping():
    print("\n启动 KataGo 取真实 ownership/policy...")
    cli = KataGoAnalysisClient(EXE, CFG, MODEL, cwd=HERE)
    cli.start()
    t0 = time.time()
    while not cli.ready and time.time() - t0 < 90:
        cli.poll(); time.sleep(0.2)
    check("KataGo 就绪", cli.ready)

    def query(moves):
        qid = cli.analyze({"moves": moves, "rules": "chinese", "komi": 7.5,
                           "boardXSize": 19, "boardYSize": 19,
                           "includeOwnership": True, "includePolicy": True})
        t0 = time.time()
        while time.time() - t0 < 90:
            for rid, r in cli.poll():
                if rid == qid:
                    return r
            time.sleep(0.1)
        return None

    # 空盘：结构 + 范围
    resp = query([])
    check("空盘拿到响应", resp is not None)
    own, pol = resp["ownership"], resp["policy"]
    check("ownership 长度=361", len(own) == 361)
    check("policy 长度=362", len(pol) == 362)
    check("ownership 全在 [-1,1]", all(-1.0 <= v <= 1.0 for v in own))

    # 关键方向检验：黑下 Q16(15,3) 后，该子所在点应强黑属地；
    # 对角点 (3,15) 空着、应明显更不黑。若 y/x 翻转，高值会落到别的索引 → 必失败。
    resp2 = query([["B", "Q16"]])
    check("单子局面拿到响应", resp2 is not None)
    o2 = resp2["ownership"]
    v_stone = ownership_at(o2, 15, 3)        # 黑子所在点
    v_opposite = ownership_at(o2, 3, 15)     # 对角空点
    check("黑子点强黑属地(>0.5)", v_stone > 0.5, "%.3f" % v_stone)
    check("黑子点比对角更黑", v_stone > v_opposite, "stone=%.3f opp=%.3f" % (v_stone, v_opposite))

    entries = policy_board_entries(pol)
    top = max(entries, key=lambda e: e[2])
    check("policy 最大点在棋盘内", 0 <= top[0] < 19 and 0 <= top[1] < 19, str(top))
    cli.stop()


if __name__ == "__main__":
    print("=" * 60)
    print(" 热力图映射测试")
    print("=" * 60)
    test_index_mapping(); print()
    test_ownership_sign(); print()
    test_policy_entries(); print()
    test_katago_real_mapping(); print()
    print("test_heatmap_mapping 全部通过 ✅")
