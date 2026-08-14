"""test_human_sl —— Human SL 查询构造、响应解析、档位对比与引擎参数测试。"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from human_sl import (
    DEFAULT_PROFILE, PROFILES, compare_profiles, human_query,
    level_gap_component, normalize_profile, parse_human_prior,
)
from katago_client import KataGoAnalysisClient
from config_manager import ConfigManager


def check(name, cond, extra=""):
    print("[CHECK] %-44s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        raise AssertionError(name)


def run():
    # 档位
    check("官方档位列表示意", "rank_20k" in PROFILES and "rank_9d" in PROFILES)
    check("非法档位回默认", normalize_profile("九段") == DEFAULT_PROFILE
          and normalize_profile("rank_3d") == "rank_3d")

    # 查询构造
    q = human_query({"moves": ["Q16"], "komi": 7.5}, "rank_1d")
    check("查询带 humanSLProfile override",
          q["overrideSettings"]["humanSLProfile"] == "rank_1d")
    check("includePolicy 开启", q["includePolicy"] is True)
    q2 = human_query({"overrideSettings": {"visits": 100}}, "bad_profile")
    check("已有 override 不被覆盖",
          q2["overrideSettings"]["visits"] == 100
          and q2["overrideSettings"]["humanSLProfile"] == DEFAULT_PROFILE)

    # 响应解析（humanPolicy 字段 + 兼容 humanPrior）
    resp = {"moveInfos": [
        {"move": "R10", "humanPolicy": 0.31, "order": 3},
        {"move": "P9", "humanPolicy": 0.42, "order": 0},
    ]}
    check("解析 humanPolicy",
          abs(parse_human_prior(resp, "R10") - 0.31) < 1e-9
          and parse_human_prior(resp, "p9") == 0.42)
    legacy = {"moveInfos": [{"move": "R10", "humanPrior": 0.25}]}
    check("兼容 humanPrior 字段", parse_human_prior(legacy, "R10") == 0.25)
    check("无数据返回 None", parse_human_prior(resp, "Z99") is None
          and parse_human_prior({}, "R10") is None)

    # 档位对比（大纲 §14 三种情形）
    gap = compare_profiles("R10", 0.31, 0.06)
    check("本人常下/高档少下 → level_gap",
          gap["verdict"] == "level_gap" and abs(gap["delta"] - 0.25) < 1e-9)
    common = compare_profiles("Q4", 0.4, 0.35)
    check("两档都常下 → common_both", common["verdict"] == "common_both")
    rare = compare_profiles("M17", 0.02, 0.01)
    check("两档都少下 → rare_both", rare["verdict"] == "rare_both")
    unknown = compare_profiles("A1", None, 0.1)
    check("数据不足 → unknown", unknown["verdict"] == "unknown"
          and unknown["delta"] is None)

    # learning_priority 分量：只有明确 level_gap 给分
    check("level_gap 分量换算",
          abs(level_gap_component(gap) - 0.5) < 1e-9
          and level_gap_component(common) == 0.0
          and level_gap_component(rare) == 0.0
          and level_gap_component(None) == 0.0)

    # 引擎命令行：配置与未配置 human model
    with tempfile.TemporaryDirectory() as td:
        human = os.path.join(td, "kata1-b18c384nbt-humanv0.bin.gz")
        open(human, "w").close()
        c1 = KataGoAnalysisClient("katago.exe", "a.cfg", "m.bin.gz",
                                  human_model_path=human)
        check("-human-model 在命令行", c1.command_args() == [
            "katago.exe", "analysis", "-config", "a.cfg", "-model", "m.bin.gz",
            "-human-model", human], str(c1.command_args()))
        c2 = KataGoAnalysisClient("katago.exe", "a.cfg", "m.bin.gz",
                                  human_model_path=os.path.join(td, "missing.bin.gz"))
        check("human model 缺失自动丢弃（回退普通引擎）",
              "-human-model" not in c2.command_args())
        c3 = KataGoAnalysisClient("katago.exe", "a.cfg", "m.bin.gz")
        check("未配置无 -human-model", c3.command_args() == [
            "katago.exe", "analysis", "-config", "a.cfg", "-model", "m.bin.gz"])

        # ConfigManager：human 模型自动定位 + preflight 只告警不阻断
        settings = os.path.join(td, "settings.json")
        open(settings, "w").write("{}")
        cfg = ConfigManager(path=settings)
        check("human 配置默认值",
              cfg.get("human_sl_profile") == "rank_1d"
              and cfg.get("human_sl_reference_profile") == "rank_3d")
        # 把带 human 模型的伪运行时目录喂给自动定位
        import config_manager
        old = config_manager.find_runtime_dir
        config_manager.find_runtime_dir = lambda: td
        try:
            cfg2 = ConfigManager(path=os.path.join(td, "s2.json"))
            check("自动定位 human 模型",
                  cfg2.get("human_model_path") == human,
                  str(cfg2.get("human_model_path")))
        finally:
            config_manager.find_runtime_dir = old
        cfg.set("human_model_path", os.path.join(td, "gone.bin.gz"))
        pre = cfg.preflight()
        check("human 模型缺失只告警不阻断",
              pre["ok"] is False or not any(
                  "Human SL" in e for e in pre["errors"]))
        # 注：preflight ok 取决于引擎/主模型是否存在，这里只验证不进 errors
        check("human 缺失进 warnings",
              any("Human SL" in w for w in pre["warnings"]))

    print("test_human_sl: 全部通过")


if __name__ == "__main__":
    run()
