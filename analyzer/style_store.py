"""棋风画像与成长路线的原子缓存。"""
from __future__ import annotations

import json
import os
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(HERE, "game_library", "style_profile_cache.json")
VERSION = 1


def save_style_cache(style_profile, growth_path, path=DEFAULT_PATH):
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


def load_style_cache(path=DEFAULT_PATH):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None

