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

## 2026-08-21（第 5 轮：W29/W30 画像棋风×队列交错 + 配置热切换）
- 场景数基准更新：30（grep 实测；本轮 +2）。注意：本波并行 agent 先落了 W28（分栏拖动），任务书里的 W28/W29 顺延为 W29/W30——**编号以入库先后为准，写场景前重新 grep 尾号**。
- W29 画像/棋风窗口×批量队列交错；W30 配置热切换全链（值热切换/点目中改值/查询口径/换引擎×在飞队列）。
- 产品 bug（P1，已修根因）：`_complete_analysis_queue_task`（app.py 队列完成路径）只刷新库/队列窗口，不刷新开着的画像/棋风窗口——前台整盘完成路径（_apply_analysis_result）有"重开重算防 stale"守卫，后台队列漏了同款。修复：完成路径补 open_player_profile/open_style_profile 的 winfo_exists 守卫重开（与前台路径同构，注释互引）。已落 W29 场景级回归（窗口身份变化 + 已关窗口不复活双断言）。
- W30 未发现产品 bug：值热切换（rules/komi/visits/candidate_count）实例属性/写盘/候选截断/缓存签名清 `_library_bg_recent`/下次查询口径全联动；换引擎热切换走 _stop_katago→_interrupt_analysis_queue：任务释放回 queued、rid 挂账清、guard 清、引擎恢复可续跑（设计语义成立）。备忘观察：换引擎失败（preflight 不过）时 `_set_msg`"已切换引擎/模型并持久化，重新加载中…"偏乐观（真实 UI 有 error 弹窗兜底，未修）。
- 场景侧陷阱模式（新）：
  1. 【泵不保证轮询】`pump_after_callbacks`×3 只是 update() 快拍，80ms 的 `_poll_loop` 定时器未必到期——依赖 poll 消费结果的步骤必须**直接同步调 `app._poll_loop()`**（或 advance 真时间）。首版 W30 因此 guard 残留 1 挂账，Phase B 队列领取被正确拒绝（拒绝本身是守卫在工作）。
  2. 【enter_scoring 清分析】无 ownership 的节点进点目会 `node.analysis=None` 并发 ownership 请求——"退出点目后候选=N"断言前必须先回流该请求；且回流内容要定制（FakeClient 对空 moves 根只回 1 候选，验证截断需 ≥4 候选供给）。
  3. 【同步 poll 的前置重定向】直接调 `_poll_loop` 会连带跑 `_kick_analysis_queue`/`_maybe_prepare_library_training_background`（后者扫 search_records("")）——凡要同步跑 poll 的场景，gl 重定向+空 tmp 队列必须**前置于第一个 client 就绪时刻**，否则会触碰真实库。
  4. 【cfg 热切换隔离】`_apply_settings` 经 `cfg.update()` 立即写 `cfg.path` 指向的真实 user_settings.json 且原地改 cfg.data/实例属性——场景须重定向 `app.cfg.path` 到 tmp + finally 里 deepcopy 还原 cfg.data 与 rules/komi/katago_exe/model_file/_candidate_count/_pv_length 七件套。
  5. 【rid 按请求时捕获】多请求源（点目 ownership/强制分析/后台预热）共用一个桩 client 时，`list(queries)[-1]` 必须在目标请求发出后立即捕获，不能事后取尾。
- 越界转记：profile_store 默认路径 def 时固化（gl 重定向无效，生产无用户面影响）→ 接力板 #8 备忘给 code-auditor。

## 2026-08-22（第 6 轮：W33/W34 备份恢复链路 + 热力图×模式交错）
- 场景数基准更新：34（grep 实测；本轮 +2，W31/W32 为本波并行 agent 先落的官子场景）。
- W33 备份与恢复链路：多日库状态→按日打包→KEEP=14 滚动清理→当日幂等（双开仿真）；恢复路径（项目无恢复 UI 入口）= 备份产物可被**真加载函数**读取——zip CRC、逐字节一致、`project_store.project_to_tree` 复原棋局树回环；故障降级（库缺失/备份目录被普通文件占用=磁盘只读等价模拟/启动线程内异常被吞）。
- W34 热力图×导航×点目/训练/drill/复习/换谱交错：点目让位与原档恢复、盲测防泄底、ownership 回流档位不漂移、连按四方一致（档位/文案/cfg/画布）、HUD 联动暂存不残留。
- 产品 bug 3 个（均已修根因 + 场景断言落回归，硬规矩 0 满足）：
  1. **P1 盲测泄底**：`_layer_heatmap_cond`（app.py）只查 scoring，缺候选/PV 已有的盲测守卫——训练用户回合/问题手 drill/错题复习开着策略热力图时，NN 推荐点排序直接画上棋盘=公布答案（W34 三处断言抓出）。修复：补 `_hide_ai_for_training()/_drill_active()/_endgame_active()` 三守卫（与 `_layer_candidate_cond` 同构）。
  2. **P2 测试写真实数据竞态**：`create_tk_root` 在 factory() **之后**才 `backup.set_enabled(False)`，而 `GoAnalyzer.__init__` 即调 `start_background_daily_backup()`——每个测试进程的**首次**构造会对真实 game_library 起备份线程（当日已备时幂等无害，新的一天会真写出一份生产备份）。修复：守卫移到 factory 之前；W33 段6 用"探针工厂记录构造期 enabled 状态"固化（`create_tk_root(lambda: (seen.append(...), None)[1])` 非 Tk 工厂即可探）。
  3. **P3 清理中断**：`backup._prune` 的 `os.remove` 在循环内无独立 try——Windows 下单个 zip 被占用（杀毒/编辑器句柄）抛 PermissionError 被外层 try 吞掉后，**其余待删件全部滞留**。修复：逐份 try/except（跳过占用件次日再清）。
- 陷阱模式（新）：
  1. 【盲测守卫清单】每个"揭示引擎观点"的棋盘叠加层都要过 `_hide_ai_for_training()/_drill_active()/_endgame_active()` 清单——候选/PV 有、热力图漏拦即本轮 P1。**新增叠加层时照抄 `_layer_candidate_cond` 的守卫组**，别只写 scoring 互斥。
  2. 【守卫先于构造】测试工厂里 set_enabled/set_path 类开关必须在 factory() 之前装好——模块级后台线程（backup/usage_log）在 __init__ 期就启动，构造后关闸已经漏了一拍。
  3. 【do_reset≠换空谱】do_reset 是"清空回根"：树与节点分析保留，热力图按当前档画根分析是**正确语义**不是残留；"换空谱"须显式换 `MoveTree`。换谱类断言先分清两种语义（W34 首版在此误报）。
  4. 【Windows 文件锁模拟】open() 持有句柄让 os.remove 抛 PermissionError，是 Windows 下最可靠的"占用/只读"模拟（目录 chmod 对写无效）。
  5. 【热力图计数】PIL 路径 headless 可用（create_image 带 tags），`find_withtag("heatmap-own"/"heatmap-pol")` 即叠加计数；但 create_oval 降级路径**不带 tag**——若未来 PIL 不可用计数会假零。
- 越界转记：本轮全量唯一红 W29（open_player_profile 空 profile 崩溃）确证=接力板 #9（code-auditor #8 store 路径修复的连带暴露，状态待领），本轮验证证据同（`get_or_rebuild` 空库返 None → app.py ~9688 AttributeError），未动（登记在案、归 code-auditor/写波 agent）。
