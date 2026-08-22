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

## 2026-08-21 · 第二波串行轮（接力板 #2 收尾，独占 app.py + ui/）——官子收束训练接入 UI（GAP-3）

**任务**：把上一波落地的 `analyzer/endgame_drill.py`（build_endgame_drills / EndgameDrill / grade_choice，纯逻辑已全绿）接入 UI，形成"出题→落子判分→揭示对比→切题→退出恢复"完整闭环。

**入口与交互设计**：
1. 入口双通道：① practice 页（ui/pages/practice.py，V6 A 区）新增"官子收束训练"卡片——C.card + C.button(primary) + th 令牌，含 `_tree_has_analysis` 主线 O(n) 检查（无分析缓存时 C.status_badge(warning) 引导"先补全分析"）；② 复盘页工具行新增「官子训练」accent 按钮（B 区，与「问题手训练」并排；「教练解读」从 colspan=2 改占半列，无测试冲突）。两入口共用 `app.open_endgame_drill`（V6/非 V6 路径同源）。
2. 训练窗口复用问题手训练的模式与风格（_prepare_child_window + COLORS/FONTS/_make_button + ttk.Scale 题号跳转 + Treeview 候选对比表 + 变化图按钮 + 导航行），但**独立槽位**（_endgame_win/_endgame_set/_endgame_result/_endgame_index/_endgame_revealed，共 16 个方法）——不与 _drill_* 混用，避免 _close_problem_drill 误伤。
3. 题面=主线父局面（`rr.node_at_move(m).parent`，与 snapshot 等价，`len(parent.moves_list()) != start_move_number` 时跳题兜底）；作答=棋盘自由落子 → `grade_choice(d, coord, context=self._assessment_context())`（单一判定源）；候选外选点**不消耗作答不揭示**，提示重选；揭示后自动显示最佳收束序列（复用 _problem_branch_overlay z=40 图层）+ 候选表填充（best/actual 标色）；先手题追加"先手官子先走"提示；总结含题型分布与正确率。
4. 空题集降级：reasons+warnings 逐条弹窗 + 状态栏"官子训练：暂无题目——{首条原因}"，引导「补全分析」。

**守卫接线（trigger-flow 零容忍）**：注册为 `active_modes()` 新元素 **"endgame"**（I13 互斥不变式自动生效），can() 的 play/pass/show_ai 集合、_foreground_busy、_block_jump、do_pass/do_takeback/do_redo/do_goto_root/do_goto_mainline_end/do_step/_start_auto_play、_scrubber_change（全程锁导航）、show_hint/_auto_hint_context_allowed（防泄题）、toggle_pv（盲阶段禁开）、_layer_candidate_cond（全程不画候选防泄露）、enter_scoring/_start_stage_training/open_problem_drill/_start_selected_mistake_review（入口互斥）共 25 处 `_endgame_active()/_block_endgame()` 接线；play() 加 `_block_endgame` 旁路拦；`_reset_for_new_game` 统一清（换棋谱零残留）；`_close_endgame_drill` 全状态清 + `_after_navigate()` 恢复候选/曲线/失误栏。

**踩坑**：① Edit 工具中途被自写 Python 脚本刷新文件状态——批量替换后须重 Read 再 Edit；② 冒烟 fixture 里节点 rootInfo 不连续会造出"伪目损题"（父缓存白视角 +3.0 → 子 0.0 = 目损 3.0），与 test_endgame_drill 的 _normal_overrides 对齐后行为确定（本题面恰好两题，切题/回看路径都被覆盖到）。

**验证（动态计数）**：py_compile；test_endgame_drill PASS；test_ui_smoke PASS（新增 test_endgame_drill_ui：25 检查点覆盖空题集降级/盲阶段/导航点目停一手被拦/候选外不消耗/一选判 best/揭示叠加与候选表/切题干净/回看保持揭示/退出零残留导航恢复）；test_ui_v6 / test_ui_design / test_visual_snapshot（8 项一致，无渲染产物变化不需 --update）/ test_adversarial（225 序列 0 违规）/ test_workflow_sim 全场景 PASS；doc_sync 一致；self_review 21 OK 0 P0/P1（全量回归 56/56）。

**遗留**：① 官子训练作答未回写 LearningEvent/错题本（题源是终局段收束点而非问题手，语义不同；若产品要求"官子能力画像"需先定事件模型）；② 榜外选点无强制分析（grade_choice 设计即"候选外无法评定"，与问题手训练的 allowMoves 路径不同，属有意差异）；③ 训练窗口仍是 tk+COLORS（B 区现状写法），CTk 化归接力板 #6 大迁移统一做。


## 2026-08-21 · 第四波串行写轮（接力板 #6 第一批，独占 app.py）——B 区 tk.Label→CTkLabel 迁移第一批

**迁移范围（高频可见区优先，宁少勿破）**：19 处构造位 / 23 个控件实例：
1. 官子训练窗 6 处（optimizer 标记的越界回添全消化）：_endgame_header / _endgame_sub / _endgame_scale_label / _endgame_instruction / 变化图按钮提示行 / _endgame_summary；
2. 形势卡/指标卡 9 处位：_build_metric_card 标题+值（3 卡 × 2 = 6 实例）、lbl_info / lbl_score / lbl_wr / lbl_territory / lbl_review_coverage_top / 「终局点目」标题 / lbl_scoring；
3. 状态栏/传输栏 4 处位：lbl_status（药丸圆角化 corner_radius=8）/ lbl_msg（圆角 chip，border 映射 border_width+border_color）/ lbl_move_num / lbl_scale。

**新基建（后续批次直接复用，写法已定型）**：
- `_make_label(parent, text, font, fg, bg, border, **kw)` 工厂：CTk 时 CTkLabel——fg→text_color、bg→fg_color、border→border_width+border_color；降级真实 tk.Label——border→highlightthickness+highlightbackground、corner_radius 忽略；width 按字体 '0' 像素宽从 tk 字符宽换算（两派单位不同，实测 lbl_move_num 16 字符→192px）。
- `_label_set(widget, text, fg, bg, border)`：动态更新统一入口（同上映射；lbl_score 语义变色/_set_status/_set_msg/覆盖率变色全部走它）。
- 防护两处：_polish_card 遇 CTkLabel 整棵跳过（否则递归会刷其内部 tkinter.Label 的 bg，破坏 card2 底上的圆角/变色视觉）；_remap_widget_colors 的 except 增补 ValueError（CTk 6 的 cget 不认 tk 属性名，Ctrl+T 主题刷新路径防崩——此前 CTkFrame 就有此隐患，本轮顺手加固）。

**CustomTkinter 6.0 实测要点（与 5.x 网上资料不同，勿凭记忆写）**：
- `.config()` 被 CTk 6 显式禁用（AttributeError："always use 'configure'"）；fg/bg 传入 configure/构造即 ValueError——所以迁移过的属性其所有更新点必须同步改；
- CTkLabel 内部仍保留 tkinter.Label 子控件 → test_workflow_sim 的 _find_widgets/_wtext 递归照常命中（W23 等 UI 仿真不受影响），`cget("text")` 可用（全部测试的 lbl_msg 文案断言原样通过）；
- padx/pady/justify/anchor/wraplength 经 **kwargs 直通有效；默认 corner_radius=0、fg_color=transparent、anchor=center（与 5.x 一致）；
- test_ui_smoke 两处 `lbl_msg.cget("fg")` 断言改为模块级 `label_fg()` 双派访问器（先试 text_color 再回落 fg，检查意图不变：成功绿/错误红语义色）。

**量化（现场 grep，宽口径=grep -c "tk\.Label" 含 ttk 与文档串提）**：宽口径 82→67；tk.Label 构造位 72→53（-19）；CTkLabel 提及 0→7；_make_label 调用位 19（_build_metric_card 工厂复用另计 3×2 实例）。

**降级验证（真实执行非纸面承诺）**：`sys.modules['customtkinter']=None` 屏蔽后整窗构建——13 个跟踪属性全部原生 tk.Label；_set_status/_set_msg/_label_set 的 fg/bg 分支颜色正确（#5abb80/#e06560/#2a4a44 实测回读）；官子训练窗以 60 手分析谱 fixture（与 test_ui_smoke 场景同构）真实开窗，6 标签全 tk.Label 且 scale 标签 width=12 字符宽语义保留。注意探针要拦 messagebox.showinfo（空题集路径会弹模态框挂起无头验证）。

**验证**：py_compile / test_ui_design / test_ui_v6（含 Token Test）/ test_ui_smoke（193 检查点，官子全链+语义色全过）/ test_adversarial（225 序列 0 违规）/ test_workflow_sim 全场景（含 W23 在线导入）/ test_visual_snapshot（8 项基线一致——控件层迁移不触 PIL 渲染产物，无需 --update）/ self_review（21 OK 0 P0/P1，全量回归 56/56）/ doc_sync 一致。

**剩余清单（下一批候选，按可见频率排序）**：① 复盘 tab 区：lbl_review_summary / lbl_profile / lbl_review_coverage / lbl_problem_position + _empty_card 空状态卡 + 导航卡两段提示文案；② 问题手训练窗 _drill_* 镜像 6 处（与官子窗同构，照搬本批模式即可）；③ 学习中心/棋谱库/批量分析队列窗口的标题与卡片段落；④ 顶栏 lbl_game_title / lbl_game_meta / brand_title / workbench_title；⑤ 胜率曲线图例 _graph_legend_label、棋力评估 _strength_summary_lbl。全部走 _make_label/_label_set，B 区禁止再手写裸 tk.Label。

## 2026-08-21 · 波 7 串行写轮（接力板 #6 第二批，独占 app.py）——B 区 tk.Label→CTkLabel 迁移第二批

**迁移范围（第一批剩余清单 ①-⑤ 全领，宁少勿破全走工厂）**：33 处构造位：
1. 复盘 tab 区 7：lbl_review_summary / lbl_profile / lbl_review_coverage / lbl_problem_position / _empty_card 内部 title+hint（#9 修复的空态窗标题已是 _make_label，本批把 _empty_card 本体补齐保持一致）/ 训练行提示文案；
2. 问题手训练窗 7（官子窗镜像 6 + 字母行提示 1）：_drill_header / _drill_sub / _drill_scale_label / _drill_instruction / 变化图按钮提示行 / _drill_summary / 字母行提示；
3. 学习中心 7（标题/导语/状态摘要条/入口卡 title+badge+desc/脚注）+ 棋谱库 4（标题/双击提示/训练方/画像身份）+ 批量队列 2（标题+说明）；
4. 顶栏 2：lbl_game_title（width=30 字符宽走工厂 '0' 宽换算）/ lbl_game_meta（width=54）；
5. 胜率图例 _graph_legend_label + 棋力摘要窗 3（视角/图例/摘要 _strength_summary_lbl）。

**更新点同步整改（CTk 6 `.config` 禁用，共 21 处）**：纯 text 更新 → `.configure(text=...)`（与官子窗第一批同款）；含 fg 更新 → `_label_set`（lbl_review_coverage 变色、lbl_problem_position 红橙、_graph_legend_label 图例换色、drill instruction 揭示/总结变色、drill summary 总结变色）。复盘 tab 区 4 属性共 7 处 + drill 窗 9 处 + 顶栏/图例/摘要 5 处。

**经验沉淀**：
- 无 font 参数的 tk.Label（视角：/画像身份…）走工厂默认 FONTS["ui"]——app 全局 ttk style "." 已是 FONTS["ui"]（app.py s.configure(".", font=FONTS["ui"])），工厂默认反而对齐标准（原 tk.Label 用 TkDefaultFont 是漏网）；
- drill 窗与官子窗同构性极好，7 构造位+9 更新点照搬镜像零改动（endgame 7819/7850/7856/8029/8075 用 .configure、7839/7927/8065 用 _label_set 的分工直接复刻）；
- test_ui_smoke 个人画像场景用 `isinstance(widget, tk.Label)` 收集 label_texts——**画像窗 9 构造位本批刻意不动**，第三批迁移时 CTkLabel 内部 tkinter.Label 理论上仍会被递归命中（第一批 W23 已验证此机制），但需实跑确认后再生效。

**量化（现场 grep）**：宽口径 `grep -c "tk\.Label"` **67→34（-33）**；tk.Label 构造位 **53→20**（含工厂降级位 1，实剩 19：画像窗 9 + 候选行 4 + brand/workbench 标题 2 + 棋局编辑卡 2 + 粘贴SGF 1 + tooltip 1）；`_make_label` 调用 21→54（+33，与构造位增量严丝合缝）；CTkLabel 提及 7（工厂内部，不变）。注：git HEAD 仍停在波 3（76），工作树含波 4-7 未提交增量。

**降级路径**：未动 `_make_label`/`_label_set` 本体（第一批已做 sys.modules 屏蔽实测），本批全部经工厂两派分发；`_polish_card` 的 CTkLabel 整棵跳过防刷色逻辑原样保留（1281-1284，本批未触碰）。

**验证**：py_compile / test_ui_smoke（全绿）/ test_ui_design / test_ui_v6 / test_adversarial（144 序列）/ test_workflow_sim 全场景（含 W29 画像空态窗/W35 hover 守卫）/ test_visual_snapshot（8 项基线一致，控件层迁移不触 PIL 渲染产物）/ doc_sync 一致 / self_review 21 OK 0 P0 0 P1 1 P2（历史项），全量回归 **57/57** 绿。

**剩余清单（第三批候选）**：① 个人画像窗 9 构造位（9749-9874 区段，先实测 test_ui_smoke isinstance(tk.Label) 收集在 CTkLabel 下是否仍命中）；② 候选行 4（_candidate_empty_label / rank_label / win_label / pv_label，注意 _keep_custom_bg 包裹语义）；③ 顶栏 brand_title / workbench_title 2；④ 棋局编辑卡 intro+快捷键提示 2；⑤ 粘贴 SGF 对话框提示 1；⑥ tooltip 内部标签（relief="solid" 反色浮窗，特殊样式建议单独评估或保留 tk）。

## 2026-08-22 · 波 11 串行写轮（接力板 #13）——备份恢复 UI 入口（数据安全闭环）

**任务**：backup.py 每日自动备份有产出、用户无恢复入口。本轮补上恢复链：列表 → 二次确认 → 校验 → 转存 → 原子替换。

**入口取舍（报告结论）**：系统设置页新增「数据安全」区块（row 3, colspan 2）+ 专用「备份与恢复」窗口，而非棋谱库右键菜单——恢复覆盖的是库+设置整体且属低频危险操作，放设置里让用户主动寻路，避免右键误触；且 dialogs.py 是波 5 工厂助手（_frame/_label/_checkbutton/_option_menu）的现成开发区，UI 薄层直接复用。设置窗高度 690→800 容纳新区块。备份列表保留 ttk.Treeview（B 区妥协方案，CTk 无替代）。

**改动清单**：
- `backup.py`：`RestoreError`（消息自含后果说明——"原库未改动"只在校验/准备段说，设置写失败的极晚期失败明说"库已恢复但设置写入失败"）；`list_backups`（轻量探测=开 zip 读 index 数局数，不做全量 CRC——完整校验留到恢复动作）；`restore_backup` 六步安全序：全量校验（CRC/zip-slip 路径逃逸/有库文件/index 可解析）→ 磁盘余量预检（×1.1+1MB）→ 同级 `.restore-staging` 解包（同卷保改名原子）→ backups/ 逐件搬进暂存（`_is_inside` 判定，防历史备份随旧库被挪进 pre_restore 最终被清理；搬移被占用则尽力搬回放弃恢复）→ 现库整体转存 `.pre_restore-<stamp>`（同秒多次恢复不撞名：撞名追加 -N 后缀；`_prune_pre_restore` 滚动保留 2 份，库目录与设置副本成对同 stamp）→ 暂存改名顶上（失败把 pre_restore 改回回滚）→ 设置 `.tmp`+`os.replace` 原子写回。
- `ui/dialogs.py`：`open_backup_manager`（单实例窗：日期/大小/局数/完好四列，损坏行标红，红色警告条常驻"不可撤销+自动转存副本"）+ `_refresh/_close/_restore_selected_backup`（模块函数+app 薄委托，测试可直调）；`_restore_selected_backup`：未选中如实提示 → ok=False 直接拒 → askyesno 二次确认（含日期/局数/覆盖警告）→ 成功 showinfo 提示**重启刷新内存态**（刻意不做内存热重载——库+设置双内存态热换风险大于收益）并关窗清引用、`_log_usage("backup_restored")`；RestoreError 走 showerror 原样透传原因。
- `app.py`：`__init__` 3 属性（_backup_mgr_win/_tv/_map）+ 4 薄委托方法（open/close/refresh/restore，模式同 open_settings）。
- 设置页入口：`open_settings` 新增数据安全 section（section() 工厂 + _label + _make_button）。

**坑与经验**：
- CRC 损坏注入不能盲翻 zip 中段字节——小 zip 的中段可能落在文件名/元数据区，校验照样通过（恢复成功=假阴性）。必须定向：`zi.header_offset + 30 + len(zi.filename) + len(zi.extra)` 打压缩数据区。
- pre_restore 时间戳 `%Y%m%d-%H%M%S` 含连字符，同秒多次恢复撞名（Windows os.rename 目标存在即 FileExistsError）；撞名追加 -N，且设置副本要用**去撞名后**的同一 stamp（`os.path.basename(pre_restore).split(".pre_restore-", 1)[-1]`），否则设置副本互相覆盖、滚动清理数对不上。
- `_index_game_count` 语义：index 缺失/不可解析=None（"未知局数"），dict 无 records 键=0（空库如实报 0）——UI 用 "—" 区分未知与 0。

**硬规矩落网说明**：本任务是功能闭环非 P0/P1 bug 修复，回归落 test_backup 3 新用例（正常往返含 backups 搬回与无残留断言/四种坏输入拒绝且现场原样/缺失报错+滚动清理）+ W38 全链场景（19 断言，现场扫描）；未新增 invariants 条目（恢复是 IO 流程非 UI 态，invariants 的 per-step 检查形态不适合，W38 的现场原样断言即等价约束）。

**验证**：py_compile / pyflakes 4 文件 0 警告 / test_backup 6 用例全过 / test_workflow_sim 全场景（新增 W38）/ test_adversarial PASS / test_ui_smoke exit 0 / 全量 57/57 绿 / self_review 20 OK 0 P0 0 P1（2 P2 均历史项）/ doc_sync 一致 / test_visual_snapshot 8 基线一致（纯对话框不触 PIL 渲染产物）。

**用户视角**：系统设置 → 数据安全 → 「备份与恢复…」→ 列表选一份（日期/局数）→「恢复所选备份…」→ 确认警告 → 完成后重启应用。恢复前当前库与设置自动转存 `game_library.pre_restore-*`（保留最近 2 份），损坏备份列表标「损坏」且拒绝恢复。

## 2026-08-22 · 波 12 串行写轮（接力板 #6 第三批重发，独占 app.py）——B 区 tk.Label→CTkLabel 迁移第三批（收口批）

**迁移范围（第二批剩余清单全领，18 处构造位）**：
1. 个人画像窗 9：标题/副标题/指标卡 label+value（5 卡 ×2 实例）/actions 行提示/learn_card 标题+副标题+摘要/优先训练脚注；
2. 候选行 4：_candidate_empty_label / rank_label / win_label / pv_label（pv 仍常驻隐藏，仅构造位迁移）+ **5 处更新点同步**（:3268 win `.config→.configure`、清空循环 2、状态文案 1、_style_candidate_row 的 rank/win/pv 选中态刷色——后者原 `except tk.TclError` 接不住 CTk 的 ValueError，改走 `_label_set` 双派并扩 except）；
3. 顶栏 brand_title / workbench_title 2；4. 棋局编辑卡 intro+快捷键 2；5. 粘贴 SGF 提示 1。

**前置实验（任务书重点，一次性探针实证）**：test_ui_smoke 画像场景的收集器 = `descendants()` 递归 winfo_children + `isinstance(widget, tk.Label)` + `cget("text")`。探针复刻该路径实测：CTkLabel 内部结构 `[CTkCanvas, Label]`，递归命中内部 tkinter.Label 且文本同步（"平均目损"被收集）→ **HIT，收集器无需改双派，直接迁移**。迁移后 test_ui_smoke 的 label_texts 断言实测收集到全部 9 处迁移标签文本，真实路径双重确认。

**_keep_custom_bg 评估结论（任务书要求）**：候选行 4 处的包裹**保留不可撤**——CTk 路径 _polish_card 遇 CTkLabel 整棵跳过，包裹冗余但无害；降级路径（无 CTk）tk.Label 仍依赖它防 _polish_card 刷白底。理由已写入代码注释（:1656 区段）。

**tooltip 1 处刻意不迁（记录理由）**：_attach_tooltip 内 `relief="solid" + borderwidth=1` 的反色浮窗（bg=text 色/fg=card 色自绘边框）——CTkLabel 无 relief 语义（border_width+border_color 是圆角包围盒描边，形态不同），overrideredirect 瞬态小浮窗非高频渲染面，迁移 ROI 低且易破紧凑形态。保留 tk.Label。

**踩坑**：降级探针中 game_library 无 set_path 式重定向（接力板 #8 只改了 store 家族与 usage_log）——探针只读+建窗不落库即可，usage_log.set_path 指临时路径防埋点渗入生产。

**量化（现场 grep）**：宽口径 `grep -c "tk\.Label"` **34→17（-17：18 构造位消除 +1 新增注释行含该字样）**；`tk.Label(` 实构造位 **20→2**（= 工厂降级位 :871 + tooltip :1409，均为设计保留）；`_make_label` 调用 54→72（+18 严丝合缝）；CTkLabel 提及 7→9（新增 2 为注释）。三批累计：tk.Label 103→2 实构造位。

**降级验证（真实执行）**：`sys.modules["customtkinter"]=None` 屏蔽后整窗构建 + 交互调用，12/12 OK——本批 18 构造位全部原生 tk.Label（brand/workbench/编辑卡/粘贴SGF 文本定位命中，rank/win/pv 3×3 全 tk.Label 且 width=2/9 字符宽语义保留），_show_candidate_state/_style_candidate_row 的 tk 分支颜色/文本回读正确。

**验证**：py_compile / test_ui_smoke（画像 label_texts 完整收集）/ test_ui_design / test_ui_v6（含 Token Test）/ test_adversarial / test_workflow_sim 全场景（含 W38）/ test_visual_snapshot（8 项基线一致——控件层迁移不触 PIL 渲染产物，无需 --update）/ doc_sync 一致 / **全量回归 57/57 绿**。

**接力板 #6 销单**。B 区 tk.Label 迁移收官：剩余 2 实构造位均为有意保留（工厂降级路径 + tooltip 特殊样式）；`ttk.Label` 3 处（目差图三段说明，8350 区段）为 ttk 家族不在本任务口径，低频说明文字，遗留候选。后续新代码维持"一律走 _make_label/_label_set 工厂，禁止手写裸 tk.Label"规矩。
