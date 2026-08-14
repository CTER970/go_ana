"""配置管理 —— 自动定位 katago-runtime、持久化用户设置、启动前检查。

纯标准库（不依赖 tkinter / KataGo），可被 test_config_manager.py 无头测试。
设置文件：analyzer/user_settings.json（与本文件同目录）。
"""
from __future__ import annotations

import glob
import copy
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(HERE, "user_settings.json")

# Windows 发行包必须与 exe 同目录的 DLL（缺任一通常导致 0xC0000135 崩溃）
REQUIRED_DLLS = ["libz.dll", "libcrypto-3-x64.dll", "libssl-3-x64.dll", "libzip.dll"]


def _merge_settings(defaults, saved):
    """把用户设置合并到默认设置上，嵌套 dict 递归合并。

    旧版 user_settings.json 可能只保存了 ``ui_state.window_size`` 这类局部字段。
    过去的浅合并会让整个 ``ui_state`` 默认结构被覆盖，升级后新增键会丢失；
    这里保留旧值，同时补齐新增默认键。若默认值是 dict 但用户文件里写坏成
    非 dict，则忽略该坏值，回退默认结构。
    """
    merged = copy.deepcopy(defaults)
    if not isinstance(saved, dict):
        return merged
    for key, value in saved.items():
        default_value = merged.get(key)
        if isinstance(default_value, dict):
            if isinstance(value, dict):
                merged[key] = _merge_settings(default_value, value)
            # saved 中该项不是 dict 时视为损坏，保留默认结构。
            continue
        merged[key] = value
    return merged


def _runtime_candidates():
    """向上最多 4 层查找 katago-runtime/，并尊重环境变量 KATAGO_RUNTIME。"""
    cands = []
    base = HERE
    for _ in range(5):
        rt = os.path.join(base, "katago-runtime")
        if os.path.isdir(rt):
            cands.append(rt)
        parent = os.path.dirname(base)
        if parent == base:
            break
        base = parent
    env = os.environ.get("KATAGO_RUNTIME")
    if env and os.path.isdir(env):
        cands.append(os.path.abspath(env))
    return cands


def _looks_like_runtime(d):
    return bool(glob.glob(os.path.join(d, "katago*.exe"))
                 or glob.glob(os.path.join(d, "katago*")))


def find_runtime_dir():
    """返回第一个看起来像运行时目录的绝对路径，找不到返回 None。"""
    for c in _runtime_candidates():
        if _looks_like_runtime(c):
            return c
    return None


def default_engine_path(rt):
    """优先 GPU/OpenCL 版，其次 CPU 版保底。"""
    if not rt:
        return ""
    for name in ("katago-opencl.exe", "katago-eigenavx2.exe"):
        p = os.path.join(rt, name)
        if os.path.exists(p):
            return p
    exes = sorted(glob.glob(os.path.join(rt, "katago*.exe")))
    return exes[0] if exes else ""


def list_engine_paths(rt=None):
    """列出运行时目录下可用的 KataGo 引擎，GPU/OpenCL 版优先。"""
    rt = rt or find_runtime_dir()
    if not rt:
        return []
    preferred = ["katago-opencl.exe", "katago-eigenavx2.exe"]
    found = []
    for name in preferred:
        p = os.path.join(rt, name)
        if os.path.exists(p):
            found.append(p)
    for p in sorted(glob.glob(os.path.join(rt, "katago*.exe"))):
        if p not in found:
            found.append(p)
    return found


def default_model_path(rt):
    """优先轻量 b18c384nbt，其次任意 .bin.gz。"""
    if not rt:
        return ""
    for sub in ("models", "."):
        base = os.path.join(rt, sub)
        for pat in ("*b18c384nbt*.bin.gz", "*b18c384*.bin.gz", "*.bin.gz"):
            fs = sorted(glob.glob(os.path.join(base, pat)))
            if fs:
                return fs[0]
    return ""


def list_model_paths(rt=None):
    """列出运行时目录下可用的模型，轻量 b18 优先，其余按文件名排序。"""
    rt = rt or find_runtime_dir()
    if not rt:
        return []
    files = []
    for sub in ("models", "."):
        base = os.path.join(rt, sub)
        files.extend(glob.glob(os.path.join(base, "*.bin.gz")))
    uniq = []
    for p in files:
        if p not in uniq:
            uniq.append(p)
    return sorted(uniq, key=lambda p: (
        0 if "b18c384" in os.path.basename(p).lower() else
        1 if "b28c512" in os.path.basename(p).lower() else 2,
        os.path.basename(p).lower(),
    ))


def default_human_model_path(rt=None):
    """自动定位 Human SL 模型（文件名含 human 的 .bin.gz，如
    kata1-b18c384nbt-humanv0.bin.gz）。找不到返回空串——未配置时
    引擎正常回退普通 KataGo（大纲 §6）。"""
    rt = rt or find_runtime_dir()
    if not rt:
        return ""
    for sub in ("models", "."):
        base = os.path.join(rt, sub)
        for pat in ("*human*.bin.gz",):
            fs = sorted(glob.glob(os.path.join(base, pat)))
            if fs:
                return os.path.normpath(fs[0])
    return ""


class ConfigManager:
    """用户设置的加载/保存/自动定位/启动检查。"""

    DEFAULTS = {
        "engine_path": "",
        "model_path": "",
        # Human SL（大纲 §6-8）：未配置时引擎正常回退普通 KataGo
        "human_model_path": "",
        "human_sl_profile": "rank_1d",          # 本人棋力档（humanSLProfile）
        "human_sl_reference_profile": "rank_3d",  # 对照档（高 2-4 级）
        "analysis_cfg": "analysis.cfg",
        "rules": "chinese",
        "komi": 7.5,
        "max_visits": 200,
        "theme": "light",
        "ui_style": "simple",
        "candidate_count": 5,
        "pv_length": 12,
        "review_scope": "profile",
        "deep_verification": {
            "enabled": True,
            "target_visits": 800,
            "max_samples_per_finding": 3,
            "auto_run": False,
        },
        "training_speed_mode": "fast",
        "library_training_visits": 120,
        "heatmap_mode": "off",
        "auto_hint": True,             # 自动在棋盘标出 AI 首选下一手（全模式默认开）
        "auto_hint_training": False,   # 训练用户回合也自动揭示首选（默认关，保留盲下训练意义）
        "profile": {
            "my_player_names": [],
            "default_profile_side": "unknown",
            "profile_window_games": 30,
        },
        "ui_state": {
            "window_size": "",
            "pane_position": 0,
            "main_tab": 0,
            "review_tab": 0,
        },
    }

    def __init__(self, path=SETTINGS_PATH):
        self.path = path
        self.data = copy.deepcopy(self.DEFAULTS)
        self.runtime_dir = None
        self.load()

    # ---- 加载 / 保存 ----
    def load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                if isinstance(saved, dict):
                    self.data = _merge_settings(self.DEFAULTS, saved)
            except Exception:
                pass  # 损坏则回退默认
        self._autofill()
        return self

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False

    def _autofill(self):
        """引擎/模型路径缺失或指向不存在的文件时，尝试自动定位。"""
        self.runtime_dir = find_runtime_dir()
        rt = self.runtime_dir
        ep = self.data.get("engine_path", "")
        if (not ep) or (not os.path.exists(ep)):
            p = default_engine_path(rt) if rt else ""
            if p:
                self.data["engine_path"] = p
        mp = self.data.get("model_path", "")
        if (not mp) or (not os.path.exists(mp)):
            p = default_model_path(rt) if rt else ""
            if p:
                self.data["model_path"] = p
        hp = self.data.get("human_model_path", "")
        if (not hp) or (not os.path.exists(hp)):
            p = default_human_model_path(rt)
            if p:
                self.data["human_model_path"] = p

    # ---- 访问 ----
    def get(self, key, fallback=None):
        if key in self.data:
            return self.data[key]
        if fallback is not None:
            return fallback
        return self.DEFAULTS.get(key)

    def set(self, key, value):
        self.data[key] = value

    def update(self, **kwargs):
        """批量更新并立即持久化（值为 None 的项跳过）。"""
        changed = False
        for k, v in kwargs.items():
            if v is None:
                continue
            if self.data.get(k) != v:
                self.data[k] = v
                changed = True
        if changed:
            self.save()
        return changed

    def cfg_abspath(self):
        """analysis_cfg 的绝对路径（相对则按 analyzer/ 解析）。"""
        cfg = self.data.get("analysis_cfg", "analysis.cfg")
        return cfg if os.path.isabs(cfg) else os.path.join(HERE, cfg)

    # ---- 启动前检查 ----
    def preflight(self):
        """返回 {'ok': bool, 'errors': [...], 'warnings': [...]}。
        errors 阻断启动；warnings（如缺 DLL）提示用户。"""
        errors, warnings = [], []
        exe = self.data.get("engine_path", "")
        if not exe:
            errors.append("引擎路径未配置（未自动找到 katago-runtime，请点「引擎/模型…」手动指定）")
        elif not os.path.exists(exe):
            errors.append("引擎文件不存在：%s" % exe)
        model = self.data.get("model_path", "")
        if not model:
            errors.append("模型路径未配置")
        elif not os.path.exists(model):
            errors.append("模型文件不存在：%s" % model)
        if not os.path.exists(self.cfg_abspath()):
            errors.append("分析配置不存在：%s" % self.cfg_abspath())
        # Human SL 模型缺失只告警不阻断（回退普通 KataGo）
        human = self.data.get("human_model_path", "")
        if human and not os.path.exists(human):
            warnings.append("Human SL 模型文件不存在：%s（将回退普通 KataGo）" % human)
        if exe and os.path.exists(exe):
            d = os.path.dirname(exe)
            for dll in REQUIRED_DLLS:
                if not os.path.exists(os.path.join(d, dll)):
                    warnings.append("依赖 DLL 缺失：%s（应与 exe 同目录 %s；缺失通常导致 0xC0000135 崩溃）" % (dll, d))
        return {"ok": len(errors) == 0, "errors": errors, "warnings": warnings}
