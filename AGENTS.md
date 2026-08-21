# AGENTS.md —— 围棋学习系统（D:\katago）

个人本地围棋分析器：KataGo 引擎 + Python/Tkinter，**单机复盘教练**定位（无对弈、无联网、数据不出本机）。

## 必读文档（按需）
- `项目说明.md` —— 项目全貌，由 doc_sync 工具保持与代码同步
- `docs/文档索引.md` —— 全部文档入口
- `analyzer/README.md` —— 模块架构与回归测试说明

## 硬性工作规则
1. **改 `analyzer/` 代码后必须自测**：跑相关 `test_*.py`，改动面大时全量回归 + `py_compile`。
2. **文档同步是强制的**：Stop hook（`.zcode/config.json` → `analyzer/doc_sync_hook.py`）会检查文档漂移，检测到不同步会要求修复——直接在 analyzer/ 下运行 `python doc_sync.py` 自动同步。
3. **自审查**：一轮代码修改完成后运行 `python analyzer/self_review.py`（7 维检查 + 回归）。
4. **项目状态数字（版本号/测试数/控件计数）禁止引用记忆值**，一律现场扫描——本项目演进快，写死的数字必漂移。

## 架构边界
- `analyzer/app.py` 为巨型文件（万行级）：**最小改动原则**，不主动重构。
- `analyzer/ui/` 为 V6 并行开发区（shell/timeline/pages/tokens）：CTk + PIL + 设计令牌标准；令牌唯一来源 `ui/tokens.py`，禁止散落硬编码色值。非 UI 任务只做最小修复。
- 纯逻辑层（board/movetree/analysis_guard/invariants/domain_invariants）语义不可改（可修 bug）。
- 不引入新第三方库（只用已装 PIL + CustomTkinter）。

## 专用 agent（`.zcode/agents/`，单一职能制，经 Agent 工具调用）

| Agent | 唯一职能 |
|---|---|
| `project-iterator` | 迭代规划与调度（扫描→清单→审批→路由，不亲自深挖） |
| `code-auditor` | 代码逻辑质量（七类逻辑缺陷审查与修复） |
| `trigger-flow-auditor` | 交互流程闭环（状态残留/进出恢复/叠加层三类零容忍） |
| `user-sim-adversary` | 仿真场景生产（唯一测试语料设计者） |
| `continuous-optimizer` | 回归守护与自动优化循环（修红+鲁棒性/解耦增强） |
| `ui-modernizer` | UI 现代化（ui/ V6 区重写标准 + app.py 旧区渐进迁移） |
| `product-analyst` | 产品对标与功能取舍（该不该有，不做怎么实现） |

各 agent 遵循**自我迭代协议**：经验与动态基准沉淀在 `docs/agent记忆/<name>.md`（continuous-optimizer 用 `docs/优化轮报.md`），定义文件永不写死数字。跨职能问题只转记不抢做。

### 自我迭代运转机制（并发轮编排）

- **三波制**：并行波（审查+各自领域写入，文件互不重叠）→ 串行写波（必须独占 app.py 的任务）→ 回归波（continuous-optimizer 跑全量收口）。
- **硬规矩：bug 必须转化为回归**——修完任何 P0/P1 bug，要么新增一条不变式（invariants/domain_invariants）或场景断言，要么在记忆文件说明为何不加。
- **真实故障进循环**：Tk 回调异常落 `analyzer/crash.log` + usage 埋点事件；continuous-optimizer 每轮先扫真实错误日志再谈增强。
- **美观可回归**：`test_visual_snapshot.py` 对 PIL 渲染产物做 hash 快照比对——视觉改动必须过快照基线（`--update` 显式更新基线）。
- **接力板与趋势**：跨 agent 转记登记 `docs/agent记忆/_接力板.md`（每波开始先读板领任务）；`docs/agent记忆/_趋势看板.md` 记录每波度量（场景数/不变式数/tk 残留/修复数）。
- **配额门槛**：启动任何波之前先跑 `python .zcode/quota_gate.py check`（exit 3 = 冷却中/超预算，不启动）；每波结束 `record --tokens <合计> --wave` 记账；agent 失败含 429/限流/额度类错误立即 `error "<信息>"` 设 5 小时冷却并停止后续波。查额度本身不许花额度——所以是脚本不是 agent。

## 测试与对抗体系
- `analyzer/test_adversarial.py`：12×12=144 序列全枚举回归（invariants.py 16 条 UI 不变式 + domain_invariants.py 7 条学习领域不变式）
- `analyzer/test_workflow_sim.py`：真实工作流仿真场景（持续增长）
- 环境：Windows + Git Bash；Python 3.11（`C:\Users\cter\AppData\Local\Programs\Python\Python311`）
