"""ui.pages.library —— 棋谱一级页面（V6 §15-18，Phase 4 第一步：只换容器）。

复用 app._build_library_into() 的全部数据逻辑（搜索/扫描/双击打开/画像身份），
仅把宿主从 Toplevel 换成页面容器；后续 Phase 再升级为紧凑行卡。
"""
from __future__ import annotations

import tkinter as tk

from ui import theme as th


class LibraryPage(tk.Frame):
    name = "library"

    def __init__(self, parent, app):
        super().__init__(parent, bg=th.t("bg"))
        self.app = app
        self._built = False

    def refresh(self):
        """Router 契约：首次进入构建（复用棋谱库全部逻辑），之后仅刷新。"""
        if not self._built:
            self.app._build_library_into(self)
            self._built = True
        else:
            self.app._refresh_library_window()
