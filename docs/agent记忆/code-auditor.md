# agent 记忆 —— code-auditor（代码逻辑质量）

> 动态事实区：由 code-auditor 每轮审查结束追加。bug 模式库 = "信号→根因→修法"。

## 2026-08-21（初始化）
- 单一职能制确立：七类逻辑审查为唯一职能，流程/UI/测试问题转记不抢做。
- 历史成果：5 轮审查经验已内化为七类清单；详见 docs/逻辑整改清单.md。

## 2026-08-21（第 6 轮：业务逻辑模块并发审查）
- 范围：review/move_quality/learning_*/candidate_*/style_*/human_sl/katago_client/sgf/score_estimator/online_import/player_profile；app.py 只读。
- 【已修】learning_store.save_event 默认值合并吞真实 0.0：信号 = 进度字段合并用 `merged[key]==default` 判"未设置"，而 0.0/"" 同时是合法业务值（用户重选 AI 最佳目损恰 0.0）。修法 = 调用方显式带 retry_status 非空时跳过 user_retry_move/retry_score_loss 的继承。验证：test_learning_store / test_learning_event / test_learning_priority / test_mistake_book / test_workflow_sim 全绿。
- 【已修·文档级】review.py player_stats 的 agree1/agree3 是百分点(0-100)，player_performance 同名键是比例(0-1)——同名不同单位是消费端混用温床；本轮仅 docstring 声明单位，不改行为。
- 模式库新增信号：「默认值哨兵」反模式——用魔法默认值判"字段未设置"，凡业务值域与默认值重叠（0.0、""、False）即藏 bug；排查时 grep `== default` / `or 0` 类合并逻辑。
- 结论：核心业务模块经前 5 轮治理已相当干净（AST 扫描无可变默认参数/同值三元）；新问题集中在"合并/继承"路径而非计算路径。
- 转记（app.py 只读发现，不抢做）：
  → trigger-flow-auditor：app.py ~6673 `_cache_human_priors_to_event` 与 ~7095 判题落库段 `except Exception: pass` 全静默吞错，失败无用户反馈（反馈一致性类）。
  → continuous-optimizer：app.py `_start_katago` 每次新建 KataGoAnalysisClient，`stop()` 不复位 started/ready/proc——当前无 restart 需求，若未来加"重启引擎"按钮会静默失效（潜在状态机不收口）。
- 待观察（魔数，不动）：candidate_recommendation.build_candidate_recommendations 的 cost 公式系数（0.045/0.35/0.45）、move_quality compute_quality_score 系数（7.0/2.0/55/35）、learning_profile recent_games=10、style_cost tendency 阈值 8.0/3.0。

## 2026-08-21 第三波：补转化历史欠账（bug→回归，协议第 0 条）

- **信号**：第一波修了 learning_store.save_event 的"默认值哨兵"bug（`retry_score_loss` 合并用 值==默认值 判"未设置"，显式 0.0 被误吞继承旧值），但未按硬规矩落回归网。
- **转化方式**：选测试级（test_learning_store.py 新增 `run_retry_zero_not_swallowed`）而非 d 系列不变式——save_event 是带临时文件的仓库级 upsert 语义，测试级更契合；且避开 domain_invariants.py 的并行冲突面。
- **断言内容**：先落 retry_score_loss=4.2 旧值 → save_attempt 显式 0.0（重选 AI 最佳）→ 读库必须仍是 0.0/P9/corrected；再走 save_event 重新分析 upsert 也不回吐旧值；对照断言：无新 retry 的事件默认合并仍继承旧重试值（语义未被破坏）。
- **验证**：test_learning_store + test_learning_event + test_learning_priority + test_mistake_book 全过；test_adversarial 144 序列 0 违规。
- **模式沉淀**：默认值哨兵（default-as-sentinel）是数值字段的通用反模式——凡 0.0/"" 为合法值的字段，禁止用 `值==默认` 判"未设置"，应改用显式标志（本例用 `has_new_retry = bool(retry_status)`）。

## 2026-08-21 第四波：GAP-3 官子收束题生成模块（功能实现，product-analyst 路由）

- 范围：新建 analyzer/endgame_drill.py（纯逻辑，无 UI/无 KataGo 进程）+ test_endgame_drill.py；不改 problem_drill/app.py（并行协议：test_workflow_sim.py、app.py 由他人在改）。
- 设计要点（对接手 ui-modernizer 的人有用）：输入 = MoveTree（主线节点带 analysis 缓存），复用 ReviewReport.evaluate() 算目损，不重评价单手；终局段 = 最后 window 手（默认 50，下限 20）；两类题源按"收束价值"降序——loss（目损≥1.5）/sente（一选 PV 对手应手 Chebyshev≤2 邻接启发式 + 错过先手相对最近后手候选代价≥1.0 目）；每题带 BoardSnapshot（initialStones+落子序列+行棋方，让子局可重建）；grade_choice 复用 candidate_assessment 单位=目。所有边界（None/空谱/短局<30手/无分析/终局段齐全<10手/无达标点）返回空题集+reasons，单手数据损坏 try/except 跳过不穿透。
- 开发中自查出 2 个未出仓即修的缺陷（都有回归断言兜住）：① 初版 _initial_stones_of 想从节点反查让子 setup，但 MoveNode 不持 tree 反向引用——恒返回 [] 的死代码，正是第 6 类"看似实现实为空转"；改为 build 时显式传 tree.initial_stones_list()。② EndgameCandidate.is_best 字段只在 quality_label 计算里用了，忘了写进构造参数（默认 False 恒假）。两者都被 test_endgame_drill 首跑抓住——新模块先写断言再过一遍是值得的。
- 信号沉淀：「构造参数漏写字段」——dataclass 加了带默认值的字段后，构造点沿用旧参数列表不报错（默认值吞掉遗漏），排查法 = 对每个语义字段写一条显式断言（如 is_best必须为 True 的行）。
- 视角换算提醒（本波 fixture 两次踩坑）：moveInfos/rootInfo 的 scoreLead 全项目按黑视角约定，白方候选优劣与黑视角反号——造 fixture 时"白方更差的候选"其黑视角 scoreLead 数值要更大，靠目测直觉写必错，必须按 _mover_score 公式倒推。
- 回归：test_endgame_drill（新建，9 组 70+ 断言）+ test_adversarial（225 序列 0 违规）+ test_problem_drill/test_review/test_movetree/test_candidate_assessment 全绿；self_review 21 OK/0 P0/0 P1。全量回归 55/56，唯一失败 test_workflow_sim.py（W29/W30）为并行 agent 进行中的新增场景（该文件 +312 行、无 endgame 引用），非本模块所致，未碰。
- 文档：项目说明.md 模块架构表训练行已补 endgame_drill.py（doc_sync 复查一致）；analyzer/README.md 文件表与测试表各补一行；接力板 #2 已改为"生成逻辑已落地，待 ui-modernizer 接 practice 入口"。

## 2026-08-21 第五波（波5 并行轮·接力板 #8）：store 家族默认路径导入期固化

- **范围**：profile_store / style_store / learning_store / mistake_book / deep_verification（横向审计同族）；app.py / test_workflow_sim.py 只读（并行冲突面，未碰）。
- **信号→根因→修法（模式库新增：def 期固化默认参数）**：
  - 信号：模块级 `DEFAULT_PATH = os.path.join(HERE, ...)` + 全体函数 `def f(path=DEFAULT_PATH)`；测试想重定向（改模块属性 / gl.LIBRARY_DIR）后，"不传 path"的调用仍写旧位置。
  - 根因：Python 默认参数在 **def 时刻**（=导入期）求值一次，之后改模块属性不影响已绑定的默认值；`__defaults__` 里冻着导入期的字符串。这不是"常量放模块级"的问题，是**把模块属性塞进 def 默认参数**这个组合的问题。
  - 取证：`save_profile.__defaults__` 打印出导入期路径；行为实验（改 DEFAULT_CACHE_PATH 后无参调用 save_profile）直接把测试数据写进生产 `game_library/profile_cache.json`（已即时用 rebuild_profile_from_library 从完好的 index.json 重建恢复——缓存是派生数据）。
  - 修法：签名改 `path=None` + 函数体首行 `path = _resolve_path(path)` 调用时解析；解析优先级 = `set_path` 重定向 > `game_library` 当前属性派生 > 内置默认。**gl 派生是关键**：测试惯例是重定向 gl.LIBRARY_DIR/INDEX_PATH/PROFILE_CACHE_PATH（W29 式），store 默认路径跟随 gl 当前属性后无需改任何调用方/不碰 app.py 即自动隔离。project_store/usage_log 本就干净（usage_log 的 set_path+default_path+调用时解析是项目内正确范本）。
- **横向取证**：5 个 store 全部同款（profile_store 7 处签名、learning_store 18 处、mistake_book 17 处、deep_verification 5 处、style_store 2 处）；app.py 无参调用点：get_or_rebuild(~9627)、save_style_cache(~9816)、learning_store save_event/get_event 等 7 处、mistake_book list_items/book_stats 等 6 处、deep_verification load_store 3 处。W16 注释（"真实 mistake_book.json 泄入测试，曾读到 7 条真实错题"）是历史旁证——当时靠双点位模块别名注入绕过，现在 set_path 一行即可。
- **bug 定级**：非纯测试基建——取证实验证明"重定向失效→数据写错位置"真实发生（写穿生产缓存）；且 W29 全程读生产 index/缓存/错题本/学习事件（测试结果依赖用户真实数据 = 不确定性 + 隐私面）。已按协议第 0 条落回归：5 个 test 文件各加 `run/test_default_path_*`，断言含结构守卫（`__defaults__ is None`）+ set_path 重定向生效 + gl 派生跟随 + 生产文件哈希不变 + 显式 path 优先级。
- **连带发现（转记 #9，未抢做）**：app.py open_player_profile 在 `get_or_rebuild()` 返回 None（空库）时直接 `profile.problem_tag_distribution.keys()` 崩溃——HEAD 同款、全新安装可复现的 P1 降级缺失，此前被固化 bug 掩盖（W29 靠生产数据泄入才不崩）。修法：None 时空态提示 return，对齐函数 docstring「空数据…给明确说明」。修好前 W29 是全量回归唯一红。
- **验证**：5 个 store 测试 + test_player_profile/test_profile_report/test_style_cost/test_style_profile/test_style_report/test_learning_event/test_learning_priority/test_project_store 全绿；test_adversarial 225 序列 0 违规；py_compile 干净；self_review 21OK/0P0/0P1；无重定向时生产路径逐字符串断言不变。test_workflow_sim 唯一失败 W29 = #9 所述 app.py 缺陷暴露（非 store 回归）。
- **教训**：测试"碰巧通过"要警惕数据来源——W29 断言"画像窗口已开"成立靠的是用户真实库；隔离修复让隐性依赖现形，这是修复的收益不是代价。

## 2026-08-22 波7b：endgame_drill.py 七类深审（波4 新模块零审查记录补账）

- **范围**：analyzer/endgame_drill.py 全量 + test_endgame_drill.py；app.py/problem_drill/review/candidate_assessment 只读取证。修 3 处逻辑错误（2 P1 + 1 P2）+ 2 处低危一致性，全量回归 57/57 绿、self_review 21OK/0P0/0P1。
- 【已修 P1】一选缺 scoreLead → `_num` 默认 0.0 编造目差：全部候选目损 clamp 成 0、全标"最佳"、grade_choice 任意作答判 best——评测建立在无证据数据上。修法：`best_info.get("scoreLead") is None → return None`（整手不出题）。信号 =「`_num` 默认值穿透判定链」：凡默认 0.0 参与目损/评级计算且结果对外展示/判分，缺失必须降级而非补默认。
- 【已修 P1】中间候选缺 scoreLead 同款编造（且与 problem_drill 处理不一致——那边默认继承一选值，两边各自编不同数）。修法：候选行/先手代价扫描遇 `scoreLead is None` 直接跳过该行，不编造。横向规则：**脏数值字段跳行（endgame）/整手降级（一选），不做默认值补位**。
- 【已修 P2·文案诚实】空题集原因恒称"目损 < 1.5 目"，但 pass-best/数据不足路径下明明存在 ≥1.5 目损手（test_pass_best_skipped 即构造此场景）——降级文案断言与事实矛盾。修法：循环内计 loss_hits（目损达标但被放弃的手数），空集时分枝出诚实文案"有 N 手目损已达阈值但一选为 pass 或候选数据不足"。这是"断言无证据"类文案在 reasons 通道的变体。
- 【已修 低】① EVAL_LABELS 与 problem_drill 重复定义 → 改 import 单一来源（与 quality_label_for_loss 同路）；② DEFAULT_ENDGAME_WINDOW 注释谎称"GAP-3 要求 40-60 区间"（全 docs 检索无此依据）→ 改为"实现选择，无文档硬性区间依据"。
- 【回归（协议第0条）】test_endgame_drill 新增 test_dirty_best_scorelead_skips_hand（一选缺 scoreLead → 不出题 + 原因说"已达/数据不足"）；test_partial_analysis_and_malformed 断言改"缺 scoreLead 候选不入表"；test_pass_best_skipped 加"空题原因指出达标手无法成题"。教训：**旧测试把编造行为断言成预期（"None 目差按 0 处理"）——修复时要连测试的"预期"一起审，别被既有断言绑架**。
- 横向一致性确认（未改）：grade_choice 与 problem_drill.grade_quiz 同走 assessment_for_loss(score_loss)、同 isCorrect 三档口径、同"榜外=无法评定"降级；_mover_score 黑→走子方换算两边逐字一致；e.loss 直取 ReviewReport.evaluate 不重算。差异项登记：grade_choice 输出键 **isPlayed vs problem_drill 的 isActual**（同一概念"选了实战着法"不同名，改名动 app.py 契约，未做，见接力板 #11）。
- 转记（app.py 只读，不抢做）→ 接力板 #11：`_drill_populate`/`_endgame_populate_table` 两处同值三元 `("%d" % c.visits) if c.visits else "0"`（两分支渲染相同，纯死写法）+ `"%.1f" if truthy else "0"`（0.0 显示成 "0" 而非 "0.0"，与列格式不一致）——endgame 表从 drill 表复制时连带继承。
- 待观察（魔数，不动）：DEFAULT_ENDGAME_WINDOW=50 / MIN_ENDGAME_WINDOW=20 / MIN_TOTAL_MOVES=30 / MIN_ANALYZED_ENDGAME=10（启发式窗口族）；ENDGAME_LOSS_THRESHOLD=1.5（与 candidate_assessment.THRESHOLD_ACCEPTABLE=1.5 数值巧合但语义独立）；SENTE_GAP_THRESHOLD=1.0、SENTE_ADJACENCY=2（先手启发式，只用于选题/标注不判分——这个边界注释写得对）；BLOWOUT_WARNING_LEAD=40.0。
- 模式库新增信号：**「默认值补位 vs 跳过降级」**——`_num(mapping, key, 0.0)` 型取值器在"展示+判分"链路上是危险默认：0.0 是目损/目差的合法值域（最佳=0），缺失与真实 0 不可区分。凡下游做比较/判级，必须显式判 None 分枝；只有纯展示兜底才允许默认值。

## 2026-08-22 波10（串行写轮·接力板 #12）：官子训练作答回写 LearningEvent，闭合学习环

- **范围**：learning_event.py / learning_store.py / learning_profile.py（事件模型与隔离）+ app.py 官子作答判分接线（独占写波）+ 三处测试。错题本（mistake_book.py）刻意零改动。
- **事件模型设计（题源语义≠问题手，双闸隔离）**：
  - 类别闸：`kind` 字段（problem / endgame_drill，旧数据无键回退 problem）；官子事件 `primary_category` 刻意留空——taxonomy 分类、复发统计（build_recurrence_index 跳空类别）、画像类别分布三处口径零污染。
  - id 闸：`endgame_event_id = event_id(game_id+"#endgame", move_no, color)`——同一盘同一手既有问题手事件又有官子作答事件（官子题正是从实战失损手派生的），save_event 按 id upsert，id 不隔离会互相覆盖进度字段；game_id 字段保持原值，remove_game 按局删除仍联动。
  - 语义边界（D5 同源）：官子作答只走 add_attempt（作答历史），不走 record_retry/apply_review_outcome——mastery 恒 new、无 review_due_date，永不进到期复习队列，也不参与实战迁移状态机。store 断言（run_endgame_drill_events）+ 冒烟断言双锚。
- **接线（app.py 最小改动）**：`_endgame_free_answer` 判分成功后调 `_endgame_persist_answer`（新方法，与 _drill_persist_free_answer 同风格：无 game_id 跳过、失败 stderr 留痕）→ `learning_store.record_endgame_drill_attempt`（get-or-create：首次作答按题面事实建事件，之后只追加 attempts）；ai_rank 由 chosenKey（"cK"）换算 K+1。
- **消费点防污染（本轮真正的审查发现）**：kind 混库后三个既有消费点会读脏数据——① `_learning_priority_context` 的 `mastery_by_move[move_no]`：官子事件（priority 0 排序列尾）会用自己的 new **盖掉同手问题手事件的 mastery**（真实 P1 级污染，dict 后写覆盖）；② 时间轴 pri_by_move（priority 0 恰好被 truthy 过滤兜住，属巧合非设计）；③ summarize_learning 的保持率/类别分布/unclassified 桶。修法：get_events/get_events_by_game 加 kind 过滤参数，①②显式 kind=problem，③在 summarize_learning 入口分流。
- **画像呈现**：learning_profile 新增 `_summarize_endgame_drills`（只看 attempts：answered/correct/accuracy/avg_answer_loss/by_kind/last_practiced）汇入 summary["endgame_drill"]；format_learning_summary 输出一行「官子训练：累计作答 N 题，答对率 X%，平均选点目损 Y」——个人画像窗 learn_card 与学习中心状态摘要同源消费；learn_card 门控放宽为"有问题手事件或有官子作答"。player_profile.py 未动（数据源 MoveQuality，无作答数据；深化项转记 #14）。
- **取舍：错题本不进**——官子作答不是错题：SRS 调度（interval/due/lapse）、掌握状态迁移、实战复发/unstable 判定全部建立在"本人实战问题手"语义上；官子题是出题人视角的训练素材（含 sente 转换题这类"实战没犯错只是价值排序问题"），进本会把训练作答冒充实战证据。mistake_book.py 零改动即零污染。
- **回归（协议第0条）**：test_learning_event +6 断言（id 隔离/字段对齐/语义边界/序列化往返/旧数据回退）；test_learning_store 新增 run_endgame_drill_events 15 断言（空 game_id 不造事件/重复作答只追加/问题手 upsert 不冲掉官子作答/kind 过滤/不进到期队列/复发索引无污染/画像一行/store_stats 单列/删除联动）；test_ui_smoke 官子场景 +2 断言（作答落库+不进 SRS），存储重定向只包住作答调用（失败必恢复 set_path）。
- **验证**：test_learning_event / test_learning_store（含新 run）/ test_endgame_drill / test_domain_invariants（D5 仍绿）/ test_adversarial / test_workflow_sim / test_ui_smoke（205 OK）全过；self_review 21OK/0P0/0P1（全量 57/57）；doc_sync 一致；py_compile 干净。
- **模式库新增信号：**「同键异义共存覆盖」**——共享存储引入新事件类别时，凡按 (game_id, move_no) 这类**粗粒度键**聚合的消费点（mastery_by_move 这类 dict 收集），后写入者静默覆盖前者；truthy/排序等 incidental 过滤恰好兜住不算防线。排查法：grep 新类别事件会流经的每个读路径，逐个问"两类事件同键并存时谁赢"。修法是让查询层带类别过滤参数，消费点显式声明口径，而不是依赖数值巧合（priority 0 falsy）。
