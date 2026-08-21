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
