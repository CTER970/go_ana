"""test_visual_snapshot —— PIL 渲染产物视觉快照基线（P0③）。

对棋子/候选点/圆角矩形三类 PIL 预渲染产物做 hash 快照比对：
  python test_visual_snapshot.py            # 与基线比对（不一致即失败）
  python test_visual_snapshot.py --update   # 显式更新基线（视觉改动时用，须在提交说明中注明原因）

目的：ui-modernizer 的视觉改动有"基准线"——改坏（锯齿回归/尺寸漂移/色值漂移）
会被测试抓住，美观从"感觉"变成"回归项"。纯 PIL 逻辑不依赖具体窗口尺寸，
无头可跑；无 PIL 环境自动跳过（降级原则）。
"""
from __future__ import annotations

import hashlib
import io as _io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASELINE_PATH = os.path.join(HERE, "test_visual_baseline.json")


def build_snapshots():
    """无头实例化 app，拦截 ImageTk.PhotoImage 捕获 PIL 图像，逐 case 取 sha256。"""
    try:
        from PIL import ImageTk  # noqa: F401
    except Exception:
        print("SKIP: PIL/ImageTk 不可用，视觉快照跳过（降级）")
        return None

    import adversarial_harness as ah
    import app as app_mod

    app = ah.make_headless_app()
    captured = []

    class _Capture:
        def __init__(self, img, master=None):
            captured.append(img)

    real_photo = app_mod.ImageTk.PhotoImage
    snaps = {}
    try:
        app_mod.ImageTk.PhotoImage = _Capture
        BLACK, WHITE = app_mod.BLACK, app_mod.WHITE
        C = app_mod.COLORS

        cases = [
            ("stone_black_r16", lambda: app._render_stone_image(BLACK, 16)),
            ("stone_white_r16", lambda: app._render_stone_image(WHITE, 16)),
            ("stone_black_r9", lambda: app._render_stone_image(BLACK, 9)),
            ("marker_candidate", lambda: app._render_candidate_marker(
                C["accent"], C["text"], 10, 220, 2)),
            ("marker_pv_black", lambda: app._render_candidate_marker(
                C["black"], C["accent"], 8, 200, 2)),
            ("marker_problem", lambda: app._render_candidate_marker(
                C["red"], C["text"], 9, 235, 3)),
            ("rounded_hud", lambda: app._render_rounded_rect(120, 40, 12, C["card"], 230)),
            ("rounded_banner", lambda: app._render_rounded_rect(200, 28, 14, C["accent_s"], 255)),
        ]
        for name, fn in cases:
            captured.clear()
            tk_img = fn()
            assert tk_img is not None and captured, "%s 渲染失败（PIL 路径未走通）" % name
            pil_img = captured[0]
            buf = _io.BytesIO()
            pil_img.save(buf, format="PNG")
            snaps[name] = {
                "sha256": hashlib.sha256(buf.getvalue()).hexdigest(),
                "size": list(pil_img.size),
                "mode": pil_img.mode,
            }
    finally:
        app_mod.ImageTk.PhotoImage = real_photo
        try:
            app.destroy()
        except Exception:
            pass
    return snaps


def main():
    update = "--update" in sys.argv
    snaps = build_snapshots()
    if snaps is None:
        print("test_visual_snapshot: SKIP")
        return

    if update or not os.path.exists(BASELINE_PATH):
        with open(BASELINE_PATH, "w", encoding="utf-8") as f:
            json.dump(snaps, f, ensure_ascii=False, indent=2, sort_keys=True)
        print("test_visual_snapshot: 基线已写入 %d 项（%s）" % (
            len(snaps), "更新" if update else "首次建立"))
        return

    with open(BASELINE_PATH, encoding="utf-8") as f:
        base = json.load(f)

    diffs = []
    for name, cur in snaps.items():
        old = base.get(name)
        if old is None:
            diffs.append("新增渲染项 %s 不在基线中（--update 收录）" % name)
        elif old != cur:
            diffs.append("%s 快照漂移：\n    基线 %s\n    当前 %s" % (name, old, cur))
    for name in base:
        if name not in snaps:
            diffs.append("基线项 %s 已消失（渲染被移除？）" % name)

    if diffs:
        print("[FAIL] 视觉快照漂移 %d 项：" % len(diffs))
        for d in diffs:
            print("  -", d)
        print("若属预期视觉改动：python test_visual_snapshot.py --update 更新基线并在改动说明中注明原因")
        sys.exit(1)
    print("[OK] 视觉快照 %d 项全部一致" % len(snaps))


if __name__ == "__main__":
    main()
