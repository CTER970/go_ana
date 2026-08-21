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
        self._logged_first = False   # 首次路由是应用初始化，不计用户意图

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
        # R0 使用埋点：真实页面切换才计数（首跳=初始化、窗口路由均不计）
        if self._logged_first:
            log_usage = getattr(self.shell.app, "_log_usage", None)
            if callable(log_usage):
                log_usage("page_open", page=name)
        else:
            self._logged_first = True
        if name == "review":
            rescale = getattr(self.shell.app, "_rescale_board_soon", None)
            if callable(rescale):
                self.shell.app.after_idle(rescale)
        return page


class Shell(tk.Frame):
    """左导航 + 右内容区。review_host 是挂载旧工作台的容器。"""

    # 减法重构 R1/R2（最新改动要求.txt）：一级导航只留三项——
    # 今日学习 / 棋谱 / 复习。"我的学习"并入首页（数据不减、页面保留为
    # 隐藏详情路由，入口在首页底部"查看学习详情"）；复盘仍是"打开某盘棋
    # 后的 Workspace"（Router 保留 review 路由，从首页/棋谱双击进入）。
    NAV = (
        ("今日学习", "今", "home"),
        ("棋谱", "谱", "library"),
        ("复习", "习", "practice"),
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
        self._brand = tk.Label(self.nav, text="go_ana", font=th.f("h1"),
                               bg=th.t("surface0"), fg=th.t("accent"))
        self._brand.pack(anchor="w", padx=th.sp("lg"),
                         pady=(th.sp("lg"), th.sp("md")))
        for label, short, key in self.NAV:
            btn = tk.Label(self.nav, text="　%s" % label, font=th.f("ui"),
                           bg=th.t("surface0"), fg=th.t("subtext"),
                           anchor="w", cursor="hand2", padx=th.sp("md"))
            btn.pack(fill="x", pady=th.sp("xs"))
            btn.bind("<Button-1>", lambda _e, k=key: self.router.go(k))
            self._nav_buttons[key] = (btn, label, short)
        # 底部：设置
        bottom = tk.Frame(self.nav, bg=th.t("surface0"))
        bottom.pack(side=tk.BOTTOM, fill="x", pady=th.sp("lg"))
        settings = tk.Label(bottom, text="　设置", font=th.f("ui"),
                            bg=th.t("surface0"), fg=th.t("subtext"),
                            anchor="w", cursor="hand2", padx=th.sp("md"))
        settings.pack(fill="x")
        settings.bind("<Button-1>", lambda _e: self.app.open_settings())
        self._settings_label = settings
        # 响应式（V6 §72）：窗口变窄先收导航（64/56），棋盘永远最后被压缩
        self.bind("<Configure>", self._on_width_change, add="+")

    def _on_width_change(self, event):
        if event.widget is not self:
            return
        self.apply_width(event.width)

    def apply_width(self, window_width):
        """按断点收放导航；收起时用单字短标签（设置→齿）。"""
        from ui.tokens import nav_metrics
        nav_w, _right = nav_metrics(window_width)
        try:
            if self.nav.winfo_width() == nav_w and self._collapsed == (nav_w < 120):
                return
        except Exception:
            pass
        self._collapsed = nav_w < 120
        self.nav.configure(width=nav_w)
        self._brand.config(text="go_ana" if not self._collapsed else "go",
                           font=th.f("h1") if not self._collapsed else th.f("section"))
        for btn, label, short in self._nav_buttons.values():
            btn.config(text=("　%s" % label) if not self._collapsed
                       else short.center(max(1, (nav_w - 8) // 12)))
        self._settings_label.config(
            text="　设置" if not self._collapsed else " 设 ".strip())
        self.set_active(self.router.current if self.router.current else "review")

    _collapsed = False

    def set_active(self, name):
        for key, (btn, _label, _short) in self._nav_buttons.items():
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
