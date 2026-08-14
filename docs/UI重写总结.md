# UI 高标准重写总结

> 目标：按主流平台（OGS/KaTrain/星阵）标准彻底重写 UI，解决清晰度/锐利度/对比度差距。
> 不是在原有 ttk/tk 基础上修补，而是用 CustomTkinter + PIL 系统性重写。

## 完成的三个工作包

### 包 A：控件全面迁移到 CustomTkinter（91 个按钮）

| 迁移项 | 数量 | 效果 |
|---|---|---|
| ttk.Button → CTkButton | **91 个** | 全部圆角（corner_radius 6-8），深色主题，hover 动效 |
| `_make_button` 工厂方法 | 3 variant | accent（强调填充）/ topbar（顶栏次要）/ default（普通） |
| `_set_toggle` CTk 兼容 | 1 处 | fg_color 切换激活态（替代 ttk style） |
| `.config()` → `.configure()` | 18 处 | CTkButton 要求 |
| 降级保护 | 全覆盖 | 无 CTk 时自动降级回 ttk.Button |

剩余 4 个 ttk.Button：1 个 `_make_button` 降级路径、1 个候选行（包 C 范围）、2 个设置窗口 "…" 按钮（width=3 太小）。

### 包 B：棋盘 PIL 锐利化（核心锐利度提升）

| 元素 | 原 Tk 绘制 | PIL 预渲染 | 效果 |
|---|---|---|---|
| 棋盘底图（木纹+网格+星位） | create_line/create_oval（无 AA） | 2x 超采样 + Lanczos 缩小 | **真抗锯齿**，网格/星位边缘锐利 |
| 棋子（黑/白） | 5 个 create_oval 叠 + stipple 阴影 | PIL RGBA 预渲染 + GaussianBlur 阴影 | **真 alpha 透明阴影**，替代 stipple 抖动 |
| 坐标 | create_text（保留） | 系统级 AA（ClearType/FreeType） | 文字本身有 AA，无需 PIL |

**缓存机制**：
- `_board_bg_image`：棋盘底图缓存，键=(BOARD_PIX, CELL, 主题)，尺寸/主题变化时重渲染
- `_stone_image_cache`：棋子 PNG 缓存，键=(color, radius, 主题)，主题变化时清空重渲染
- PIL 不可用时自动降级回 Tk create_oval（保证可用）

### 包 C：交互现代化

| 改动 | 效果 |
|---|---|
| 字号阶梯补全 | score 14→20（胜率数字放大为视觉焦点）、h1 15→16、title 12→14、section 10→11、data 10→11、新增 micro 8 |
| 状态药丸胶囊化 | lbl_status 从直角 highlightbackground 改为 accent_s 底色 + 更大 padding，视觉更柔和 |
| `_set_status` 统一方法 | 10 处散落的 lbl_status.config 统一收口，文字色+底色联动（红/琥珀/绿语义色） |

## 统一深色色板（对比度合理化）

核心改进：各区域亮度等差递进，消除"暗区突然有亮部分"的突兀感。

| 区域 | 色值 | 亮度 | 与上级差 |
|---|---|---|---|
| bg 背景 | `#1a1f1d` | 0.115 | — |
| card 卡片 | `#222826` | 0.149 | +0.034 |
| card2 次卡片 | `#2a312e` | 0.183 | +0.034 |
| board 棋盘 | `#3a302a` | 0.197 | +0.015（不突兀） |
| muted 边框 | `#3a443f` | 0.253 | +0.056 |
| accent 强调 | `#3da896` | 0.525 | 降饱和度，不刺眼 |
| text 文字 | `#d8dcd9` | 0.857 | 对比度 0.74（超 WCAG AA） |

原三套主题（LIGHT/DARK/CYBERPUNK）已合并为单一统一深色 `_UNIFIED_COLORS`。

## 技术栈

- **CustomTkinter 6.0.0**：CTkButton（圆角按钮）、CTk 基类（原生圆角窗口）、set_appearance_mode（深色模式）
- **Pillow 12.0.0**：Image/ImageDraw/ImageTk/ImageFilter（棋盘底图 + 棋子 PNG 预渲染）
- **高 DPI 适配**：SetProcessDpiAwareness(1) + tk scaling + CTk ScalingTracker

## 验证结果

| 验证项 | 结果 |
|---|---|
| py_compile（app.py） | ✅ 通过 |
| 对抗检测 test_adversarial.py（144 序列 × 14 不变式） | ✅ 0 违规 |
| 全量非 Tk 回归（34 项） | ✅ 全部通过 |
| test_ui_smoke.py | ⚠️ 既有窗口宽度问题（CTk 前 1500>1280，CTk 后 1771>1280） |

## 妥协点（第一版未做）

- **13 个 Treeview 保留 ttk**（CTk 无原生 Treeview，重写为 CTkScrollableFrame 工作量占包 A 的 40%）
- **13 个 Toplevel 保留 tk.Toplevel**（CTkToplevel 只接 fg_color，标题栏不可美化）
- **动态叠加层（候选点/hover/热力图）暂保留 create_oval**（锯齿在动态小元素上不明显，PIL 化工作量大）
- **设置窗口的 "…" 文件选择按钮保留 ttk**（width=3 太小，CTkButton width 语义不同会变形）
