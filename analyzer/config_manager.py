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


# Human SL 可用性状态（human_sl_status 返回值里的 state 取值）
HUMAN_SL_CONFIGURED = "configured"      # 用户显式配置且文件存在
HUMAN_SL_AUTODETECTED = "autodetected"  # 未配置，但在运行时目录自动发现
HUMAN_SL_MISSING = "missing"            # 未安装（未配置且未发现）

_HUMAN_SL_MESSAGES = {
    HUMAN_SL_CONFIGURED: "Human SL 模型已配置：%s",
    HUMAN_SL_AUTODETECTED: "Human SL 模型自动发现：%s（未显式配置）",
    HUMAN_SL_MISSING: ("Human SL 模型未安装：学习优先级的「水平差异」分量"
                       "自动不参与计算（已剔除权重，排序仍稳定）；"
                       "可下载 kata1-b18c384nbt-humanv0.bin.gz 放入 "
                       "katago-runtime/models/ 后重启生效"),
}


class ConfigManager:
    """用户设置的加载/保存/自动定位/启动检查。"""

    DEFAULTS = {
        "engine_path": "",
        "model_path": "",
        # Human SL（大纲 §6-8）：未配置时引擎正常回退普通 KataGo
        "human_model_path": "",
        "human_sl_profile": "rank_1d",          # 本人棋力档（humanSLProfile）
        "human_sl_reference_profile": "rank_3d",  # 对照档（高 2-4 级）
        # 稳定学习棋力档（反馈 #11）：判题容差/Human SL 都以它为准，
        # 用户设置一次；单局表现只用于报告展示，不再反过来改判题门槛。
        # 为空时回退单局表现档（旧行为）。示例："业余1段" / "野狐3D"
        "user_learning_rank": "",
        # 对局情境（反馈 #13）：正式比赛/段位赛/网络慢棋…，影响学习优先级
        # 的 game_importance 分量。默认"网络对局"（权重 0.55，用时制未知的
        # 普通网棋）：绝大多数复盘来自网棋，介于快慢棋之间；空串/未识别
        # 类型回落 0.5 基准（learning_priority.DEFAULT_GAME_IMPORTANCE），
        # 老配置文件没有该键时合并本默认，不崩。
        "default_game_type": "网络对局",
        "analysis_cfg": "analysis.cfg",
        "rules": "chinese",
        "komi": 7.5,
        "max_visits": 200,
        "theme": "light",
        "ui_style": "simple",
        "candidate_count": 3,   # v2：候选区精简为 3 个大按钮（用户需求）
        "pv_length": 12,
        "review_scope": "profile",
        "deep_verification": {
            "enabled": True,
            "target_visits": 800,
            "max_samples_per_finding": 3,
            # auto_run 默认关闭的理由（治理复核，结论：保守不改）：
            # 深验证按 800 visits 重算 = 常规分析（200）的 4 倍单点开销，
            # 每个发现最多再抽 3 处样本，一局几十个发现时整局复盘要多等
            # 数分钟（OpenCL/CPU 尤甚）。收益是压噪（复查可疑判定、降低
            # 单次低 visits 的误报），但属于"锦上添花"而非正确性必需，
            # 且当前没有"深验证翻案率"的真实数据支撑默认开启的性价比。
            # 等积累数据后再评估是否默认开；用户可手动触发。
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
        # Human SL 模型来源（human_sl_status 用）：configured/autodetected/missing
        self._human_model_source = HUMAN_SL_MISSING
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
        if hp and os.path.exists(hp):
            self._human_model_source = HUMAN_SL_CONFIGURED
        else:
            p = default_human_model_path(rt)
            if p:
                self.data["human_model_path"] = p
                self._human_model_source = HUMAN_SL_AUTODETECTED
            else:
                self._human_model_source = HUMAN_SL_MISSING

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

    # ---- Human SL 可用性查询 ----
    def human_sl_status(self):
        """Human SL 可用性的结构化状态（治理"静默失效"：本轮只提供查询
        口，UI 接入由后续任务做）。无副作用，可随时调用。

        返回：
          state          "configured"（用户配置且存在）/ "autodetected"
                         （运行时目录自动发现）/ "missing"（未安装）
          available      模型文件当前是否真实存在
          model_path     模型路径（可能为空串或指向不存在的文件）
          profile        本人棋力档（humanSLProfile，如 rank_1d）
          reference_profile  对照更高档（如 rank_3d）
          level_gap_excluded  为 True 时学习优先级的 level_gap 分量
                         自动不参与（learning_priority.level_gap_of → None）
          message        面向用户的中文说明（将来直接给 UI 显示）

        注意：引擎进程侧是否真正加载（-human-model 生效）由
        KataGoAnalysisClient.human_model_usable()/human_model_active 判断，
        本函数只负责配置/文件层面。
        """
        path = self.data.get("human_model_path", "")
        available = bool(path and os.path.exists(path))
        state = self._human_model_source
        if available and state == HUMAN_SL_MISSING:
            # 兜底：load 之后经 set()/update() 直接改了路径（没走 _autofill）
            state = HUMAN_SL_CONFIGURED
        template = _HUMAN_SL_MESSAGES[state]
        message = template % path if "%s" in template else template
        return {
            "state": state,
            "available": available,
            "model_path": path,
            "profile": self.data.get("human_sl_profile", ""),
            "reference_profile": self.data.get("human_sl_reference_profile", ""),
            "level_gap_excluded": not available,
            "message": message,
        }

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


def human_sl_status(settings_path=SETTINGS_PATH):
    """模块级便捷查询：读设置文件并返回 Human SL 状态（不写盘）。

    供将来 UI / 诊断脚本复用；字段说明见 ConfigManager.human_sl_status。
    """
    return ConfigManager(path=settings_path).human_sl_status()
