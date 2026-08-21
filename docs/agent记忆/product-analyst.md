# agent 记忆 —— product-analyst（产品对标与取舍）

> 动态事实区：由 product-analyst 每次分析结束追加。对标矩阵带来源 URL。

## 2026-08-21（初始化）
- 单一职能制确立：只做"该不该有"判定；实现路由给执行者。
- 历史成果：docs/产品对标与整改清单.md；版本基准 v5.0（以动态扫描为准）。

## 2026-08-21（v2 体检，只分析不实现——并发轮避免写冲突）
- 基准扫描：APP_VERSION v5.0（app.py:15）、54 个 test_*.py、V6 UI 四页面（home/library/practice/learning）。
- 定义中"常见待完善方向"清单核实结果：热力概览 ✅已实现（`_refresh_heat_bar`）、轻量补曲线 ✅已实现（`quick_scan_mainline` visits 分档 + 入库自动预扫 app.py:4613）、问题手 drill ✅（problem_drill.py 训练窗口）——三项从 GAP 剔除。定式/官子/死活、指导棋、日本规则/非19路仍缺。
- 对标调研（2026-08）：KaTrain（teaching game 自动撤坏棋、territory loss 可视化 v1.4+，github.com/sanderland/katrain）；LizzieYzy Next（双引擎同步/交替对比、多 GTP 引擎、鹰眼，github.com/wimi321/lizzieyzy-next）；星阵（选点/子差/领地/胜率全景 + 特训/死活题，m.19x19.com/engine/productIntro）。涨棋网未再命中独立官方页，v1 矩阵行可信度下调。
- GAP 结论：GAP-3 官子专项训练（中优先级，纯本地可生成，与棋风"官子收束"维打通）→ code-auditor+ui-modernizer；GAP-4 死活/定式题库（低，需题库授权）暂缓；GAP-5 日本规则/非19路（低）→ code-auditor。指导棋、双引擎、通用 GTP 按定位/性价比否决（维持 v1）。
- REDUNDANT：本轮零新增；v5.0 全部新模块定位内且接线；online_import 收敛为导入助手属可接受边界。
- STRENGTH：学习闭环（LearningEvent+taxonomy+SRS+unstable）、教练防火墙、复核队列、usage_log R0 埋点。
- 协作出口已写入 docs/产品对标与整改清单.md v2 段（含调度表）。定义的"常见待完善方向"清单建议下轮修订（三项已过时）。
