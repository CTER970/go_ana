"""棋风画像与成长路线的原子缓存。"""
from __future__ import annotations

import json
import os
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
_FILE_NAME = "style_profile_cache.json"

# ---- 默认路径调用时解析（usage_log.set_path 同款约定）----
# 历史教训（W29 审查）：def f(path=DEFAULT_PATH) 在导入期把路径固化进
# 默认参数，重定向对"走默认值"的调用无效，数据可能写错位置。
_state = {"path": None}


def default_path():
    """内置默认路径（不受 set_path 重定向影响）。"""
    return os.path.join(HERE, "game_library", _FILE_NAME)


# 兼容引用：运行期生效的默认以 get_path() 为准。
DEFAULT_PATH = default_path()
VERSION = 1


def get_path():
    """当前生效的默认存储路径：set_path 重定向 > game_library.LIBRARY_DIR 派生。"""
    if _state["path"]:
        return _state["path"]
    try:
        import game_library as _gl
        return os.path.join(_gl.LIBRARY_DIR, _FILE_NAME)
    except Exception:
        return default_path()


def set_path(path):
    """重定向默认存储路径（测试用）；None 恢复默认。调用时解析，立即生效。"""
    _state["path"] = path or None


def _resolve_path(path):
    return path or get_path()


def save_style_cache(style_profile, growth_path, path=None):
    path = _resolve_path(path)
    data = {
        "version": VERSION,
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "profileId": style_profile.profile_id,
        "analysisSignatureGroup": dict(
            style_profile.analysis_signature_summary or {}),
        "styleProfile": style_profile.to_dict(),
        "growthPath": growth_path.to_dict(),
        "sourceGameIds": list(style_profile.source_game_ids or []),
        "warnings": list(style_profile.warnings or []),
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return data


def load_style_cache(path=None):
    path = _resolve_path(path)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None

