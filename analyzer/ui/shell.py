"""ui.shell —— V6 App Shell：左侧一级导航 + 页面容器 + 路由（V6 §6-8、§94）。

五个一级页面（首页/棋谱/复盘/复习/我的学习）+ 底部设置。
Phase 2 约定：旧工作台**整体**嵌入为复盘页（功能不变）；棋谱/复习/
我的学习暂路由到既有窗口（Phase 4/7/8 再内嵌），避免一次性重写。
"""
from __future__ import annotations

import tkinter as tk

from ui import theme as th
from ui import tokens


class Router:
    """极简页面路由（V6 §80）：go(name) 切换容器页并通知 Shell。"""

    def __init__(self, shell):
        self.shell = shell
        self.current = None

    def go(self, name, **context):
        page = self.shell.pages.get(name)
        if page is None:
            self.shell.activate_window_route(name)
            return None
        if hasattr(page, "refresh"):
            try:
                page.refresh(**context)
            except TypeError:
                page.refresh()
        for key, other in self.shell.pages.items():
            if other is not None:
                other.pack_forget()
        page.pack(fill="both", expand=True)
        self.shell.set_active(name)
        self.current = name
        return page


class Shell(tk.Frame):
    """左导航 + 右内容区。review_host 是挂载旧工作台的容器。"""

    NAV = (
        ("首页", "home"),
        ("棋谱", "library"),
        ("复盘", "review"),
        ("复习", "practice"),
        ("我的学习", "learning"),
    )

    def __init__(self, parent, app):
        super().__init__(parent, bg=th.t("surface0"))
        self.app = app
        self.pages = {"home": None, "library": None, "review": None,
                      "practice": None, "learning": None}
        self._nav_buttons = {}
        self._build_nav()
        self.content = tk.Frame(self, bg=th.t("bg"))
        self.content.pack(side=tk.LEFT, fill="both", expand=True)
        self.router = Router(self)

    # ---- 左导航（V6 §7：展开 176 / 收起 64；底部设置） ----
    def _build_nav(self):
        self.nav = tk.Frame(self, bg=th.t("surface0"), width=176)
        self.nav.pack(side=tk.LEFT, fill="y")
        self.nav.pack_propagate(False)
        tk.Label(self.nav, text="go_ana", font=th.f("h1"),
                 bg=th.t("surface0"), fg=th.t("accent")).pack(
                     anchor="w", padx=th.sp("lg"), pady=(th.sp("lg"), th.sp("md")))
        for label, key in self.NAV:
            btn = tk.Label(self.nav, text="　%s" % label, font=th.f("ui"),
                           bg=th.t("surface0"), fg=th.t("subtext"),
                           anchor="w", cursor="hand2", padx=th.sp("md"))
            btn.pack(fill="x", pady=th.sp("xs"))
            btn.bind("<Button-1>", lambda _e, k=key: self.router.go(k))
            self._nav_buttons[key] = btn
        # 底部：设置
        bottom = tk.Frame(self.nav, bg=th.t("surface0"))
        bottom.pack(side=tk.BOTTOM, fill="x", pady=th.sp("lg"))
        settings = tk.Label(bottom, text="　设置", font=th.f("ui"),
                            bg=th.t("surface0"), fg=th.t("subtext"),
                            anchor="w", cursor="hand2", padx=th.sp("md"))
        settings.pack(fill="x")
        settings.bind("<Button-1>", lambda _e: self.app.open_settings())

    def set_active(self, name):
        for key, btn in self._nav_buttons.items():
            active = key == name
            try:
                btn.configure(
                    bg=th.t("accent_s") if active else th.t("surface0"),
                    fg=th.t("text") if active else th.t("subtext"))
            except tk.TclError:
                pass

    # ---- 窗口路由（Phase 2 过渡：未内嵌的页面打开既有窗口） ----
    def activate_window_route(self, name):
        if name == "library":
            self.app.open_game_library()
        elif name == "practice":
            self.app.open_mistake_book()
        elif name == "learning":
            self.app.open_player_profile()
        else:
            return
        # 复盘页保持显示（窗口浮在其上），导航高亮跟随
        review = self.pages.get("review")
        if review is not None and not review.winfo_ismapped():
            review.pack(fill="both", expand=True)
        self.set_active(name)
        self.router.current = name

    # ---- 页面注册 ----
    def register(self, name, page):
        self.pages[name] = page
        return page
