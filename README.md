# KataGo 个人围棋分析器

这是一个可直接运行的本地 KataGo 工作区，包含三部分：

- `analyzer/`：自研 Python/Tk 图形分析器，面向个人复盘、AI 推荐、问题手、阶段训练、错题本和个人画像。
- `katago-runtime/`：Windows x64 开箱即用运行时，包含 KataGo 引擎、模型、配置和依赖 DLL。
- `KataGo/`：KataGo 官方开源源码、文档和训练脚本。

当前分析器版本为 v4.38。本轮补齐个人分析最重要的三个闭环：问题手详情按“问题根因 / AI 手目的 / 实战后果”展示结构化证据；候选卡区分“AI 最优 / 稳健易懂 / 当前棋力参考”，并明确 prior 只是引擎策略信号；棋谱区支持多选 SGF、粘贴 SGF 文本和可暂停、继续、失败重试的持久化整盘分析队列。

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
| 看完整项目详版 | [项目说明.md](项目说明.md) | 长篇总说明，覆盖源码、运行时、引擎、模型和分析器。 |
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
