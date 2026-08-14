"""Persistent response-cache helpers for stage training.

The cache stores KataGo analysis by a stable position key derived from the
complete move sequence. It is separate from the move tree so prepared training
branches do not appear as review variations.
"""
from __future__ import annotations

import hashlib
import json


CACHE_VERSION = 1
MAX_CACHED_MOVES = 8


def position_key(initial_stones, moves, rules="chinese", komi=7.5, board_size=19):
    """Return a stable key for a position across project reloads."""
    payload = {
        "boardSize": int(board_size),
        "initialStones": initial_stones or [],
        "moves": moves or [],
        "rules": str(rules or "chinese"),
        "komi": float(komi),
    }
    raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def compact_analysis(response, move_limit=MAX_CACHED_MOVES):
    """Keep the fields needed for training play and final grading."""
    if not response or "error" in response:
        return None
    move_infos = sorted(
        (response.get("moveInfos") or []),
        key=lambda item: item.get("order", 999),
    )[:max(1, int(move_limit))]
    out = {
        "rootInfo": dict(response.get("rootInfo") or {}),
        "moveInfos": [dict(item) for item in move_infos],
    }
    if response.get("turnNumber") is not None:
        out["turnNumber"] = response.get("turnNumber")
    return out


def model_signature(model_path):
    """Use a portable model identifier; callers may append file metadata."""
    path = str(model_path or "").replace("\\", "/")
    return path.rsplit("/", 1)[-1]


def package_matches(package, task, signature):
    """Return whether a saved package is valid for this training setup."""
    if not isinstance(package, dict) or package.get("version") != CACHE_VERSION:
        return False
    if package.get("taskId") != (task or {}).get("id"):
        return False
    if str(package.get("taskPlayerColor", "both")) != str((task or {}).get("playerColor", "both")):
        return False
    expected = signature or {}
    actual = package.get("signature") or {}
    for key in ("model", "rules", "komi", "visits", "boardSize"):
        if str(actual.get(key)) != str(expected.get(key)):
            return False
    return isinstance(package.get("entries"), dict)


def put_analysis(package, key, response):
    """Insert one compact response and return True when the package changed."""
    compact = compact_analysis(response)
    if not key or compact is None:
        return False
    entries = package.setdefault("entries", {})
    if entries.get(key) == compact:
        return False
    entries[key] = compact
    return True
