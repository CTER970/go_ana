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
