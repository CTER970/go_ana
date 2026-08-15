"""ui.theme —— V6 主题绑定：令牌 → 可用调色板/字体。

app.py 启动时调用 bind() 把令牌灌入其工作副本（COLORS/FONTS 保持
既有键兼容），所有旧 UI 行为不变；新页面经 t() / f() / sp() 取值。
"""
from __future__ import annotations

from ui import tokens

_T = dict(tokens.PALETTE)
_F = dict(tokens.FONT_STACK)
_S = dict(tokens.SPACE)
_R = dict(tokens.RADIUS)


def bind(palette=None, fonts=None, space=None, radius=None):
    """外部（app.py）注入运行时副本（支持主题热替换）。"""
    global _T, _F, _S, _R
    if palette is not None:
        _T = dict(palette)
    if fonts is not None:
        _F = dict(fonts)
    if space is not None:
        _S = dict(space)
    if radius is not None:
        _R = dict(radius)


def t(key, fallback=None):
    return _T.get(key, fallback if fallback is not None else _T["text"])


def f(key):
    return _F.get(key, _F["ui"])


def sp(key):
    return _S.get(key, _S["md"])


def radius(key):
    return _R.get(key, _R["card"])
