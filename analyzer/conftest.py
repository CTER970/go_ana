"""conftest —— pytest 入口适配（交接移交项①：test_ui_v6 缺 app fixture）。

test_ui_v6 的 test_structure/test_state 按脚本约定收 app 参数；直跑时由
run() 自建，pytest 收集时由此 fixture 提供。复用 adversarial_harness 的
单例 root（含弹窗 mock / 自启关闭 / Tcl 瞬态重试 / 埋点关闭）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pytest


@pytest.fixture(scope="module")
def app():
    """无头 GoAnalyzer（module 级复用，测试结束销毁单例）。"""
    import adversarial_harness as ah
    application = ah.make_headless_app()
    yield application
    ah.destroy_app()
