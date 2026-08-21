"""test_backup —— 每日自动备份测试（临时目录隔离，不碰真实 game_library）。

覆盖：打包内容、当日幂等、备份目录排除、临时文件排除、按份清理。
"""
import os
import shutil
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import backup as bk


def check(name, cond, extra=""):
    print("[CHECK] %-42s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        raise AssertionError(name)


def _make_library(root):
    """搭一个最小 game_library：sgf + projects + index + 一份设置。"""
    lib = os.path.join(root, "game_library")
    os.makedirs(os.path.join(lib, "sgf"))
    os.makedirs(os.path.join(lib, "projects"))
    with open(os.path.join(lib, "sgf", "a.sgf"), "w", encoding="utf-8") as f:
        f.write("(;GM[1];B[pd])")
    with open(os.path.join(lib, "projects", "a.kga.json"), "w",
              encoding="utf-8") as f:
        f.write("{}")
    with open(os.path.join(lib, "index.json"), "w", encoding="utf-8") as f:
        f.write("{}")
    with open(os.path.join(lib, "index.json.tmp"), "w", encoding="utf-8") as f:
        f.write("")   # 应被排除
    settings = os.path.join(root, "user_settings.json")
    with open(settings, "w", encoding="utf-8") as f:
        f.write('{"max_visits": 200}')
    return lib, settings


def test_backup_content_and_idempotent():
    tmp = tempfile.mkdtemp(prefix="backup_test_")
    try:
        lib, settings = _make_library(tmp)
        bdir = os.path.join(lib, "backups")
        first = bk.create_daily_backup(
            backup_dir=bdir, library_dir=lib, settings_path=settings)
        check("返回 zip 路径", first is not None and os.path.isfile(first))
        with zipfile.ZipFile(first) as zf:
            names = set(zf.namelist())
        check("包含库内文件",
              {"sgf/a.sgf", "projects/a.kga.json", "index.json",
               "user_settings.json"} <= names, str(sorted(names))[:4])
        check("排除 .tmp 与备份目录自身",
              not any(n.endswith(".tmp") or n.startswith("backups/")
                      for n in names))
        # 当日幂等：再次调用不产生第二份
        again = bk.create_daily_backup(
            backup_dir=bdir, library_dir=lib, settings_path=settings)
        zips = [n for n in os.listdir(bdir) if n.endswith(".zip")]
        check("同日只备一次", again == first and len(zips) == 1, str(zips))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_prune_keeps_recent():
    tmp = tempfile.mkdtemp(prefix="backup_test_")
    try:
        bdir = os.path.join(tmp, "backups")
        os.makedirs(bdir)
        for day in range(1, 21):   # 20 份历史备份
            with open(os.path.join(
                    bdir, "go-ana-backup-202608%02d.zip" % day), "wb") as f:
                f.write(b"x")
        bk._prune(bdir, keep=14)
        left = sorted(n for n in os.listdir(bdir) if n.endswith(".zip"))
        check("清理后保留 14 份", len(left) == 14, str(len(left)))
        check("保留最新的（08-20 在）",
              "go-ana-backup-20260820.zip" in left
              and "go-ana-backup-20260801.zip" not in left)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_empty_library_no_backup():
    tmp = tempfile.mkdtemp(prefix="backup_test_")
    try:
        empty = os.path.join(tmp, "game_library")
        os.makedirs(empty)
        result = bk.create_daily_backup(
            backup_dir=os.path.join(empty, "backups"), library_dir=empty,
            settings_path="")   # 空串=不包含设置文件（None 会回落真实路径）
        check("空库不产出备份", result is None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    print("=" * 60)
    print(" backup 测试（每日自动备份）")
    print("=" * 60)
    test_backup_content_and_idempotent(); print()
    test_prune_keeps_recent(); print()
    test_empty_library_no_backup(); print()
    print("test_backup 全部通过 ✅")
