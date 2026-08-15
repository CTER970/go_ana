# 基于个人历史实战的围棋学习系统（go_ana）

KataGo 负责客观判断，个人历史决定学习重点，系统持续追踪你是否真的改掉了过去的错误。

这是一个可直接运行的本地 KataGo 工作区，包含三部分：

- `analyzer/`：自研 Python/Tk 学习系统，核心闭环：导入棋谱 → KataGo 建立客观事实 → 识别最值得本人学习的局面 → 主动复盘（隐藏答案重新落子）→ 按实际目损判定 → 归入个人错误模型 → 间隔复习 → 观察真实棋局是否复发 → 更新学习画像。
- `katago-runtime/`：Windows x64 开箱即用运行时，包含 KataGo 引擎、模型、配置和依赖 DLL。
- `KataGo/`：KataGo 官方开源源码、文档和训练脚本。

当前版本为 v5.0（学习系统第一版，改造方案见 [项目大纲.md](项目大纲.md)）。相对 v4.38 的核心升级：

- **学习优先级排序**：问题手不再只按目损排序，而是 severity × 复发 × 可学习性 × 掌握度的加权排序，每盘默认聚焦 5 个学习节点，同一场战斗最多 2 题。
- **主动复盘**：训练时棋盘自由落子作答；榜外选点自动送 KataGo 强制分析（allowMoves）后按实际目损判定，绝不因"不在 AI 前几选"判错。
- **判分重做**：废除"AI 前 3 名 = 正确"——第 4 选亏 0.4 目判优秀，第 2 选亏 5 目判可疑；阈值按棋力档与局面复杂度动态放宽。
- **错误分类体系**：九类技术错误（弱棋/攻防/方向/先后手/死活/计算/棋形/全局/官子），分类必存证据，证据不足明确"待分类"。
- **错题本升级**：完整作答历史、五档掌握状态（new/understanding/retained/transferred/unstable），实战复发自动标记 unstable。
- **Human SL 接入**：放入官方 human 模型自动启用 `-human-model`；问题手训练会自动对实战手做"本人档 + 更高档"双查询并持久缓存，档位差大的问题在学习排序中被抬升（"当前棋力特别值得改"）。未放模型时优雅回退，不影响其他功能。
- **LLM 教练防火墙**：EvidencePacket 作为唯一事实入口，教练输出经程序校验（数字/选点逐项核对），不合法一律回退确定性解释——无网络也完整可用。
- **学习画像**：重复错误率、主动纠正率、延迟保留率、类别维度实战复发、第一训练主题（一次只给一个）。
- **错误链**：区分"最大爆炸点"与"根源错误"（第 151 手崩盘，该学的是第 63 手）。

## 本仓库（GitHub）内容说明

本仓库只包含分析器源码和项目文档。以下内容只在本地工作区存在，不入库（见 `.gitignore`）：

- `katago-runtime/`：引擎二进制和神经网络模型体积过大（单个模型超过 GitHub 100MB 限制），可从 [KataGo 官方发布页](https://github.com/lightvector/KataGo/releases) 获取引擎，从 [katago.org](https://katago.org/index.html) 下载模型。
- `KataGo/`：官方源码克隆，可随时从 [lightvector/KataGo](https://github.com/lightvector/KataGo) 重新克隆。
- `analyzer/game_library/`、`analyzer/user_settings.json`：个人对局数据与本机配置。首次运行会按 `config_manager.DEFAULTS` 自动生成默认设置。

## 快速启动

最简单方式：

```powershell
cd D:\katago
.\run.bat
```

也可以直接进入分析器目录：

```powershell
cd D:\katago\analyzer
python app.py
```

第一次使用时，如果想创建桌面快捷方式：

```powershell
cd D:\katago
.\create-shortcut.bat
```

## 主要功能

- 棋盘落子、撤回、快进、分支、自动播放和快捷键导航。
- 自动启动本地 KataGo analysis 引擎，展示推荐点、胜率、目差、主变和棋盘候选标记。
- 导入/导出 SGF，保存 `.kga.json` 复盘项目，保留分析缓存，方便下次继续。
- 多选或粘贴 SGF 入库，按内容安全去重，并通过本地持久化队列连续分析多盘棋。
- 批量分析整盘，生成问题手、胜率曲线、三阶段表现、中文复盘摘要和 Markdown 报告。
- 问题手提供可追溯三段式讲解；候选点提供基于目损、PV 复杂度、prior 和当前表现档的分层建议。
- 本地棋谱库、阶段训练、跨棋局错题本、个人画像、棋风画像和成长路线。
- 终局点目、ownership/policy 热力映射，以及真实 KataGo IPC 验证链路。

## 文档入口

| 读者 | 推荐文档 | 说明 |
|---|---|---|
| 第一次使用 | [快速开始.txt](快速开始.txt) | 面向新手的双击启动说明。 |
| 想了解整个项目 | [docs/文档索引.md](docs/文档索引.md) | 新整理的文档导航，先看这里。 |
| 使用分析器 | [analyzer/README.md](analyzer/README.md) | 分析器功能、界面、快捷键、配置和测试。 |
| 看完整项目说明 | [项目说明.md](项目说明.md) | 当前产品能力、数据存储、工作流、验证结果与使用边界。 |
| 看当前开发状态 | [KataGo/当前任务.md](KataGo/当前任务.md) | 最近任务执行结果和验证边界。 |
| 看实现思路 | [KataGo/当前任务实现思路](KataGo/当前任务实现思路) | UI/产品化改进的设计取舍与实现记录。 |
| 只用引擎运行时 | [katago-runtime/README-RUN.txt](katago-runtime/README-RUN.txt) | 命令行 KataGo 运行和模型选择说明。 |

## 常用验证

进入 `analyzer/` 后可运行：

```powershell
python -m py_compile app.py evidence_explanation.py candidate_recommendation.py analysis_queue.py
python test_analysis_product.py
python test_ui_product.py
python test_ui_design.py
```

更完整的非 UI 回归可运行各 `test_*.py` 文件。真实 Tk UI 冒烟测试为：

```powershell
python test_ui_smoke.py
```

说明：v4.31 真实 Tk 综合冒烟已完整通过；v4.38 已通过新增三项能力纯逻辑测试、棋谱库回归、UI 静态结构、产品逻辑和配置测试。当前 Codex 捆绑 Python 缺少可用 Tcl/Tk 初始化文件，因此本轮未复跑真实 Tk 类测试。

## 目录速览

```text
D:\katago
├─ analyzer\          # 图形分析器与测试
├─ katago-runtime\    # 已配置好的 KataGo 引擎和模型
├─ KataGo\            # 官方 KataGo 源码与文档
├─ docs\              # 本项目整理后的文档入口
├─ run.bat            # 启动分析器
├─ create-shortcut.bat# 创建桌面快捷方式
├─ 快速开始.txt       # 新手快速说明
└─ 项目说明.md        # 全量项目说明
```
