"""ui.tokens —— V6 设计令牌唯一来源（V6 方案 §57-64）。

app.py 的调色板从此处导入（单一事实源）；新增页面/组件只允许引用
本模块令牌，禁止散落硬编码色值（Token Test 会检查）。

V6 新增语义：
- learning_priority 紫（#9B8AFB）：只用于"学习价值"，绝不与白方指标
  （white_metric）混用（V6 §59）；
- 表面色阶 surface0-3（V6 §57 背景四档）。
"""
from __future__ import annotations

# 背景四档（V6 §57；与既有 bg/card/card2 亮度递进保持一致）
SURFACES = {
    "surface0": "#181D1E",   # App Background（比 bg 更深一档，用于 shell 底）
    "surface1": "#222829",   # = bg
    "surface2": "#2B3233",   # ≈ card
    "surface3": "#343D3E",   # ≈ card2
}

PALETTE = {
    "bg":       "#222829",
    "card":     "#2c3334",
    "card2":    "#363e3f",
    "board":    "#d4a85a",
    "board2":   "#e0b870",
    "grid":     "#4a3618",
    "star":     "#3a2810",
    "coord":    "#6b5630",
    "text":     "#e8ecec",
    "subtext":  "#a8b1ac",
    "accent":   "#3db8a0",
    "accent_h": "#54d4be",
    "accent_s": "#2a4a44",
    "accent_m": "#4d9182",
    "black":    "#1a1a1a",
    "white":    "#f8f8f0",
    "stone_hl_dark":         "#4a4540",
    "stone_hl_dark_bright":  "#6a6058",
    "stone_hl_light":        "#ffffff",
    "stone_hl_light_shade":  "#d0d0c8",
    "heat_green": "#3db8a0",
    "heat_green_dark": "#2a8a70",
    "red":      "#e06560",
    "red_s":    "#4a2e2c",
    "amber":    "#e0a043",
    "amber_s":  "#4a3a25",
    "green":    "#5abb80",
    "muted":    "#454f4a",
    "shadow":   "#15191a",
    "purple":   "#aa8ed8",       # 旧 token：白方指标等辅助用途（存量）
    # ---- V6 新增 ----
    "learning_priority": "#9B8AFB",   # 学习价值专用紫（时间轴外圈/重点标记）
    "white_metric":      "#aa8ed8",   # 白方指标（承接旧 purple 的辅助用途）
}
PALETTE.update(SURFACES)

# 字体阶梯（V6 §60：收敛为 6 级；Tk 尺寸可按 DPI 校准）
UI_FONT = "Microsoft YaHei UI Segoe UI PingFang SC Noto Sans CJK SC Helvetica"
DATA_FONT = "Consolas JetBrains Mono Sarasa Mono SC Menlo Courier New"
FONT_STACK = {
    "display": (UI_FONT, 18, "bold"),   # 页面大标题（首页问候等）
    "h1":      (UI_FONT, 16, "bold"),
    "h2":      (UI_FONT, 13, "bold"),
    "title":   (UI_FONT, 14, "bold"),   # 以下与既有 FONTS 对齐，逐步收敛
    "score":   (UI_FONT, 20, "bold"),
    "section": (UI_FONT, 11, "bold"),
    "body":    (UI_FONT, 10),
    "ui":      (UI_FONT, 10),
    "small":   (UI_FONT, 9),
    "data":    (DATA_FONT, 11),
    "data_s":  (DATA_FONT, 10),
    "micro":   (UI_FONT, 8),
}

# 间距（V6 §62：4px 基准，扩展 24/32 两档）
SPACE = {"xs": 2, "sm": 4, "md": 8, "lg": 12, "xl": 16, "xxl": 24, "xxxl": 32}

# 圆角（V6 §63；CTk 可用时生效，Tk 降级忽略）
RADIUS = {"button": 8, "card": 10, "panel": 12, "chip": 14}

# 复盘页断点（V6 §72：宽度 → 左导航宽/右栏宽）
BREAKPOINTS = (
    (1600, 176, 440),   # Large
    (1200, 64, 400),    # Medium
    (1040, 56, 360),    # Compact
)


def nav_metrics(window_width):
    """按窗口宽返回 (nav_width, right_panel_width)（V6 §72）。"""
    for min_width, nav, right in BREAKPOINTS:
        if window_width >= min_width:
            return nav, right
    return BREAKPOINTS[-1][1], BREAKPOINTS[-1][2]
