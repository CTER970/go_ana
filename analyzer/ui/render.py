"""ui.render —— V6 渲染工具：PIL 超采样抗锯齿预渲染（3x + Lanczos）。

与 app.py 已验证的棋子管线同一思路（超采样绘制 → Lanczos 缩小 = 真抗
锯齿），但本模块在 ui/ 包内自成一套实现，不 import app.py 的私有函数。

所有工厂在 PIL / ImageTk 不可用时返回 None，调用方（如 timeline 的
轨道/色杆/紫圈/手柄）必须保留原生 canvas 绘制作为降级路径。
"""
from __future__ import annotations

try:  # pragma: no cover - 环境探测
    from PIL import Image, ImageDraw, ImageTk
    _HAS_PIL = True
except Exception:  # pragma: no cover
    Image = ImageDraw = ImageTk = None
    _HAS_PIL = False

# 超采样倍数（3x：与 app.py 棋子管线一致，边缘锐利精细）
SUPER_SAMPLE = 3


def hex_to_rgb(value):
    """#rrggbb → (r, g, b)；非法输入返回 None。"""
    text = str(value or "").strip()
    if len(text) == 7 and text[0] == "#":
        try:
            return (int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16))
        except ValueError:
            return None
    return None


def color_or(color, fallback):
    """颜色 → PIL 可用的 RGBA 元组（解析失败回退 fallback）。"""
    rgb = hex_to_rgb(color)
    if rgb is None:
        rgb = fallback
    return (rgb[0], rgb[1], rgb[2], 255)


def photo(width, height, draw_fn, ss=SUPER_SAMPLE):
    """超采样渲染一张透明底位图并缩回目标尺寸。

    draw_fn(pil_draw, w, h, ss) 在 (w*ss, h*ss) 画布上绘制（所有坐标
    乘 ss 由调用方负责；线宽/半径由本函数的 ss 提示换算）。
    返回 ImageTk.PhotoImage；无 PIL / 尺寸非法 / 无 Tk 环境时返回 None
    （调用方降级到原生绘制）。
    """
    if not _HAS_PIL:
        return None
    w, h = int(width), int(height)
    if w <= 0 or h <= 0:
        return None
    try:
        img = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
        draw_fn(ImageDraw.Draw(img), w, h, ss)
        if ss != 1:
            img = img.resize((w, h), Image.LANCZOS)
        return ImageTk.PhotoImage(img)
    except Exception:
        # 无默认 Tk root / ImageTk 初始化失败等 → 走调用方降级路径
        return None
