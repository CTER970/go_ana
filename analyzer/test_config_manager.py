"""test_config_manager —— 配置管理无头测试（自动定位 / preflight / 持久化 / 可移植 / 无硬编码）。"""
import os
import sys
import json
import tempfile
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import config_manager as cm
from config_manager import (ConfigManager, find_runtime_dir,
                            default_engine_path, default_model_path,
                            list_engine_paths, list_model_paths)


def check(name, cond, extra=""):
    print(("[CHECK] %-34s %s %s" % (name, "OK" if cond else "FAIL", extra)))
    if not cond:
        raise AssertionError(name)


def test_autolocate():
    rt = find_runtime_dir()
    check("找到 katago-runtime", rt is not None and os.path.isdir(rt), str(rt))
    exe = default_engine_path(rt) if rt else ""
    check("默认引擎存在", bool(exe) and os.path.exists(exe), exe)
    check("默认引擎是 katago*.exe", bool(exe) and "katago" in os.path.basename(exe).lower())
    model = default_model_path(rt) if rt else ""
    check("默认模型存在", bool(model) and os.path.exists(model), model)
    check("默认模型是 .bin.gz", bool(model) and model.endswith(".bin.gz"))


def test_preflight():
    cfg = ConfigManager()
    pre = cfg.preflight()
    check("preflight ok", pre["ok"], str(pre["errors"]))
    check("engine_path 存在", os.path.exists(cfg.get("engine_path")))
    check("model_path 存在", os.path.exists(cfg.get("model_path")))


def test_runtime_choices():
    rt = find_runtime_dir()
    engines = list_engine_paths(rt)
    models = list_model_paths(rt)
    check("发现至少 1 个引擎", len(engines) >= 1, str(engines))
    check("GPU/OpenCL 引擎优先", os.path.basename(engines[0]).lower() == "katago-opencl.exe", str(engines))
    check("发现至少 1 个模型", len(models) >= 1, str(models))
    check("轻量 b18 模型优先", "b18c384" in os.path.basename(models[0]).lower(), str(models))


def test_roundtrip():
    fd, tmp = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        c1 = ConfigManager(path=tmp)
        c1.update(rules="japanese", komi=6.5, max_visits=800,
                  training_speed_mode="deep", library_training_visits=160,
                  heatmap_mode="policy", candidate_count=4, pv_length=18,
                  review_scope="both",
                  ui_state={
                      "window_size": "1200x800", "pane_position": 640,
                      "main_tab": 1, "review_tab": 1})
        c2 = ConfigManager(path=tmp)
        check("roundtrip rules", c2.get("rules") == "japanese", c2.get("rules"))
        check("roundtrip komi", c2.get("komi") == 6.5, c2.get("komi"))
        check("roundtrip visits", c2.get("max_visits") == 800)
        check("roundtrip training speed", c2.get("training_speed_mode") == "deep")
        check("roundtrip library visits", c2.get("library_training_visits") == 160)
        check("roundtrip heatmap", c2.get("heatmap_mode") == "policy")
        check("roundtrip candidate count", c2.get("candidate_count") == 4)
        check("roundtrip pv length", c2.get("pv_length") == 18)
        check("roundtrip review scope", c2.get("review_scope") == "both")
        check("default ui style", c2.get("ui_style") == "simple")
        c2.update(ui_style="cyberpunk", theme="dark")
        c3 = ConfigManager(path=tmp)
        check("roundtrip ui style", c3.get("ui_style") == "cyberpunk")
        check("roundtrip theme", c3.get("theme") == "dark")
        check("roundtrip ui state",
              c3.get("ui_state")["window_size"] == "1200x800"
              and c3.get("ui_state")["main_tab"] == 1)
    finally:
        os.remove(tmp)


def test_nested_defaults_merge():
    """旧设置只保存部分嵌套字段时，升级后应补齐新增默认键。"""
    fd, tmp = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({
                "ui_state": {"window_size": "1100x700"},
                "profile": {"default_profile_side": "B"},
                "deep_verification": {"enabled": False},
            }, f)
        cfg = ConfigManager(path=tmp)
        ui_state = cfg.get("ui_state")
        profile = cfg.get("profile")
        deep = cfg.get("deep_verification")
        check("嵌套 ui_state 保留旧值", ui_state["window_size"] == "1100x700")
        check("嵌套 ui_state 补默认键", "pane_position" in ui_state and "main_tab" in ui_state)
        check("嵌套 profile 保留旧值", profile["default_profile_side"] == "B")
        check("嵌套 profile 补默认键", isinstance(profile["my_player_names"], list))
        check("嵌套 deep 保留旧值", deep["enabled"] is False)
        check("嵌套 deep 补默认键", deep["target_visits"] == 800)

        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"ui_state": "broken"}, f)
        cfg2 = ConfigManager(path=tmp)
        check("损坏嵌套设置回退默认结构", isinstance(cfg2.get("ui_state"), dict))
    finally:
        os.remove(tmp)


def test_portable_walk():
    """模拟项目挪到别处：analyzer 向上 walk 仍能找到 katago-runtime。"""
    tmp = tempfile.mkdtemp()
    try:
        fake_rt = os.path.join(tmp, "katago-runtime")
        fake_ana = os.path.join(tmp, "proj", "analyzer")
        os.makedirs(fake_rt)
        os.makedirs(fake_ana)
        open(os.path.join(fake_rt, "katago-eigenavx2.exe"), "w").close()
        orig = cm.HERE
        cm.HERE = fake_ana
        try:
            rt = cm.find_runtime_dir()
            check("向上 walk 找到 runtime", rt is not None and os.path.basename(rt) == "katago-runtime", str(rt))
        finally:
            cm.HERE = orig
    finally:
        shutil.rmtree(tmp)


def test_no_hardcode():
    src_cm = open(os.path.join(HERE, "config_manager.py"), encoding="utf-8").read()
    check("config_manager 无 D:\\katago 硬编码",
          ("D:\\katago" not in src_cm) and ("D:/katago" not in src_cm))
    app_path = os.path.join(HERE, "app.py")
    if os.path.exists(app_path):
        src_app = open(app_path, encoding="utf-8").read()
        check("app.py 无 D:\\katago 硬编码",
              ("D:\\katago" not in src_app) and ("D:/katago" not in src_app))


if __name__ == "__main__":
    print("=" * 60)
    print(" config_manager 测试")
    print("=" * 60)
    test_autolocate(); print()
    test_preflight(); print()
    test_runtime_choices(); print()
    test_roundtrip(); print()
    test_nested_defaults_merge(); print()
    test_portable_walk(); print()
    test_no_hardcode(); print()
    print("config_manager 全部通过 ✅")
