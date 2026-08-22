"""test_backup —— 每日自动备份 + 备份恢复测试（临时目录隔离，不碰真实 game_library）。

覆盖：打包内容、当日幂等、备份目录排除、临时文件排除、按份清理；
恢复链路（restore_backup）：正常恢复/损坏 zip 拒绝/pre_restore 转存/
设置原子写回/历史备份不丢/zip-slip 拒绝/非 zip 拒绝/转存滚动清理。
"""
import datetime
import json
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


def _diverge_library(lib, settings):
    """恢复前破坏现场：删棋谱、清空 index、改设置——证恢复真的回去了。"""
    os.remove(os.path.join(lib, "sgf", "a.sgf"))
    with open(os.path.join(lib, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"version": 1, "records": []}, f)
    with open(settings, "w", encoding="utf-8") as f:
        json.dump({"max_visits": 999}, f)


def _lib_state(lib, settings):
    """读回库与设置现状（棋谱在否/index 局数/设置值）。"""
    return {
        "sgf": os.path.isfile(os.path.join(lib, "sgf", "a.sgf")),
        "games": len(json.load(
            open(os.path.join(lib, "index.json"), encoding="utf-8"))
            .get("records") or []),
        "visits": json.load(open(settings, encoding="utf-8")).get("max_visits"),
    }


def test_restore_normal_roundtrip():
    """正常恢复：库+设置回到备份时刻，pre_restore 转存破坏后的现场，
    历史备份 zip 不因恢复丢失，无暂存目录残留。"""
    tmp = tempfile.mkdtemp(prefix="backup_restore_")
    try:
        lib, settings = _make_library(tmp)
        bdir = os.path.join(lib, "backups")
        zip_path = bk.create_daily_backup(
            backup_dir=bdir, library_dir=lib, settings_path=settings)
        check("前置：备份已产出", bool(zip_path))

        listing = bk.list_backups(bdir)
        check("list_backups 列出该份（最新在前）",
              len(listing) == 1 and listing[0]["ok"]
              and listing[0]["path"] == zip_path)
        check("列表含局数（index records 数）", listing[0]["games"] == 0,
              str(listing[0]["games"]))   # _make_library 的 index 是空 {}

        _diverge_library(lib, settings)
        res = bk.restore_backup(zip_path, backup_dir=bdir, library_dir=lib,
                                settings_path=settings)
        check("恢复成功返回结果", isinstance(res, dict))
        state = _lib_state(lib, settings)
        check("棋谱文件已找回", state["sgf"] is True)
        check("index 回到备份时刻", state["games"] == 0, str(state))
        check("设置回到备份时刻（200）", state["visits"] == 200)
        check("结果带 restored_games/pre_restore",
              res["restored_games"] == 0 and bool(res["pre_restore"])
              and res["settings_restored"] is True)
        check("pre_restore 转存了破坏后现场",
              os.path.isdir(res["pre_restore"])
              and not os.path.isfile(os.path.join(
                  res["pre_restore"], "sgf", "a.sgf")))
        stamp = os.path.basename(res["pre_restore"]).split(
            ".pre_restore-", 1)[-1]
        set_pre = os.path.join(
            os.path.dirname(settings),
            "user_settings.json.pre_restore-" + stamp)
        check("设置 pre_restore 副本存在且存破坏值（999）",
              os.path.isfile(set_pre)
              and json.load(open(set_pre, encoding="utf-8")).get("max_visits")
              == 999, set_pre)
        check("备份 zip 不因恢复丢失（随 backups/ 搬回新库）",
              os.path.isfile(os.path.join(bdir, os.path.basename(zip_path))))
        check("无 restore-staging 残留",
              not any(n.endswith(".restore-staging")
                      for n in os.listdir(tmp)))
        check("无 .restore.tmp 残留",
              not any(n.endswith(".restore.tmp") for n in os.listdir(tmp)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_restore_rejects_corrupt_and_unsafe():
    """坏输入必须拒绝且原库分毫不动：非 zip / CRC 损坏 / 路径逃逸 /
    无库文件。"""
    tmp = tempfile.mkdtemp(prefix="backup_reject_")
    try:
        lib, settings = _make_library(tmp)
        bdir = os.path.join(lib, "backups")
        good = bk.create_daily_backup(
            backup_dir=bdir, library_dir=lib, settings_path=settings)
        _diverge_library(lib, settings)   # 现场已破坏：恢复失败必须保持破坏态
        broken_state = _lib_state(lib, settings)

        # a) 非 zip
        notzip = os.path.join(bdir, "go-ana-backup-19990101.zip")
        with open(notzip, "wb") as f:
            f.write(b"this is not a zip at all")
        items = {i["date"]: i for i in bk.list_backups(bdir)}
        check("列表把非 zip 标为损坏（ok=False）",
              items["19990101"]["ok"] is False)
        try:
            bk.restore_backup(notzip, backup_dir=bdir, library_dir=lib,
                              settings_path=settings)
            raise AssertionError("非 zip 应被拒绝")
        except bk.RestoreError as e:
            check("非 zip 拒绝且报因", "无法打开" in str(e), str(e))

        # b) CRC 损坏：定向翻转某文件压缩数据区一字节（中段盲翻可能落在
        #    文件名等元数据上不触发校验——恢复成功=假阴性，必须打数据区）
        corrupt = os.path.join(bdir, "go-ana-backup-19990102.zip")
        with zipfile.ZipFile(good) as zf:
            zi = zf.infolist()[0]           # 第一个成员（sgf/a.sgf）
            data_start = zi.header_offset + 30 + len(zi.filename) \
                + len(zi.extra)
        raw = bytearray(open(good, "rb").read())
        raw[data_start + 1] ^= 0xFF         # 压缩数据区中部
        with open(corrupt, "wb") as f:
            f.write(bytes(raw))
        try:
            bk.restore_backup(corrupt, backup_dir=bdir, library_dir=lib,
                              settings_path=settings)
            raise AssertionError("CRC 损坏应被拒绝")
        except bk.RestoreError as e:
            check("CRC 损坏拒绝且报因",
                  any(k in str(e) for k in ("CRC", "损坏", "无法打开")),
                  str(e))

        # c) 路径逃逸（zip-slip）：手工造含 ../ 的 zip
        slip = os.path.join(bdir, "go-ana-backup-19990103.zip")
        with zipfile.ZipFile(slip, "w") as zf:
            zf.writestr("../escape.txt", "boom")
            zf.writestr("index.json", "{}")
        try:
            bk.restore_backup(slip, backup_dir=bdir, library_dir=lib,
                              settings_path=settings)
            raise AssertionError("路径逃逸应被拒绝")
        except bk.RestoreError as e:
            check("zip-slip 拒绝且报因", "非法路径" in str(e), str(e))
        check("逃逸文件未被解出到上级",
              not os.path.exists(os.path.join(tmp, "escape.txt")))

        # d) 无库文件（只有设置）
        empty = os.path.join(bdir, "go-ana-backup-19990104.zip")
        with zipfile.ZipFile(empty, "w") as zf:
            zf.writestr("user_settings.json", "{}")
        try:
            bk.restore_backup(empty, backup_dir=bdir, library_dir=lib,
                              settings_path=settings)
            raise AssertionError("无库文件应被拒绝")
        except bk.RestoreError as e:
            check("无库文件拒绝且报因", "没有棋谱库文件" in str(e), str(e))

        check("全部拒绝后原库保持拒绝前状态",
              _lib_state(lib, settings) == broken_state)
        check("拒绝路径无暂存残留",
              not any(n.endswith(".restore-staging")
                      for n in os.listdir(tmp)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_restore_missing_file_and_prune():
    """不存在的 zip 明确报错；多次恢复后 pre_restore 只留最近 2 份。"""
    tmp = tempfile.mkdtemp(prefix="backup_pr_")
    try:
        lib, settings = _make_library(tmp)
        bdir = os.path.join(lib, "backups")
        try:
            bk.restore_backup(os.path.join(bdir, "go-ana-backup-19990101.zip"),
                              backup_dir=bdir, library_dir=lib,
                              settings_path=settings)
            raise AssertionError("缺失文件应报错")
        except bk.RestoreError as e:
            check("缺失 zip 报「不存在」", "不存在" in str(e), str(e))

        # 三轮：破坏 → 恢复（同秒恢复也不撞名；每轮都转存一份 pre_restore）
        stamps = []
        for i in range(3):
            if i:
                _diverge_library(lib, settings)
            with open(os.path.join(lib, "sgf", "a.sgf"),
                      "w", encoding="utf-8") as f:
                f.write("(;GM[1];B[pd])")   # 恢复破坏的棋谱供下轮再删
            z = bk.create_daily_backup(
                backup_dir=bdir, library_dir=lib, settings_path=settings)
            if z is None:   # 当日已备：直接用既有 zip
                z = os.path.join(bdir, "go-ana-backup-%s.zip"
                                 % datetime.date.today().strftime("%Y%m%d"))
            res = bk.restore_backup(z, backup_dir=bdir, library_dir=lib,
                                    settings_path=settings)
            stamps.append(res["pre_restore"])
        pres = [n for n in os.listdir(tmp)
                if n.startswith("game_library.pre_restore-")]
        check("pre_restore 滚动保留最近 2 份", len(pres) == 2, str(pres))
        check("保留的是最新两轮（含最新）",
              sorted(pres)[-1] in {os.path.basename(s) for s in stamps[-2:]})
        spres = [n for n in os.listdir(tmp)
                 if n.startswith("user_settings.json.pre_restore-")]
        check("设置副本同样滚动保留 2 份", len(spres) == 2, str(spres))
        check("库可用（棋谱在）", os.path.isfile(
            os.path.join(lib, "sgf", "a.sgf")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    print("=" * 60)
    print(" backup 测试（每日自动备份 + 恢复）")
    print("=" * 60)
    test_backup_content_and_idempotent(); print()
    test_prune_keeps_recent(); print()
    test_empty_library_no_backup(); print()
    test_restore_normal_roundtrip(); print()
    test_restore_rejects_corrupt_and_unsafe(); print()
    test_restore_missing_file_and_prune(); print()
    print("test_backup 全部通过 ✅")
