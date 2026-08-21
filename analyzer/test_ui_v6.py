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
    check("左导航三项（减法 R1/R2：我的学习并入首页详情）",
          [item[2] for item in app.shell.NAV] == [
              "home", "library", "practice"])
    check("复习页注册为一级页面、学习页保留为隐藏详情路由",
          app.shell.pages.get("practice") is not None
          and app.shell.pages.get("learning") is not None
          and getattr(app.shell.pages["practice"], "name", "") == "practice"
          and getattr(app.shell.pages["learning"], "name", "") == "learning")
    check("默认进入复盘工作区（棋盘优先）", app.router.current == "review")
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
    # Phase 7/8：复习页与我的学习页可路由且可重复刷新（空库/真实库均不崩）
    app.router.go("practice")
    app.update()
    check("路由切到复习页（内嵌，不弹窗）",
          app.router.current == "practice"
          and app.practice_page.winfo_ismapped())
    app.practice_page.refresh()
    app.router.go("learning")
    app.update()
    check("学习详情页仍可从首页入口路由（隐藏页，不在导航）",
          app.router.current == "learning"
          and app.learning_page.winfo_ismapped())
    app.learning_page.refresh()
    app.update()
    check("复习/学习页重复刷新稳定",
          app.practice_page.winfo_exists() and app.learning_page.winfo_exists())
    app.router.go("review")
    app.update()


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

    # 审查 #6：首页复习数单一来源——事件库有到期走事件，否则过渡期
    # 回退书侧（P0-4 调度字段迁移完成后回退移除）
    from ui.pages.home import HomePage
    from learning_store import get_due_reviews
    from mistake_book import book_stats
    due_events = len(get_due_reviews())
    expected = due_events if due_events else int(book_stats().get("due") or 0)
    check("首页复习数单一来源（事件优先/书侧过渡回退）",
          HomePage._data(app)["due"] == expected,
          "%s vs %s" % (HomePage._data(app)["due"], expected))

    # 学习时间轴（Phase 6）：目损色杆 / 学习价值紫圈 / 点击跳转
    # 渲染器无关断言：PIL 路径色杆/紫圈画进预渲染图（无 tick 标签项），
    # 用 _tick_specs()（两种渲染路径共用的数据描述）验证。
    check("时间轴已挂载", hasattr(app, "timeline"))
    app.timeline.set_data(100, [
        {"move": 10, "loss": 2.0, "priority": None},
        {"move": 50, "loss": 7.0, "priority": 0.9},
        {"move": 60, "loss": 0.5, "priority": None},   # <1 目：无标记
    ], current=50)
    app.update()
    specs = app.timeline._tick_specs()
    check("目损≥1目画色杆、<1目无标记", len(specs) == 2)
    check("学习价值节点画紫圈（专用令牌）",
          sum(1 for s in specs if s[3]) == 1)
    app._timeline_jump(50)
    check("时间轴点击跳转主线", 0 <= app.tree.current.depth <= 50)

    # 棋谱页内嵌（Phase 4 只换容器）：不触发收件箱导入，纯读
    app.scan_library_inbox = lambda silent=True: None
    app.open_game_library()
    app.update()
    check("棋谱页内嵌为一级页面", app.router.current == "library"
          and app._lib_win is app.library_page)
    app._close_library_window()
    app.update()
    check("内嵌关闭=返回复盘页（不销毁页面）",
          app.router.current == "review"
          and app.library_page.winfo_exists())
    app.open_game_library()
    app.update()
    check("再次进入棋谱页正常刷新", app.router.current == "library")
    app.router.go("review")
    app.update()


def run():
    test_tokens()
    import usage_log
    import backup
    usage_log.set_enabled(False)   # 测试不写使用埋点
    backup.set_enabled(False)      # 测试不触发真实备份
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
