"""test_profile_store —— 长期画像缓存持久化测试。

player_profile.py 由另一个 agent 并行实现，本测试通过 ``player_profile_cls=``
注入一个**本地桩 dataclass**，确保测试自包含、不依赖对方接口。
桩覆盖了三种 PlayerProfile 可能的形态：纯 dataclass / 带 to_dict / 带 from_dict，
用以验证 profile_store 的鸭子类型适配与优雅降级。
"""
import os
import sys
import tempfile
import shutil
import json
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import profile_store as ps


def check(name, cond, extra=""):
    print(("[CHECK] %-38s %s %s" % (name, "OK" if cond else "FAIL", extra)))
    if not cond:
        raise AssertionError(name)


# ===================== 桩 PlayerProfile =====================
# 模拟并行 agent 可能实现的接口。它同时提供 to_dict / from_dict / from_summaries
# / merge_game，覆盖 profile_store 的所有委派路径。
@dataclass
class StubProfile:
    """本地桩：字段对齐 spec §9.4 的子集，便于断言。"""
    profile_id: str = ""
    player_names: list = field(default_factory=list)
    generated_at: str = ""
    games_count: int = 0
    evaluated_moves_count: int = 0
    avg_loss: float = 0.0
    quality_distribution: dict = field(default_factory=dict)
    phase_stats: dict = field(default_factory=dict)
    trend: list = field(default_factory=list)
    strengths: list = field(default_factory=list)
    weaknesses: list = field(default_factory=list)
    recommendations: list = field(default_factory=list)
    version: int = ps.PROFILE_CACHE_VERSION

    def to_dict(self):
        return {
            "profile_id": self.profile_id,
            "player_names": list(self.player_names),
            "generated_at": self.generated_at,
            "games_count": self.games_count,
            "evaluated_moves_count": self.evaluated_moves_count,
            "avg_loss": self.avg_loss,
            "quality_distribution": dict(self.quality_distribution),
            "phase_stats": dict(self.phase_stats),
            "trend": list(self.trend),
            "strengths": list(self.strengths),
            "weaknesses": list(self.weaknesses),
            "recommendations": list(self.recommendations),
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data):
        d = data or {}
        return cls(
            profile_id=d.get("profile_id", ""),
            player_names=list(d.get("player_names") or []),
            generated_at=d.get("generated_at", ""),
            games_count=int(d.get("games_count") or 0),
            evaluated_moves_count=int(d.get("evaluated_moves_count") or 0),
            avg_loss=float(d.get("avg_loss") or 0.0),
            quality_distribution=dict(d.get("quality_distribution") or {}),
            phase_stats=dict(d.get("phase_stats") or {}),
            trend=list(d.get("trend") or []),
            strengths=list(d.get("strengths") or []),
            weaknesses=list(d.get("weaknesses") or []),
            recommendations=list(d.get("recommendations") or []),
            version=int(d.get("version") or ps.PROFILE_CACHE_VERSION),
        )

    @classmethod
    def from_summaries(cls, summaries):
        """简化聚合：平均目损 + 棋局数。"""
        summaries = [s for s in (summaries or []) if isinstance(s, dict)]
        n = len(summaries)
        if n == 0:
            return None
        losses = [float(s.get("avg_score_loss", 0.0) or 0.0) for s in summaries]
        avg = sum(losses) / n
        return cls(
            profile_id="from-library",
            games_count=n,
            evaluated_moves_count=sum(int(s.get("evaluated_moves", 0) or 0) for s in summaries),
            avg_loss=avg,
        )

    def merge_game(self, new_game_data):
        """简化增量：把新一局的目损并入均值，棋局数 +1。"""
        s = ps._normalize_game_summary(new_game_data) or {}
        prev_n = self.games_count
        prev_total = self.avg_loss * prev_n
        new_loss = float(s.get("avg_score_loss", 0.0) or 0.0)
        n = prev_n + 1
        return StubProfile(
            profile_id=self.profile_id,
            games_count=n,
            evaluated_moves_count=self.evaluated_moves_count + int(s.get("evaluated_moves", 0) or 0),
            avg_loss=(prev_total + new_loss) / n if n else 0.0,
        )


def make_profile(games=12, avg_loss=2.3):
    return StubProfile(
        profile_id="tester",
        player_names=["测试者"],
        games_count=games,
        evaluated_moves_count=games * 40,
        avg_loss=avg_loss,
        quality_distribution={"best": 10, "good": 30, "blunder": 3},
        phase_stats={"opening": {"avg_loss": 1.4}},
        trend=[{"game": 1, "avg_loss": 3.0}, {"game": 2, "avg_loss": 2.0}],
        strengths=["布局方向感好"],
        weaknesses=["中盘攻击方向"],
        recommendations=["加强中盘计算"],
    )


# ===================== 测试 1：保存/读取往返 =====================
def test_save_load_roundtrip():
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "profile_cache.json")
        original = make_profile(games=12, avg_loss=2.3)
        written = ps.save_profile(original, path,
                                  source_fingerprint="abc123",
                                  window_games=30)
        check("save_profile 返回绝对路径", os.path.abspath(written) == written, written)
        check("缓存文件已生成", os.path.exists(path))

        # 信封字段
        with open(path, "r", encoding="utf-8") as f:
            env = json.load(f)
        check("信封 version", env["version"] == ps.PROFILE_CACHE_VERSION, str(env["version"]))
        check("信封 games_count", env["games_count"] == 12, str(env["games_count"]))
        check("信封 fingerprint", env["source_fingerprint"] == "abc123")
        check("信封 window_games", env["window_games"] == 30)
        check("信封 generated_at 非空", bool(env["generated_at"]))
        check("信封含 profile dict", isinstance(env["profile"], dict))

        loaded = ps.load_profile(path, player_profile_cls=StubProfile)
        check("load 返回 StubProfile", isinstance(loaded, StubProfile), str(type(loaded)))
        check("games_count 往返一致", loaded.games_count == 12, str(loaded.games_count))
        check("avg_loss 往返一致", abs(loaded.avg_loss - 2.3) < 1e-9, str(loaded.avg_loss))
        check("quality_distribution 往返", loaded.quality_distribution["blunder"] == 3)
        check("trend 长度往返", len(loaded.trend) == 2)
        check("strengths 往返", loaded.strengths == ["布局方向感好"])
        print("[PASS] test_save_load_roundtrip\n")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ===================== 测试 2：版本管理 / 前向兼容 =====================
def test_version_handling():
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "profile_cache.json")

        # 缺少 version 字段的旧缓存 → 视为当前版本，仍可读
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"generated_at": "2020-01-01", "profile": {"games_count": 5}}, f)
        loaded = ps.load_profile(path, player_profile_cls=StubProfile)
        check("缺 version 仍可读", isinstance(loaded, StubProfile) and loaded.games_count == 5)

        # 比当前更新的版本号 → 前向兼容：标记 _cache_version_newer 仍读取
        future = ps.PROFILE_CACHE_VERSION + 5
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"version": future, "profile": {"games_count": 7}}, f)
        loaded = ps.load_profile(path, player_profile_cls=StubProfile)
        check("更新版本号仍读取", isinstance(loaded, StubProfile) and loaded.games_count == 7)
        check("标记前向兼容", getattr(loaded, "_cache_version_newer", False) is True)

        # 当前版本号 → 不标记
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"version": ps.PROFILE_CACHE_VERSION,
                       "profile": {"games_count": 9}}, f)
        loaded = ps.load_profile(path, player_profile_cls=StubProfile)
        check("当前版本不标记前向", getattr(loaded, "_cache_version_newer", False) is False)
        print("[PASS] test_version_handling\n")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ===================== 测试 3：缺失字段降级 =====================
def test_missing_fields_degradation():
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "profile_cache.json")

        # 1) 文件不存在 → None，不抛异常
        loaded = ps.load_profile(os.path.join(tmp, "nope.json"), player_profile_cls=StubProfile)
        check("文件不存在返回 None", loaded is None)

        # 2) JSON 损坏 → None，不抛异常
        with open(path, "w", encoding="utf-8") as f:
            f.write("{这不是合法 json")
        loaded = ps.load_profile(path, player_profile_cls=StubProfile)
        check("JSON 损坏返回 None", loaded is None)

        # 3) 旧画像 dict 缺大量字段 → 缺字段补默认值，能用的字段保留
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "profile": {"games_count": 3}}, f)
        loaded = ps.load_profile(path, player_profile_cls=StubProfile)
        check("缺字段不报错", isinstance(loaded, StubProfile))
        check("保留存在的字段", loaded.games_count == 3)
        check("缺失字段取默认值", loaded.avg_loss == 0.0 and loaded.trend == []
              and loaded.quality_distribution == {})

        # 4) 缓存信封里有未知字段 → 不报错
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "unknown_top": "x",
                       "profile": {"games_count": 1, "unknown_inner": "y"}}, f)
        loaded = ps.load_profile(path, player_profile_cls=StubProfile)
        check("未知字段不报错", isinstance(loaded, StubProfile) and loaded.games_count == 1)

        # 5) player_profile_cls=None 且 player_profile 模块不存在 → 返回原始 dict
        #    （模拟并行模块尚未实现）
        loaded = ps.load_profile(path, player_profile_cls=None)
        # 真实环境会 import player_profile；这里手动屏蔽以模拟缺失
        orig_import = ps._import_player_profile
        ps._import_player_profile = lambda: None
        try:
            loaded = ps.load_profile(path, player_profile_cls=None)
        finally:
            ps._import_player_profile = orig_import
        check("画像模块缺失返回 dict", isinstance(loaded, dict)
              and loaded.get("games_count") == 1)
        print("[PASS] test_missing_fields_degradation\n")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ===================== 测试 4：增量更新 =====================
def test_incremental_update():
    base = make_profile(games=10, avg_loss=3.0)
    # 新一局目损 1.0
    new_summary = {"avg_score_loss": 1.0, "evaluated_moves": 45}

    updated = ps.update_profile_incremental(base, new_summary)
    check("增量返回 StubProfile", isinstance(updated, StubProfile))
    # (3.0*10 + 1.0)/11 ≈ 2.818
    check("增量棋局数 +1", updated.games_count == 11, str(updated.games_count))
    check("增量均值正确", abs(updated.avg_loss - (3.0 * 10 + 1.0) / 11) < 1e-9,
          str(updated.avg_loss))
    check("增量 evaluated_moves 累加", updated.evaluated_moves_count == 10 * 40 + 45)

    # 入参 None + 摘要 → 走 from_summaries 重建
    updated2 = ps.update_profile_incremental(None, new_summary,
                                             player_profile_cls=StubProfile)
    check("None 入参走重建", isinstance(updated2, StubProfile) and updated2.games_count == 1)

    # 入参 + 入参都 None → 返回 None / 不抛
    check("双 None 安全",
          ps.update_profile_incremental(None, None, player_profile_cls=StubProfile) is None)
    print("[PASS] test_incremental_update\n")


# ===================== 测试 5：从棋局库重建 + 指纹/缓存失效 =====================
def test_rebuild_from_library():
    tmp = tempfile.mkdtemp()
    try:
        index_path = os.path.join(tmp, "index.json")
        cache_path = os.path.join(tmp, "profile_cache.json")

        # 造 3 条 record，2 条带 profileSummary
        index = {"version": 1, "records": [
            {"id": "g1", "name": "g1", "updatedAt": "2026-06-01",
             "profileSummary": {"version": 1, "avg_score_loss": 4.0,
                                "evaluated_moves": 50, "blunder_count": 3}},
            {"id": "g2", "name": "g2", "updatedAt": "2026-06-02",
             "profileSummary": {"version": 1, "avg_score_loss": 2.0,
                                "evaluated_moves": 50, "blunder_count": 1}},
            {"id": "g3", "name": "g3", "updatedAt": "2026-06-03"},  # 无画像摘要
        ]}
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(index, f)

        # 重建
        profile = ps.rebuild_profile_from_library(
            index_path, cache_path, window_games=30,
            player_profile_cls=StubProfile, save=True)
        check("重建返回 StubProfile", isinstance(profile, StubProfile))
        check("重建棋局数=2（仅含画像摘要）", profile.games_count == 2, str(profile.games_count))
        check("重建均值=(4+2)/2=3", abs(profile.avg_loss - 3.0) < 1e-9, str(profile.avg_loss))
        check("重建写回缓存文件", os.path.exists(cache_path))

        # 指纹：基于含摘要的 2 条
        env = ps.load_cache_envelope(cache_path)
        check("缓存信封可读", env is not None and env.get("source_fingerprint"))
        fp = env["source_fingerprint"]

        # 指纹一致 → 缓存不失效
        check("指纹一致不失效", ps.is_cache_stale(cache_path, fp) is False)

        # 指纹不一致 → 失效
        check("指纹不一致失效", ps.is_cache_stale(cache_path, "different") is True)
        old_env = dict(env)
        old_env["version"] = ps.PROFILE_CACHE_VERSION - 1
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(old_env, f)
        check("缓存算法版本变化失效", ps.is_cache_stale(cache_path, fp) is True)

        # 空 index → rebuild 返回 None
        empty_index = os.path.join(tmp, "empty.json")
        with open(empty_index, "w", encoding="utf-8") as f:
            json.dump({"records": []}, f)
        check("空 index 重建返回 None",
              ps.rebuild_profile_from_library(empty_index, cache_path,
                                              player_profile_cls=StubProfile,
                                              save=False) is None)

        # get_or_rebuild：缓存有效时直接读，不触发重建副作用
        reloaded = ps.get_or_rebuild(index_path, cache_path,
                                     player_profile_cls=StubProfile)
        check("get_or_rebuild 命中缓存", isinstance(reloaded, StubProfile)
              and reloaded.games_count == 2)

        # index 变化（加一条带摘要）→ 指纹变 → get_or_rebuild 重建
        index["records"].append(
            {"id": "g4", "name": "g4", "updatedAt": "2026-06-04",
             "profileSummary": {"version": 1, "avg_score_loss": 1.0,
                                "evaluated_moves": 40}})
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(index, f)
        rebuilt = ps.get_or_rebuild(index_path, cache_path,
                                    player_profile_cls=StubProfile)
        check("index 变化后重建", isinstance(rebuilt, StubProfile)
              and rebuilt.games_count == 3, str(getattr(rebuilt, "games_count", None)))

        # index.json 损坏 → 不抛异常，返回 None
        with open(index_path, "w", encoding="utf-8") as f:
            f.write("not json{{{")
        check("index 损坏不抛异常",
              ps.rebuild_profile_from_library(index_path, cache_path,
                                              player_profile_cls=StubProfile,
                                              save=False) is None)
        print("[PASS] test_rebuild_from_library\n")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ===================== 测试 6：原子写 + to_dict 缺失降级 =====================
def test_atomic_write_and_dataclass_fallback():
    tmp = tempfile.mkdtemp()
    try:
        # 不带 to_dict/from_dict 的纯 dataclass → profile_store 应退化为 asdict
        @dataclass
        class PlainProfile:
            games_count: int = 0
            avg_loss: float = 0.0
            nested: dict = field(default_factory=lambda: {"a": 1})

        p = PlainProfile(games_count=4, avg_loss=1.5)
        path = os.path.join(tmp, "profile_cache.json")
        ps.save_profile(p, path)
        with open(path, "r", encoding="utf-8") as f:
            env = json.load(f)
        check("纯 dataclass asdict 降级序列化",
              env["profile"]["games_count"] == 4 and env["profile"]["nested"] == {"a": 1})

        # 原子写：写过程中无残留 tmp 文件
        check("无残留 tmp", not os.path.exists(path + ".tmp"))
        print("[PASS] test_atomic_write_and_dataclass_fallback\n")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    test_save_load_roundtrip()
    test_version_handling()
    test_missing_fields_degradation()
    test_incremental_update()
    test_rebuild_from_library()
    test_atomic_write_and_dataclass_fallback()
    print("==== 所有 profile_store 测试通过 ====")


if __name__ == "__main__":
    main()
