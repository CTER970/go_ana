# agent 记忆 —— user-sim-adversary（仿真场景生产）

> 动态事实区：由 user-sim-adversary 每轮仿真结束追加。场景沉淀本体在 analyzer/test_workflow_sim.py。

## 2026-08-21（初始化）
- 单一职能制确立：测试语料唯一设计者；多轮自动循环归 continuous-optimizer。
- 基准快照：test_workflow_sim.py 场景数以 grep -c "^def scenario_" 实测（2026-08-21 时为 20）。

## 2026-08-21（第 3 轮：W23-W25 在线导入/页面路由/队列交错）
- 场景数基准更新：25（grep 实测）。
- 新场景类型：
  - W23 在线导入全链（URL/OGS/错误/关窗中断）——"后台线程下载 + events 队列 + after 轮询"是新的异步形态，与桩引擎回流不同源。
  - W24 V6 Shell 页面路由×前台模式互斥——页面切换=视图切换，不得清点目/drill/棋局。
  - W25 批量队列×前台导航密集交错（非独占让路变体，补 W6/W13 的普通操作面）。
- bug/陷阱模式：
  1. 【桩绑定时机】ui/dialogs.py 在 open_* 时 `from online_import import ...` 把函数绑进闭包——**mock 必须在开窗前装到模块属性**，中途 patch 无效（首版 W23 触发了真实 OGS 网络请求 + 真启 KataGo）。可变 mode dict 切换桩分支。
  2. 【双点位注入】窗口外迁 ui/dialogs.py 后，app.py 的模块别名注入不够——对话框 per-call `from mistake_book import list_items` 直连源模块，注入须同时覆盖 app 别名 + mistake_book 源；且 book_stats 内部以位置参数调 list_items(path)，包装器要忽略位置参数恒重定向，否则双路径 TypeError（W16 修复）。
  3. 【消息单行滚动】状态消息会被队列进度/完成提示毫秒级覆盖——"消息不撒谎"断言须 hook _set_msg 记录历史，不能读终态。
  4. 【断言漂移】导航类场景断言"根分析身份不变"要钉住 node 对象（tree.current 会随导航移动）。
- 越界转记：
  - test_ui_design.py 失败（缺 marker `text="界面风格："`）源于并行会话未提交的 app.py 减法重构（app.py -481 行/ui 外迁），非本轮改动，转记 code-auditor/对应重构 agent。
  - self_review 全量回归时段性红（W16/ui_design）均为并行编辑期竞态，单跑复核 W16 已由本轮双点位注入修复。

## 2026-08-21（第 4 轮：W26/W27 复习拦截沉淀 + 教练链路首场景）
- 场景数基准更新：27（grep 实测）。
- W26 复习中启动训练/drill 被拦（硬规矩补账）：trigger-flow-auditor 第一波 2 个高危守卫（_start_stage_training / open_problem_drill 的错题复习拦截）落成场景级回归——注入法 + 真实按钮路径双验（工具栏「问题手训练」按钮 + 棋谱库「开始阶段训练」按钮，按钮经 _find_button_by_text 按文案在控件树中定位后 invoke）；拦后复习态完好可继续作答、退出后两入口恢复可用。
- W27 教练解读窗口×导航交错（coach 链路首场景）：解读窗口重建不叠窗、根局面/无分析退化提示、复习态下解读不干扰判分链。
- 埋点验证：usage_log ui_exception 事件在受控异常（直接调 app._log_tk_exception）下确实落盘——set_path 临时文件 + set_enabled(True) 测完还原（W26 内嵌步骤，finally 双还原）。
- bug/陷阱模式（本轮均为场景侧调试，未发现产品 bug，守卫本身工作正常）：
  1. 【fixture 同构】drill 判问题手需要**问题手节点自身**带负目损 analysis（与 harness blunder fixture 同构），只有父节点候选分析不够——W26 首版 drill 空窗即此因。
  2. 【V6 页面≠弹窗】棋谱库在 V6 布局是页面：_close_library_window 走 router.go("review") 而非清 _lib_win——窗口关闭断言须分支页面/弹窗两种形态（W24 只测了 drill 弹窗形态）。
  3. 【seed 会清态】seed_fixture 内部 clean() 会清 _mistake_review，且复习态拦截 do_step——"复习态×X"场景必须先布 fixture 再注复习态，导航在注入前完成。
- 接力板：本轮无新增转记项（未发现产品 bug）。
