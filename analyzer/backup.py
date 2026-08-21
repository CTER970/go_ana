"""backup —— 学习数据每日自动备份（启动时后台执行）。

背景：错题本/画像/LearningEvent 是数月积累的个人学习资产，一次误删记录
或磁盘问题就全部归零（index.json 的 .bak 只防写入中断，防不了目录级
灾难）。本模块在应用启动时把 game_library/ 与 user_settings.json 打包成
当日 zip，保留最近 keep 份：

- 按日期幂等：同一天只备一次（双开/重启不重复打包）；
- 先写 .tmp 再 os.replace（与项目其他持久化同一原子约定）；
- 备份目录本身排除在外，避免套娃膨胀；
- 任何失败静默返回 None——备份问题不允许阻断启动。

体量预期：个人棋局库为 MB 级（SGF+JSON），每日 zip 成本可忽略；
若未来库显著变大（>数百 MB），再考虑增量方案。
"""
from __future__ import annotations

import datetime
import os
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
    """只保留最近 keep 份备份（按文件名日期排序，旧者删）。"""
    try:
        names = sorted(
            n for n in os.listdir(backup_dir)
            if n.startswith("go-ana-backup-") and n.endswith(".zip"))
        for name in names[:-keep] if keep > 0 else []:
            os.remove(os.path.join(backup_dir, name))
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
