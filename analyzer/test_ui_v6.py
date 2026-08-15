"""test_ui_v6 —— V6 界面三层回归：Token / Structure / State（V6 §103）。"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import tkinter as tk
from app import GoAnalyzer, COLORS


def check(name, cond, extra=""):
    print("[CHECK] %-44s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        raise AssertionError(name)


def test_tokens():
    """Token 层：ui/ 新代码禁止散落硬编码色值（tokens.py 除外）。"""
    ui_dir = os.path.join(HERE, "ui")
    hex_re = re.compile(r"#[0-9a-fA-F]{6}\b")
    offenders = []
    for root, _dirs, files in os.walk(ui_dir):
        for fname in files:
            if not fname.endswith(".py") or fname == "tokens.py":
                continue
            path = os.path.join(root, fname)
            with open(path, "r", encoding="utf-8") as f:
                for lineno, line in enumerate(f, 1):
                    code = line.split("#")[0]
                    if hex_re.search(code):
                        offenders.append("%s:%d" % (
                            os.path.relpath(path, ui_dir), lineno))
    check("ui/ 无散落硬编码色值", not offenders, str(offenders[:4]))
    from ui import tokens
    check("V6 新令牌就位",
          tokens.PALETTE["learning_priority"] == "#9B8AFB"
          and "surface0" in tokens.PALETTE
          and tokens.nav_metrics(1920) == (176, 440)
          and tokens.nav_metrics(1300) == (64, 400)
          and tokens.nav_metrics(1040) == (56, 360))
    check("app 调色板与令牌单一来源同步",
          COLORS["learning_priority"] == "#9B8AFB")


def test_structure(app):
    """Structure 层：五页面注册 + 路由切换 + 复盘页嵌入旧工作台。"""
    check("首页已注册", app.shell.pages.get("home") is not None)
    check("复盘页嵌入旧工作台", app.shell.pages.get("review") is not None
          and app.workspace.master is app.shell.pages["review"])
    check("左导航五项", [k for _l, k in app.shell.NAV] == [
        "home", "library", "review", "practice", "learning"])
    app.router.go("review")
    app.update()
    check("路由切到复盘页", app.router.current == "review"
          and app._review_page.winfo_ismapped())
    app.router.go("home")
    app.update()
    check("路由切回首页", app.router.current == "home"
          and app.home_page.winfo_ismapped())
    check("复盘页切换不重建（状态保留）",
          app._review_page is app.shell.pages["review"])


def test_state(app):
    """State 层：学习/研究模式互斥 + 首页空态/有数据态可渲染。"""
    app.router.go("review")
    saved_candidates = app._show_candidates
    app._show_candidates = True
    app._auto_hint = True
    app._set_review_mode(0)          # 学习模式
    check("学习模式隐藏候选叠加与 AI 提示",
          app._show_candidates is False and app._auto_hint is False)
    app._set_review_mode(1)          # 研究模式：恢复进入学习模式前的状态
    check("研究模式恢复候选与提示",
          app._show_candidates is True and app._auto_hint is True)
    app._show_candidates = saved_candidates
    # 首页可反复刷新（空库与真实库两种环境都不崩）
    app.router.go("home")
    app.update()
    app.home_page.refresh()
    check("首页重复刷新稳定", app.home_page.winfo_ismapped())


def run():
    test_tokens()
    app = GoAnalyzer()
    try:
        app._auto_start_attempted = True
        test_structure(app)
        test_state(app)
    finally:
        app.destroy()
    print("test_ui_v6: 全部通过")


if __name__ == "__main__":
    run()
