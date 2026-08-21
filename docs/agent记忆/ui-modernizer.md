# agent 记忆 —— ui-modernizer（UI 现代化·双区制）

> 动态事实区：由 ui-modernizer 每个方向完成时追加。短板进度与控件计数在此维护。

## 2026-08-21（初始化）
- 双区制确立：ui/ V6 区（重写标准，令牌唯一来源 ui/tokens.py）+ app.py 旧区（渐进迁移）。
- 基准快照（2026-08-21，动态验证）：app.py tk.Label 103 / CTkLabel 0 / tk.Toplevel 16。

## 2026-08-21 · 并行轮（只动 A 区）——Token Test 修复 + on_accent 令牌化

**动态盘点（现场 grep，非记忆值）**：
- ui/ 共 13 个 .py（shell/components/timeline/render/theme/tokens/dialogs + pages/home·library·learning·practice，共约 1832 行）；app.py 接入点 11 处（tokens.PALETTE :152、Shell/HomePage/LibraryPage/LearningPage/PracticePage :817-825、components.segmented :878、LearningTimeline :1107、dialogs :2830/:5807、nav_metrics :10211）。
- pages/shell/timeline 颜色全部走 th.t()/令牌，无散落 hex；timeline PIL 预渲染+原生降级双路径完好；间距仅 2 处 pady=1（发丝级，可接受）。
- dialogs.py 仍为全 tk 控件（经 app COLORS 取色=令牌传导，色值合规；CTk 化留后续串行轮——本轮 22:20 有并行 agent 在改它，避让）。
- shell.py 导航收起标签 center 计算含魔法数 (nav_w-8)//12，与 nav_metrics 令牌轻度失联（低 ROI，遗留）。

**本轮修复（方向 1：令牌唯一来源闭环）**：
1. `test_ui_v6.py` Token Test 检测 bug：`line.split("#")[0]` 意在剥注释，实际把字符串字面量里的 `#rrggbb` 一并截掉——**硬编码色值检测恒空转**。改为 `re.sub(r"\s+#.*$", "", line)`（只剥 # 前有空白的注释），并验证：字面量 hex 能命中、注释/令牌引用不误报。
2. `ui/tokens.py` PALETTE 新增 `on_accent: "#ffffff"`（彩色填充上的文字色语义）。
3. `ui/components.py` 5 处 `"#ffffff"` → `th.t("on_accent")`（button primary/danger ×2 路径 + segmented 选中态）。修复前该文件本应被 Token Test 拦下，因检测 bug 漏网。

**验证**：test_ui_v6（含修复后 Token Test）/test_ui_design/test_ui_product/test_ui_smoke 全过；test_adversarial 144 序列 PASS；doc_sync 无漂移。

**遗留短板（下轮候选，按 ROI）**：① dialogs.py 全 tk 控件 CTk 化（需串行轮，注意并行冲突）；② shell.py 收起态标签排版魔法数收敛到 tokens；③ B 区 app.py 迁移基准本轮只读未动（按并发轮协议留串行）。

**转记（code-auditor）**：并行轮期间 `test_workflow_sim.py` W23#14 失败（批量导入消息期望"新增1/失败1"，实际"分析队列：在线对局.sgf（0/4）"）。本轮 ui-modernizer 改动仅为色值令牌+测试检测正则，与该场景无因果；时序指向并行 agent 对 app.py（工作区有未提交改动）或已提交的 dialogs.py 外迁（5ec7825）。建议 code-auditor 在串行轮复核。

## 2026-08-21 · 第三波并行轮（接力板 #5）——ui/dialogs.py CTk 化

**动态盘点（现场读文件）**：dialogs.py（外迁重构后）共 4 个窗口函数：open_online_import（W23 真实 UI 全链仿真覆盖）/ show_training_report / open_mistake_book（W16 双点位注入覆盖）/ open_settings（test_ui_smoke 开关窗覆盖）。色值全部经 app COLORS 惰性 import 传导（合规），但控件全 tk/ttk（约 tk.Frame 17、tk.Label 30+、ttk.Entry 7、ttk.OptionMenu 4、tk/ttk.Checkbutton 2）。

**本轮改动（仅 ui/dialogs.py，未动 app.py）**：
1. 文件头新增 CTk 工厂助手 5 个（_frame/_label/_entry/_checkbutton/_option_menu），统一"CTk 优先 + 无 CTk 降级 tk/ttk"双路径；CTkLabel 在混排 tk 容器里显式传 fg_color（transparent 在纯 tk 父容器会露底）。
2. 迁移计数（tk/ttk→CTk，CTk 可用时）：CTkFrame 17、CTkLabel ~30、CTkEntry 7（URL/OGS 输入 + 设置 5 个数值项）、CTkOptionMenu 4、CTkCheckBox 2 —— 共约 60 个控件。
3. 保留 ttk 的（按迁移表/克制原则）：Treeview ×2（CTk 无替代）、Scrollbar、Combobox ×2、Spinbox ×2、设置"…"小按钮 ×2、风格预览 tk.Canvas；窗口仍走 app._make_centered_toplevel/_prepare_child_window 的 tk.Toplevel（改 CTkToplevel 需动 app 区 configure(bg=) 链路，避让并行区，遗留串行轮）。
4. W23 兼容关键：CTkEntry 外层 winfo_class 为 Frame，但 test_workflow_sim._find_widgets 递归能命中其内部 tk.Entry，textvariable 共享故 insert/delete 仍生效——W23 实测通过。
5. 踩坑：CTkCheckBox 初版写了 checkmark_color="#ffffff"，被 Token Test（上轮修复的检测）当场拦截——改为省略参数用 CTk 默认白。token 闭环已在起作用。

**验证**：py_compile / test_ui_design / test_ui_v6（含 Token Test）/ test_ui_smoke / test_adversarial（144 PASS）/ test_visual_snapshot（8 项基线一致，无需 --update——本改动不触 PIL 渲染产物）/ test_mistake_book / test_online_import / test_workflow_sim（含 W23 在线导入真实 UI 全链）全部通过。

**遗留**：① 窗口壳 tk.Toplevel→CTkToplevel（需配合 app._prepare_child_window 改 fg_color 链路）；② 设置页 Combobox/Spinbox 仍 ttk（低频，视觉影响小）；③ 上轮转记的 W23#14 时序问题本轮 W23 全过，视为并行期已消解。


## 2026-08-21 · 串行写波（接力板 #7，用户直提）——主窗布局重构：棋盘增大 + 边栏密集化

**用户原话**："现在的棋盘布局不对，棋盘可以增大，其他功能可以做成边栏然后适当密集缩小"。对标 KaTrain/LizzieYzy"棋盘左大区 + 右窄信息栏"。

**布局方案与落地**：
1. 右边栏 480 → **396**（`RIGHT_PANEL_WIDTH`，tokens.BREAKPOINTS right 列同步 396/396/356）；pane minsize 400→360。
2. 顶栏 appbar 64 → **42**：副标题两处收进 tooltip（"本地 AI 研究工作台"/"局面判断·推荐研究·问题复盘"）；品牌标 38→24 且 `_draw_brand_mark` 改为按 Canvas 实际尺寸等比绘制（未映射时 winfo=1 需回退 24——踩坑）；按钮去 big、padding 降档；**"棋谱库"顶栏按钮删除**（与左导航"棋谱"重复，test_ui_design 的 `_make_button(app_actions, "保存"` 标记不受影响）。
3. 密集化：传输栏 pady 7→4、时间轴行 56→46（`LearningTimeline(height=42)` 显式传参，否则 canvas 请求 52 在 pack_propagate(False) 容器里被裁）；指标卡 padding 9/7→8/5；状态 chip 12/6→8/3；tab 帧 8/8→6/6。
4. 棋盘放大：`BOARD_SCALE` 0.84→**0.95**；`_do_resize` 宽扣除 28→12、高预留 190→158、fallback 常量 40→19（= padx12+sash7）；**新增钳制循环**（MARGIN=0.78×cell 取整会使 BOARD_PIX 超 avail，逐档回退 cell）——0.95 高占比下这是防溢出关键。

**量化结果（公式路径，确定性口径）**：1600×900 下 606×606→**704×704**（+16.2% 线性/+34.9% 面积）；1200×800 下 488×488→**606×606**（+24.2%/+54.2%）。实测路径同窗口亦无溢出（棋盘底边距传输栏 10px）。

**联动坑（都已处理）**：① `wr_canvas` 硬编码 width=410 会在 396 面板溢出→改 `max(240, RIGHT_PANEL_WIDTH-66)`；② 旧分栏记忆 sash 钳制 `max(500, w-440)` → `max(420, w-367)` 迁移；③ test_ui_smoke"右侧≥460"断言编码旧布局意图→改引 `RIGHT_PANEL_WIDTH` 常量（防再漂移）。

**验证**：py_compile / test_ui_design / test_ui_v6 / test_ui_product / test_ui_smoke / test_adversarial（144 PASS）/ test_visual_snapshot（8 项一致，无需 --update——布局不动 PIL 渲染产物）/ test_workflow_sim 全场景 / self_review（21 OK，0 P0/P1，全量回归 55/55）全绿；doc_sync 一致（docs/UI设计说明.md 的 480 描述 doc_sync 不覆盖，已手工同步）。

**遗留（下轮候选）**：① 接力板 #6（B 区 app.py tk→CTk 迁移）仍待领，本轮 appbar/右栏新代码维持 tk+COLORS 令牌写法（与 B 区现状一致）；② 顶栏游戏元信息 font=micro（8pt）偏小，若用户反馈可读性差可回 small；③ 窗口极窄（<1040）时边栏仍 396 固定，未做 Compact 档 356 的响应式接线（BREAKPOINTS 已留位）。
