# agent 记忆 —— trigger-flow-auditor（交互流程闭环）

> 动态事实区：由 trigger-flow-auditor 每轮审查结束追加。流程缺陷模式 = "操作路径→残留→对标处理"。

## 2026-08-21（初始化）
- 单一职能制确立：三类零容忍为唯一职能。
- 已知反面教训：_load_project_from_path 曾漏调 exit_scoring（点目残留锁盘）——同类调用链缺口是最高频模式。

## 2026-08-21（第 2 轮：并发只读审查 + 入口缺口修复）
- **架构现状（动态验证，勿引用旧记忆）**：换棋谱清理已统一收敛到 `_reset_for_new_game`（app.py:5173），`_load_project_from_path`/`do_import_sgf`/`do_reset`/`do_reset` 全走它；旧清单里的 `_auto_open_after_import` 已不存在（→ `_maybe_auto_analyze_library_game`）。叠加层已图层化（`_overlay_layers` z-order + condition 互斥，app.py:3594 附近），点目独占 z30。
- **新缺陷模式：模式入口函数的"对等拦截矩阵"不完整**。enter_scoring/_start_auto_play 拦截全部 4 个兄弟模式，但 open_problem_drill/_start_stage_training 漏了 mistake_review 拦截、drill 漏了 _stop_auto_play。模式越多个，入口函数拦截矩阵越容易漏一格——审查方法：对每个模式入口画 N×N 矩阵逐格核对（比顺读代码快）。
  - 修复 1：_start_stage_training 补 mistake_review active 拦截（复习题面下开训练→落子对旧题面判分）。
  - 修复 2：open_problem_drill 补 mistake_review 拦截 + _stop_auto_play（quiz 字母绑定局面，自动播放推进棋盘字母浮错位置）。
  - 验证：py_compile + test_adversarial 144 序列 0 违规 + test_problem_drill_ui/test_training_controls/test_mistake_book 全 PASS。
- **并发会话事实**：本轮同会话有其它 agent 改 learning_store.py/review.py，test_workflow_sim W23（在线导入消息计数）/W25（队列×前台根分析身份）失败与 app.py 触发流程无关（git diff app.py 仅含本 agent 9 行守卫）。判定方法：先 `git diff analyzer/app.py` 确认自己改动面，再看失败 check 是否落在自己改动面内。
- **越界转记**：无（图层架构与 ui/ 相关观察未发现新问题）。

## 2026-08-21（第 3 轮：历史欠账补转化——bug A/B 落回归网）
- **背景**：第 2 轮修复的 2 个入口拦截 bug 未按硬规矩第 0 条转化为回归项，本轮补上。
- **转化落地**（invariants.py + actions.py + test_adversarial.py 接线）：
  - 新增不变式 I19（入口拦截：注入 mistake_review.active 后调入口，training/drill 任一 active 即违规——锁死 bug A/B 的守卫）+ I20（open_problem_drill 执行后 _auto_play 必须为 False——锁死"不停自动播放"半边）。注册在新增 POST_ENTRY_INVARIANTS 组，仅 entry 类动作后检查。
  - actions.py 新增 3 个 entry 类动作：entry_training_in_review / entry_drill_in_review / entry_drill_autoplay。复习态注入用真实 dict 形状 {"active","item","parent","attempts"}（照 app.py:_start_selected_mistake_review:8782）。
  - **设计要点（防假回归）**：入口被拦的动作若后续代码 raise，_try 会吞掉异常返回 blocked，不变式照样通过——所以必须"活性验证"：无复习态时同一路径确实把 _training 置 active（已验证 True），证明动作打得到守卫。
  - **不能合并的动作**：mistake 拦截路径在守卫处提前 return（不会走到 _stop_auto_play），"复习中开 drill"与"自动播放中开 drill"必须拆成两个动作，否则 I20 在正确的拦截行为下反而误报。
- **验证**：test_adversarial 15×15=225 序列 0 违规（原 12×12=144）；test_workflow_sim 全场景通过；三文件 py_compile 通过。
- **经验**：12 动作涨到 15，对抗枚举规模随动作数平方涨——后续加动作优先考虑合并前置（同 seed 同路径）或控制总量。
- **越界转记**：无。

## 2026-08-22（第 4 轮：主窗布局重构专项审查）
- **审查面**：commit e301125（棋盘 0.84→0.95、右栏 480→396、顶栏 64→42、删顶栏「棋谱库」按钮、副标题收 tooltip、_do_resize 钳制循环、sash 迁移 440→367）。走查链：_do_resize/_build_ui/_restore_pane_position/_draw_brand_mark/_on_configure。
- **修了 3 个（1 高 2 中，全部在布局触发链上）**：
  1. [高] sash 拖到极限棋盘被右栏裁剪且永不重算（app.py _on_configure 只认根窗口事件；Tk 探针实证：sash_place 后 canvas 被压到 pane 宽、canvas 改尺寸不会反拉 sash——补 `_board_panel` 自身 `<Configure>` 走同一 80ms 防抖链，无振荡风险）。对标：KaTrain 无可拖分栏（棋盘固定左大区），本项目保留 sash 就必须保证任意 sash 位棋盘自适应。
  2. [高·竞态] `_restore_pane_position` 在窗口未映射时 winfo 宽度=1 → max_position=420 → 保存的 sash 位（如实测 936）被钉死在左极限（棋盘挤成 370px 小方块、右栏占满半屏）。修复：未映射进 120ms×5 重试链。**通用坑：一切"按 winfo 宽度做钳制/迁移"的代码都要先问未映射时 winfo 返回什么**。
  3. [中] _do_resize 估算路径（复盘页未显示时）假定右栏=396，无视 sash 实位——用户拖宽右栏后在别的页拉伸窗口，回复盘页棋盘超宽被裁。修复：优先 `workspace.sash_coord(0)[0]`。
- **回归转化（硬规矩第 0 条）**：新增 test_workflow_sim.py W28 场景（契约：任意 sash 位 面板宽-BOARD_PIX≥0；左极限重算生效、右极限单调放大；withdraw/deiconify 模拟未映射竞态，sash 必须恢复到保存位不得钉死 420）。**未加 invariants.py 条目的原因**：Tk 几何/事件行为不属于棋局状态不变式，invariants harness 无 Tk 几何断言形态，W28 场景（真实 Tk 实例 + advance 泵事件）是正确落点。
- **验证**：test_adversarial 225 序列 0 违规；test_workflow_sim W1–W29 通过（W30 失败归属并发 agent 的配置热切换 WIP，见下）；test_ui_smoke/design/product/v6 全过；self_review 56/56 全绿；doc_sync 一致。
- **删「棋谱库」顶栏按钮的承接核验（全通）**：Ctrl+L / 左导航「棋谱」页（LibraryPage 复用 _build_library_into 全功能）/ 右栏「棋谱」tab 按钮 / 无 Shell 回退 Toplevel 四路完整；双击打开→_close_library_window→router.go("review") 闭环在。副标题 tooltip 可达（_attach_tooltip Enter 520ms）。
- **sash 迁移 440→367 无半迁移**：restore 时钳制（367=右 pane minsize 360+sash 7，与 add(right,minsize=360) 自洽）+ _persist_workspace_state 关闭时重写，config 默认 0，无其他读者。但注意上面竞态 bug 藏在钳制的宽度来源里——迁移本身对，宽度来源错。
- **并发会话事实**：同一时间另一 agent 在做 endgame_drill + W29/W30 场景（test_workflow_sim.py mtime 滚动，app.py 00:19 被其加画像窗口重开重算逻辑）。W29 已被其修好；W30（配置热切换×队列领取）失败时其仍在写——判定归属方法同第 2 轮：git diff 确认自己改动面 + 失败 check 是否落在自己改动面内。编辑 app.py 前每次重新 ls mtime。
- **越界转记（→ product-analyst）**：docs/UI设计说明.md §六 子窗口表「学习中心｜顶栏「学习中心」」「个人画像｜复盘工具/学习中心」的入口列已失真——学习中心 Toplevel 当前无常驻 UI 入口（仅 API 保留，学习详情走首页「查看学习详情」），属产品层入口收敛问题。

## 2026-08-22（第 5 轮：官子收束训练交互闭环专项，波 5 并行）
- **审查面**：官子训练全闭环（practice 两态入口/Scale 跳题/候选内外作答/揭示/实战延续/总结/按钮-关窗-Esc 三退出）+ 互斥矩阵 5 模式双向逐格 + z40 叠加层生命周期 + 边角（首尾题/Scale 越界/单题集/连续作答）。矩阵与叠加层结论：**全格有拦有提示**（I13/active_modes 注册完备，enter_scoring/_start_stage_training/open_problem_drill/_start_selected_mistake_review/_start_auto_play 双向全拦；叠加层在切题/总结/关闭/换谱四路径全清）。教练解读不拦官子＝允许且与 drill 一致（只读弹窗、不泄当前题答案）。
- **修了 5 个（1 致命 3 高 1 中，全部 headless 探针实证后修）**：
  1. [致命] `_scrubber_commit` 只拦点目——官子/问题手作答中/错题复习/阶段训练的导航锁全被"拖时间轴松手"绕过：题面被推离、训练窗仍激活、点击被**错位局面判分**（探针：commit(10) 后 depth 60→10，C2 点击仍记成第 55 手作答）。修复：抽 `_scrubber_locked_reason()` 让 change/commit 同源（新发现：阶段训练键盘拦、拖动漏，一并补格），被拦时 `_update_scale()` 把滑块视觉弹回题面。
  2. [高] Esc 关窗**全线失效**（所有 `_prepare_child_window` 子窗口）：`event_generate("<WM_DELETE_WINDOW>")` 是 WM 协议名不是事件名，Tcl 层直接 bad event type（回调异常被吞、窗口纹丝不动）。修复：`_dispatch_wm_delete` —— `win.protocol("WM_DELETE_WINDOW")` 查询返回 Tcl 命令名，`win.tk.call(cb)` 反调＝等价点 X（未注册的窗口返回内建 destroy，语义不变）。
  3. [高] 「结束并查看总结」后棋盘不锁：落子仍作答并把窗口从总结页翻回题目态（探针：answered 0→1、指令栏变"✓ 合理：C2…"）。修复：`_endgame_show_summary` 置 `_endgame_revealed=True` 终态锁盘。
  4. [中] 走完最后一题「下一题」总结仍显示「已中途结束」——`index >= len` 判定分支是死代码（没有任何路径把 index 推到 len）。修复：末题下一题置 `index=len` 进终态 + finished 判定补 `answered >= len`（drill 同款同修）。
  5. [高·同构] 问题手 drill 的 3/4 同款缺陷（总结态不锁盘、已结束死分支、`_drill_show_variation` 无终态守卫）——官子代码是 drill 复制的，缺陷也是复制的。
- **回归转化（硬规矩第 0 条）**：test_ui_smoke.test_endgame_drill_ui 扩 8 断言（3b 总结态锁盘不判分/7b 松手不绕锁/7c 自然完成判已结束+终态 index/Esc 拆"绑定接线+分发器直调"两半）；invariants I5 + snapshot_modes + harness clean() 补 endgame_active（换谱残留网此前对官子是盲区）。
- **新缺陷模式（候选第四类零容忍）：同一动作的多入口只拦一半**。动作有 拖动(motion)/松手(release)、键盘/鼠标、按钮/直调 多个触发入口，守卫只写在其中一个入口＝绕锁。审查方法：对每个锁定动作 grep 全部触发入口（`_scrubber_change`/`_scrubber_commit`、`do_step`/`_on_key`、`_on_click`/`play`），逐入口核对守卫同源。
- **终态死代码模式**：状态机守卫写了 `index >= len` 但没有赋值路径到达 len → 终态不可达、文案死分支、总结态不锁。看到 `>= len` 守卫必须反查赋值路径。
- **无头环境事实**：`event_generate` 键盘事件不进 Toplevel 绑定（实测触发 0 次，focus_set 也没用）——键盘类回归在 headless 下必须拆"绑定存在性 + 处理函数直调"验证，别指望合成按键。
- **并发会话事实**：波 5 另有 agent 改 profile_store/style_store/learning_store（#8 路径固化修复）并暴露接力板 #9（open_player_profile 缺 None 守卫→W29 红）。已用 git stash 四文件重跑证实 W29 与本轮改动无关，不重复登记。app.py 编辑期间 mtime 一次外移（02:10），重读后再改——多 agent 同文件必须每笔编辑前对 mtime。
- **低危记录未修（越界转记接力板 #10）**：揭示锁盘态 hover 幽灵子仍显示（暗示可落子）；`_endgame_reveal(answer_coord=...)` 死参数。
- **验证**：test_adversarial 225 序列 0 违规；test_ui_smoke 201 check 全绿；全量 51/52（W29＝接力板 #9 既有）；self_review 21OK/0P0/0P1；doc_sync 一致。
