"""profile_store —— 长期个人画像缓存持久化（纯逻辑，不依赖 tkinter / KataGo 进程）。

职责（见 当前任务.md §2.4、§13、§27.6）：
  1. 保存和读取长期画像缓存（``game_library/profile_cache.json``）。
  2. 管理画像版本（``version`` 字段，前向兼容）。
  3. 支持从棋局库索引（``game_library/index.json``）重新构建画像。
  4. 处理旧数据缺失字段时的优雅降级——任何缺字段、字段损坏、文件缺失都不报错。

设计原则：
  * 纯 Python，无 tkinter 依赖。
  * 复用 ``player_profile.PlayerProfile``，本模块只负责
    序列化/反序列化与缓存指纹，**不**计算目损、不实现评价阈值。
  * 画像模块不直接修改 ``index.json``（见 §27.7），重建只**读取**索引中的
    ``profileSummary`` 轻量摘要。

缓存文件结构（§27.6 + 任务规范）::

    {
      "version": 1,
      "generated_at": "2026-06-29 12:00:00",
      "games_count": 30,
      "source_fingerprint": "...",
      "window_games": 30,
      "profile": { ... PlayerProfile 序列化 ... }
    }

``PlayerProfile`` 的精确字段由并行实现的 ``player_profile.py`` 决定。为避免与之
强耦合，本模块在序列化时优先调用 ``profile.to_dict()`` / ``PlayerProfile.from_dict()``，
若不存在则退化为 ``dataclasses`` 反射，缺失字段以默认值补齐。
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from datetime import datetime
from typing import Any, Optional

# 画像缓存版本号：结构发生破坏性变化时 +1，加载端据此判断兼容性。
PROFILE_CACHE_VERSION = 2

# 默认参与长期画像的最近棋局数（§27.6 windowGames）。
DEFAULT_WINDOW_GAMES = 30


# ===================== 默认缓存路径（调用时解析） =====================
# 历史教训（W29 审查）：def f(path=DEFAULT_CACHE_PATH) 会在导入期把路径
# 固化进默认参数，之后 set_path/模块属性重定向对"走默认值"的调用无效，
# 数据可能写错位置（测试写穿生产 game_library）。默认路径必须每次调用现取：
# set_path 重定向 > game_library 当前属性派生 > 内置默认。
HERE = os.path.dirname(os.path.abspath(__file__))

_state = {"cache_path": None, "index_path": None}


def default_cache_path() -> str:
    """内置默认缓存路径（不受 set_path 重定向影响）。"""
    return os.path.join(HERE, "game_library", "profile_cache.json")


def default_index_path() -> str:
    """内置默认索引路径（不受 set_path 重定向影响）。"""
    return os.path.join(HERE, "game_library", "index.json")


# 兼容引用：运行期生效的默认以 get_cache_path()/get_index_path() 为准。
DEFAULT_CACHE_PATH = default_cache_path()
DEFAULT_INDEX_PATH = default_index_path()


def get_cache_path() -> str:
    """当前生效的缓存路径：set_path 重定向 > game_library.PROFILE_CACHE_PATH。"""
    if _state["cache_path"]:
        return _state["cache_path"]
    try:
        import game_library as _gl
        return _gl.PROFILE_CACHE_PATH
    except Exception:
        return default_cache_path()


def get_index_path() -> str:
    """当前生效的索引路径：set_path 重定向 > game_library.INDEX_PATH。"""
    if _state["index_path"]:
        return _state["index_path"]
    try:
        import game_library as _gl
        return _gl.INDEX_PATH
    except Exception:
        return default_index_path()


def set_path(cache_path=None, index_path=None):
    """重定向默认路径（测试用，与 usage_log.set_path 同款约定）。

    非 None 生效，None 恢复该项默认；调用时解析，立即对后续调用生效。
    """
    _state["cache_path"] = cache_path or None
    _state["index_path"] = index_path or None


def _resolve_cache_path(path: Optional[str]) -> str:
    return path or get_cache_path()


def _resolve_index_path(path: Optional[str]) -> str:
    return path or get_index_path()


def _now() -> str:
    """统一的本地时间戳字符串（与 game_library._now 保持一致）。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ===================== PlayerProfile 序列化适配 =====================
# 这里用「鸭子类型 + 反射」做宽松适配，确保旧缓存缺字段时仍可读取。

def _profile_to_dict(profile: Any) -> dict:
    """把 PlayerProfile 序列化为 dict。

    优先使用其自带的 ``to_dict()``；否则用 ``dataclasses.asdict``；
    再否则退化到 ``vars()``。保证返回一个普通 dict。
    """
    if profile is None:
        return {}
    # 1) 自带序列化方法（推荐路径）
    to_dict = getattr(profile, "to_dict", None)
    if callable(to_dict):
        try:
            data = to_dict()
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    # 2) dataclass 反射
    if dataclasses.is_dataclass(profile) and not isinstance(profile, type):
        try:
            return dataclasses.asdict(profile)
        except Exception:
            # asdict 对不可序列化字段（如自定义对象）会抛错，继续降级
            pass
    # 3) __dict__
    if hasattr(profile, "__dict__"):
        return dict(vars(profile))
    return {}


def _build_profile_from_dict(cls: type, data: dict) -> Any:
    """用 ``cls`` 从 dict 重建实例，容忍缺字段。

    优先用 ``cls.from_dict(data)``；否则按 dataclass 字段名逐个取值，
    缺失字段使用字段默认值/默认工厂，保证旧数据缺字段也不报错。
    """
    if data is None:
        data = {}
    # 1) 自带反序列化方法
    from_dict = getattr(cls, "from_dict", None)
    if callable(from_dict):
        try:
            return from_dict(data)
        except Exception:
            # 自带方法对该数据不适用（可能是字段全缺），退化为反射构造
            pass
    # 2) dataclass 反射构造
    if dataclasses.is_dataclass(cls):
        kwargs = {}
        for f in dataclasses.fields(cls):
            if f.name in data:
                kwargs[f.name] = data[f.name]
            # 缺字段：让构造函数用其默认值（不显式传该 key）
        try:
            return cls(**kwargs)
        except Exception:
            # 极端兜底：尝试无参构造
            try:
                return cls()
            except Exception:
                return None
    return None


def _import_player_profile():
    """惰性导入 PlayerProfile；缺失时返回 None。"""
    try:
        from player_profile import PlayerProfile
        return PlayerProfile
    except Exception:
        return None


def _profile_games_count(profile: Any) -> int:
    """尽量从画像对象上读出统计棋局数；读不到返回 0。"""
    for key in ("games_count", "gamesCount"):
        v = getattr(profile, key, None)
        if isinstance(v, int):
            return v
    d = _profile_to_dict(profile)
    for key in ("games_count", "gamesCount"):
        v = d.get(key)
        if isinstance(v, int):
            return v
    return 0


# ===================== 指纹 =====================
def compute_source_fingerprint(summaries: list[dict]) -> str:
    """根据最近 N 条棋局库 ``profileSummary`` 生成缓存指纹。

    指纹输入字段（§27.6）：record id、profileSummary.version、updatedAt、
    profileSide。指纹一致 → 缓存有效，可直接读取；不一致 → 重建。

    ``summaries`` 为 dict 列表，每项形如::

        {"id": "...", "profile_summary": {...}, "updated_at": "...",
         "profile_side": "B"}
    """
    parts = []
    for item in summaries or []:
        if not isinstance(item, dict):
            continue
        rid = item.get("id") or item.get("record_id") or ""
        ps = item.get("profile_summary") or item.get("profileSummary") or {}
        if not isinstance(ps, dict):
            ps = {}
        ver = ps.get("version", "")
        updated = (item.get("updated_at") or item.get("updatedAt")
                   or ps.get("updated_at") or ps.get("updatedAt") or "")
        side = (item.get("profile_side") or item.get("profileSide")
                or ps.get("profile_side") or ps.get("profileSide") or "")
        parts.append("%s|%s|%s|%s" % (rid, ver, updated, side))
    raw = "\n".join(parts)
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()


# ===================== 主接口：保存 / 读取 =====================
def save_profile(profile: Any, path: Optional[str] = None,
                 *, source_fingerprint: Optional[str] = None,
                 window_games: int = DEFAULT_WINDOW_GAMES) -> str:
    """保存长期画像缓存到 ``path``（原子写：tmp + os.replace）。

    参数：
      profile:            PlayerProfile 实例（或任何可序列化的画像对象）。
      path:               缓存文件路径，默认 ``game_library/profile_cache.json``。
      source_fingerprint: 可选，缓存对应的棋局库指纹；不提供则留空。
      window_games:       参与画像的最近棋局数（默认 30）。

    返回实际写入的绝对路径。任何序列化失败都不抛出（吞掉异常返回路径），
    避免画像保存失败影响主流程（§13.4「不破坏旧字段、不因未知字段失败」）。
    """
    path = os.path.abspath(_resolve_cache_path(path))
    os.makedirs(os.path.dirname(path), exist_ok=True)

    envelope = {
        "version": PROFILE_CACHE_VERSION,
        "generated_at": _now(),
        "games_count": _profile_games_count(profile),
        "source_fingerprint": source_fingerprint or "",
        "window_games": int(window_games),
        "profile": _profile_to_dict(profile),
    }

    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(envelope, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        # 写入失败：清理 tmp，不让异常冒泡到 UI
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
    return path


def load_profile(path: Optional[str] = None,
                 *, player_profile_cls: Optional[type] = None) -> Any:
    """从 ``path`` 读取长期画像缓存，返回 PlayerProfile 实例。

    优雅降级（§13.4、§27 退出条件「空数据、旧 index 均不会报错」）：
      * 文件不存在 → 返回 ``None``。
      * JSON 损坏 → 返回 ``None``。
      * 旧版本缺字段 / 未知字段 → 用默认值补齐，正常返回。
      * ``player_profile.py`` 尚未实现 → 返回原始 dict（调用方可继续降级展示）。
      * 缓存版本号比当前更新（前向兼容）→ 仍尝试读取，但附 ``_cache_version_newer``。

    参数：
      path:                缓存文件路径。
      player_profile_cls:  可选，显式传入 PlayerProfile 类（测试便于注入桩）。
                           默认惰性 ``import player_profile``。
    """
    path = _resolve_cache_path(path)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            envelope = json.load(f)
    except Exception:
        return None
    if not isinstance(envelope, dict):
        return None

    # 旧格式可能没有 envelope，直接就是画像 dict —— 兼容之。
    if "profile" in envelope and isinstance(envelope["profile"], dict):
        profile_data = envelope["profile"]
    else:
        profile_data = envelope

    cls = player_profile_cls if player_profile_cls is not None else _import_player_profile()
    if cls is None:
        # player_profile.py 尚未实现：返回原始 dict，调用方可降级展示
        if isinstance(profile_data, dict):
            profile_data = dict(profile_data)
            profile_data.setdefault("_cache_raw", True)
        return profile_data

    profile = _build_profile_from_dict(cls, profile_data)
    # 标记前向兼容：缓存版本比当前实现更新
    if isinstance(profile, dict):
        if envelope.get("version", PROFILE_CACHE_VERSION) > PROFILE_CACHE_VERSION:
            profile["_cache_version_newer"] = True
    elif profile is not None:
        try:
            setattr(profile, "_cache_version_newer",
                    envelope.get("version", PROFILE_CACHE_VERSION) > PROFILE_CACHE_VERSION)
        except Exception:
            pass
    return profile


def load_cache_envelope(path: Optional[str] = None) -> Optional[dict]:
    """仅读取缓存信封（含 version/fingerprint/时间戳），不反序列化画像对象。

    供调用方判断指纹是否过期（§27.6「指纹一致时直接读取缓存」）。
    """
    path = _resolve_cache_path(path)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return data


def is_cache_stale(path: Optional[str] = None,
                   current_fingerprint: Optional[str] = None) -> bool:
    """缓存是否需要重建。

    文件不存在 → True（需重建）。
    读取失败 → True。
    指纹不一致 / 指纹缺失 → True。
    指纹一致 → False（可直接用）。
    """
    path = _resolve_cache_path(path)
    env = load_cache_envelope(path)
    if env is None:
        return True
    if int(env.get("version", 0) or 0) != PROFILE_CACHE_VERSION:
        return True
    if not current_fingerprint:
        return True
    cached = env.get("source_fingerprint") or ""
    return cached != current_fingerprint


# ===================== 增量更新 =====================
def update_profile_incremental(existing: Any, new_game_data: Any,
                               *, player_profile_cls: Optional[type] = None) -> Any:
    """把一盘新棋的画像摘要增量并入已有画像。

    本模块只负责「装配 + 委派」：真正的聚合统计由 ``player_profile.py`` 完成
    （如 ``PlayerProfile.merge_game``）。这里做的是：
      1. 若 existing 提供了 ``merge_game(new_game_data)`` 方法 → 委派之；
      2. 否若 PlayerProfile 提供 ``from_summaries([...])`` → 收集已有最近趋势
         + 新摘要重建（退化为批量重建，语义上仍正确）；
      3. 都没有 → 退化为返回 new_game_data 所代表的「全新画像」（最小可用）。

    参数：
      existing:            已有 PlayerProfile（可为 None）。
      new_game_data:       新一局的画像摘要，通常是 dict（index.json 的 profileSummary）
                           或 PlayerProfile 子结构。
      player_profile_cls:  可选，显式传入 PlayerProfile 类（测试便于注入桩）。
                           默认惰性 ``import player_profile``。

    返回更新后的 PlayerProfile（不修改入参的不可变语义；若 merge_game 是原地
    修改则返回它本身）。
    """
    # 1) 委派给画像自带的增量合并方法（推荐路径）
    if existing is not None:
        merge = getattr(existing, "merge_game", None)
        if callable(merge):
            try:
                result = merge(new_game_data)
                return result if result is not None else existing
            except Exception:
                pass
        update = getattr(existing, "update_with_game", None)
        if callable(update):
            try:
                result = update(new_game_data)
                return result if result is not None else existing
            except Exception:
                pass

    cls = (player_profile_cls if player_profile_cls is not None
           else _import_player_profile())
    # 2) 退化为「收集已有摘要 + 新摘要」批量重建
    if cls is not None:
        from_summaries = getattr(cls, "from_summaries", None)
        if callable(from_summaries):
            try:
                existing_summaries = _extract_recent_summaries(existing)
                new_summary = _normalize_game_summary(new_game_data)
                combined = list(existing_summaries)
                if new_summary:
                    combined.append(new_summary)
                if combined:
                    rebuilt = from_summaries(combined)
                    if rebuilt is not None:
                        return rebuilt
            except Exception:
                pass

    # 3) 极端兜底：把 new_game_data 当成新的画像对象直接返回
    if new_game_data is not None and not isinstance(new_game_data, dict):
        return new_game_data
    return existing


def _extract_recent_summaries(profile: Any) -> list[dict]:
    """从画像对象上尽量抠出可参与重建的「每局摘要」列表。"""
    for key in ("recent_summaries", "recentSummaries", "source_summaries",
                "game_summaries", "gameSummaries"):
        v = getattr(profile, key, None)
        if isinstance(v, list):
            return [x for x in v if isinstance(x, dict)]
    d = _profile_to_dict(profile)
    for key in ("recent_summaries", "recentSummaries", "source_summaries",
                "game_summaries", "gameSummaries"):
        v = d.get(key)
        if isinstance(v, list):
            return [x for x in v if isinstance(x, dict)]
    # 从 recent_trend 里抠（每点若带 summary）
    trend = d.get("recent_trend") or d.get("recentTrend")
    if isinstance(trend, list):
        out = []
        for pt in trend:
            if isinstance(pt, dict) and pt.get("summary"):
                out.append(pt["summary"])
        if out:
            return out
    return []


def _normalize_game_summary(new_game_data: Any) -> Optional[dict]:
    """把多种形态的「新局数据」统一成可重建用的 dict 摘要。"""
    if new_game_data is None:
        return None
    if isinstance(new_game_data, dict):
        # 可能是 envelope 形式 {"profile_summary": {...}}
        for k in ("profile_summary", "profileSummary"):
            if isinstance(new_game_data.get(k), dict):
                return new_game_data[k]
        return new_game_data
    # 画面对象 → 序列化
    d = _profile_to_dict(new_game_data)
    return d if d else None


# ===================== 从棋局库重建 =====================
def _read_index_records(index_path: str) -> list[dict]:
    """读取 ``index.json`` 的 records，任何错误都返回 []。"""
    if not os.path.exists(index_path):
        return []
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    records = data.get("records")
    if not isinstance(records, list):
        return []
    return [r for r in records if isinstance(r, dict)]


def _collect_profile_summaries(records: list[dict], limit: int) -> tuple[list[dict], list[dict]]:
    """从 records 抽出 (用于重建的摘要列表, 用于指纹的原始项列表)。

    取「含 profileSummary 且最近活动」的前 ``limit`` 条，按最近活动倒序。
    返回的两个列表顺序一致，便于同时重建画像与算指纹。
    """
    def activity_key(r: dict) -> str:
        return (r.get("lastOpenedAt") or r.get("updatedAt")
                or r.get("importedAt", ""))

    with_summary = [r for r in records
                    if isinstance(r.get("profileSummary"), dict)
                    or isinstance(r.get("profile_summary"), dict)]
    with_summary.sort(key=activity_key, reverse=True)
    if limit and limit > 0:
        with_summary = with_summary[:limit]

    summaries = []
    fingerprint_items = []
    for r in with_summary:
        ps = r.get("profileSummary") or r.get("profile_summary") or {}
        summaries.append(ps)
        fingerprint_items.append({
            "id": r.get("id"),
            "profile_summary": ps,
            "updated_at": (
                ps.get("updatedAt") or ps.get("updated_at")
                or r.get("updatedAt", "")),
            "profile_side": r.get("profileSide") or r.get("profile_side"),
        })
    # player_profile 的趋势入口要求从旧到新。
    summaries.reverse()
    fingerprint_items.reverse()
    return summaries, fingerprint_items


def rebuild_profile_from_library(index_path: Optional[str] = None,
                                 cache_path: Optional[str] = None,
                                 *, window_games: int = DEFAULT_WINDOW_GAMES,
                                 player_profile_cls: Optional[type] = None,
                                 save: bool = True) -> Any:
    """从棋局库 ``index.json`` 重建长期画像并（默认）写回缓存。

    流程（§14.3、§27.6）：
      1. 读取 index.json 的 records；
      2. 取含 ``profileSummary`` 的最近 ``window_games`` 条；
      3. 委派 ``PlayerProfile.from_summaries([...])`` 聚合；
      4. 计算指纹，``save_profile`` 写回缓存；
      5. 返回 PlayerProfile 实例（无可用数据 / 画像模块缺失时返回 None）。

    任何一步失败都不抛出（优雅降级）。
    """
    index_path = _resolve_index_path(index_path)
    cache_path = _resolve_cache_path(cache_path)
    records = _read_index_records(index_path)
    summaries, fingerprint_items = _collect_profile_summaries(records, window_games)
    if not summaries:
        return None

    cls = (player_profile_cls if player_profile_cls is not None
           else _import_player_profile())
    if cls is None:
        # player_profile.py 尚未实现：无法重建，返回 None（UI 提示「尚未生成」）
        return None

    from_summaries = getattr(cls, "from_summaries", None) or getattr(cls, "from_games", None)
    if not callable(from_summaries):
        return None

    try:
        profile = from_summaries(summaries)
    except Exception:
        return None
    if profile is None:
        return None

    fingerprint = compute_source_fingerprint(fingerprint_items)
    if save:
        save_profile(profile, cache_path,
                     source_fingerprint=fingerprint,
                     window_games=window_games)
    return profile


def get_or_rebuild(index_path: Optional[str] = None,
                   cache_path: Optional[str] = None,
                   *, window_games: int = DEFAULT_WINDOW_GAMES,
                   player_profile_cls: Optional[type] = None) -> Any:
    """缓存有效则直接读，否则从棋局库重建并写回（§27.6 推荐用法）。

    返回 PlayerProfile 实例；任何失败/无数据均返回 None，不抛异常。
    """
    index_path = _resolve_index_path(index_path)
    cache_path = _resolve_cache_path(cache_path)
    records = _read_index_records(index_path)
    _summaries, fingerprint_items = _collect_profile_summaries(records, window_games)
    fingerprint = compute_source_fingerprint(fingerprint_items)

    if not is_cache_stale(cache_path, fingerprint):
        cached = load_profile(cache_path, player_profile_cls=player_profile_cls)
        if cached is not None:
            return cached

    return rebuild_profile_from_library(
        index_path, cache_path,
        window_games=window_games,
        player_profile_cls=player_profile_cls,
        save=True,
    )
