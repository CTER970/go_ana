"""backup —— 学习数据每日自动备份（启动时后台执行）+ 备份恢复。

背景：错题本/画像/LearningEvent 是数月积累的个人学习资产，一次误删记录
或磁盘问题就全部归零（index.json 的 .bak 只防写入中断，防不了目录级
灾难）。本模块在应用启动时把 game_library/ 与 user_settings.json 打包成
当日 zip，保留最近 keep 份：

- 按日期幂等：同一天只备一次（双开/重启不重复打包）；
- 先写 .tmp 再 os.replace（与项目其他持久化同一原子约定）；
- 备份目录本身排除在外，避免套娃膨胀；
- 任何失败静默返回 None——备份问题不允许阻断启动。

恢复（restore_backup）由「备份与恢复」窗口调用：全量校验通过才动手，
现库先整体转存为 pre_restore 副本再原子替换——校验失败/zip 损坏/空间
不足时原库分毫不动。

体量预期：个人棋局库为 MB 级（SGF+JSON），每日 zip 成本可忽略；
若未来库显著变大（>数百 MB），再考虑增量方案。
"""
from __future__ import annotations

import datetime
import json
import os
import shutil
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))

LIBRARY_DIR = os.path.join(HERE, "game_library")
SETTINGS_PATH = os.path.join(HERE, "user_settings.json")
BACKUP_DIR = os.path.join(LIBRARY_DIR, "backups")
KEEP = 14

_state = {"enabled": True}


def set_enabled(enabled):
    """后台备份总开关（无头测试置 False，防测试触发真实库备份）。"""
    _state["enabled"] = bool(enabled)


def create_daily_backup(backup_dir=None, library_dir=None,
                        settings_path=None, keep=KEEP):
    """创建当日备份（已存在则跳过），返回 zip 路径或 None。

    纯函数式：路径全部可注入，测试用临时目录，不碰真实数据。
    """
    backup_dir = BACKUP_DIR if backup_dir is None else backup_dir
    library_dir = LIBRARY_DIR if library_dir is None else library_dir
    # 注意 None=用默认路径；传空串可表达"不包含设置文件"（测试用）
    settings_path = SETTINGS_PATH if settings_path is None else settings_path
    if not os.path.isdir(library_dir):
        return None
    stamp = datetime.date.today().strftime("%Y%m%d")
    dest = os.path.join(backup_dir, "go-ana-backup-%s.zip" % stamp)
    if os.path.exists(dest):
        return dest   # 今日已备（幂等：双开/当日重启不重复）
    files = _collect_files(library_dir, exclude_dir=backup_dir)
    if settings_path and os.path.isfile(settings_path):
        files.append(settings_path)
    if not files:
        return None
    try:
        os.makedirs(backup_dir, exist_ok=True)
        tmp = dest + ".tmp"
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in files:
                arcname = _arc_name(path, library_dir, settings_path)
                zf.write(path, arcname)
        os.replace(tmp, dest)
    except OSError:
        return None
    _prune(backup_dir, keep)
    return dest


def _collect_files(root, exclude_dir=None):
    """收集 root 下全部常规文件（排除备份目录与临时文件）。"""
    exclude_dir = os.path.abspath(exclude_dir) if exclude_dir else None
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        if exclude_dir and os.path.abspath(dirpath) == exclude_dir:
            dirnames[:] = []
            continue
        for fname in filenames:
            if fname.endswith((".tmp", ".bak", ".old")):
                continue
            out.append(os.path.join(dirpath, fname))
    return out


def _arc_name(path, library_dir, settings_path):
    """zip 内路径：库内文件相对 game_library/；设置文件带前缀防撞名。"""
    if os.path.abspath(path) == os.path.abspath(settings_path):
        return "user_settings.json"
    return os.path.relpath(path, library_dir)


def _prune(backup_dir, keep):
    """只保留最近 keep 份备份（按文件名日期排序，旧者删）。

    单份被占用（杀毒扫描/编辑器打开句柄）只跳过该份、不中断整批——
    次日启动重试即可清掉（W33：外层 try 吞掉 PermissionError 会让
    其余待删件全部滞留）。
    """
    try:
        names = sorted(
            n for n in os.listdir(backup_dir)
            if n.startswith("go-ana-backup-") and n.endswith(".zip"))
        for name in names[:-keep] if keep > 0 else []:
            try:
                os.remove(os.path.join(backup_dir, name))
            except OSError:
                pass   # 该份暂被占用：跳过，次日再清
    except OSError:
        pass


def start_background_daily_backup():
    """应用启动入口：后台线程执行当日备份，不阻塞 UI。

    daemon 线程 + 全静默——备份失败不影响使用（下次启动再试）；
    无头测试环境由 set_enabled(False) 关闭（adversarial_harness 统一处理）。
    """
    if not _state["enabled"]:
        return
    import threading

    def _work():
        try:
            create_daily_backup()
        except Exception:
            pass

    threading.Thread(target=_work, daemon=True, name="daily-backup").start()


# ===================== 备份恢复（数据安全闭环的另一半）=====================

class RestoreError(Exception):
    """恢复失败（携带面向用户的中文原因）。

    约定：校验/准备阶段抛出时原库未做任何改动；只有消息里明示
    「库已恢复」的极晚期失败（如设置文件被占用）例外。
    """


def list_backups(backup_dir=None):
    """列出可用备份（按文件名日期降序 = 最新在前）。

    轻量探测：只开 zip 读 index.json 估算局数，不做全量 CRC——
    完整校验在恢复动作前由 restore_backup 做。返回
    [{"path", "date", "size", "games"(int|None), "ok"(bool)}]；
    ok=False 表示连轻量探测都失败（打不开/无条目），恢复时会被拒绝。
    """
    backup_dir = BACKUP_DIR if backup_dir is None else backup_dir
    try:
        names = os.listdir(backup_dir)
    except OSError:
        return []
    out = []
    for name in sorted(names, reverse=True):
        if not (name.startswith("go-ana-backup-") and name.endswith(".zip")):
            continue
        path = os.path.join(backup_dir, name)
        item = {"path": path, "date": name[len("go-ana-backup-"):-4],
                "size": 0, "games": None, "ok": False}
        try:
            item["size"] = os.path.getsize(path)
            with zipfile.ZipFile(path) as zf:
                zf.namelist()          # 打不开（BadZipFile）即 ok=False
                item["games"] = _index_game_count(zf)
                item["ok"] = True
        except (OSError, zipfile.BadZipFile, KeyError, ValueError):
            pass
        out.append(item)
    return out


def _index_game_count(zf):
    """从 zip 内 index.json 数局数；index 缺失或整体不可解析返回 None（不猜）。
    dict 但无 records 键 = 0 局（空库形态，如实报 0）。"""
    try:
        if "index.json" not in zf.namelist():
            return None
        data = json.loads(zf.read("index.json").decode("utf-8"))
        if not isinstance(data, dict):
            return None
        recs = data.get("records")
        return len(recs) if isinstance(recs, list) else 0
    except (OSError, zipfile.BadZipFile, ValueError, UnicodeDecodeError):
        return None


def _unsafe_arc_names(names):
    """zip 内路径逃逸检查（zip-slip）：绝对路径/盘符/.. 一律拒绝。"""
    bad = []
    for name in names:
        pure = name.replace("\\", "/")
        parts = pure.split("/")
        if (pure.startswith("/") or pure.startswith("..")
                or ":" in parts[0] or ".." in parts):
            bad.append(name)
    return bad


def _is_inside(child, parent):
    """child 路径是否位于 parent 目录内（跨盘符/非法路径返回 False）。"""
    try:
        return os.path.commonpath(
            [os.path.abspath(child), os.path.abspath(parent)]) \
            == os.path.abspath(parent)
    except ValueError:
        return False


def restore_backup(zip_path, backup_dir=None, library_dir=None,
                   settings_path=None, keep_pre_restore=2):
    """从备份 zip 恢复棋谱库与设置（任一步失败都不动原库）。

    安全顺序：
      1. 全量校验：文件在/可打开/CRC 完好/无路径逃逸/有库文件/index 可解析；
      2. 磁盘余量预检（解压所需大小 ×1.1 余量）；
      3. 解包到同级暂存目录（同卷保证改名原子性）；
      4. 现库整体转存为 ``<library>.pre_restore-<时间戳>``（回滚锚点），
         backups/ 目录搬进暂存目录——历史备份不因恢复而丢；
      5. 暂存目录改名顶上（失败则把 pre_restore 改回原位回滚）；
      6. 设置文件同样先转存副本，再 .tmp + os.replace 原子写回。

    成功返回 {"restored_games"(int|None), "pre_restore"(str|None),
    "settings_restored"(bool)}；失败抛 RestoreError（原因自含后果说明）。
    """
    backup_dir = BACKUP_DIR if backup_dir is None else backup_dir
    library_dir = LIBRARY_DIR if library_dir is None else library_dir
    settings_path = SETTINGS_PATH if settings_path is None else settings_path

    # ---- 1) 全量校验（此段任何失败：原库未动）----
    if not zip_path or not os.path.isfile(zip_path):
        raise RestoreError("备份文件不存在：%s" % zip_path)
    try:
        zf = zipfile.ZipFile(zip_path)
    except (OSError, zipfile.BadZipFile):
        raise RestoreError("备份文件无法打开（不是有效的 zip 备份）。")
    try:
        try:
            bad_member = zf.testzip()
        except zipfile.BadZipFile:   # 局部头损坏连校验都进行不下去
            raise RestoreError(
                "备份文件损坏（无法校验），已放弃恢复，原库未改动。")
        if bad_member is not None:
            raise RestoreError(
                "备份文件损坏（%s CRC 校验失败），已放弃恢复，原库未改动。"
                % os.path.basename(bad_member))
        names = zf.namelist()
        bad_paths = _unsafe_arc_names(names)
        if bad_paths:
            raise RestoreError(
                "备份内含非法路径（%s…），疑似被篡改或损坏，已拒绝恢复。"
                % bad_paths[0])
        lib_names = [n for n in names
                     if n != "user_settings.json" and not n.startswith("backups/")]
        if not lib_names:
            raise RestoreError("备份内没有棋谱库文件，无法恢复。")
        if "index.json" in names:
            try:
                data = json.loads(zf.read("index.json").decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                raise RestoreError(
                    "备份内的 index.json 无法解析，备份可能损坏，已放弃恢复。")
            if not isinstance(data, dict):
                raise RestoreError(
                    "备份内的 index.json 结构异常，已放弃恢复。")
        restored_games = _index_game_count(zf)

        # ---- 2) 空间预检 ----
        need = sum(int(zi.file_size or 0) for zi in zf.infolist())
        try:
            free = shutil.disk_usage(os.path.dirname(os.path.abspath(
                library_dir)) or ".").free
        except OSError:
            free = None
        if free is not None and free < int(need * 1.1) + (1 << 20):
            raise RestoreError(
                "磁盘空间不足（约需 %.1f MB，剩余 %.1f MB），已放弃恢复，"
                "原库未改动。" % (need / 1048576.0, free / 1048576.0))

        # ---- 3) 解包到暂存目录（同卷 sibling，保证后续改名原子）----
        staging = library_dir + ".restore-staging"
        shutil.rmtree(staging, ignore_errors=True)
        try:
            os.makedirs(staging)
            for member in lib_names:
                zf.extract(member, staging)
        except (OSError, zipfile.BadZipFile) as e:
            shutil.rmtree(staging, ignore_errors=True)
            raise RestoreError(
                "解包备份失败（%s），原库未改动。" % e)

        # 设置先落到 .tmp（不做最终替换，等库替换成功后再顶上）
        settings_tmp = None
        if settings_path and "user_settings.json" in names:
            settings_tmp = settings_path + ".restore.tmp"
            try:
                with open(settings_tmp, "wb") as f:
                    f.write(zf.read("user_settings.json"))
            except OSError as e:
                shutil.rmtree(staging, ignore_errors=True)
                if os.path.exists(settings_tmp):
                    try:
                        os.remove(settings_tmp)
                    except OSError:
                        pass
                raise RestoreError(
                    "写入设置临时文件失败（%s），原库与设置均未改动。" % e)
    finally:
        zf.close()

    # ---- 4) 现库转存 pre_restore（backups 目录先搬进暂存目录）----
    # 只在备份目录确在库内时搬移（生产形态 game_library/backups/）——
    # 否则历史备份会随旧库一起被挪进 pre_restore，最终被清理掉。
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    pre_restore = library_dir + ".pre_restore-" + stamp
    _n = 1
    while os.path.exists(pre_restore):   # 同秒多次恢复不撞名
        pre_restore = "%s.pre_restore-%s-%d" % (library_dir, stamp, _n)
        _n += 1
    if (backup_dir and _is_inside(backup_dir, library_dir)
            and os.path.isdir(backup_dir)):
        moved = []
        try:
            os.makedirs(os.path.join(staging, "backups"))
            for fname in os.listdir(backup_dir):
                shutil.move(os.path.join(backup_dir, fname),
                            os.path.join(staging, "backups", fname))
                moved.append(fname)
        except OSError:
            for fname in moved:   # 尽力搬回（单件被占用则留在暂存目录一并清）
                try:
                    shutil.move(os.path.join(staging, "backups", fname),
                                os.path.join(backup_dir, fname))
                except OSError:
                    pass
            shutil.rmtree(staging, ignore_errors=True)
            raise RestoreError(
                "备份目录被占用无法搬移，已放弃恢复，原库未改动。")

    had_library = os.path.isdir(library_dir)
    if had_library:
        try:
            os.rename(library_dir, pre_restore)
        except OSError as e:
            shutil.rmtree(staging, ignore_errors=True)
            raise RestoreError(
                "转存当前库失败（%s），原库未改动。" % e)

    # ---- 5) 暂存目录顶上（失败回滚）----
    try:
        os.rename(staging, library_dir)
    except OSError as e:
        if had_library:
            try:
                os.rename(pre_restore, library_dir)   # 回滚到原库
            except OSError:
                raise RestoreError(
                    "恢复失败（%s），且回滚失败——原库已转存到 %s，"
                    "请手动改回目录名。" % (e, pre_restore))
        raise RestoreError("恢复失败（%s），已回滚，原库未改动。" % e)

    # ---- 6) 设置原子写回（先转存副本；此段失败库已恢复，消息如实）----
    settings_restored = False
    if settings_tmp:
        # 与库 pre_restore 同一（去撞名后的）时间戳，方便成对追溯
        set_stamp = os.path.basename(pre_restore).split(".pre_restore-", 1)[-1]
        try:
            if os.path.isfile(settings_path):
                shutil.copy2(settings_path,
                             settings_path + ".pre_restore-" + set_stamp)
            os.replace(settings_tmp, settings_path)
            settings_restored = True
        except OSError as e:
            raise RestoreError(
                "棋谱库已恢复，但设置文件写入失败（%s）——设置仍为旧值，"
                "关闭占用该文件的程序后可重新恢复。" % e)
        finally:
            if os.path.exists(settings_tmp):
                try:
                    os.remove(settings_tmp)
                except OSError:
                    pass
    elif os.path.exists(settings_path + ".restore.tmp"):
        try:
            os.remove(settings_path + ".restore.tmp")
        except OSError:
            pass

    _prune_pre_restore(library_dir, settings_path, keep_pre_restore)
    return {"restored_games": restored_games,
            "pre_restore": pre_restore if had_library else None,
            "settings_restored": settings_restored}


def _prune_pre_restore(library_dir, settings_path, keep=2):
    """清理历史 pre_restore 转存（库目录与设置副本各保留最近 keep 份）。

    单个被占用（杀毒/备份软件）跳过不中断——转存是安全网，宁可多留
    也不为清理冒险。
    """
    base = os.path.dirname(os.path.abspath(library_dir))
    prefix = os.path.basename(library_dir) + ".pre_restore-"
    try:
        olds = sorted(n for n in os.listdir(base) if n.startswith(prefix))
        for name in olds[:-keep] if keep > 0 else olds:
            p = os.path.join(base, name)
            try:
                if os.path.isdir(p):
                    shutil.rmtree(p)
                else:
                    os.remove(p)
            except OSError:
                pass
    except OSError:
        pass
    if not settings_path:
        return
    sdir = os.path.dirname(os.path.abspath(settings_path))
    sprefix = os.path.basename(settings_path) + ".pre_restore-"
    try:
        olds = sorted(n for n in os.listdir(sdir) if n.startswith(sprefix))
        for name in olds[:-keep] if keep > 0 else olds:
            try:
                os.remove(os.path.join(sdir, name))
            except OSError:
                pass
    except OSError:
        pass
