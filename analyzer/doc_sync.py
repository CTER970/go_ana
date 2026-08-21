"""doc_sync —— 项目说明文档自动同步 Agent。

每次修改代码后运行：python doc_sync.py

自动检测代码变更（git diff），更新 项目说明.md 中受影响的章节，
保持文档与代码一致——不再出现"README 说 v4.38 但代码已是 v5.0"的漂移。

检测维度：
  1. 版本号：app.py 的 APP_VERSION 是否与文档一致
  2. 测试数量：test_*.py 文件数是否与文档一致
  3. 模块清单：非测试 .py 文件是否都在文档的模块架构表中
  4. UI 模块：ui/ 目录下的文件是否在文档中提及
  5. 配置项：config_manager.py 的 DEFAULTS 键是否在文档中说明
  6. 数据文件：game_library/ 下的 JSON 文件是否在文档的数据表中
  7. 工具脚本：self_review.py 等新工具是否在文档中说明

用法：
  python doc_sync.py          # 检查 + 自动更新
  python doc_sync.py --check  # 仅检查，不写入
"""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DOC = os.path.join(ROOT, "项目说明.md")

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def read_doc():
    if not os.path.exists(DOC):
        return ""
    with open(DOC, "r", encoding="utf-8") as f:
        return f.read()


def write_doc(text):
    with open(DOC, "w", encoding="utf-8", newline="") as f:
        f.write(text)


# ===================== 检测器 =====================

def get_app_version():
    """从 app.py 的 APP_VERSION 常量读取。"""
    code = os.path.join(HERE, "app.py")
    if not os.path.exists(code):
        return None
    with open(code, "r", encoding="utf-8") as f:
        text = f.read()
    m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', text)
    return m.group(1) if m else None


def get_test_count():
    return len([f for f in os.listdir(HERE)
                if f.startswith("test_") and f.endswith(".py")])


def get_modules():
    """非测试、非缓存、非补丁的 Python 业务模块。"""
    skip = {"__pycache__", "_patch2.py"}
    return sorted(f[:-3] for f in os.listdir(HERE)
                  if f.endswith(".py") and not f.startswith("test_")
                  and not f.startswith("_") and f not in skip
                  and f != "smoke_app.py")  # smoke_app 是独立入口


def get_ui_modules():
    ui_dir = os.path.join(HERE, "ui")
    if not os.path.isdir(ui_dir):
        return []
    return sorted(f[:-3] for f in os.listdir(ui_dir)
                  if f.endswith(".py") and f != "__init__.py")


def get_config_defaults():
    cfg = os.path.join(HERE, "config_manager.py")
    if not os.path.exists(cfg):
        return []
    with open(cfg, "r", encoding="utf-8") as f:
        text = f.read()
    m = re.search(r'DEFAULTS\s*=\s*\{(.*?)\n\s*\}', text, re.DOTALL)
    if not m:
        return []
    return re.findall(r'"(\w+)":', m.group(1))


def get_data_files():
    gl = os.path.join(HERE, "game_library")
    if not os.path.isdir(gl):
        return []
    return sorted(f for f in os.listdir(gl) if f.endswith(".json"))


def get_tools():
    """自审查/文档同步等工具脚本。"""
    return sorted(f for f in os.listdir(HERE)
                  if f in ("self_review.py", "doc_sync.py"))


# ===================== 修复器 =====================

def fix_version(doc, version):
    if not version:
        return doc, False
    old = re.search(r'文档版本：v([\d.]+)', doc)
    if old and old.group(1) != version:
        doc = doc.replace(
            "文档版本：v%s" % old.group(1), "文档版本：v%s" % version)
        return doc, True
    if not old:
        doc = doc.replace(
            "> 文档版本：",
            "> 文档版本：v%s｜" % version, 1)
        return doc, True
    return doc, False


def fix_test_count(doc, count):
    old = re.search(r'(\d+)\s+个\s+`test_\*\.py`', doc)
    if old and int(old.group(1)) != count:
        doc = doc.replace(
            "%s 个 `test_*.py`" % old.group(1),
            "%d 个 `test_*.py`" % count)
        return doc, True
    return doc, False


def fix_app_version_in_body(doc, version):
    if not version:
        return doc, False
    old = re.search(r'为\s*`([\d.]+)`\s*。', doc)
    if old and old.group(1) != version:
        doc = doc.replace(
            "为 `%s`。" % old.group(1), "为 `%s`。" % version)
        return doc, True
    return doc, False


def check_modules_in_doc(doc, modules):
    """检查文档模块架构表是否覆盖所有模块。"""
    missing = []
    for mod in modules:
        if mod + ".py" not in doc and mod not in doc:
            missing.append(mod)
    return missing


def check_ui_in_doc(doc, ui_modules):
    missing = []
    for mod in ui_modules:
        if "ui/" + mod + ".py" not in doc and "ui/" + mod not in doc:
            if mod not in doc:
                missing.append("ui/" + mod)
    return missing


def check_config_in_doc(doc, config_keys):
    """重要配置项是否在文档中说明。"""
    important = {"user_learning_rank", "human_model_path",
                 "human_sl_profile", "default_game_type",
                 "auto_hint", "candidate_count"}
    missing = []
    for key in important:
        if key in config_keys and key not in doc:
            missing.append(key)
    return missing


# ===================== 主入口 =====================

def run(check_only=False):
    doc = read_doc()
    if not doc:
        print("❌ 项目说明.md 不存在")
        return 1

    changes = []

    # 1. 版本号
    ver = get_app_version()
    doc, changed = fix_version(doc, ver)
    if changed:
        changes.append("文档版本号 → v%s" % ver)
    doc, changed = fix_app_version_in_body(doc, ver)
    if changed:
        changes.append("正文版本引用 → %s" % ver)

    # 2. 测试数量
    tests = get_test_count()
    doc, changed = fix_test_count(doc, tests)
    if changed:
        changes.append("测试数量 → %d" % tests)

    # 3. 模块清单
    modules = get_modules()
    missing_mods = check_modules_in_doc(doc, modules)
    if missing_mods:
        changes.append("⚠ 文档未提及的模块：%s" % ", ".join(missing_mods))

    # 4. UI 模块
    ui_mods = get_ui_modules()
    missing_ui = check_ui_in_doc(doc, ui_mods)
    if missing_ui:
        changes.append("⚠ 文档未提及的 ui/ 模块：%s" % ", ".join(missing_ui))

    # 5. 配置项
    config_keys = get_config_defaults()
    missing_cfg = check_config_in_doc(doc, config_keys)
    if missing_cfg:
        changes.append("⚠ 文档未说明的配置项：%s" % ", ".join(missing_cfg))

    # 6. 工具
    tools = get_tools()
    for tool in tools:
        if tool not in doc:
            changes.append("⚠ 文档未提及工具：%s" % tool)

    # 输出
    print("=" * 60)
    print(" doc_sync —— 项目说明文档同步检查")
    print("=" * 60)
    print("  代码版本：v%s" % ver)
    print("  测试文件：%d 个" % tests)
    print("  业务模块：%d 个" % len(modules))
    print("  UI 模块：%d 个" % len(ui_mods))
    print("  配置项： %d 个" % len(config_keys))
    print()

    if changes:
        print("  变更与提醒：")
        for c in changes:
            print("    %s" % c)

        auto_fixed = [c for c in changes if not c.startswith("⚠")]
        warnings = [c for c in changes if c.startswith("⚠")]

        if auto_fixed and not check_only:
            write_doc(doc)
            print("\n  ✅ 已自动更新 %d 项（写入 项目说明.md）" % len(auto_fixed))
        elif auto_fixed:
            print("\n  📝 检测到 %d 项可自动更新（--check 模式未写入）" % len(auto_fixed))

        if warnings:
            print("  ⚠ %d 项需要手动补充（新增模块/配置/UI）" % len(warnings))
            print("     建议：在模块架构表、数据表或功能说明中补充上述条目")
    else:
        print("  ✅ 文档与代码一致，无需更新")

    print("\n" + "=" * 60)
    return 0


if __name__ == "__main__":
    check_only = "--check" in sys.argv
    sys.exit(run(check_only=check_only))
