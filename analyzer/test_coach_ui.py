"""test_coach_ui —— 教练解读 UI 接线回归（M8）。

验证复盘工具栏"教练解读"按钮背后的完整链路：
analyzed fixture → EvidencePacket → get_coach_explanation（确定性回退）→
弹窗渲染 → 关闭清理。纯逻辑（Provider 校验/防幻觉）见 test_coach.py。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import tkinter as tk

from app import GoAnalyzer


def check(name, cond, extra=""):
    print("[CHECK] %-44s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        raise AssertionError(name)


def run():
    from adversarial_harness import seed_fixture
    app = GoAnalyzer()
    app._auto_start_attempted = True
    try:
        # 入口守卫：根局面（无落子）不崩、给出可操作提示
        seed_fixture(app, "simple")
        app.tree.current = app.tree.root
        app.show_coach_explanation()
        check("根局面守卫提示不崩", app._coach_win is None)

        # 完整链路：analyzed fixture → 教练解读窗口
        seed_fixture(app, "analyzed")
        node = app.tree.current
        while node.children:
            node = node.children[0]
            if node.analysis is not None and node.parent.analysis is not None:
                break
        app.tree.current = node
        app.show_coach_explanation()
        app.update()
        check("教练解读窗口打开", app._coach_win is not None
              and app._coach_win.winfo_exists())
        texts = []
        def walk(w):
            if isinstance(w, tk.Text):
                texts.append(w.get("1.0", "end"))
            for c in w.winfo_children():
                walk(c)
        walk(app._coach_win)
        body = "\n".join(texts)
        check("窗口含结构化章节",
              "一句话总结" in body and "发生了什么" in body
              and "来源：deterministic" in body)
        app._close_coach_window()
        app.update()
        check("窗口关闭清理引用", app._coach_win is None)

        # Human SL 状态接入设置页（治理遗留：模型缺失不再静默）
        app.open_settings()
        app.update()
        labels = []
        def walk2(w):
            if isinstance(w, tk.Label) and "Human SL" in str(w.cget("text")):
                labels.append(str(w.cget("text")))
            for c in w.winfo_children():
                walk2(c)
        walk2(app._settings_win)
        check("设置页含 Human SL 状态行", bool(labels), labels[:1])
        app._close_settings_window()
    finally:
        app.destroy()
    print("test_coach_ui: 全部通过")


if __name__ == "__main__":
    run()
