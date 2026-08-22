"""test_style_store —— 棋风画像缓存持久化 + 默认路径调用时解析回归。

style_store 此前无专属测试；W29 审查发现其默认路径在导入期固化进
def 默认参数（set_path/game_library 重定向对默认调用无效，测试可能
写穿生产 style_profile_cache.json），修复后补上回归网。
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import style_store
from style_store import load_style_cache, save_style_cache
from style_profile import StyleProfile
from growth_path import GrowthPath


def check(name, cond, extra=""):
    print("[CHECK] %-40s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        raise AssertionError(name)


def _artifacts():
    profile = StyleProfile()
    profile.source_game_ids = ["g1", "g2"]
    growth = GrowthPath(main_goal="厚势导向")
    return profile, growth


def run_roundtrip():
    tmp = tempfile.mkdtemp(prefix="style-store-")
    try:
        path = os.path.join(tmp, "style_profile_cache.json")
        profile, growth = _artifacts()
        data = save_style_cache(profile, growth, path)
        check("缓存文件已生成", os.path.exists(path))
        check("信封 version", data["version"] == style_store.VERSION)
        check("信封含 profileId", data.get("profileId") is not None)

        loaded = load_style_cache(path)
        check("load 返回 dict", isinstance(loaded, dict))
        check("roundtrip profileId 一致",
              loaded.get("profileId") == data.get("profileId"))
        check("不存在的缓存返回 None",
              load_style_cache(os.path.join(tmp, "nope.json")) is None)
        print("[PASS] run_roundtrip")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def run_default_path_redirection():
    """W29 审查回归：默认路径必须调用时解析（set_path/gl 派生生效）。"""
    import hashlib
    import game_library as gl

    def _digest(path):
        if not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    real = style_store.default_path()
    before = _digest(real)
    tmp = tempfile.mkdtemp(prefix="style-redirect-")
    try:
        check("save_style_cache 默认参数未固化",
              style_store.save_style_cache.__defaults__[0] is None)
        check("load_style_cache 默认参数未固化",
              style_store.load_style_cache.__defaults__[0] is None)

        redirected = os.path.join(tmp, "style_profile_cache.json")
        style_store.set_path(redirected)
        try:
            profile, growth = _artifacts()
            save_style_cache(profile, growth)
            check("set_path 重定向写入生效", os.path.exists(redirected))
            check("默认读取命中重定向位置", load_style_cache() is not None)
            check("生产缓存未被触碰", _digest(real) == before)
        finally:
            style_store.set_path(None)
        check("set_path(None) 恢复默认",
              style_store.get_path() == style_store.default_path())

        gl_orig = gl.LIBRARY_DIR
        gl.LIBRARY_DIR = tmp
        try:
            check("默认路径跟随 game_library.LIBRARY_DIR",
                  style_store.get_path() == redirected)
        finally:
            gl.LIBRARY_DIR = gl_orig
        print("[PASS] run_default_path_redirection")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
        style_store.set_path(None)


if __name__ == "__main__":
    run_roundtrip()
    run_default_path_redirection()
    print("test_style_store: PASS")
