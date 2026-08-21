"""UI 结构回归测试。

不创建 Tk root，因此在缺少 Tcl/Tk 的构建环境里也能保护主题、布局和交互层级。
真实控件生命周期仍由 test_ui_smoke.py 覆盖。
"""
from __future__ import annotations

import ast
from pathlib import Path


HERE = Path(__file__).resolve().parent
APP = HERE / "app.py"
STYLE_VIEW = HERE / "style_view.py"


def _class_method(tree, class_name, method_name):
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == method_name:
                    return child
    raise AssertionError("%s.%s 不存在" % (class_name, method_name))


def main():
    app_source = APP.read_text(encoding="utf-8")
    style_source = STYLE_VIEW.read_text(encoding="utf-8")
    # 设置/错题本等窗口已外迁 ui/dialogs.py（app.py 瘦身），标记在两处搜
    dialogs_path = APP.parent / "ui" / "dialogs.py"
    dialogs_source = (dialogs_path.read_text(encoding="utf-8")
                      if dialogs_path.exists() else "")
    app_source += "\n" + dialogs_source
    tree = ast.parse(APP.read_text(encoding="utf-8"))

    init = _class_method(tree, "GoAnalyzer", "__init__")
    cfg_assign_line = None
    cfg_read_line = None
    for node in ast.walk(init):
        if isinstance(node, ast.Assign):
            if any(
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                    and target.attr == "cfg"
                    for target in node.targets):
                cfg_assign_line = min(cfg_assign_line or node.lineno, node.lineno)
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Attribute)
                and isinstance(node.func.value.value, ast.Name)
                and node.func.value.value.id == "self"
                and node.func.value.attr == "cfg"):
            cfg_read_line = min(cfg_read_line or node.lineno, node.lineno)
    assert cfg_assign_line is not None and cfg_read_line is not None
    assert cfg_assign_line < cfg_read_line, "ConfigManager 必须先于主题读取初始化"

    required_app_markers = [
        "tk.PanedWindow(",
        "def _build_transport_bar(",
        "def _draw_candidate_overlay(",
        "def _draw_board_empty_state(",
        "def _refresh_game_context(",
        "def _persist_workspace_state(",
        "def _attach_tooltip(",
        "def _prepare_child_window(",
        "def _show_candidate_state(",
        "def _show_candidate_rows(",
        "def _build_metric_card(",
        "def _update_situation_metrics(",
        "def _style_candidate_row(",
        "def _refresh_visual_palette(",
        "def _draw_brand_mark(",
        "def _draw_style_preview(",
        "CYBERPUNK_COLORS",
        "UI_STYLE_LABELS",
        '"Topbar.TButton"',
        '"Workspace.TNotebook"',
        '_UNIFIED_COLORS',   # 统一深色色板（替代旧三套主题）
        "fit_window_size(",
        "self.review_views = review_views",
        'review_views.add(summary_page, text="总结")',
        'review_views.add(problems_page, text="问题手")',
        '("研究", self._build_card_analysis)',
        '("复盘", self._build_card_review)',
        '("棋谱", self._build_card_sgf)',
        '("导航", self._build_card_play)',
        '_make_button(c, "分析当前局面"',
        '_make_button(c, "系统设置"',
        '"批量导入", self.do_import_sgf_batch',
        '"粘贴 SGF", self.open_paste_sgf',
        '"分析队列", self.open_analysis_queue',
        "build_evidence_explanation(",
        "def _kick_analysis_queue(",
        "def _handle_analysis_queue_result(",
        "请求发送失败：%s",
        "ctx[\"done\"] % 10 == 0",
        "def _pause_analysis_queue(",
        "def _retry_failed_analysis_queue(",
        'text=" AI 推荐 · 单击落子（主变模式下点选看变化） "',
        'text="启动 KataGo 后显示推荐点与变化图"',
        '"黑胜率"',
        '"白胜率"',
        '"目差"',
        'text="界面风格："',
        '"外观"',
        '"引擎与规则"',
        '"分析参数"',
        '"个人画像"',
        'width=270, height=92',
        'text="设置保存到 user_settings.json"',
        "auto_hint_training=auto_hint_training_v",
        "from ui.tokens import PALETTE as _TOKEN_PALETTE",
        "self._candidate_rank_labels",
        '"个人画像", self.open_player_profile',
        '"棋风与成长", self.open_style_profile',
        '_make_button(app_actions, "保存"',
    ]
    for marker in required_app_markers:
        assert marker in app_source, "缺少 UI 结构：%s" % marker

    for key in (
            "bg", "card", "card2", "board", "board2", "grid", "text",
            "subtext", "accent", "accent_s", "red", "amber", "green",
            "muted", "shadow"):
        assert ('"%s":' % key) in app_source, "主题缺色值：%s" % key

    assert "ttk.Notebook(self)" in style_source
    assert 'text="棋风维度"' in style_source
    assert 'text="成长与复核"' in style_source

    print("test_ui_design: PASS")


def test_ui_design():
    main()


if __name__ == "__main__":
    main()
