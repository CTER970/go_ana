"""KataGo 围棋分析器（最小原型）—— tkinter UI 层。

三层架构：
  [本文件 app.py]  棋盘 UI：显示、点击落子、显示 AI 推荐
  [movetree.py]    棋局状态层：BoardState + MoveTree（撤回/快进/分支/分析缓存）
  [katago_client]  KataGoAnalysisClient：启动 katago analysis，收发 JSON

运行：
  cd D:\\katago\\analyzer
  python app.py
"""
from __future__ import annotations

# 应用版本唯一来源：窗口标题与其他展示位一律引用本常量，避免多处手写漂移
APP_VERSION = "5.0"

import os
import sys
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, filedialog
from tkinter import ttk
try:
    from PIL import Image, ImageDraw, ImageTk, ImageFilter
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False
try:
    import customtkinter as ctk
    ctk.set_appearance_mode("dark")   # 默认深色主题（对标 OGS/KaTrain 深色模式共识）
    _HAS_CTK = True
except ImportError:
    ctk = None
    _HAS_CTK = False

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from board import BLACK, WHITE, EMPTY, IllegalMove, color_letter
from movetree import MoveTree, point_to_xy, xy_to_point, COLS
from katago_client import KataGoAnalysisClient
from sgf import export_sgf, import_sgf
from project_store import save_project, load_project
from game_library import (add_sgf_to_library, append_training_session, delete_record, inbox_dir,
                          get_record, get_recent_profile_summaries,
                          get_recent_style_records, load_training_cache,
                          import_sgf_text,
                          refresh_training_task, save_training_cache,
                          scan_inbox, scan_paths, search_records, touch_record, update_project_snapshot,
                          update_profile_side,
                          update_training_settings)
from config_manager import ConfigManager
from config_manager import list_engine_paths, list_model_paths
from analysis_guard import AnalysisGuard
from heatmap import ownership_is_black, policy_board_entries
from score_estimator import ScoreEstimator, ownership_territory_split
from review import (GRADE_BAD, GRADE_DOUBT, GRADE_GOOD, ReviewReport,
                    LOSS_DEFAULT_THRESHOLD, highlight_intervals)
from move_quality import (PROBLEM_TAGS, QUALITY_LABELS,
                          VERSION as QUALITY_VERSION)
from player_profile import (GameProfileSummary, build_game_profile_summary, build_profile,
                            compare_game_to_baseline,
                            prioritize_weaknesses, weakness_trends)
from profile_store import get_or_rebuild
from review_report import generate_markdown_report
from profile_report import generate_profile_markdown
from training import describe_training_task, grade_training_session, normalize_player_color, player_color_label
from training_analysis import analyze_training
from training_cache import (CACHE_VERSION, model_signature, package_matches, position_key,
                            put_analysis)
from branch_comparison import build_branch_comparison
from evidence_explanation import build_evidence_explanation, format_evidence_explanation
from analysis_queue import AnalysisQueue
from mistake_book import (apply_training_outcomes, book_stats,
                          list_items as list_mistake_items,
                          postpone_item as postpone_mistake_item,
                          record_graded_attempt as record_graded_attempt_mb,
                          record_review as record_mistake_review,
                          set_mastered as set_mistake_mastered,
                          sync_profile_summary as sync_mistake_summary)
from style_profile import build_style_profile
from style_cost import attach_style_costs, build_style_costs
from growth_path import apply_verified_findings, build_growth_path
from style_report import render_style_report
from style_store import save_style_cache
from style_view import StyleProfileWindow
from deep_verification import (
    DeepVerificationTask, build_verification_tasks, load_store,
    merge_and_save_tasks, set_task_status, update_task_result)
from problem_drill import build_problem_drill, new_drill_result, grade_quiz, drill_difficulty_label
from ui_product import build_game_context, fit_window_size, semantic_message_kind

# ===================== 常量（无硬编码路径；引擎/模型/规则/贴目/强度由 ConfigManager 持久化管理）=====================
BOARD_SIZE = 19
MAX_CANDIDATES = 5        # 研究面板最多显示五个候选点
POLL_MS = 80              # 轮询 KataGo 结果间隔
PLAY_MS = 800             # 自动播放每步间隔（ms）
HEAT_LABELS = ["关", "地盘", "策略"]
HEAT_KEYS = ["off", "ownership", "policy"]
LOSS_THRESHOLD = LOSS_DEFAULT_THRESHOLD   # 目损阈值（≥此值画红圈/进失误榜）
REVIEW_TOP_N = 12                         # 问题棋列表显示前 N 手
RIGHT_PANEL_WIDTH = 480                   # 研究工作区保持稳定宽度，左侧棋盘随窗口伸缩
BOARD_SCALE = 0.84                        # 棋盘占可用区域比例（用户反馈：整体缩小一档）
TRAINING_SPEED_MODES = {
    "fast": ("快速", 80),
    "balanced": ("均衡", 140),
    "deep": ("精细", 240),
}

# ===================== 棋谱研究工作台 · 调色板 / 字体 =====================
# 明亮版深色色板（对标 OGS/KaTrain 深色模式，但整体更通透明亮）：
# 各区域亮度等差递进（bg 0.150 → card 0.192 → card2 0.234），过渡平滑。
# 棋盘用明亮 kaya 榧木色（亮度 0.676），黑白棋子在其上对比强烈——主流围棋平台的视觉风格。
# V6：令牌唯一来源迁至 ui/tokens.py（含 learning_priority 专用紫等新令牌），
# 此处保留原字面量注释供历史对照。
_UNIFIED_COLORS = {
    "bg":       "#222829",   # 0.150 窗口底（明亮版，不沉闷）
    "card":     "#2c3334",   # 0.192 卡片底（清晰浮出背景）
    "card2":    "#363e3f",   # 0.234 次级卡片/HUD底
    "board":    "#d4a85a",   # 0.676 明亮 kaya 榧木棋盘（对标 OGS/KaTrain）
    "board2":   "#e0b870",   # 木纹浅档（更亮一档）
    "grid":     "#4a3618",   # 深褐网格（明亮棋盘上清晰锐利）
    "star":     "#3a2810",   # 深褐星位
    "coord":    "#6b5630",   # 坐标（暖褐）
    "text":     "#e8ecec",   # 0.921 主文本（明亮，清晰）
    "subtext":  "#a8b1ac",   # 0.681 次文本
    "accent":   "#3db8a0",   # 0.567 青绿强调（鲜活明亮）
    "accent_h": "#54d4be",   # 强调-亮（hover）
    "accent_s": "#2a4a44",   # 强调-底色（选中/激活）
    "accent_m": "#4d9182",   # 强调-中
    "black":    "#1a1a1a",   # 棋子（明亮棋盘上对比强烈）
    "white":    "#f8f8f0",   # 棋子（微暖白，云子质感）
    "stone_hl_dark":         "#4a4540",   # 黑子高光（暖中灰，明亮棋盘上可见）
    "stone_hl_dark_bright":  "#6a6058",   # 黑子高光中心
    "stone_hl_light":        "#ffffff",   # 白子左上高光
    "stone_hl_light_shade":  "#d0d0c8",   # 白子右下球阴影
    "heat_green": "#3db8a0",              # policy 热力图（统一用 accent 系）
    "heat_green_dark": "#2a8a70",
    "red":      "#e06560",   # 恶手/危险（明亮版）
    "red_s":    "#4a2e2c",   # 红底
    "amber":    "#e0a043",   # 疑问/警告（明亮版）
    "amber_s":  "#4a3a25",   # 橙底
    "green":    "#5abb80",   # 好手/成功（明亮版）
    "muted":    "#454f4a",   # 中性边框/分隔（bg→muted 差 0.05，柔和分隔）
    "shadow":   "#15191a",   # 棋子投影/阴影
    "purple":   "#aa8ed8",   # 白方/特殊
}
from ui.tokens import PALETTE as _TOKEN_PALETTE   # V6 令牌唯一来源
_UNIFIED_COLORS = dict(_TOKEN_PALETTE)
LIGHT_COLORS = _UNIFIED_COLORS    # 向后兼容：原三套合并为统一深色
DARK_COLORS = _UNIFIED_COLORS
CYBERPUNK_COLORS = _UNIFIED_COLORS
UI_STYLE_LABELS = {
    "simple": "深色",
}
COLORS = dict(LIGHT_COLORS)   # 工作副本；_toggle_theme 原地替换内容
# 跨平台字体 fallback：Windows 用微软雅黑，macOS 用苹方，Linux 用 Noto
_UI_FONT = "Microsoft YaHei UI Segoe UI PingFang SC Noto Sans CJK SC Helvetica"
_DATA_FONT = "Consolas JetBrains Mono Sarasa Mono SC Menlo Courier New"
FONTS = {
    "h1":      (_UI_FONT, 16, "bold"),    # 主标题（放大）
    "title":   (_UI_FONT, 14, "bold"),    # 区段标题（放大）
    "score":   (_UI_FONT, 20, "bold"),    # 胜率/目差数字（视觉焦点，显著放大）
    "section": (_UI_FONT, 11, "bold"),    # 卡片小标题（放大）
    "ui":      (_UI_FONT, 10),            # 正文
    "data":    (_DATA_FONT, 11),          # 等宽数据（放大，胜率/目差对齐）
    "small":   (_UI_FONT, 9),             # 次文本
    "micro":   (_UI_FONT, 8),             # 候选点字母编号等微标注
    "h2":      (_UI_FONT, 13, "bold"),    # 传输条手数标题（与 tokens 阶梯对齐）
    "btn_lg":  (_UI_FONT, 12),            # 顶栏/传输条大按钮（用户反馈：上下按钮增大）
    "data_l":  (_DATA_FONT, 13),          # 手数计数等大号等宽数字
}

# 间距系统（4px 基准网格）：统一所有 padx/pady，避免散乱的 3/6/9/14 等凭手感值
SPACE = {"xs": 2, "sm": 4, "md": 8, "lg": 12, "xl": 16}


# 主窗口基类：优先用 CustomTkinter（原生圆角+深色+高DPI），降级回 tk.Tk（无 CTk 时）
_GoAnalyzerBase = ctk.CTk if _HAS_CTK else tk.Tk


class GoAnalyzer(_GoAnalyzerBase):
    def after(self, ms, func=None, *args):
        """登记主窗口的定时回调，关闭时可以统一取消。"""
        job = super().after(ms, func, *args)
        if func is not None:
            jobs = getattr(self, "_after_jobs", None)
            if jobs is not None:
                jobs.add(job)
        return job

    def after_cancel(self, job):
        jobs = getattr(self, "_after_jobs", None)
        if jobs is not None:
            jobs.discard(job)
        try:
            return super().after_cancel(job)
        except tk.TclError:
            # 已执行或已被 Tk 清掉的任务无需再报错。
            return None

    def _cancel_scheduled_callbacks(self):
        for job in list(getattr(self, "_after_jobs", ())):
            self.after_cancel(job)

    @staticmethod
    def _focus_child_window(win):
        """窗口仍存在时才把焦点交给它，避免已关闭窗口的 idle 回调报错。"""
        try:
            if win.winfo_exists():
                win.focus_set()
        except tk.TclError:
            pass

    def _shutdown_application(self):
        """幂等关闭：先断开异步回调，再收束持久化与引擎资源。"""
        if getattr(self, "_is_shutting_down", False):
            return
        self._is_shutting_down = True
        self._cancel_scheduled_callbacks()
        try:
            self._persist_workspace_state()
        except Exception:
            pass
        for action in (
                self._stop_auto_play,
                self._close_graph,
                self._flush_active_training_cache,
                self._stop_katago):
            try:
                action()
            except Exception:
                pass

    def destroy(self):
        """保证直接销毁窗口也不会遗留引擎或 Tk 定时回调。"""
        self._shutdown_application()
        return super().destroy()

    def __init__(self):
        # 必须早于 super().__init__：CustomTkinter 构造期间也会注册 after 回调。
        self._after_jobs = set()
        self._is_shutting_down = False
        # 高 DPI 适配（Windows）：必须在创建 Tk root 前声明 DPI 感知，
        # 否则在高分屏（4K/Retina）上整个界面模糊发虚——这是"清晰度/锐利度"差距的根源之一。
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)   # PROCESS_SYSTEM_DPI_AWARE
        except Exception:
            pass   # 非 Windows 或已设置，忽略
        super().__init__()
        self.title("围棋学习系统  ·  v%s" % APP_VERSION)
        # tk scaling 按 DPI 自适应：默认 1.0（96dpi）在高分屏上字体控件偏小，
        # 用实际 DPI/72 让 Tk 的点单位正确映射物理像素，字体和线条更锐利。
        try:
            import ctypes
            dpi = ctypes.windll.user32.GetDpiForSystem() or 96
            self.tk.call('tk', 'scaling', dpi / 72.0)
        except Exception:
            pass
        self.cfg = ConfigManager()              # 主题读取必须早于任何控件创建
        self._ui_state = dict(self.cfg.get("ui_state", {}) or {})
        self._current_game_label = "新棋局"
        # 主题：在 _build_ui 前替换 COLORS 内容，所有 widget 创建时自动取到正确色值。
        # 默认深色（对标 OGS/KaTrain 深色模式共识，长时间复盘护眼）；用户切换后记住选择。
        saved_theme = self.cfg.get("theme", "")
        self._theme_dark = (saved_theme == "dark") if saved_theme else True   # 无配置时默认深色
        self._ui_style = str(self.cfg.get("ui_style", "simple") or "simple")
        if self._ui_style not in UI_STYLE_LABELS:
            self._ui_style = "simple"
        self._apply_color_palette()
        self.configure(bg=COLORS["bg"])
        self.resizable(True, True)         # 全屏自适应：解锁窗口缩放

        self.size = BOARD_SIZE
        self._candidate_count = max(
            1, min(MAX_CANDIDATES, int(self.cfg.get("candidate_count", 5) or 5)))
        self._pv_length = max(1, min(30, int(self.cfg.get("pv_length", 12) or 12)))
        self._review_scope_mode = (
            self.cfg.get("review_scope", "profile")
            if self.cfg.get("review_scope", "profile") in ("profile", "both")
            else "profile")
        self.tree = MoveTree(self.size)
        self.client = None
        self.guard = AnalysisGuard()   # 异步请求守卫（session 隔离旧引擎实例的过期结果）
        self._candidate_actions = []    # 右侧推荐按钮对应的 (x, y, pv, move)
        self._candidate_buttons = []
        self._candidate_rank_labels = []
        self._candidate_win_labels = []
        self._candidate_pv_labels = []
        self._candidate_rows = []
        self._candidate_empty_label = None
        self._candidate_recommendations = []
        self._heat_mode = HEAT_KEYS.index(self.cfg.get("heatmap_mode")) if self.cfg.get("heatmap_mode") in HEAT_KEYS else 0
        self.rules = self.cfg.get("rules", "chinese")
        self.komi = self.cfg.get("komi", 7.5)
        self.katago_exe = self.cfg.get("engine_path", "")   # 当前引擎（对话框可改并持久化）
        self.model_file = self.cfg.get("model_path", "")    # 当前模型
        self._tv_win = None              # 树视图 Toplevel
        self._tv = None                  # ttk.Treeview 控件
        self._tv_map = {}                # treeview iid -> MoveNode
        self._lib_win = None             # 棋谱库 Toplevel
        self._learning_center_win = None  # 学习中心 Toplevel（大纲 §56 四入口）
        self._lib_tv = None              # 棋谱库 Treeview
        self._lib_map = {}               # library row iid -> record
        self._lib_search_var = None      # 棋谱库搜索框
        self._analysis_queue = AnalysisQueue(
            os.path.join(HERE, "game_library", "analysis_queue.json"))
        self._analysis_queue_pending = {}
        self._analysis_queue_current = None
        self._analysis_queue_win = None
        self._analysis_queue_tv = None
        self._library_record_id = None   # 当前棋局若来自棋谱库，保存回该记录
        self._profile_win = None         # 长期个人画像 Toplevel
        self._style_win = None           # 棋风与成长路线窗口
        self._style_profile = None
        self._growth_path = None
        self._style_verification_pending = {}
        self._style_verification_queue = []
        self._mistake_book_win = None    # 错题本 Toplevel
        self._mistake_book_tv = None
        self._mistake_book_map = {}
        self._mistake_book_stats_label = None
        self._mistake_due_only_var = None
        self._settings_win = None        # 设置窗口（只保留一个实例）
        # ---- 问题手训练（涨棋网风格 quiz 钻取：逐题隐藏答案 → 选点对比表 → 变化图）----
        self._drill_win = None           # 问题手训练 Toplevel
        self._drill = None               # problem_drill.ProblemDrill
        self._drill_result = None        # problem_drill.DrillResult
        self._drill_index = 0
        self._drill_revealed = False
        self._drill_user_color = "both"
        self._drill_overlay = None       # quiz 阶段棋盘字母覆盖 {"letters": {(x,y): "A"}}
        self._drill_scale = None         # 训练窗口题号进度条
        self._drill_scale_label = None
        self._drill_scale_row = None
        self._drill_scale_suppress = False  # 同步进度条时抑制 _on_drill_scale 跳转
        # ---- 常驻形式判断（棋盘 HUD：胜率/目差，跨所有模式可见）----
        self._show_situation = False   # 形式判断 HUD 默认关闭：避免右上角卡片遮挡棋盘，用户按【形式判断】按钮主动开启
        # ---- 棋盘候选点叠加层（A/B/C/D/E 圆圈）默认关闭：用户主动按【候选点】按钮开启 ----
        self._show_candidates = False
        # ---- 自动 AI 首选提示（全模式默认开：分析回流即在棋盘标出 AI 下一手）----
        self._auto_hint = bool(self.cfg.get("auto_hint", True))
        self._mistake_review = None       # 当前隐藏答案的单题测验
        self._training = None            # 当前阶段训练会话
        self._training_report = None     # 最近一次训练评价
        self._training_last_feedback = None  # 训练中最近一手用户反馈(目损/评级)
        self._pending_training_record_id = None  # 等待自动分析完成后直接进入训练的棋局库记录
        self._library_bg_pending = {}    # qid -> 后台棋局库分析上下文
        self._library_bg_current = None  # 当前后台完善训练题的棋局库记录上下文
        self._library_bg_recent = set()  # 本轮已尝试的记录，避免反复扫同一盘失败棋
        self._training_cache_bg_pending = {}  # qid -> 后台训练应手缓存请求
        self._training_cache_bg_current = None
        self._training_prefetch_pending = {}  # qid -> 训练预热上下文
        self._training_prefetch_cache = {}    # (parent_nid, color, move) -> analysis
        self._training_prefetch_waiters = {}  # (parent_nid, color, move) -> 已实际下出的节点
        self._active_training_cache = None     # 当前训练题的持久化应手缓存
        self._active_training_cache_dirty = 0
        self._training_deferred_nodes = {}     # 引擎未启动时等待兜底分析的节点

        # ---- 点目 / 终局数棋模式 ----
        self.scoring_mode = False             # 是否处于点目模式
        self.score_estimator = None           # ScoreEstimator（点目模式期间）
        self.dead_points = set()              # 已标记死子（GTP 点串，大写）
        self._scoring_result = None           # 当前显示的 ScoreResult（随死子实时刷新）
        self._await_scoring_ownership = False # 进入点目时若缺 ownership，等返回后补 AI 建议
        self._double_pass_prompted = None     # 已提示过双 pass 的节点 nid（避免重复弹窗）
        self._scoring_suggestion_prompted = None  # 已提示过 AI 死子建议的节点 nid（避免重复弹窗）

        # ---- 复盘三件套（批量分析 / 失误榜 / 胜率曲线）----
        self._batch_target_nids = set()       # 本批【新请求】的 todo 节点 nid（只含本次发出分析的）
        self._batch_total = 0                 # 本批 todo 数（_batch_done 从 0 涨到此即完成）
        self._batch_done = 0
        self._batch_done0 = 0                 # 批量启动时主线已分析数（进度显示分子用）
        self._batch_mainline_total = 0        # 主线总节点数（进度显示分母用）
        self._tv_review = None                # 失误榜 ttk.Treeview
        self._quality_by_move = {}            # move_number -> MoveQualityResult
        self._review_map = {}                 # 失误榜 iid -> MoveNode（双击跳转）
        self._problem_eval_map = {}            # 问题棋 iid -> MoveEvaluation
        self._review_selected_move_no = None    # 保持问题选择，避免导航刷新后跳回首条
        self._selected_problem_eval = None     # 当前查看的深度对比问题手
        self._problem_compare_mode = "summary"
        self._problem_compare_pending = {}     # qid -> 实战/AI 深算上下文
        self._drill_forced_pending = {}        # qid -> (move_number, coord) 榜外手强制分析
        self._human_sl_pending = {}            # qid -> (move_number, profile_kind) Human SL 双档查询
        self._mistake_forced_pending = {}      # qid -> (item_id, played, color) 复习榜外强制分析
        self._human_sl_cache = {}              # move_number -> {current/stronger prior}（内存缓存）
        self._problem_compare_queue = []       # 自动深算最严重的若干恶手
        self._problem_branch_overlay = None    # 当前棋盘显示的比较分支
        self._graph_win = None                # 胜率曲线 Toplevel
        self._graph_canvas = None
        self._graph_heat_canvas = None        # 整盘问题手热力概览色带（对标 LizzieYzy 底部热力条）
        self._graph_pts = []                  # 曲线 [(move_number, x_pix, y_pix)]（点击命中用）
        self._strength_win = None             # 棋力评估 Toplevel（阶段进度条标亮）
        self._strength_canvas = None
        self._strength_tv = None              # 棋力评估阶段明细 Treeview
        self._strength_side_var = None        # 棋力评估视角：双方/黑方/白方
        self._strength_summary_lbl = None
        self._strength_segs = []              # 当前进度条各段（含 frac，点击跳转用）
        self._current_loss_val = None         # 当前节点这手的目损（redraw 画红圈用，缓存）
        self._current_quality_result = None   # move_quality 精细评价结果（6 级色圈 + 原因）

        # ---- 易用性：自动播放 / 自动启动 ----
        self._auto_play = False               # 自动播放中
        self._auto_play_job = None            # after 句柄（暂停时 cancel）
        self._auto_start_attempted = False    # 自动启动只尝试一次（避免反复弹错）
        self._show_pv = False                 # 主变 10 步显示模式（首选 pv 前 10 步标号）
        self._pv_idx = 0                      # 主变当前显示的候选索引（0-based，可切前几选）

        # ---- 棋盘几何 ----
        self.CELL = 30
        self.MARGIN = 26
        self.BOARD_PIX = self.MARGIN * 2 + self.CELL * (self.size - 1)
        self._fullscreen = False
        self._redrawing = False     # redraw 重入标志（节流）
        self._redraw_dirty = False  # redraw 途中再次调用时标记 dirty
        self._resize_job = None
        # ---- PIL 锐利化缓存（棋盘底图 + 棋子 PNG）----
        # Tk Canvas 的 create_oval/create_line 无原生抗锯齿，PIL 2x 超采样 + Lanczos 缩小实现真 AA。
        # 缓存键：(BOARD_PIX, CELL, 主题哈希)；尺寸/主题变化时重渲染。
        self._board_bg_image = None      # ImageTk.PhotoImage 棋盘底图（木纹+网格+星位+坐标）
        self._board_bg_key = None        # 缓存键
        self._stone_image_cache = {}     # {(color, radius): ImageTk.PhotoImage} 棋子带阴影
        self._stone_cache_key = None     # 棋子缓存主题键
        self._hover_point = None          # 当前合法悬停落点 (x, y)
        self._hint_point = None           # 用户主动请求的 AI 提示落点
        self._hint_pending_nid = None     # 等待分析返回后自动显示提示的节点
        self._hint_auto = False           # 当前 _hint_point 是否由「自动首选」设置（影响标注文案）

        self._setup_style()
        self._build_ui()
        self._init_overlay_layers()   # 图层注册表：新增图层零改 redraw，互斥集中管理
        self._init_event_bus()        # 事件总线：各域订阅 navigated/analysis_applied，解耦刷新扇出
        self.after_idle(self._sync_toggle_styles)   # 启动同步 toggle 按钮激活态
        self.redraw()
        self.bind("<Left>", lambda e: self.do_undo())
        self.bind("<Right>", lambda e: self.do_redo())
        self.bind("<Home>", lambda e: self.do_goto_root())
        self.bind("<End>", lambda e: self.do_goto_mainline_end())
        self.bind("<space>", lambda e: self.toggle_auto_play())
        self.bind("<Prior>", lambda e: self.do_step(-10))    # PageUp
        self.bind("<Next>", lambda e: self.do_step(10))      # PageDown
        self.bind("<F11>", lambda e: self._toggle_fullscreen())
        self.bind("<Escape>", lambda e: self._on_escape())
        self.bind("<F1>", lambda e: self.show_hint())
        self.bind("<Control-z>", lambda e: self.do_takeback())
        self.bind("<Control-Z>", lambda e: self.do_takeback())
        self.bind("<Control-p>", lambda e: self.do_pass())   # 停一手 / 虚手
        self.bind("<Control-P>", lambda e: self.do_pass())
        self.bind("<Control-t>", lambda e: self._toggle_theme())
        self.bind("<Control-T>", lambda e: self._toggle_theme())
        self.bind("<Control-o>", lambda e: self.do_import_sgf())
        self.bind("<Control-O>", lambda e: self.do_import_sgf())
        self.bind("<Control-s>", lambda e: self.do_save_project())
        self.bind("<Control-S>", lambda e: self.do_save_project())
        self.bind("<Control-l>", lambda e: self.open_game_library())
        self.bind("<Control-L>", lambda e: self.open_game_library())
        self.bind("<Control-r>", lambda e: self.force_analyze())
        self.bind("<Control-R>", lambda e: self.force_analyze())
        self.bind("<Configure>", self._on_configure)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(POLL_MS, self._poll_loop)
        self.after(120, lambda: self.scan_library_inbox(silent=True))
        self.after(200, self._maybe_autostart)               # 打开即自动启动引擎
        self.after(600, self._kick_analysis_queue)

    def _on_escape(self):
        """Esc：点目模式时退出点目（避免棋盘被锁住的体感），否则退出全屏。"""
        if self.scoring_mode:
            self.exit_scoring()
        else:
            self._exit_fullscreen()

    # ===================== UI 构建 =====================
    def _current_palette(self):
        """返回统一深色调色板（三套已合并）。"""
        return _UNIFIED_COLORS

    def _apply_color_palette(self):
        COLORS.clear()
        COLORS.update(self._current_palette())
        if _HAS_CTK:
            ctk.set_appearance_mode("dark")

    def _theme_button_text(self):
        return "深色主题"

    def _style_display_name(self):
        return UI_STYLE_LABELS.get(getattr(self, "_ui_style", "simple"), "简洁")

    def _setup_style(self):
        """研究型桌面工具主题：克制层级、清晰状态和稳定的主次操作。"""
        s = ttk.Style(self)
        try:
            s.theme_use("clam")
        except Exception:
            pass
        s.configure(".", background=COLORS["bg"], foreground=COLORS["text"], font=FONTS["ui"])
        s.configure("TFrame", background=COLORS["bg"])
        s.configure("TLabelframe", background=COLORS["card"], bordercolor=COLORS["muted"],
                    relief="solid", borderwidth=1)
        s.configure("TLabelframe.Label", background=COLORS["card"],
                    foreground=COLORS["text"], font=FONTS["section"], padding=(4, 1))
        # ===== 按钮统一反馈层（质感/交互增强：描边可见 + 三态 map + 按压下沉 + 内容位移）=====
        # clam 下 relief="solid" 才真正绘制 bordercolor 描边（"flat" 不画边）；
        # pressed→"sunken" 营造下沉，shiftrelief=1 让按下时内容向右下偏移 1px（真触觉位移）。
        # 与项目卡片语言（Card/Section.TLabelframe 早已 solid+1px muted 边）保持一致。
        _BTN_FOCUS = COLORS["accent_s"]          # 键盘聚焦环：浅主色，不抢眼
        # 默认按钮（设置对话框等）：卡片底 + muted 细描边
        s.configure("TButton", font=FONTS["ui"], padding=(10, 6),
                    background=COLORS["card"], foreground=COLORS["text"],
                    bordercolor=COLORS["muted"], borderwidth=1,
                    focuscolor=_BTN_FOCUS, relief="solid", shiftrelief=1)
        s.map("TButton",
              background=[("active", COLORS["accent_s"]), ("pressed", COLORS["accent_s"]),
                          ("disabled", COLORS["card2"])],
              bordercolor=[("active", COLORS["accent"]), ("pressed", COLORS["accent"]),
                           ("disabled", COLORS["muted"])],
              foreground=[("disabled", COLORS["subtext"])],
              relief=[("pressed", "sunken")])
        # 主操作按钮（导入/播放/训练…）：主色实底 + 白字 + 深一档主色细边（与描边类统一）
        s.configure("Accent.TButton", font=FONTS["ui"], padding=(11, 7),
                    background=COLORS["accent"], foreground="#ffffff",
                    bordercolor=COLORS["accent_h"], borderwidth=1,
                    focuscolor=_BTN_FOCUS, relief="solid", shiftrelief=1)
        s.map("Accent.TButton",
              background=[("active", COLORS["accent_h"]), ("pressed", COLORS["accent_h"]),
                          ("disabled", COLORS["accent_m"])],
              bordercolor=[("active", COLORS["accent_h"]), ("pressed", COLORS["shadow"]),
                           ("disabled", COLORS["accent_m"])],
              foreground=[("disabled", "#eef4f1")],
              relief=[("pressed", "sunken")])
        # 次要按钮（停一手/提示/悔棋…）：休息无边幽灵态、hover 浮出浅底+描边、pressed 下沉
        s.configure("Quiet.TButton", font=FONTS["ui"], padding=(9, 5),
                    background=COLORS["card2"], foreground=COLORS["subtext"],
                    bordercolor=COLORS["card2"], borderwidth=1,
                    focuscolor=_BTN_FOCUS, relief="flat", shiftrelief=1)
        s.map("Quiet.TButton",
              background=[("active", COLORS["accent_s"]), ("pressed", COLORS["accent_s"]),
                          ("disabled", COLORS["card2"])],
              bordercolor=[("active", COLORS["accent_m"]), ("pressed", COLORS["accent"]),
                           ("disabled", COLORS["muted"])],
              foreground=[("active", COLORS["text"]), ("disabled", COLORS["subtext"])],
              relief=[("pressed", "sunken"), ("active", "solid")])
        # 顶栏按钮（棋谱库/保存/主题）：宽 padding、hover 转主色调
        s.configure("Topbar.TButton", font=FONTS["ui"], padding=(13, 7),
                    background=COLORS["card2"], foreground=COLORS["text"],
                    bordercolor=COLORS["muted"], borderwidth=1,
                    focuscolor=_BTN_FOCUS, relief="solid", shiftrelief=1)
        s.map("Topbar.TButton",
              background=[("active", COLORS["accent_s"]), ("pressed", COLORS["accent_s"]),
                          ("disabled", COLORS["card2"])],
              bordercolor=[("active", COLORS["accent_m"]), ("pressed", COLORS["accent"]),
                           ("disabled", COLORS["muted"])],
              foreground=[("active", COLORS["accent"]), ("disabled", COLORS["subtext"])],
              relief=[("pressed", "sunken")])
        # 大号按钮（顶栏/传输区，用户反馈要求更大更好点）：字号 12 + 加大内边距
        for _base, _pad in (("Tool", (14, 9)), ("Accent", (16, 9)),
                            ("Topbar", (18, 9)), ("TButton", (14, 9))):
            s.configure("%s.Lg.TButton" % _base, font=FONTS["btn_lg"],
                        padding=_pad)
        # transport bar 导航按钮（|‹ ← → ›|）：此前【无 map、无 hover/press】，现补全三态
        s.configure("Tool.TButton", font=FONTS["ui"], padding=(7, 5),
                    background=COLORS["card"], foreground=COLORS["text"],
                    bordercolor=COLORS["muted"], borderwidth=1,
                    focuscolor=_BTN_FOCUS, relief="solid", shiftrelief=1)
        s.map("Tool.TButton",
              background=[("active", COLORS["accent_s"]), ("pressed", COLORS["accent_s"]),
                          ("disabled", COLORS["card2"])],
              bordercolor=[("active", COLORS["accent"]), ("pressed", COLORS["accent"]),
                           ("disabled", COLORS["muted"])],
              foreground=[("active", COLORS["accent"]), ("disabled", COLORS["subtext"])],
              relief=[("pressed", "sunken")])
        # 激活态 toggle 按钮（热力图/主变/AI首选/形式判断/树视图 等开关激活时）：
        # accent_s 底 + accent 边 + accent_h 字，与未激活 base 样式明显区分（不只靠 ✓ 文字）
        s.configure("ToggleActive.TButton", font=FONTS["ui"], padding=(9, 5),
                    background=COLORS["accent_s"], foreground=COLORS["accent_h"],
                    bordercolor=COLORS["accent"], borderwidth=1,
                    focuscolor=_BTN_FOCUS, relief="solid", shiftrelief=1)
        s.map("ToggleActive.TButton",
              background=[("active", COLORS["accent_s"]), ("pressed", COLORS["accent_s"]),
                          ("disabled", COLORS["card2"])],
              bordercolor=[("active", COLORS["accent_h"]), ("pressed", COLORS["accent_h"]),
                           ("disabled", COLORS["muted"])],
              foreground=[("active", COLORS["accent_h"]), ("disabled", COLORS["subtext"])],
              relief=[("pressed", "sunken")])
        # Treeview（候选点 / 失误榜 / 训练表 / 棋谱库）：卡片底 + 1px 边框 + 主色粗体表头
        # Treeview 现代化：行高加大（34px 更宽松）、字号略增（data 11 等宽对齐）、
        # 无边框（靠容器分隔）、选中色 accent_s + 表头加大 padding
        s.configure("Treeview", background=COLORS["card"], fieldbackground=COLORS["card"],
                    foreground=COLORS["text"], rowheight=34, font=FONTS["data"],
                    borderwidth=0, relief="flat")
        s.map("Treeview", background=[("selected", COLORS["accent_s"])],
              foreground=[("selected", COLORS["text"])])
        s.configure("Treeview.Heading", background=COLORS["card2"],
                    foreground=COLORS["accent"], font=FONTS["section"], relief="flat",
                    padding=(10, 8), borderwidth=0)
        s.map("Treeview.Heading", background=[("active", COLORS["accent_s"])])
        s.configure("TNotebook", background=COLORS["bg"], borderwidth=0, tabmargins=(0, 0, 0, 0))
        s.configure("TNotebook.Tab", font=FONTS["ui"], padding=(15, 8),
                    background=COLORS["card2"], foreground=COLORS["subtext"])
        s.map("TNotebook.Tab",
              background=[("selected", COLORS["card"]), ("active", COLORS["accent_s"])],
              foreground=[("selected", COLORS["accent"]), ("active", COLORS["text"])])
        s.configure("Workspace.TNotebook", background=COLORS["bg"],
                    borderwidth=0, tabmargins=(0, 0, 0, 0))
        s.configure("Workspace.TNotebook.Tab", font=FONTS["section"],
                    padding=(18, 9), background=COLORS["card2"],
                    foreground=COLORS["subtext"], borderwidth=0)
        s.map("Workspace.TNotebook.Tab",
              background=[("selected", COLORS["accent_s"]),
                          ("active", COLORS["card"])],
              foreground=[("selected", COLORS["accent"]),
                          ("active", COLORS["text"])])
        # 输入框（引擎/模型设置对话框）：卡片底 + focus 主色边
        s.configure("TEntry", fieldbackground=COLORS["card"], foreground=COLORS["text"],
                    bordercolor=COLORS["muted"], lightcolor=COLORS["muted"],
                    darkcolor=COLORS["muted"], insertcolor=COLORS["text"], padding=3)
        s.map("TEntry", bordercolor=[("focus", COLORS["accent"])],
              lightcolor=[("focus", COLORS["accent"])])
        # 卡片（LabelFrame）：浅灰底 + 1px 细边框 + 主色小标题
        s.configure("Card.TLabelframe", background=COLORS["card"], bordercolor=COLORS["muted"],
                    relief="solid", borderwidth=1, padding=(10, 8))
        s.configure("Card.TLabelframe.Label", background=COLORS["card"],
                    foreground=COLORS["text"], font=FONTS["section"])
        s.configure("Section.TLabelframe", background=COLORS["card"],
                    bordercolor=COLORS["muted"], relief="solid", borderwidth=1,
                    padding=(8, 6))
        s.configure("Section.TLabelframe.Label", background=COLORS["card"],
                    foreground=COLORS["text"], font=FONTS["section"], padding=(2, 0))
        s.configure("Review.Horizontal.TProgressbar", troughcolor=COLORS["muted"],
                    background=COLORS["accent"], bordercolor=COLORS["muted"],
                    lightcolor=COLORS["accent"], darkcolor=COLORS["accent"],
                    thickness=7)
        # ===== 输入/选择控件统一（此前用 ttk 默认=简陋，与按钮/卡片视觉割裂）=====
        # 滚动条：浅槽 + 中性滑块，hover/press 转主色
        for _sb in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
            s.configure(_sb, background=COLORS["card2"], troughcolor=COLORS["bg"],
                        bordercolor=COLORS["bg"], arrowcolor=COLORS["subtext"],
                        relief="flat", borderwidth=0, arrowsize=14)
        s.map("TScrollbar",
              background=[("active", COLORS["accent_m"]), ("pressed", COLORS["accent"])],
              arrowcolor=[("active", COLORS["accent"])])
        # 下拉框 / 数值步进：卡片底 + focus 主色描边 + 选中主色
        for _cb in ("TCombobox", "TSpinbox"):
            s.configure(_cb, fieldbackground=COLORS["card"], foreground=COLORS["text"],
                        background=COLORS["card2"], bordercolor=COLORS["muted"],
                        lightcolor=COLORS["muted"], darkcolor=COLORS["muted"],
                        selectbackground=COLORS["accent_s"], selectforeground=COLORS["text"],
                        insertcolor=COLORS["text"], arrowcolor=COLORS["accent"],
                        relief="flat", borderwidth=1, padding=3)
            s.map(_cb,
                  bordercolor=[("focus", COLORS["accent"])],
                  lightcolor=[("focus", COLORS["accent"])],
                  darkcolor=[("focus", COLORS["accent"])],
                  arrowcolor=[("active", COLORS["accent_h"])])
        # 勾选 / 单选：指示器选中态主色
        for _cr in ("TCheckbutton", "TRadiobutton"):
            s.configure(_cr, background=COLORS["bg"], foreground=COLORS["text"],
                        focuscolor=COLORS["accent_s"], indicatorcolor=COLORS["card"],
                        bordercolor=COLORS["muted"], relief="flat")
            s.map(_cr,
                  indicatorcolor=[("selected", COLORS["accent"]), ("pressed", COLORS["accent_h"])],
                  background=[("active", COLORS["bg"])],
                  foreground=[("disabled", COLORS["subtext"])])
        # 选项菜单（OptionMenu 触发按钮）：按钮化，与描边类对齐
        s.configure("TMenubutton", font=FONTS["ui"], padding=(10, 5),
                    background=COLORS["card"], foreground=COLORS["text"],
                    bordercolor=COLORS["muted"], borderwidth=1, relief="solid",
                    arrowcolor=COLORS["accent"])
        s.map("TMenubutton",
              background=[("active", COLORS["accent_s"]), ("pressed", COLORS["accent_s"])],
              bordercolor=[("active", COLORS["accent"])],
              foreground=[("active", COLORS["accent"])])
        # 滑块（drill 题号进度等；MoveScrubber 已自绘不受影响）：浅槽 + 主色滑块
        s.configure("Horizontal.TScale", background=COLORS["accent"],
                    troughcolor=COLORS["card2"], bordercolor=COLORS["card2"],
                    lightcolor=COLORS["accent"], darkcolor=COLORS["accent"])

    def _draw_brand_mark(self):
        """顶部品牌圆标随风格重绘；Canvas 图形不会被普通控件映射自动改色。"""
        mark = getattr(self, "brand_mark", None)
        if mark is None:
            return
        try:
            mark.delete("all")
            mark.configure(bg=COLORS["card"])
            if getattr(self, "_ui_style", "simple") == "cyberpunk":
                mark.create_oval(
                    2, 2, 36, 36, fill=COLORS["card2"],
                    outline=COLORS["red"], width=2)
                mark.create_oval(
                    7, 7, 31, 31, fill=COLORS["accent_s"],
                    outline=COLORS["accent"], width=2)
                mark.create_text(
                    19, 18, text="棋", fill=COLORS["accent_h"],
                    font=("Microsoft YaHei UI", 13, "bold"))
            else:
                mark.create_oval(
                    2, 2, 36, 36, fill=COLORS["accent"], outline=COLORS["accent"])
                mark.create_text(
                    19, 18, text="棋", fill="#ffffff",
                    font=("Microsoft YaHei UI", 13, "bold"))
        except tk.TclError:
            pass

    def _draw_style_preview(self, canvas, style_key):
        """设置窗口里的风格缩略预览：只表达色彩与信息层级，不承担真实布局。"""
        palette = CYBERPUNK_COLORS if style_key == "cyberpunk" else (
            DARK_COLORS if self._theme_dark else LIGHT_COLORS)
        try:
            canvas.delete("all")
            canvas.configure(bg=palette["bg"], highlightbackground=palette["muted"])
            w = int(canvas.cget("width"))
            h = int(canvas.cget("height"))
            canvas.create_rectangle(0, 0, w, h, fill=palette["bg"], outline=palette["bg"])
            canvas.create_rectangle(8, 8, w - 8, h - 8, fill=palette["card"],
                                    outline=palette["muted"], width=1)
            canvas.create_rectangle(8, 8, w - 8, 13, fill=palette["accent"],
                                    outline=palette["accent"])
            canvas.create_rectangle(18, 22, 86, h - 18, fill=palette["board"],
                                    outline=palette["grid"], width=1)
            for i in range(4):
                x = 27 + i * 15
                y = 31 + i * 9
                canvas.create_oval(x - 4, y - 4, x + 4, y + 4,
                                   fill=palette["black"] if i % 2 == 0 else palette["white"],
                                   outline=palette["accent_m"])
            canvas.create_rectangle(100, 22, w - 20, 40, fill=palette["card2"],
                                    outline=palette["muted"])
            canvas.create_text(108, 31, anchor="w", text="胜率 57.2%",
                               fill=palette["text"], font=FONTS["small"])
            canvas.create_rectangle(100, 48, w - 20, h - 20, fill=palette["accent_s"],
                                    outline=palette["accent"])
            canvas.create_text(108, 60, anchor="w", text="A  D4  ·  PV",
                               fill=palette["accent_h"], font=FONTS["small"])
        except tk.TclError:
            pass

    def _make_button(self, parent, text, command, variant="default", big=False, **kw):
        """按钮工厂：有 CustomTkinter 时返回圆角 CTkButton，否则降级 ttk.Button。

        variant: "accent"（主操作，强调色填充）/ "topbar"（顶栏次要）/ "default"
        big=True 用大号样式（顶栏/传输区，用户反馈要求更大更好点）。
        保持与原 ttk.Style 六种按钮样式语义对应，迁移时零行为变更。
        """
        if big:
            if _HAS_CTK:
                kw.setdefault("height", 36)
                kw.setdefault("font", FONTS["btn_lg"])
            else:
                kw["style"] = {
                    "accent": "Accent.Lg.TButton",
                    "topbar": "Topbar.Lg.TButton",
                    "default": "Tool.Lg.TButton",
                }.get(variant, "Tool.Lg.TButton")
        if _HAS_CTK:
            if variant == "accent":
                return ctk.CTkButton(
                    parent, text=text, command=command,
                    fg_color=COLORS["accent"], hover_color=COLORS["accent_h"],
                    text_color="#ffffff", corner_radius=8,
                    font=kw.pop("font", (FONTS["ui"][0], FONTS["ui"][1])), **kw)
            if variant == "topbar":
                return ctk.CTkButton(
                    parent, text=text, command=command,
                    fg_color=COLORS["card2"], hover_color=COLORS["accent_s"],
                    text_color=COLORS["text"], corner_radius=8,
                    border_width=1, border_color=COLORS["muted"],
                    font=kw.pop("font", (FONTS["ui"][0], FONTS["ui"][1])), **kw)
            return ctk.CTkButton(
                parent, text=text, command=command,
                fg_color=COLORS["card"], hover_color=COLORS["accent_s"],
                text_color=COLORS["text"], corner_radius=6,
                border_width=1, border_color=COLORS["muted"],
                font=kw.pop("font", (FONTS["ui"][0], FONTS["ui"][1])), **kw)
        # 降级：无 CTk 时用原 ttk 按钮（big 分支可能已注入 style）
        style = kw.pop("style", None) or {
            "accent": "Accent.TButton", "topbar": "Topbar.TButton"
        }.get(variant, "TButton")
        return ttk.Button(parent, text=text, command=command, style=style, **kw)

    def _make_card_frame(self, parent, title=None, corner_radius=10, **kw):
        """卡片容器工厂：CTk 时返回 CTkFrame 圆角卡片，否则降级 ttk.LabelFrame。

        替代散落的 ttk.LabelFrame(style="Card/Section.TLabelframe")，统一圆角视觉。
        title 仅降级路径（ttk.LabelFrame）生效；CTk 路径靠内部内容自标识（保持紧凑）。
        """
        if _HAS_CTK:
            fg = kw.pop("fg_color", COLORS["card"])
            return ctk.CTkFrame(parent, fg_color=fg, corner_radius=corner_radius, **kw)
        return ttk.LabelFrame(parent, text=" %s " % title if title else "",
                              style="Section.TLabelframe")

    def _build_ui(self):
        # ---- V6 App Shell（Phase 2）：左导航 + 页面容器 + 路由 ----
        # 旧工作台整体嵌入"复盘页"，功能不变；首页为新增一级页面。
        from ui import theme as _v6theme
        from ui.pages.home import HomePage
        from ui.shell import Shell
        _v6theme.bind(palette=COLORS, fonts=FONTS, space=SPACE)
        self.shell = Shell(self, self)
        self.shell.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.router = self.shell.router
        from ui.pages.library import LibraryPage
        self.home_page = self.shell.register(
            "home", HomePage(self.shell.content, self))
        self.library_page = self.shell.register(
            "library", LibraryPage(self.shell.content, self))
        review_page = tk.Frame(self.shell.content, bg=COLORS["bg"])
        self.shell.register("review", review_page)
        self._review_page = review_page
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        self.minsize(
            min(1040, max(800, screen_w - 32)),
            min(640, max(560, screen_h - 80)))

        # 顶部应用栏：单一品牌色、明确主操作，避免普通工具栏的拼装感。
        tk.Frame(review_page, bg=COLORS["accent"], height=3).pack(
            side=tk.TOP, fill=tk.X)
        appbar = tk.Frame(review_page, bg=COLORS["card"], height=64,
                          highlightthickness=1, highlightbackground=COLORS["muted"])
        appbar.pack(side=tk.TOP, fill=tk.X)
        appbar.pack_propagate(False)
        brand = tk.Frame(appbar, bg=COLORS["card"])
        brand.pack(side=tk.LEFT, padx=(16, 8), pady=9)
        self.brand_mark = tk.Canvas(
            brand, width=38, height=38, bg=COLORS["card"],
            highlightthickness=0)
        self.brand_mark.pack(side=tk.LEFT, padx=(0, 9))
        self._draw_brand_mark()
        brand_copy = tk.Frame(brand, bg=COLORS["card"])
        brand_copy.pack(side=tk.LEFT)
        tk.Label(brand_copy, text="KataGo 个人复盘", font=FONTS["h1"],
                 bg=COLORS["card"], fg=COLORS["text"]).pack(anchor="w")
        tk.Label(brand_copy, text="本地 AI 研究工作台",
                 font=FONTS["small"], bg=COLORS["card"],
                 fg=COLORS["subtext"]).pack(anchor="w")
        app_actions = tk.Frame(appbar, bg=COLORS["card"])
        app_actions.pack(side=tk.RIGHT, padx=14, pady=10)
        self._make_button(app_actions, "导入棋谱", self.do_import_sgf,
                          variant="accent", big=True).pack(side=tk.LEFT, padx=3)
        self._make_button(app_actions, "学习中心", self.open_learning_center,
                          variant="topbar", big=True).pack(side=tk.LEFT, padx=3)
        self._make_button(app_actions, "棋谱库", self.open_game_library,
                          variant="topbar", big=True).pack(side=tk.LEFT, padx=3)
        self._make_button(app_actions, "保存", self.do_save_project,
                          variant="topbar", big=True).pack(side=tk.LEFT, padx=3)
        self.btn_theme = self._make_button(
            app_actions, self._theme_button_text(), self._toggle_theme, variant="topbar")
        self.btn_theme.pack(side=tk.LEFT, padx=3)
        # V6 §23：学习 | 研究 分段控件。当前默认研究（Phase 5 完成复盘页
        # 学习化改造后默认切学习）。学习模式隐藏 AI 提示与候选叠加。
        from ui.components import segmented
        self._review_mode_segment, self._review_mode_state = segmented(
            appbar, ["学习模式", "研究模式"], self._set_review_mode, initial=1)
        self._review_mode_segment.pack(side=tk.RIGHT, padx=(0, 12), pady=12)
        tk.Frame(appbar, bg=COLORS["muted"], width=1).pack(
            side=tk.LEFT, fill=tk.Y, padx=(4, 14), pady=13)
        game_context = tk.Frame(appbar, bg=COLORS["card"])
        game_context.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8), pady=9)
        self.lbl_game_title = tk.Label(
            game_context, text="新棋局", width=30, anchor="w",
            bg=COLORS["card"], fg=COLORS["text"], font=FONTS["section"])
        self.lbl_game_title.pack(anchor="w")
        self.lbl_game_meta = tk.Label(
            game_context, text="黑方 vs 白方 · 中国规则 · 贴 7.5",
            width=54, anchor="w", bg=COLORS["card"],
            fg=COLORS["subtext"], font=FONTS["small"])
        self.lbl_game_meta.pack(anchor="w")

        # 可调分栏：棋盘始终是视觉主体（最大化时占窗口约 2/3），右侧保持可读。
        workspace = tk.PanedWindow(
            review_page, orient=tk.HORIZONTAL, bg=COLORS["muted"], bd=0,
            sashwidth=7, sashrelief=tk.FLAT, opaqueresize=True)
        workspace.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=8)
        left = tk.Frame(workspace, bg=COLORS["bg"], width=620)
        right = tk.Frame(workspace, bg=COLORS["bg"], width=RIGHT_PANEL_WIDTH)
        right.pack_propagate(False)
        workspace.add(left, minsize=420, stretch="always")
        workspace.add(right, minsize=400, stretch="never")
        self.workspace = workspace
        self._board_panel = left
        self._right_panel = right

        # 棋盘 + 细阴影边框
        board_wrap = tk.Frame(left, bg=COLORS["shadow"])
        board_wrap.pack(padx=8, pady=(4, 8))
        self.canvas = tk.Canvas(board_wrap, width=self.BOARD_PIX, height=self.BOARD_PIX,
                                bg=COLORS["board"], highlightthickness=1,
                                highlightbackground=COLORS["grid"], highlightcolor=COLORS["grid"])
        self.canvas.pack(padx=(1, 2), pady=(1, 2))
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Motion>", self._on_board_motion)
        self.canvas.bind("<Leave>", self._on_board_leave)
        self._build_transport_bar(left)

        # ---- 右侧：形势总览 + 研究路径标签页 ----
        header = tk.Frame(right, bg=COLORS["bg"])
        header.pack(fill="x", pady=(0, 7))
        heading = tk.Frame(header, bg=COLORS["bg"])
        heading.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Frame(heading, bg=COLORS["accent"], width=4, height=38).pack(
            side=tk.LEFT, fill=tk.Y, padx=(0, 9))
        heading_copy = tk.Frame(heading, bg=COLORS["bg"])
        heading_copy.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(heading_copy, text="分析工作台", font=FONTS["title"],
                 bg=COLORS["bg"], fg=COLORS["text"]).pack(anchor="w")
        tk.Label(heading_copy, text="局面判断 · 推荐研究 · 问题复盘",
                 font=FONTS["small"], bg=COLORS["bg"],
                 fg=COLORS["subtext"]).pack(anchor="w")
        self.lbl_status = tk.Label(
            header, text="● 未启动", fg=COLORS["subtext"], bg=COLORS["accent_s"],
            font=FONTS["small"], padx=12, pady=6,
            highlightthickness=0)
        self.lbl_status.pack(side=tk.RIGHT, padx=(8, 0))

        overview = self._make_card_frame(right, "当前形势", corner_radius=10)
        overview.pack(fill="x", pady=(0, 8))
        self._build_card_score(overview)
        self._polish_card(overview)

        tabs = ttk.Notebook(right, style="Workspace.TNotebook")
        tabs.pack(fill="both", expand=True, pady=(0, 8))
        for title, builder in [("研究", self._build_card_analysis),
                               ("复盘", self._build_card_review),
                               ("棋谱", self._build_card_sgf),
                               ("导航", self._build_card_play)]:
            tab = tk.Frame(tabs, bg=COLORS["card"], padx=8, pady=8)
            tabs.add(tab, text=title)
            builder(tab)
            self._polish_card(tab)
        self.tabs = tabs
        self.tabs.bind(
            "<<NotebookTabChanged>>", self._remember_workspace_selection,
            add="+")
        self.review_views.bind(
            "<<NotebookTabChanged>>", self._remember_workspace_selection,
            add="+")

        # 底部状态栏
        self.lbl_msg = tk.Label(right, text="就绪",
                                fg=COLORS["subtext"], bg=COLORS["card2"],
                                wraplength=RIGHT_PANEL_WIDTH - 30,
                                justify=tk.LEFT, font=FONTS["small"], padx=9, pady=7,
                                highlightthickness=1, highlightbackground=COLORS["muted"])
        self.lbl_msg.pack(anchor="w", fill="x")
        self._restore_workspace_state()
        self._refresh_game_context()
        # 默认进入复盘工作区（棋盘是主视觉）；"今日学习"经左栏按需进入
        try:
            self.router.go("review")
        except Exception:
            pass

    def _assessment_context(self):
        """判分上下文唯一来源（审查 P0-1/P0-2/#8）。

        只认用户设置的稳定棋力 user_learning_rank；单盘表现禁止参与
        判题容差。未设置时用基础容差，并一次性提示可去设置里配置。
        complexity 固定 0（1-learnability 伪接线已删，等真实数据）。
        """
        from candidate_assessment import build_assessment_context
        stable = str(self.cfg.get("user_learning_rank") or "").strip()
        ctx = build_assessment_context(stable_rank=stable)
        if not stable and not getattr(self, "_rank_hint_shown", False):
            self._rank_hint_shown = True
            self._set_msg("判分使用基础容差；在设置里填写『学习棋力』可获得个性化标准")
        return ctx

    def _rescale_board_soon(self):
        """进入复盘页/恢复分栏后重算棋盘自适应尺寸（不依赖窗口事件）。"""
        try:
            self._do_resize(self.winfo_width(), self.winfo_height())
        except Exception:
            pass

    def _set_review_mode(self, index):
        """学习/研究模式切换（V6 §24/§31）。学习模式隐藏 AI 提示与候选叠加。"""
        learning = index == 0
        if learning:
            self._mode_saved = {
                "candidates": getattr(self, "_show_candidates", False),
                "auto_hint": getattr(self, "_auto_hint", True),
            }
            self._show_candidates = False
            self._auto_hint = False
            self._set_msg("学习模式：AI 提示已隐藏——先自己想，再落子作答")
        else:
            saved = getattr(self, "_mode_saved", {})
            self._show_candidates = saved.get(
                "candidates", getattr(self, "_show_candidates", False))
            self._auto_hint = saved.get(
                "auto_hint", bool(self.cfg.get("auto_hint", True)))
            self._set_msg("研究模式：候选与 AI 提示已恢复")
        try:
            if hasattr(self, "btn_candidates"):
                self.btn_candidates.configure(
                    text="候选点 ✓" if self._show_candidates else "候选点 ✗")
        except Exception:
            pass
        self.redraw()

    def _build_transport_bar(self, parent):
        """棋盘下方常驻导航；无需切换标签页即可完成复盘的基本前后移动。"""
        transport = tk.Frame(
            parent, bg=COLORS["card"], highlightthickness=1,
            highlightbackground=COLORS["muted"], padx=9, pady=7)
        transport.pack(fill="x", padx=8, pady=(0, 4))
        top = tk.Frame(transport, bg=COLORS["card"])
        top.pack(fill="x")
        self.lbl_move_num = tk.Label(
            top, text="第 0 手 · 黑方", font=FONTS["h2"],
            bg=COLORS["card"], fg=COLORS["text"], width=16, anchor="w")
        self.lbl_move_num.pack(side=tk.LEFT, padx=(2, 8))
        for text, command, tooltip in [
                ("|‹", self.do_goto_root, "回到棋谱起点（Home）"),
                ("←", self.do_undo, "上一步（←）")]:
            button = self._make_button(top, text, command, variant="default",
                                       big=True, width=52 if _HAS_CTK else 4)
            button.pack(side=tk.LEFT, padx=4)
            self._attach_tooltip(button, tooltip)
        self.btn_play = self._make_button(
            top, "▶ 播放", self.toggle_auto_play, variant="accent", big=True)
        self.btn_play.pack(side=tk.LEFT, padx=3)
        for text, command, tooltip in [
                ("→", self.do_redo, "下一步（→）"),
                ("›|", self.do_goto_mainline_end, "跳到主线末尾（End）")]:
            button = self._make_button(top, text, command, variant="default",
                                       big=True, width=52 if _HAS_CTK else 4)
            button.pack(side=tk.LEFT, padx=4)
            self._attach_tooltip(button, tooltip)
        self.btn_pass = self._make_button(top, "停一手", self.do_pass, variant="default", big=True)
        self.btn_pass.pack(side=tk.LEFT, padx=(8, 2))
        self._attach_tooltip(
            self.btn_pass, "当前方虚手（停一手），对方连下；研究让子棋调轮次用（Ctrl+P）")
        hint_button = self._make_button(top, "提示", self.show_hint, variant="default", big=True)
        hint_button.pack(side=tk.RIGHT, padx=4)
        self._attach_tooltip(hint_button, "在棋盘标出 AI 首选（F1）")
        takeback_button = self._make_button(top, "悔棋", self.do_takeback, variant="default", big=True)
        takeback_button.pack(side=tk.RIGHT, padx=4)
        self._attach_tooltip(takeback_button, "撤回最近一手（Ctrl+Z）")
        self.btn_situation = self._make_button(
            top, "形式判断 ✗", self.toggle_situation, variant="default", big=True)
        self.btn_situation.pack(side=tk.RIGHT, padx=4)
        self._attach_tooltip(self.btn_situation, "在棋盘常驻显示胜率 / 目差形式判断")
        # 训练控制（结束/重来/原实战）：仅训练激活时常驻显示，由 _update_training_controls 显隐
        train_ctrls = tk.Frame(top, bg=COLORS["card"])
        self.btn_train_original = self._make_button(
            train_ctrls, "原实战", self.training_show_original, variant="default")
        self.btn_train_original.pack(side=tk.LEFT, padx=1)
        self.btn_train_restart = self._make_button(
            train_ctrls, "重来", self.training_restart, variant="default")
        self.btn_train_restart.pack(side=tk.LEFT, padx=1)
        self.btn_train_finish = self._make_button(
            train_ctrls, "结束训练", self.training_finish_now, variant="accent")
        self.btn_train_finish.pack(side=tk.LEFT, padx=1)
        self._attach_tooltip(self.btn_train_finish, "结束本次阶段训练并生成报告")
        self._attach_tooltip(self.btn_train_restart, "回到阶段起点重下")
        self._attach_tooltip(self.btn_train_original, "对照查看原实战走法")
        self._train_ctrls = train_ctrls   # 默认不 pack，训练激活时再显示

        # 学习时间轴（V6 §38-43，合并进度条）：一条画布同时承担手数拖动
        # 导航 + 目损色杆 + 学习价值紫圈——此前时间轴与进度条分两行且
        # 视觉不同步，用户反馈"两条不重合"，合并为单条
        from ui.timeline import LearningTimeline
        scale_row = tk.Frame(transport, bg=COLORS["card"], height=56)
        scale_row.pack(fill="x", pady=(8, 2))
        scale_row.pack_propagate(False)
        self.timeline = LearningTimeline(
            scale_row, on_change=self._scrubber_change,
            on_commit=self._scrubber_commit, colors=COLORS, fonts=FONTS)
        self.timeline.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(2, 8))
        # 兼容旧引用（set_range/set_position/is_dragging/redraw 同名）
        self.scale = self.timeline
        self._attach_tooltip(
            self.timeline, "拖动手柄或点击轨道跳到任意一手；色杆=目损（黄/橙/红），紫圈=学习重点")
        self.lbl_scale = tk.Label(
            scale_row, text="0 / 0", width=10, anchor="e",
            bg=COLORS["card"], fg=COLORS["subtext"], font=FONTS["data_l"])
        self.lbl_scale.pack(side=tk.RIGHT)

    def _timeline_jump(self, move_no):
        """时间轴点击跳到主线第 N 手（与进度条同一路径，点目/训练态拦截一致）。"""
        self._scrubber_change(int(move_no))

    def _update_training_controls(self):
        """训练激活时在 transport bar 常驻 结束/重来/原实战 按钮；结束则隐藏。"""
        active = bool(self._training and self._training.get("active"))
        frame = getattr(self, "_train_ctrls", None)
        if frame is None:
            return
        try:
            shown = frame.winfo_ismapped()
        except tk.TclError:
            shown = False
        if active and not shown:
            frame.pack(side=tk.RIGHT, padx=4)
        elif not active and shown:
            frame.pack_forget()

    def _keep_custom_bg(self, widget):
        """标记自绘小卡片/徽章的背景色，避免 _polish_card 把它们刷回普通白底。"""
        try:
            setattr(widget, "_ui_keep_bg", True)
        except Exception:
            pass
        return widget

    def _polish_card(self, widget):
        """tk 子控件统一成卡片白底，减少 tkinter 默认灰底的拼装感。"""
        for child in widget.winfo_children():
            keep_bg = bool(getattr(child, "_ui_keep_bg", False))
            if isinstance(child, (tk.Frame, tk.Label)) and not keep_bg:
                try:
                    child.configure(bg=COLORS["card"])
                except Exception:
                    pass
            if (isinstance(child, tk.Label) and not keep_bg
                    and child.cget("fg") == COLORS["accent"]):
                try:
                    child.configure(font=FONTS["title"])
                except Exception:
                    pass
            self._polish_card(child)

    def _restore_workspace_state(self):
        """恢复窗口尺寸、分栏位置和最近使用的分析页；越界值自动忽略。"""
        size = str(self._ui_state.get("window_size") or "")
        width, height = fit_window_size(
            size, self.winfo_screenwidth(), self.winfo_screenheight())
        self.geometry("%dx%d" % (width, height))
        for notebook, key in (
                (self.tabs, "main_tab"),
                (self.review_views, "review_tab")):
            try:
                index = int(self._ui_state.get(key, 0) or 0)
                if 0 <= index < notebook.index("end"):
                    notebook.select(index)
            except (tk.TclError, TypeError, ValueError):
                pass
        pane_position = int(self._ui_state.get("pane_position", 0) or 0)
        if pane_position > 0:
            self.after(180, lambda: self._restore_pane_position(pane_position))

    def _restore_pane_position(self, position):
        try:
            max_position = max(500, self.workspace.winfo_width() - 440)
            self.workspace.sash_place(0, max(500, min(position, max_position)), 0)
        except tk.TclError:
            pass
        self.after(60, self._rescale_board_soon)

    def _remember_workspace_selection(self, _event=None):
        """只更新内存；关闭窗口时与尺寸、分栏一起原子保存。"""
        try:
            self._ui_state["main_tab"] = int(self.tabs.index(self.tabs.select()))
            self._ui_state["review_tab"] = int(
                self.review_views.index(self.review_views.select()))
        except tk.TclError:
            pass

    def _persist_workspace_state(self):
        self._remember_workspace_selection()
        if not self._fullscreen:
            width, height = self.winfo_width(), self.winfo_height()
            if width >= 900 and height >= 600:
                self._ui_state["window_size"] = "%dx%d" % (width, height)
        try:
            self._ui_state["pane_position"] = int(
                self.workspace.sash_coord(0)[0])
        except tk.TclError:
            pass
        self.cfg.update(ui_state=dict(self._ui_state))

    def _refresh_game_context(self):
        """把棋局身份和研究口径固定在顶栏，导航时只更新手数。"""
        if not hasattr(self, "lbl_game_title"):
            return
        black = str(getattr(self.tree, "_sgf_pb", "黑方") or "黑方")
        white = str(getattr(self.tree, "_sgf_pw", "白方") or "白方")
        total = 0
        node = self.tree.root
        while node.children:
            node = node.children[0]
            total += 1
        result = str(getattr(self.tree, "_sgf_re", "") or "")
        title, meta = build_game_context(
            self._current_game_label, black, white, self.rules, self.komi,
            self.tree.current.depth, total, result)
        self.lbl_game_title.config(text=title)
        self.lbl_game_meta.config(text=meta)

    def _attach_tooltip(self, widget, text):
        """为紧凑图标按钮补足可发现性；延迟出现，移开即销毁。"""
        state = {"job": None, "window": None}

        def hide(_event=None):
            if state["job"] is not None:
                try:
                    self.after_cancel(state["job"])
                except tk.TclError:
                    pass
                state["job"] = None
            if state["window"] is not None:
                try:
                    state["window"].destroy()
                except tk.TclError:
                    pass
                state["window"] = None

        def show():
            state["job"] = None
            if not widget.winfo_exists():
                return
            tip = tk.Toplevel(self)
            tip.overrideredirect(True)
            tip.attributes("-topmost", True)
            x = widget.winfo_rootx() + max(8, widget.winfo_width() // 2)
            y = widget.winfo_rooty() + widget.winfo_height() + 7
            tip.geometry("+%d+%d" % (x, y))
            tk.Label(
                tip, text=text, bg=COLORS["text"], fg=COLORS["card"],
                font=FONTS["small"], padx=8, pady=4,
                relief="solid", borderwidth=1).pack()
            state["window"] = tip

        def schedule(_event=None):
            hide()
            state["job"] = self.after(520, show)

        widget.bind("<Enter>", schedule, add="+")
        widget.bind("<Leave>", hide, add="+")
        widget.bind("<ButtonPress>", hide, add="+")

    def _prepare_child_window(self, win, title, width, height, *,
                              minsize=None, resizable=(True, True)):
        """统一产品窗口的标题、层级、初始尺寸和居中位置。"""
        win.title(title)
        win.configure(bg=COLORS["bg"])
        win.transient(self)
        if minsize:
            win.minsize(*minsize)
        win.resizable(*resizable)

        self.update_idletasks()
        parent_w = max(1, self.winfo_width())
        parent_h = max(1, self.winfo_height())
        parent_x = self.winfo_rootx()
        parent_y = self.winfo_rooty()
        screen_w = win.winfo_screenwidth()
        screen_h = win.winfo_screenheight()
        x = max(0, min(screen_w - width, parent_x + (parent_w - width) // 2))
        y = max(0, min(screen_h - height, parent_y + (parent_h - height) // 2))
        win.geometry("%dx%d+%d+%d" % (width, height, x, y))
        self.after_idle(lambda: self._focus_child_window(win))
        win.bind("<Escape>", lambda e: win.event_generate("<WM_DELETE_WINDOW>"))
        return win

    def _make_centered_toplevel(self, title, width, height, on_close=None,
                                *, minsize=None, resizable=(True, True)):
        """窗口创建工厂：一步完成 Toplevel 创建 + 居中 + WM_DELETE 绑定 + Esc 关闭。

        替代散落的 tk.Toplevel() + _prepare_child_window + protocol(WM_DELETE_WINDOW) 三步法。
        on_close：关闭回调（如 _close_graph / _close_library_window）。
        """
        win = tk.Toplevel(self)
        self._prepare_child_window(
            win, title, width, height, minsize=minsize, resizable=resizable)
        if on_close is not None:
            win.protocol("WM_DELETE_WINDOW", on_close)
        return win

    def _set_toggle(self, btn, active, base_style="TButton"):
        """切换按钮激活态样式：激活=ToggleActive.TButton（accent_s 底/accent 边），
        否则回退 base_style。与 ✓ 文案冗余编码，色弱用户也能看出开关状态。

        兼容 CTkButton：CustomTkinter 不支持 style 参数，用 fg_color 切换激活态
        （激活=accent_s 底，非激活=card/card2 底）。
        """
        if btn is None:
            return
        if _HAS_CTK and isinstance(btn, ctk.CTkButton):
            try:
                btn.configure(fg_color=COLORS["accent_s"] if active else COLORS["card2"],
                              text_color=COLORS["accent_h"] if active else COLORS["text"])
            except Exception:
                pass
            return
        try:
            btn.configure(style="ToggleActive.TButton" if active else base_style)
        except tk.TclError:
            pass

    def _set_button_variant(self, btn, accent):
        """切换按钮在 accent（强调填充）/ default（普通）两种 variant 间的视觉态。

        用于 drill 变化图按钮与对比模式按钮：激活=accent，否则=default。
        兼容 CTkButton：CustomTkinter 不支持 style 参数，用 fg_color 切换。
        """
        if btn is None:
            return
        if _HAS_CTK and isinstance(btn, ctk.CTkButton):
            try:
                if accent:
                    btn.configure(fg_color=COLORS["accent"],
                                  hover_color=COLORS["accent_h"],
                                  text_color="#ffffff")
                else:
                    btn.configure(fg_color=COLORS["card"],
                                  hover_color=COLORS["accent_s"],
                                  text_color=COLORS["text"])
            except Exception:
                pass
            return
        try:
            btn.configure(style="Accent.TButton" if accent else "Tool.TButton")
        except tk.TclError:
            pass

    def _sync_toggle_styles(self):
        """启动后同步各 toggle 按钮激活态样式（默认 _show_situation/_auto_hint=True 等）。"""
        self._set_toggle(getattr(self, "btn_heat", None), getattr(self, "_heat_mode", 0) != 0)
        self._set_toggle(getattr(self, "btn_pv", None), getattr(self, "_show_pv", False))
        self._set_toggle(getattr(self, "btn_auto_hint", None), getattr(self, "_auto_hint", True))
        self._set_toggle(getattr(self, "btn_candidates", None), getattr(self, "_show_candidates", False))
        self._set_toggle(getattr(self, "btn_situation", None),
                         getattr(self, "_show_situation", True), "Quiet.TButton")

    def _dialog_button_bar(self, parent):
        """子窗口标准操作区：卡片底 + 1px 细边框（与内容区分隔）。
        返回 inner；按钮 pack(side=RIGHT)，主操作(Accent) 先 pack 落在最右——
        符合「内容在上、右下主操作」的对话框惯例。"""
        bar = tk.Frame(parent, bg=COLORS["card"], highlightthickness=1,
                       highlightbackground=COLORS["muted"])
        bar.pack(fill="x", side=tk.BOTTOM, padx=10, pady=(4, 10))
        inner = tk.Frame(bar, bg=COLORS["card"])
        inner.pack(fill="x", padx=12, pady=9)
        return inner

    def _empty_card(self, parent, title, hint=""):
        """空状态卡片：主色左条 + 标题 + 引导文案，替代孤零零的「暂无」文本。"""
        card = tk.Frame(parent, bg=COLORS["card"], highlightthickness=1,
                        highlightbackground=COLORS["muted"])
        tk.Frame(card, bg=COLORS["accent"], width=4).pack(side=tk.LEFT, fill=tk.Y)
        body = tk.Frame(card, bg=COLORS["card"])
        body.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=14, pady=12)
        tk.Label(body, text=title, font=FONTS["title"], fg=COLORS["text"],
                 bg=COLORS["card"], anchor="w").pack(fill="x")
        if hint:
            tk.Label(body, text=hint, font=FONTS["ui"], fg=COLORS["subtext"],
                     bg=COLORS["card"], anchor="w", wraplength=420,
                     justify=tk.LEFT).pack(fill="x", pady=(4, 0))
        return card

    # ===================== 卡片构建（grid 对齐、主操作 Accent）=====================
    def _build_metric_card(self, parent, col, title, value="—", fg=None):
        """形势概览里的轻量指标卡：CTkFrame 圆角（替代 tk.Frame 直角边框）。"""
        if _HAS_CTK:
            card = ctk.CTkFrame(parent, fg_color=COLORS["card2"], corner_radius=8)
        else:
            card = self._keep_custom_bg(tk.Frame(
                parent, bg=COLORS["card2"], highlightthickness=1,
                highlightbackground=COLORS["muted"], padx=9, pady=7))
        card.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else 5, 0))
        parent.columnconfigure(col, weight=1)
        title_label = self._keep_custom_bg(tk.Label(
            card, text=title, bg=COLORS["card2"], fg=COLORS["subtext"],
            font=FONTS["small"], anchor="w"))
        title_label.pack(anchor="w", padx=9, pady=(7, 0))
        value_label = self._keep_custom_bg(tk.Label(
            card, text=value, bg=COLORS["card2"], fg=fg or COLORS["text"],
            font=FONTS["section"], anchor="w"))
        value_label.pack(anchor="w", padx=9, pady=(2, 7))
        return value_label

    def _build_card_play(self, c):
        """低频导航与棋局编辑；高频前后移动已固定在棋盘下方。"""
        c.columnconfigure(0, weight=1)
        c.columnconfigure(1, weight=1)
        intro = tk.Label(
            c, text="常用前后移动已固定在棋盘下方；这里集中放置分支和棋局编辑。",
            bg=COLORS["card"], fg=COLORS["subtext"], font=FONTS["small"],
            justify=tk.LEFT, wraplength=RIGHT_PANEL_WIDTH - 55)
        intro.grid(row=0, column=0, columnspan=2, sticky="ew", padx=3, pady=(1, 9))
        self._make_button(c, "上一分支", self.do_prev_branch,
                          variant="default").grid(row=1, column=0, sticky="ew", padx=3, pady=3)
        self._make_button(c, "下一分支", self.do_next_branch,
                          variant="default").grid(row=1, column=1, sticky="ew", padx=3, pady=3)
        self.btn_tree = self._make_button(c, "棋局树", self.toggle_treeview, variant="default")
        self.btn_tree.grid(row=2, column=0, columnspan=2, sticky="ew", padx=3, pady=3)
        ttk.Separator(c, orient=tk.HORIZONTAL).grid(
            row=3, column=0, columnspan=2, sticky="ew", padx=3, pady=10)
        self._make_button(c, "停一手", self.do_pass,
                          variant="default").grid(row=4, column=0, sticky="ew", padx=3, pady=3)
        self._make_button(c, "回到起点", self.do_reset,
                          variant="default").grid(row=4, column=1, sticky="ew", padx=3, pady=3)
        tk.Label(
            c, text="快捷键：←/→ 前后移动 · Home/End 首尾 · Space 播放 · Ctrl+P 停一手 · F11 全屏",
            bg=COLORS["card"], fg=COLORS["subtext"], font=FONTS["small"],
            justify=tk.LEFT, wraplength=RIGHT_PANEL_WIDTH - 55).grid(
                row=5, column=0, columnspan=2, sticky="ew", padx=3, pady=(10, 0))

    def _build_card_analysis(self, c):
        """AI 分析控制 + 可配置的候选研究模块。"""
        c.columnconfigure(0, weight=1)
        c.columnconfigure(1, weight=1)
        c.columnconfigure(2, weight=1)
        self.btn_start = self._make_button(
            c, "启动 KataGo", self.toggle_katago, variant="default")
        self.btn_start.grid(row=0, column=0, sticky="ew", padx=4, pady=4)
        self._make_button(c, "分析整盘", self.analyze_mainline,
                          variant="accent").grid(row=0, column=1, sticky="ew", padx=4, pady=4)
        self._make_button(c, "快速预扫", self.quick_scan_mainline,
                          variant="default").grid(row=0, column=2, sticky="ew", padx=4, pady=4)
        self._make_button(c, "分析当前局面", self.force_analyze,
                          variant="default").grid(row=1, column=0, sticky="ew", padx=4, pady=4)
        self._make_button(c, "系统设置", self.open_settings,
                          variant="default").grid(row=1, column=1, sticky="ew", padx=4, pady=4)
        self.btn_heat = self._make_button(
            c, "热力图: %s" % HEAT_LABELS[self._heat_mode], self.cycle_heatmap, variant="default")
        self.btn_heat.grid(row=2, column=0, sticky="ew", padx=4, pady=4)
        self.btn_pv = self._make_button(
            c, "主变 %d 步" % self._pv_length, self.toggle_pv, variant="default")
        self.btn_pv.grid(row=2, column=1, sticky="ew", padx=4, pady=4)
        self.btn_auto_hint = self._make_button(
            c, "AI首选 ✓" if self._auto_hint else "AI首选 ✗",
            self.toggle_auto_hint, variant="default")
        self.btn_auto_hint.grid(row=3, column=0, sticky="ew", padx=4, pady=4)
        self._attach_tooltip(
            self.btn_auto_hint,
            "开启后分析回流即在棋盘自动标出 AI 首选下一手（训练用户回合默认不揭示）")
        self.btn_candidates = self._make_button(
            c, "候选点 ✓" if self._show_candidates else "候选点 ✗",
            self.toggle_candidates, variant="default")
        self.btn_candidates.grid(row=3, column=1, sticky="ew", padx=4, pady=4)
        self._attach_tooltip(
            self.btn_candidates,
            "在棋盘叠加显示 AI 推荐 A/B/C/D/E 候选点圆圈（默认关闭，点击开启）")
        # AI 推荐容器：CTkFrame 圆角（替代 ttk.LabelFrame 直角）
        if _HAS_CTK:
            recommend = ctk.CTkFrame(c, fg_color=COLORS["card"], corner_radius=10)
        else:
            recommend = ttk.LabelFrame(
                c, text=" AI 推荐 · 单击落子（主变模式下点选看变化） ",
                style="Section.TLabelframe")
        recommend.grid(row=4, column=0, columnspan=2, sticky="ew", padx=3, pady=(9, 0))
        try:
            recommend.columnconfigure(0, weight=1)
        except Exception:
            pass
        self._candidate_empty_label = tk.Label(
            recommend, text="启动 KataGo 后显示推荐点与变化图",
            bg=COLORS["card2"], fg=COLORS["subtext"], font=FONTS["ui"],
            justify=tk.LEFT, anchor="w", padx=10, pady=11,
            highlightthickness=1, highlightbackground=COLORS["muted"])
        self._keep_custom_bg(self._candidate_empty_label)
        self._candidate_empty_label.grid(row=0, column=0, sticky="ew")
        # 候选区 v2（用户需求）：最多 3 个大按钮——序号数字 + 坐标 + 胜率，
        # 单击直接落子；不再展示 徽章/推荐理由/PV 文字说明
        for idx in range(3):
            # 候选行容器：CTkFrame 圆角卡片（替代 tk.Frame 1px 直角边框）
            if _HAS_CTK:
                row = ctk.CTkFrame(recommend, fg_color=COLORS["card2"], corner_radius=8)
            else:
                row = self._keep_custom_bg(tk.Frame(
                    recommend, bg=COLORS["card2"], highlightthickness=1,
                    highlightbackground=COLORS["muted"], padx=8, pady=7))
            row.grid(row=idx + 1, column=0, sticky="ew", pady=4)
            try:
                row.columnconfigure(1, weight=1)
            except Exception:
                pass
            # 序号：与棋盘红色数字一致的第几选
            rank_label = self._keep_custom_bg(tk.Label(
                row, text=str(idx + 1), width=2, anchor="center",
                bg=COLORS["card"], fg=COLORS["red"], font=FONTS["title"],
                padx=5, pady=4, highlightthickness=1,
                highlightbackground=COLORS["muted"]))
            rank_label.grid(row=0, column=0, sticky="nsw", padx=(0, 8))
            # 大按钮：显示坐标，单击即落子（原双击行为升级为单击）
            btn = self._make_button(
                row, "%d  —" % (idx + 1), state=tk.DISABLED, big=True,
                command=lambda i=idx: self._select_candidate(i))
            btn.grid(row=0, column=1, sticky="ew")
            # 胜率（替代原先的徽章/理由/PV 文字）
            win_label = tk.Label(
                row, text="—", width=9, anchor="e",
                bg=COLORS["card2"], fg=COLORS["text"], font=FONTS["data_l"])
            self._keep_custom_bg(win_label)
            win_label.grid(row=0, column=2, sticky="e", padx=(8, 0))
            pv_label = self._keep_custom_bg(tk.Label(
                row, text="", anchor="w",
                bg=COLORS["card2"], fg=COLORS["subtext"], font=FONTS["small"]))
            # pv_label 常驻隐藏：保留引用兼容清空逻辑，不再参与布局
            for target in (row, rank_label, win_label):
                target.bind("<Button-1>", lambda event, i=idx: self._select_candidate(i))
            self._candidate_rows.append(row)
            self._candidate_buttons.append(btn)
            self._candidate_rank_labels.append(rank_label)
            self._candidate_win_labels.append(win_label)
            self._candidate_pv_labels.append(pv_label)
            row.grid_remove()

    def _build_card_score(self, c):
        """紧凑形势卡：当前手、双方胜率、目差与必要的点目信息。"""
        c.columnconfigure(0, weight=1)
        # hero 区：CTkFrame 圆角卡片（替代 tk.Frame 1px 直角边框）
        if _HAS_CTK:
            hero = ctk.CTkFrame(c, fg_color=COLORS["card2"], corner_radius=10)
        else:
            hero = self._keep_custom_bg(tk.Frame(
                c, bg=COLORS["card2"], highlightthickness=1,
                highlightbackground=COLORS["muted"], padx=10, pady=9))
        hero.grid(row=0, column=0, sticky="ew", padx=3)
        try:
            hero.columnconfigure(0, weight=1)
            hero.columnconfigure(1, weight=1)
        except Exception:
            pass
        self.lbl_info = tk.Label(
            hero, text="当前：黑方\n第 0 手", justify=tk.LEFT,
            bg=COLORS["card2"], fg=COLORS["text"], font=FONTS["data"])
        self._keep_custom_bg(self.lbl_info)
        self.lbl_info.grid(row=0, column=0, sticky="w")
        self.lbl_score = tk.Label(
            hero, text="等待分析", justify=tk.RIGHT,
            bg=COLORS["card2"], fg=COLORS["subtext"], font=FONTS["score"])
        self._keep_custom_bg(self.lbl_score)
        self.lbl_score.grid(row=0, column=1, sticky="e")
        # 指标卡容器：CTkFrame 透明底（让指标卡圆角浮出）
        if _HAS_CTK:
            metric_grid = ctk.CTkFrame(c, fg_color="transparent")
        else:
            metric_grid = tk.Frame(c, bg=COLORS["card"])
        metric_grid.grid(row=1, column=0, sticky="ew", padx=3, pady=(7, 2))
        self.lbl_metric_black = self._build_metric_card(
            metric_grid, 0, "黑胜率", "—", COLORS["text"])
        self.lbl_metric_white = self._build_metric_card(
            metric_grid, 1, "白胜率", "—", COLORS["purple"])
        self.lbl_metric_lead = self._build_metric_card(
            metric_grid, 2, "目差", "—", COLORS["accent"])
        self.lbl_wr = tk.Label(
            c, text="黑 —  ·  白 —  ｜ 目差 —",
            bg=COLORS["card"], fg=COLORS["subtext"], font=FONTS["data"])
        self.lbl_wr.grid(row=2, column=0, sticky="w", padx=4, pady=(6, 2))
        self.wr_canvas = tk.Canvas(
            c, width=410, height=18, bg=COLORS["card"],
            highlightthickness=0)
        self.wr_canvas.grid(row=3, column=0, sticky="ew", padx=3)
        self._wr_bar_img = None   # PIL 胜率条图片引用（防 GC）
        self.lbl_territory = tk.Label(
            c, text="", bg=COLORS["card"], fg=COLORS["subtext"],
            font=FONTS["small"])
        self.lbl_territory.grid(row=4, column=0, sticky="w", padx=4, pady=(3, 0))
        # 分析进度条（常驻形势卡底部，所有标签页可见——解决研究模式下看不到进度的问题）
        coverage_row_top = tk.Frame(c, bg=COLORS["card"])
        coverage_row_top.grid(row=5, column=0, sticky="ew", padx=4, pady=(6, 0))
        self.review_coverage_bar_top = ttk.Progressbar(
            coverage_row_top, orient=tk.HORIZONTAL, mode="determinate",
            maximum=100, style="Review.Horizontal.TProgressbar")
        self.review_coverage_bar_top.pack(side=tk.LEFT, fill="x", expand=True)
        self.lbl_review_coverage_top = tk.Label(
            coverage_row_top, text="尚未分析", width=13, anchor="e",
            bg=COLORS["card"], fg=COLORS["subtext"], font=FONTS["small"])
        self.lbl_review_coverage_top.pack(side=tk.RIGHT, padx=(8, 0))

        # 表现数据仍在主窗口维护，但不再常驻占用总览空间；详细内容进入复盘/个人画像。
        # _tv_rating 仅作数据容器（不 grid），避免与覆盖率行同格重叠；
        # 画像摘要 lbl_profile 由复盘页维护（_build_card_review）。
        self._tv_rating = ttk.Treeview(
            c, columns=("player", "level", "elo", "range", "loss", "sample", "confidence"),
            show="headings", height=2, selectmode="none")
        for col, title, width, anchor in [
                ("player", "棋手", 50, "w"), ("level", "棋力", 52, "center"),
                ("elo", "等价Elo", 64, "center"),
                ("range", "区间", 56, "center"), ("loss", "吻合", 42, "center"),
                ("sample", "有效", 42, "center"), ("confidence", "可信", 38, "center")]:
            self._tv_rating.heading(col, text=title)
            self._tv_rating.column(col, width=width, minwidth=width, stretch=False, anchor=anchor)
        # 点目面板（_scoring_inner 动态显示/隐藏；_scoring_frame 空时零高度）
        self._scoring_frame = tk.Frame(c, bg=COLORS["card"])
        self._scoring_frame.grid(row=7, column=0, sticky="ew", padx=3, pady=(4, 0))
        self._scoring_inner = tk.Frame(self._scoring_frame, bg=COLORS["card"])
        tk.Label(self._scoring_inner, text="终局点目", font=FONTS["ui"], bg=COLORS["card"],
                 fg=COLORS["accent"]).pack(anchor="w")
        row_sc = tk.Frame(self._scoring_inner, bg=COLORS["card"]); row_sc.pack(fill="x", pady=2)
        self._btn_sc_exit = self._make_button(row_sc, "退出点目", self.exit_scoring, variant="default")
        self._btn_sc_exit.pack(side=tk.LEFT, padx=4)
        self._btn_sc_suggest = self._make_button(row_sc, "AI 死子建议", self.apply_ai_dead_suggestion, variant="default")
        self._btn_sc_suggest.pack(side=tk.LEFT, padx=4)
        self._btn_sc_confirm = self._make_button(row_sc, "确认结果", self.confirm_score, variant="accent")
        self._btn_sc_confirm.pack(side=tk.LEFT, padx=4)
        self.lbl_scoring = tk.Label(self._scoring_inner, text="", justify=tk.LEFT, bg=COLORS["card"],
                                    fg=COLORS["text"], font=FONTS["data"], anchor="w")
        self.lbl_scoring.pack(anchor="w", pady=(2, 0))

    def _build_card_review(self, c):
        """复盘：工具常驻，内容按“总结 / 问题手”拆页避免纵向拥挤。"""
        c.columnconfigure(0, weight=1)
        c.rowconfigure(1, weight=1)

        tools = self._make_card_frame(c, "复盘工具")
        tools.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        try:
            tools.columnconfigure(0, weight=1)
            tools.columnconfigure(1, weight=1)
        except Exception:
            pass
        self._make_button(tools, "胜率曲线", self.toggle_graph,
                          variant="default").grid(row=0, column=0, sticky="ew", padx=4)
        self._make_button(tools, "导出报告", self.do_export_review_report,
                          variant="default").grid(row=0, column=1, sticky="ew", padx=4)
        self.btn_review_scope = self._make_button(
            tools, "复盘范围：双方", self.toggle_review_scope, variant="default")
        self.btn_review_scope.grid(row=1, column=0, sticky="ew", padx=2, pady=(4, 0))
        self.btn_complete_analysis = self._make_button(
            tools, "补全分析", self.analyze_mainline, variant="accent")
        self.btn_complete_analysis.grid(row=1, column=1, sticky="ew", padx=2, pady=(4, 0))
        self._make_button(tools, "个人画像", self.open_player_profile,
                          variant="default").grid(row=2, column=0, sticky="ew", padx=2, pady=(4, 0))
        self._make_button(tools, "棋风与成长", self.open_style_profile,
                          variant="default").grid(row=2, column=1, sticky="ew", padx=2, pady=(4, 0))
        self._make_button(tools, "棋力评估", self.toggle_strength_eval,
                          variant="default").grid(row=3, column=0, sticky="ew", padx=2, pady=(4, 0))
        self._make_button(tools, "问题手训练", self.open_problem_drill,
                          variant="accent").grid(row=3, column=1, sticky="ew", padx=2, pady=(4, 0))

        review_views = ttk.Notebook(c)
        review_views.grid(row=1, column=0, sticky="nsew")
        summary_page = tk.Frame(review_views, bg=COLORS["card"], padx=5, pady=6)
        problems_page = tk.Frame(review_views, bg=COLORS["card"], padx=5, pady=6)
        review_views.add(summary_page, text="总结")
        review_views.add(problems_page, text="问题手")
        self.review_views = review_views

        training = self._make_card_frame(summary_page, "阶段训练")
        training.pack(fill="x", pady=(0, 6))
        train_row = tk.Frame(training, bg=COLORS["card"])
        train_row.pack(fill="x")
        for i in range(4):
            train_row.columnconfigure(i, weight=1)
        self._make_button(train_row, "提示", self.show_hint,
                          variant="default").grid(row=0, column=0, sticky="ew", padx=4)
        tk.Label(train_row, text="结束 / 重来 / 原实战 已移至棋盘下方常驻栏",
                 bg=COLORS["card"], fg=COLORS["subtext"], font=FONTS["small"]).grid(
                     row=0, column=1, columnspan=3, sticky="w", padx=4)

        overview = self._make_card_frame(summary_page, "阶段概览")
        overview.pack(fill="x", pady=(0, 6))
        self.lbl_review_summary = tk.Label(
            overview, text="", font=FONTS["ui"], bg=COLORS["card"],
            fg=COLORS["subtext"], justify=tk.LEFT,
            wraplength=RIGHT_PANEL_WIDTH - 55)
        self.lbl_review_summary.pack(anchor="w", fill="x")
        self.lbl_profile = tk.Label(
            overview, text="", font=FONTS["small"], bg=COLORS["card"],
            fg=COLORS["subtext"], justify=tk.LEFT,
            wraplength=RIGHT_PANEL_WIDTH - 55)
        self.lbl_profile.pack(anchor="w", fill="x", pady=(4, 0))
        coverage_row = tk.Frame(overview, bg=COLORS["card"])
        coverage_row.pack(fill="x", pady=(5, 0))
        self.review_coverage_bar = ttk.Progressbar(
            coverage_row, orient=tk.HORIZONTAL, mode="determinate",
            maximum=100, style="Review.Horizontal.TProgressbar")
        self.review_coverage_bar.pack(side=tk.LEFT, fill="x", expand=True)
        self.lbl_review_coverage = tk.Label(
            coverage_row, text="尚未分析", width=13, anchor="e",
            bg=COLORS["card"], fg=COLORS["subtext"], font=FONTS["small"])
        self.lbl_review_coverage.pack(side=tk.RIGHT, padx=(8, 0))

        commentary = self._make_card_frame(summary_page, "对局分析")
        commentary.pack(fill="both", expand=True)
        commentary_frame = tk.Frame(commentary, bg=COLORS["card2"])
        commentary_frame.pack(fill="both", expand=True)
        self.txt_game_commentary = tk.Text(
            commentary_frame, height=5, wrap=tk.WORD, relief="flat", borderwidth=0,
            bg=COLORS["card2"], fg=COLORS["text"], font=FONTS["ui"],
            padx=7, pady=6, cursor="arrow")
        self.txt_game_commentary.pack(side=tk.LEFT, fill="both", expand=True)
        commentary_scroll = ttk.Scrollbar(
            commentary_frame, orient="vertical", command=self.txt_game_commentary.yview)
        commentary_scroll.pack(side=tk.RIGHT, fill="y")
        self.txt_game_commentary.configure(yscrollcommand=commentary_scroll.set)
        self.txt_game_commentary.configure(state=tk.DISABLED)
        problems = self._make_card_frame(problems_page, "问题棋分析")
        problems.pack(fill="both", expand=True)
        rvf = tk.Frame(problems, bg=COLORS["card"])
        rvf.pack(fill="both", expand=True)
        self._tv_review = ttk.Treeview(
            rvf, columns=("move", "side", "coord", "quality", "loss", "impact",
                          "best", "category", "tags"),
            show="headings", height=4)
        for col, txt, w, anch in [
                ("move", "手数", 38, "e"), ("side", "方", 26, "center"),
                ("coord", "实战", 42, "center"), ("quality", "AI参考评价", 66, "center"),
                ("loss", "目损", 40, "e"), ("impact", "胜率损失", 56, "e"),
                ("best", "AI建议", 48, "center"),
                ("category", "学习类别", 64, "center"), ("tags", "标签", 58, "w")]:
            self._tv_review.heading(col, text=txt)
            self._tv_review.column(col, width=w, anchor=anch)
        self._tv_review.pack(side=tk.LEFT, fill="both", expand=True)
        _rsb = ttk.Scrollbar(rvf, orient="vertical", command=self._tv_review.yview)
        self._tv_review.configure(yscrollcommand=_rsb.set)
        _rsb.pack(side=tk.RIGHT, fill="y")
        self._tv_review.bind("<Double-1>", self._on_review_double_click)
        self._tv_review.bind("<<TreeviewSelect>>", self._on_problem_select)
        self._tv_review.tag_configure(
            "bad", foreground=COLORS["red"], background=COLORS["red_s"])
        self._tv_review.tag_configure(
            "inaccuracy", foreground=COLORS["amber"], background=COLORS["amber_s"])
        self._tv_review.tag_configure("unknown", foreground=COLORS["subtext"])

        intent_frame = tk.Frame(problems, bg=COLORS["card2"])
        problem_nav = tk.Frame(problems, bg=COLORS["card"])
        problem_nav.pack(fill="x", pady=(6, 0))
        problem_nav.columnconfigure(0, weight=1)
        problem_nav.columnconfigure(1, weight=1)
        self.lbl_problem_position = tk.Label(
            problem_nav, text="暂无问题手", anchor="w",
            bg=COLORS["card"], fg=COLORS["subtext"], font=FONTS["small"])
        self.lbl_problem_position.grid(
            row=0, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        self._make_button(problem_nav, "← 上一问题",
                   lambda: self._navigate_problem(-1), variant="default").grid(
                       row=1, column=0, sticky="ew", padx=(0, 2))
        self._make_button(problem_nav, "下一问题 →",
                   lambda: self._navigate_problem(1), variant="default").grid(
                       row=1, column=1, sticky="ew", padx=(2, 0))
        compare_actions = tk.Frame(problems, bg=COLORS["card"])
        compare_actions.pack(fill="x", pady=(6, 0))
        for index in range(3):
            compare_actions.columnconfigure(index, weight=1)
        self.btn_compare_summary = self._make_button(
            compare_actions, "对比结论",
            lambda: self._set_problem_compare_mode("summary"), variant="default")
        self.btn_compare_summary.grid(row=0, column=0, sticky="ew", padx=(0, 2))
        self.btn_compare_actual = self._make_button(
            compare_actions, "实战变化",
            lambda: self._set_problem_compare_mode("actual"),
            variant="default", state=tk.DISABLED)
        self.btn_compare_actual.grid(row=0, column=1, sticky="ew", padx=4)
        self.btn_compare_ai = self._make_button(
            compare_actions, "AI 变化",
            lambda: self._set_problem_compare_mode("ai"),
            variant="default", state=tk.DISABLED)
        self.btn_compare_ai.grid(row=0, column=2, sticky="ew", padx=(2, 0))
        intent_frame.pack(fill="both", expand=True, pady=(6, 0))
        self.txt_problem_intent = tk.Text(
            intent_frame, height=5, wrap=tk.WORD, relief="flat", borderwidth=0,
            bg=COLORS["card2"], fg=COLORS["text"], font=FONTS["ui"],
            padx=7, pady=6, cursor="arrow")
        self.txt_problem_intent.pack(side=tk.LEFT, fill="both", expand=True)
        intent_scroll = ttk.Scrollbar(
            intent_frame, orient="vertical", command=self.txt_problem_intent.yview)
        intent_scroll.pack(side=tk.RIGHT, fill="y")
        self.txt_problem_intent.configure(yscrollcommand=intent_scroll.set, state=tk.DISABLED)

    def _build_card_sgf(self, c):
        """棋谱：导入 / 导出 / 点目（终局）。"""
        for i in range(3):
            c.columnconfigure(i, weight=1)
        self._make_button(c, "导入 SGF", self.do_import_sgf,
                          variant="default").grid(row=0, column=0, sticky="ew", padx=4, pady=4)
        self._make_button(c, "导出 SGF", self.do_export_sgf,
                          variant="default").grid(row=0, column=1, sticky="ew", padx=4, pady=4)
        self._make_button(c, "点目（终局）", self.toggle_scoring,
                          variant="default").grid(row=0, column=2, sticky="ew", padx=4, pady=4)
        self._make_button(c, "打开项目", self.do_open_project,
                          variant="default").grid(row=1, column=0, sticky="ew", padx=4, pady=4)
        self._make_button(c, "保存项目", self.do_save_project,
                          variant="default").grid(row=1, column=1, columnspan=2, sticky="ew", padx=4, pady=4)
        self._make_button(c, "棋谱库", self.open_game_library,
                          variant="default").grid(row=2, column=0, columnspan=3, sticky="ew", padx=4, pady=4)
        self._make_button(c, "批量导入", self.do_import_sgf_batch,
                          variant="default").grid(row=3, column=0, sticky="ew", padx=4, pady=4)
        self._make_button(c, "粘贴 SGF", self.open_paste_sgf,
                          variant="default").grid(row=3, column=1, sticky="ew", padx=4, pady=4)
        self._make_button(c, "分析队列", self.open_analysis_queue,
                          variant="default").grid(row=3, column=2, sticky="ew", padx=4, pady=4)

    # ===================== KataGo 生命周期 =====================
    def toggle_katago(self):
        if self.client and self.client.is_alive():
            self._stop_katago()
        else:
            self._start_katago()

    def _start_katago(self, quiet=False):
        # 启动前检查（引擎/模型/cfg 缺失阻断；DLL 缺失告警）
        pre = self.cfg.preflight()
        if not pre["ok"]:
            msg = "；".join(pre["errors"])
            if quiet:
                self._set_status("● 未配置", "red")
                self._set_msg("自动启动跳过：%s" % msg)
            else:
                messagebox.showerror("无法启动 KataGo", "\n".join(pre["errors"]))
            return
        if pre["warnings"]:
            self._set_msg("⚠️ " + "；".join(pre["warnings"]))
        cfg_path = self.cfg.cfg_abspath()
        human_model = self.cfg.get("human_model_path") or None
        self.client = KataGoAnalysisClient(
            self.katago_exe, cfg_path, self.model_file, cwd=HERE,
            human_model_path=human_model)
        try:
            self.client.start()
        except Exception as e:
            if quiet:
                self._set_status("● 启动失败", "red")
                self._set_msg("自动启动失败：%s" % e)
            else:
                messagebox.showerror("启动失败", str(e))
            self.client = None
            return
        self.guard.new_session()      # 新引擎会话：旧请求结果一律丢弃
        self._clear_training_prefetch()
        self.btn_start.configure(text="停止 KataGo")
        self._set_status("● 模型加载中", "amber")

    def _stop_katago(self):
        self._interrupt_analysis_queue()
        if self.client:
            self.client.stop()
            self.client = None
        self.btn_start.configure(text="启动 KataGo")
        self._set_status("● 未启动", "subtext")
        self.guard.clear()
        self._clear_training_prefetch()
        self._library_bg_pending = {}
        self._library_bg_current = None
        self._training_cache_bg_pending = {}
        self._training_cache_bg_current = None
        self._problem_compare_pending = {}
        self._reset_batch_state()      # 停引擎：中止批量计数，in-flight 请求不再回流

    def _maybe_autostart(self):
        """应用启动时自动拉起 KataGo（仅一次；失败安静提示，不弹 messagebox）。"""
        if self._auto_start_attempted:
            return
        self._auto_start_attempted = True
        self._start_katago(quiet=True)

    def force_analyze(self):
        """强制清掉缓存并重新分析当前局面。"""
        if not self._ensure_ready():
            return
        self._clear_hint()
        self.tree.current.analysis = None
        self._request_analysis(self.tree.current)

    def _ensure_ready(self):
        if not (self.client and self.client.is_alive()):
            self._set_msg("请先启动 KataGo")
            return False
        if not self.client.ready:
            self._set_msg("KataGo 仍在加载…")
            return False
        return True

    # ===================== 落子 / 导航 =====================
    def _on_click(self, event):
        xy = self._pixel_to_xy(event.x, event.y)
        if xy is None:
            return
        if self.scoring_mode:
            self._on_scoring_click(xy[0], xy[1])
        elif ((self._drill_active() or getattr(self, "_drill_overlay", None) is not None)
                and not self._drill_revealed):
            # 主动复盘（大纲 §24）：quiz 阶段允许在棋盘自由落子作答。
            # 按训练状态而非 overlay 判断——候选全 pass 时画不出字母也仍可作答。
            self._drill_free_answer(xy[0], xy[1])
        elif self._drill_active() or getattr(self, "_drill_overlay", None) is not None:
            # 揭示后棋盘锁定：变化图切换用训练窗口按钮，避免误改局面。
            self._set_msg("问题手训练已揭示，棋盘锁定；下一题继续作答")
        else:
            self.play(xy[0], xy[1])

    def _on_board_motion(self, event):
        """鼠标靠近合法空交叉点时，显示当前行棋方的虚化棋子。"""
        xy = self._pixel_to_xy(event.x, event.y)
        hover = None
        can_preview = not self.scoring_mode and not self._show_pv
        tr = self._training
        if tr and tr.get("active"):
            user_color = normalize_player_color(tr.get("user_color"))
            to_move = color_letter(self.tree.current.board.to_move)
            if tr.get("ai_playing") or (user_color in ("B", "W") and to_move != user_color):
                can_preview = False
        if xy is not None and can_preview:
            board = self.tree.current.board
            if board.stone_at(xy[0], xy[1]) == EMPTY:
                hover = xy   # 只判空点；自杀/打劫由点击 play() 时 tree.play 校验
        self._set_hover_point(hover)

    def _on_board_leave(self, _event=None):
        self._set_hover_point(None)

    def _set_hover_point(self, point):
        if point == self._hover_point:
            return
        self._hover_point = point
        # 增量更新 hover 预览（不整盘 redraw）：默认开形式判断 + 地盘热力图时，
        # 整盘 redraw 会重画 ~361 椭圆 + ownership_territory_split 全盘扫描，鼠标移动即卡。
        c = getattr(self, "canvas", None)
        if c is None:
            return
        c.delete("hover-stone")
        c.delete("coord-hint")
        if point is not None and not self.scoring_mode and not self._show_pv:
            hx, hy = point
            M, D = self.MARGIN, self.CELL
            if self.tree.current.board.stone_at(hx, hy) == EMPTY:
                self._draw_hover_stone(c, M + hx * D, M + hy * D, D * 0.46,
                                       self.tree.current.board.to_move)
                # 悬停坐标提示（棋盘左下角）
                c.create_text(M, self.BOARD_PIX - 4,
                              text="%s%d" % (COLS[hx], self.size - hy),
                              anchor="n", fill=COLORS["subtext"],
                              font=FONTS["small"], tags=("coord-hint",))

    def play(self, x, y):
        if self._block_in_scoring("落子"):
            return
        if self._block_training_turn():
            return
        self._stop_auto_play()
        self._hover_point = None
        ok, reason = self.tree.play(x, y)
        if not ok:
            self._set_msg("非法落子：%s" % reason)
            self.redraw()
            return
        self._after_navigate()
        self._ripple_last_move()
        if self._mistake_review_after_user_move(self.tree.current):
            return
        self._training_after_user_move(self.tree.current)

    def _ripple_last_move(self):
        """落子波纹：最后一手画一圈扩散环，350ms 后清除（落子触感反馈）。"""
        c = getattr(self, "canvas", None)
        if c is None:
            return
        lm = self.tree.current.board.last_move
        if not lm:
            return
        M, D = self.MARGIN, self.CELL
        cx, cy = M + lm[0] * D, M + lm[1] * D
        R = D * 0.46
        c.create_oval(cx - R * 1.4, cy - R * 1.4, cx + R * 1.4, cy + R * 1.4,
                      outline=COLORS["accent"], width=2, stipple="gray50",
                      tags=("ripple",))
        self.after(350, lambda: self.canvas.delete("ripple"))

    def do_undo(self):
        """导航页“上一步”在训练中自动采用完整回合悔棋语义。"""
        return self.do_takeback()

    def do_takeback(self):
        """全模式悔棋：普通模式退一手，训练模式退回用户上一手之前。"""
        if self._drill_active():
            self._set_msg("问题手训练中不能悔棋，请用窗口的「上一题 / 下一题」")
            return
        self._stop_auto_play()
        self._clear_hint()
        was_scoring = self.scoring_mode
        if was_scoring:
            self.exit_scoring()
        tr = self._training
        if tr and tr.get("active") and not tr.get("finished"):
            return self._training_takeback()
        if self.tree.undo():
            self._after_navigate()
            if was_scoring:
                self._set_msg("已退出点目模式并悔棋，回到第 %d 手" % self.tree.current.depth)
            else:
                self._set_msg("已悔棋，回到第 %d 手" % self.tree.current.depth)
            return True
        if was_scoring:
            self._set_msg("已退出点目模式（已在起点，无法悔棋）")
            return False
        self._set_msg("已经在棋局起点，无法悔棋")
        return False

    def _training_takeback(self):
        """撤销最近一手用户棋及其后的 AI 应手，保持训练回合状态一致。"""
        tr = self._training
        if not tr or not tr.get("active") or tr.get("finished"):
            return False
        user_color = normalize_player_color(tr.get("user_color"))
        nodes = list(tr.get("nodes") or [])
        user_index = None
        for index in range(len(nodes) - 1, -1, -1):
            node = nodes[index]
            if node.move and node.move[0] == user_color:
                user_index = index
                break
        if user_index is None:
            self._set_msg("训练刚开始，还没有你的落子可以撤回")
            return False
        user_node = nodes[user_index]
        target = user_node.parent
        if target is None:
            return False
        removed = nodes[user_index:]
        for node in removed:
            self.guard.invalidate_node(node)
            self._training_deferred_nodes.pop(node.nid, None)
        self._clear_training_prefetch()
        tr["nodes"] = nodes[:user_index]
        if tr.get("attempt_steps"):
            tr["attempt_steps"] = list(tr["attempt_steps"][:-1])
        tr["awaiting"] = "user"
        tr["ai_playing"] = False
        tr["finishing"] = False
        tr["current_turn_hint_used"] = False
        self.tree.current = target
        self._apply_cached_training_analysis(target)
        self._after_navigate()
        self._set_msg("训练悔棋：已撤回你上一手及 AI 应手，请重新选择")
        self._schedule_training_prefetch()
        return True

    def do_redo(self):
        if self._block_in_scoring("快进"):
            return
        if self._drill_active():
            self._set_msg("问题手训练中不能快进，请用窗口的「下一题」")
            return
        if self._mistake_review and self._mistake_review.get("active"):
            self._set_msg("错题复习中不能快进，请先完成或关闭当前题面")
            return
        tr = self._training
        if tr and tr.get("active") and not tr.get("finished"):
            self._set_msg("阶段训练中不能快进，请先结束训练")
            return
        self._stop_auto_play()
        if self.tree.redo():
            self._after_navigate()

    def do_reset(self):
        """清空回根：统一走 _reset_for_new_game 清所有临时状态（含点目自动退出）。

        不再用 _block_in_scoring 拦截——清空本身就是"放弃当前局面"，点目应随之退出。
        与 do_import_sgf/_load_project_from_path 共用同一份清理清单。
        """
        if self._drill_active():
            self._close_problem_drill()
        self._reset_for_new_game()   # 统一清理：停auto_play/退点目/清训练/drill/复习/缓存
        self.tree.reset()
        self._after_navigate()

    def do_pass(self):
        """当前方虚着（pass）：创建真实节点并触发 KataGo 分析（moves 带 ["X","pass"]）。"""
        if self._block_in_scoring("Pass"):
            return
        if self._drill_active() or getattr(self, "_drill_overlay", None) is not None:
            # 问题手训练期间棋盘锁定（作答 + 揭示后），停一手同样拦截避免破坏题面
            self._set_msg("问题手训练中，不能停一手")
            return
        if self._block_training_turn():
            return
        self._stop_auto_play()
        ok, reason = self.tree.play_pass()
        if not ok:
            self._set_msg("Pass 失败：%s" % reason)
            return
        self._after_navigate()
        if self._mistake_review_after_user_move(self.tree.current):
            return
        self._training_after_user_move(self.tree.current)

    # ===================== 模式管理器（守卫收口）=====================
    def active_modes(self):
        """返回当前激活的独占模式集合（新增功能只需在此注册，守卫自动生效）。

        返回 set，元素 ∈ {"scoring", "training", "drill", "mistake_review"}。
        """
        modes = set()
        if self.scoring_mode:
            modes.add("scoring")
        tr = self._training
        if tr and tr.get("active") and not tr.get("finished"):
            modes.add("training")
        if self._drill_active() or getattr(self, "_drill_overlay", None) is not None:
            modes.add("drill")
        if self._mistake_review and self._mistake_review.get("active"):
            modes.add("mistake_review")
        return modes

    def can(self, action):
        """查询某动作在当前模式下是否被允许（统一守卫入口）。

        action ∈ {"play", "navigate", "jump", "pass", "show_ai"}。
        新增模式或动作只需在此扩展，所有守卫函数自动一致。
        """
        modes = self.active_modes()
        if action in ("play", "pass"):
            # 落子/Pass：任一独占模式都拦（训练另有回合检查）
            if modes & {"scoring", "drill", "mistake_review"}:
                return False
            if "training" in modes and self._block_training_turn_silent():
                return False
            return True
        if action in ("navigate", "jump"):
            # 导航/跳转：任一独占模式都拦
            return len(modes) == 0
        if action == "show_ai":
            # AI 候选显示：训练/drill/复习时隐藏（防泄露答案）
            return not (modes & {"training", "drill", "mistake_review"})
        return True

    def _block_training_turn_silent(self):
        """静默检查训练回合（can() 内部用，不设消息）。"""
        tr = self._training
        if not tr or not tr.get("active") or tr.get("ai_playing"):
            return False
        user_color = normalize_player_color(tr.get("user_color"))
        if user_color not in ("B", "W"):
            return False
        to_move = color_letter(self.tree.current.board.to_move)
        return to_move != user_color

    def _block_in_scoring(self, action: str) -> bool:
        """点目模式下禁止任何修改 MoveTree 的操作（落子/导航/Pass/导入）。"""
        if self.scoring_mode:
            self._set_msg("点目模式下不能%s，按【点目】或 Esc 退出后可继续操作" % action)
            return True
        return False

    def _block_training_turn(self) -> bool:
        """训练模式下只允许用户在自己的回合落子。"""
        if self._block_training_turn_silent():
            self._set_msg("训练中：还没轮到你，等待 AI 应手。")
            return True
        return False

    def _block_jump(self, action="跳转"):
        """子窗口点击跳转的统一守卫：点目 / 问题手训练 / 阶段训练 / 错题复习 激活时禁止跳棋盘。"""
        if self.scoring_mode:
            self._set_msg("点目模式下不能%s，按【点目】或 Esc 退出后可继续操作" % action)
            return True
        if self._drill_active() or getattr(self, "_drill_overlay", None) is not None:
            self._set_msg("问题手训练中不能%s，请先关闭训练窗口" % action)
            return True
        tr = self._training
        if tr and tr.get("active") and not tr.get("finished"):
            self._set_msg("阶段训练中不能%s，请先结束训练" % action)
            return True
        if self._mistake_review and self._mistake_review.get("active"):
            self._set_msg("错题复习中不能%s，请先完成或关闭当前题面" % action)
            return True
        return False

    # ===================== 跳转 / 自动播放（易用性）=====================
    def do_goto_root(self):
        """Home：跳到根节点。"""
        if self._block_in_scoring("跳转"):
            return
        if self._drill_active():
            self._set_msg("问题手训练中不能跳转，请先关闭训练窗口")
            return
        if self._mistake_review and self._mistake_review.get("active"):
            self._set_msg("错题复习中不能跳转，请先完成或关闭当前题面")
            return
        tr = self._training
        if tr and tr.get("active") and not tr.get("finished"):
            self._set_msg("阶段训练中不能跳转，请先结束训练")
            return
        self._stop_auto_play()
        self.tree.reset()
        self._after_navigate()

    def do_goto_mainline_end(self):
        """End：跳到主线末尾。"""
        if self._block_in_scoring("跳转"):
            return
        if self._drill_active():
            self._set_msg("问题手训练中不能跳转，请先关闭训练窗口")
            return
        if self._mistake_review and self._mistake_review.get("active"):
            self._set_msg("错题复习中不能跳转，请先完成或关闭当前题面")
            return
        tr = self._training
        if tr and tr.get("active") and not tr.get("finished"):
            self._set_msg("阶段训练中不能跳转，请先结束训练")
            return
        self._stop_auto_play()
        while self.tree.redo():
            pass
        self._after_navigate()

    def do_step(self, delta: int):
        """PageUp/Down：delta>0 快进、delta<0 撤回，至多 |delta| 步（到根/末尾为止）。"""
        if self._block_in_scoring("翻页"):
            return
        if self._drill_active():
            self._set_msg("问题手训练中不能翻页，请先关闭训练窗口")
            return
        if self._mistake_review and self._mistake_review.get("active"):
            self._set_msg("错题复习中不能翻页，请先完成或关闭当前题面")
            return
        tr = self._training
        if tr and tr.get("active") and not tr.get("finished"):
            self._set_msg("阶段训练中不能翻页，请先结束训练")
            return
        self._stop_auto_play()
        if delta < 0:
            for _ in range(-delta):
                if not self.tree.undo():
                    break
        else:
            for _ in range(delta):
                if not self.tree.redo():
                    break
        self._after_navigate()

    def toggle_auto_play(self):
        """Space /【▶ 播放】按钮：自动播放 ↔ 暂停。"""
        if self._auto_play:
            self._stop_auto_play()
            self._set_msg("已暂停自动播放")
        else:
            self._start_auto_play()

    def _start_auto_play(self):
        if self._block_in_scoring("自动播放"):
            return
        if self._training and self._training.get("active") and not self._training.get("finished"):
            self._set_msg("训练中不能自动播放主线，请先结束训练")
            return
        if self._drill_active():
            self._set_msg("问题手训练中不能自动播放，请先关闭训练窗口")
            return
        if not self.tree.can_redo():
            self._set_msg("已在主线末尾，无下一手可播放")
            return
        self._auto_play = True
        self.btn_play.configure(text="⏸ 暂停")
        self._auto_play_step()

    def _auto_play_step(self):
        if not self._auto_play:
            return
        if not self.tree.redo():
            self._stop_auto_play()
            self._set_msg("自动播放结束（主线末尾）")
            return
        self._after_navigate()
        self._set_msg("自动播放中…（Space 暂停）")
        if self._auto_play_job is not None:       # 防 after id 泄漏（手动连续触发时）
            try:
                self.after_cancel(self._auto_play_job)
            except Exception:
                pass
        self._auto_play_job = self.after(PLAY_MS, self._auto_play_step)

    def _stop_auto_play(self):
        if not self._auto_play and self._auto_play_job is None:
            return
        self._auto_play = False
        if self._auto_play_job is not None:
            try:
                self.after_cancel(self._auto_play_job)
            except Exception:
                pass
            self._auto_play_job = None
        self.btn_play.configure(text="▶ 播放")

    # ---- 棋局进度条（可拖动滑块，跳转主线第 N 手）----
    def _scrubber_change(self, n):
        """拖动中（实时）→ 轻量跳转：只刷新棋盘 + 缓存分析 + 进度条。

        跳过 _after_navigate 里的重活（_update_review_state 会重算整盘 move_quality），
        否则每个 motion 都跑一遍整盘统计，拖动会卡死/无响应。重活留到松手(_commit)。
        """
        if self.scoring_mode:
            return
        # 作答期间锁定导航（与点目模式同待遇）：训练 quiz / 错题复习测验
        # 进行中拖时间轴会把题目局面换掉，字母/作答状态浮在错误局面上
        if (getattr(self, "_drill_overlay", None) is not None
                and not self._drill_revealed):
            self._set_msg("问题手作答中：请先在棋盘落子作答，或揭示答案后再导航")
            return
        if self._mistake_review and self._mistake_review.get("active"):
            self._set_msg("复习测验中：请先作答本题")
            return
        # 防御：首次交互即校准范围，修复某些加载路径后 scrubber 范围未更新、
        # 导致初始 _max=1 拖动中间被吞掉的"拖不动、要点末尾才生效"问题。
        self._update_scale()
        node = ReviewReport(self.tree).node_at_move(int(n))
        if node is None or node is self.tree.current:
            return
        self.tree.current = node
        self._hover_point = None
        self._hint_point = None
        self._hint_auto = False
        self._problem_branch_overlay = None
        self.redraw()
        cur = self.tree.current
        if cur.analysis:
            self._render_analysis(cur.analysis)
        else:
            self._clear_analysis()
        self._update_scale()

    def _scrubber_commit(self, n):
        """松手 → 跳到最终手并做完整复盘刷新（含整盘统计、树视图等重活）。"""
        if self.scoring_mode:
            return
        node = ReviewReport(self.tree).node_at_move(int(n))
        if node is not None and node is not self.tree.current:
            self.tree.current = node
        self._after_navigate()

    def _update_scale(self):
        """导航/落子/播放后同步进度条范围与位置（用户正拖动时不动它）。"""
        line = ReviewReport(self.tree).mainline_nodes()
        max_n = max(1, len(line) - 1)
        # current 在主线则用其主线 index；分支节点（不在主线）回退到 depth 近似（进度条只跟主线）
        cur = next((i for i, nd in enumerate(line) if nd is self.tree.current), None)
        if cur is None:
            cur = min(self.tree.current.depth, max_n)
        self.scale.set_range(max_n)
        # 用户正在拖动时由拖动控制位置，不抢回
        if not getattr(self.scale, "is_dragging", False):
            self.scale.set_position(cur)
        self.lbl_scale.config(text="%d/%d" % (cur, max_n))

    def do_export_sgf(self):
        """导出当前主线（根→当前节点）为 SGF 文件。

        若已确认点目结果（tree.score_result），写入 RE[] 与点目摘要 C[]。
        """
        try:
            text = export_sgf(
                self.tree, black_name=getattr(self.tree, "_sgf_pb", "黑方"),
                white_name=getattr(self.tree, "_sgf_pw", "白方"),
                komi=self.komi, rule=self.rules,
                score_result=getattr(self.tree, "score_result", None),
                focus_color=self._review_focus_color())
        except Exception as e:
            self._set_msg("导出失败：%s" % e)
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".sgf",
            filetypes=[("SGF 棋谱", "*.sgf"), ("所有文件", "*.*")],
            initialfile="game.sgf", parent=self)
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception as e:
            messagebox.showerror("保存失败", str(e))
            return
        self._set_msg("已导出：%s（主线 %d 手）" % (os.path.basename(path), self.tree.current.depth))

    def do_export_review_report(self):
        """导出 Markdown 复盘报告（表现估计、文字分析、阶段与问题棋）。"""
        try:
            text = generate_markdown_report(
                self.tree, black_name=getattr(self.tree, "_sgf_pb", "黑方"),
                white_name=getattr(self.tree, "_sgf_pw", "白方"),
                komi=self.komi, rule=self.rules,
                score_result=getattr(self.tree, "score_result", None))
        except Exception as e:
            self._set_msg("报告生成失败：%s" % e)
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".md",
            filetypes=[("Markdown 报告", "*.md"), ("所有文件", "*.*")],
            initialfile="review-report.md", parent=self)
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception as e:
            messagebox.showerror("保存失败", str(e))
            return
        self._set_msg("已导出复盘报告：%s" % os.path.basename(path))

    def do_save_project(self):
        """保存带 analysis 缓存的复盘项目文件。"""
        path = filedialog.asksaveasfilename(
            defaultextension=".kga.json",
            filetypes=[("KataGo 分析器项目", "*.kga.json"), ("JSON 文件", "*.json"), ("所有文件", "*.*")],
            initialfile="game.kga.json", parent=self)
        if not path:
            return
        try:
            self._refresh_review_summary_artifact()
            save_project(path, self.tree, rules=self.rules, komi=self.komi,
                         meta={"engine": self.katago_exe, "model": self.model_file})
            if self._library_record_id:
                update_project_snapshot(self._library_record_id, self.tree, rules=self.rules, komi=self.komi)
        except Exception as e:
            messagebox.showerror("保存项目失败", str(e))
            return
        self._set_msg("已保存项目：%s（含分析缓存）" % os.path.basename(path))

    def _analysis_signature(self, visits=None):
        return {
            "model": os.path.basename(self.model_file or ""),
            "rules": self.rules,
            "komi": self.komi,
            "visits": (
                int(visits) if visits is not None
                else int(self.cfg.get("max_visits", 200))),
            "quality_version": QUALITY_VERSION,
        }

    def _ensure_profile_identity(self):
        """按手工设置→棋手名→默认设置解析画像执棋方。"""
        if not self._library_record_id:
            return getattr(self.tree, "_profile_side", "unknown")
        rec = get_record(self._library_record_id) or {}
        side = rec.get("profileSide")
        if side in ("B", "W", "both"):
            self.tree._profile_side = side
            return side
        profile_cfg = self.cfg.get("profile", {}) or {}
        names = {
            str(name).strip().casefold()
            for name in (profile_cfg.get("my_player_names") or [])
            if str(name).strip()
        }
        black_name = str(getattr(self.tree, "_sgf_pb", "") or "").strip().casefold()
        white_name = str(getattr(self.tree, "_sgf_pw", "") or "").strip().casefold()
        black_match = bool(black_name and black_name in names)
        white_match = bool(white_name and white_name in names)
        if black_match and white_match:
            side = "both"
        elif black_match:
            side = "B"
        elif white_match:
            side = "W"
        else:
            side = profile_cfg.get("default_profile_side", "unknown")
        if side not in ("B", "W", "both"):
            side = "unknown"
        self.tree._profile_side = side
        if side != "unknown":
            update_profile_side(self._library_record_id, side)
        return side

    def _refresh_review_summary_artifact(self):
        """从现有 analysis 本地重建轻量摘要，不触发 KataGo。"""
        rr = ReviewReport(self.tree)
        visits = int(self.cfg.get("max_visits", 200))
        self.tree._analysis_model = os.path.basename(self.model_file or "")
        self._ensure_profile_identity()
        self.tree._analysis_signature = self._analysis_signature(visits)
        self.tree._review_summary_v2 = rr.review_summary_v2(
            visits=visits,
            analysis_signature=self.tree._analysis_signature)
        return self.tree._review_summary_v2

    def do_open_project(self):
        """打开复盘项目文件，恢复棋局树、analysis 缓存、点目结果与当前节点。"""
        # 不用 _block_in_scoring 拦截：_load_project_from_path 内部会自动 exit_scoring，
        # 用户意图是换棋谱，应允许选文件，打开时自动退出点目（与棋谱库双击、导入 SGF 行为一致）。
        path = filedialog.askopenfilename(
            filetypes=[("KataGo 分析器项目", "*.kga.json"), ("JSON 文件", "*.json"), ("所有文件", "*.*")],
            parent=self)
        if not path:
            return
        self._load_project_from_path(path, os.path.basename(path), library_record_id=None)

    def _load_project_from_path(self, path, label=None, library_record_id=None):
        """从项目文件替换当前棋局。"""
        try:
            new_tree, data = load_project(path)
        except Exception as e:
            messagebox.showerror("打开项目失败", str(e))
            return
        self._stop_auto_play()
        # 切换棋局必须先退出点目：score_estimator 建在旧 board 上，_draw_scoring_overlay
        # 会把旧棋谱地盘图叠到新棋盘，且 _block_in_scoring 会锁住新棋盘所有操作。
        # exit_scoring 对非点目态是 no-op，安全调用。
        if self.scoring_mode:
            self.exit_scoring()
        self._reset_problem_comparison_state()
        self.tree = new_tree
        if self._drill_win is not None:
            self._close_problem_drill()   # 切换项目：关闭问题手训练，避免旧 quiz 残留到新棋盘
        # v1 项目尚未保存 PB/PW；从棋谱库原始 SGF 补回，兼容已经入库的旧棋谱。
        if not data.get("blackName") and (library_record_id or (data.get("meta") or {}).get("libraryId")):
            rec = get_record(library_record_id or (data.get("meta") or {}).get("libraryId"))
            sgf_path = (rec or {}).get("sgfPath")
            try:
                with open(sgf_path, "r", encoding="utf-8", errors="replace") as f:
                    named_tree = import_sgf(f.read(), size=new_tree.size)
                self.tree._sgf_pb = getattr(named_tree, "_sgf_pb", "黑方")
                self.tree._sgf_pw = getattr(named_tree, "_sgf_pw", "白方")
            except (OSError, TypeError, ValueError):
                pass
        self.rules = data.get("rules", self.rules)
        self.komi = data.get("komi", self.komi)
        self._library_record_id = library_record_id or (data.get("meta") or {}).get("libraryId")
        self._current_game_label = label or os.path.basename(path)
        # 统一换棋谱清理（替代散落的手写清单，与 do_import_sgf/do_reset 共用）
        self._reset_for_new_game()
        self._after_navigate()
        self._refresh_treeview()
        self._set_msg("已打开项目：%s（分析缓存已恢复）" % (label or os.path.basename(path)))
        if self._library_record_id:
            touch_record(self._library_record_id)
            self._ensure_training_task()
            self._refresh_library_window()
        # 打开棋谱后自动关闭棋谱库窗口：用户意图是"选一盘棋来复盘"，库窗口不应继续挡在前面。
        self._close_library_window()

    def do_import_sgf(self):
        """从 SGF 文件回放主线，替换当前棋局。"""
        path = filedialog.askopenfilename(
            filetypes=[("SGF 棋谱", "*.sgf"), ("所有文件", "*.*")], parent=self)
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            new_tree = import_sgf(text, size=self.size)
        except Exception as e:
            messagebox.showerror("导入失败", str(e))
            return
        # 换棋谱统一范式：调 _reset_for_new_game 一次性清所有临时状态（与 _load_project_from_path 对齐）
        self.tree = new_tree
        self._current_game_label = os.path.basename(path)
        self._library_record_id = None
        self._reset_for_new_game()
        self._update_review_state()   # 基于新树重算 loss / 清失误榜 / 重绘曲线
        self._update_scale()          # 进度条范围同步到新主线长度
        self.redraw()
        self._clear_analysis()
        self._refresh_treeview()      # 若树视图窗口开着，刷新到新树
        skipped = getattr(new_tree, "_sgf_skipped", 0)
        lib_note = ""
        try:
            rec = add_sgf_to_library(path, text, self.tree, rules=self.rules, komi=self.komi)
            self._library_record_id = rec.get("id")
            self._refresh_library_window()
            lib_note = "；已加入棋谱库"
        except Exception as e:
            lib_note = "；入库失败：%s" % e
        self._refresh_game_context()
        self._set_msg("已导入：%s（%d 手，跳过 %d）%s" % (
            os.path.basename(path), new_tree.current.depth, skipped, lib_note))
        # 导入后自动关闭棋谱库窗口（如果开着）：与双击打开棋谱行为一致。
        self._close_library_window()
        if self._library_record_id:
            self.after(300, self._maybe_auto_analyze_library_game)

    def do_import_sgf_batch(self):
        """多选 SGF 入库并进入持久化整盘分析队列，不切走当前棋局。"""
        if self._block_in_scoring("批量导入"):
            return
        paths = filedialog.askopenfilenames(
            title="批量导入 SGF",
            filetypes=[("SGF 棋谱", "*.sgf"), ("所有文件", "*.*")], parent=self)
        if not paths:
            return
        result = scan_paths(
            list(paths), rules=self.rules, komi=self.komi, source_kind="batch")
        records = list(result.get("imported") or []) + list(result.get("duplicates") or [])
        queued = self._enqueue_records_for_analysis(records)
        self._refresh_library_window()
        failed = result.get("failed") or []
        self._set_msg("批量导入：新增 %d，重复 %d，进入队列 %d，失败 %d" % (
            len(result.get("imported") or []), len(result.get("duplicates") or []),
            queued, len(failed)))
        if failed:
            detail = "\n".join("%s：%s" % (
                os.path.basename(item.get("path", "")), item.get("error", ""))
                for item in failed[:10])
            messagebox.showwarning("部分棋谱导入失败", detail, parent=self)
        self.after(20, self._kick_analysis_queue)

    def open_paste_sgf(self):
        """粘贴 SGF 文本入库；解析成功后自动排队分析。"""
        win = tk.Toplevel(self)
        self._prepare_child_window(win, "粘贴 SGF", 680, 460, minsize=(560, 360))
        tk.Label(
            win, text="粘贴完整 SGF 文本（仅本地解析，不访问网络）",
            bg=COLORS["bg"], fg=COLORS["subtext"], font=FONTS["ui"]).pack(
                anchor="w", padx=12, pady=(12, 6))
        frame = tk.Frame(win, bg=COLORS["card"])
        frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        text = tk.Text(
            frame, wrap=tk.NONE, bg=COLORS["card2"], fg=COLORS["text"],
            insertbackground=COLORS["text"], font=FONTS["data"], padx=8, pady=8)
        text.pack(side=tk.LEFT, fill="both", expand=True)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        scroll.pack(side=tk.RIGHT, fill="y")
        text.configure(yscrollcommand=scroll.set)

        def submit():
            raw = text.get("1.0", tk.END).strip()
            if not raw:
                self._set_msg("请先粘贴 SGF 文本")
                return
            try:
                name = "粘贴棋谱-%s.sgf" % datetime.now().strftime("%Y%m%d-%H%M%S")
                rec, created = import_sgf_text(
                    raw, rules=self.rules, komi=self.komi, name=name)
                queued = self._enqueue_records_for_analysis([rec])
            except Exception as exc:
                messagebox.showerror("粘贴导入失败", str(exc), parent=win)
                return
            win.destroy()
            self._refresh_library_window()
            self._set_msg("%s；%s" % (
                "已导入粘贴棋谱" if created else "该棋谱已存在",
                "已进入分析队列" if queued else "队列中已有该棋谱"))
            self.after(20, self._kick_analysis_queue)

        bar = self._dialog_button_bar(win)
        self._make_button(bar, "取消", win.destroy, variant="default").pack(side=tk.RIGHT)
        self._make_button(bar, "导入并分析", submit, variant="accent").pack(
            side=tk.RIGHT, padx=(0, 6))
        text.focus_set()

    def _after_navigate(self):
        """每次落子/导航后统一刷新。

        架构（事件总线）：先做临时态清理 + redraw（同步，必须在 listener 之前），
        再 _emit("navigated") 让各域 listener 响应。26 个调用点不变，内部改用事件总线。
        """
        self._refresh_review_scope_button()
        # 临时态清理（必须在 redraw 之前）
        self._hover_point = None
        self._hint_point = None
        self._hint_pending_nid = None
        self._hint_auto = False
        self._problem_branch_overlay = None
        self.redraw()
        # 事件总线扇出：各域订阅 navigated，按注册顺序响应（解耦）
        self._emit("navigated")

    def _check_double_pass(self):
        """双方连续 Pass 时弹窗询问是否进入点目（不强制）。同一节点只提示一次。"""
        if self.scoring_mode or not self.tree.double_pass:
            return
        nid = self.tree.current.nid
        if self._double_pass_prompted == nid:
            return
        self._double_pass_prompted = nid
        if messagebox.askyesno("双方连续 Pass", "双方连续 Pass，是否进入点目？"):
            self.enter_scoring()

    # ===================== 分析请求 / 结果 =====================
    def _training_visits(self):
        mode = self.cfg.get("training_speed_mode", "fast")
        return TRAINING_SPEED_MODES.get(mode, TRAINING_SPEED_MODES["fast"])[1]

    def _analysis_query_for(self, tree, node, rules, komi, visits):
        return self._analysis_query_from_moves(tree, node.moves_list(), rules, komi, visits)

    def _analysis_query_from_moves(self, tree, moves, rules, komi, visits):
        return {
            "moves": moves,
            "initialStones": tree.initial_stones_list(),    # 让子 setup（HA/AB/AW）
            "rules": rules,
            "komi": komi,
            "boardXSize": self.size,
            "boardYSize": self.size,
            "maxVisits": int(visits),
            "includeOwnership": True,   # 地盘热力图（+1 黑属地 / -1 白属地）
            "includePolicy": True,      # 策略热力图（NN 原始策略，末位=pass）
        }

    def _request_analysis(self, node, training=False, visits=None):
        if not (self.client and self.client.is_alive() and self.client.ready):
            if training and node is not None:
                self._training_deferred_nodes[node.nid] = node
                if not (self.client and self.client.is_alive()):
                    self._start_katago(quiet=True)
                self._set_msg("训练变化未预生成，正在自动启动 KataGo 补算…")
            return False
        if visits is None:
            visits = self._training_visits() if training else int(self.cfg.get("max_visits", 200))
        q = self._analysis_query_for(self.tree, node, self.rules, self.komi, visits)
        self.guard.invalidate_node(node)   # 清掉该节点旧挂账，避免延迟响应覆盖新结果
        qid = self.client.analyze(q)
        self.guard.register(qid, node)
        suffix = "训练快速" if training else "分析"
        if node is self.tree.current and not self._hide_ai_for_training():
            self._clear_candidate_module("正在分析当前局面…")
            self.redraw()
        self._set_msg("%s中…（手数 %d，visits %d）" % (suffix, node.depth, int(visits)))
        return True

    def _drain_training_deferred(self):
        if not (self.client and self.client.is_alive() and self.client.ready):
            return
        pending = list(self._training_deferred_nodes.values())
        self._training_deferred_nodes = {}
        for node in pending:
            if node.analysis is None and not self.guard.has_pending(node):
                self._request_analysis(node, training=True)

    def _poll_loop(self):
        try:
            if self.client and self.client.is_alive():
                # 检测就绪（点目模式下保留"点目模式"状态栏，不被每 80ms 覆盖）
                if self.client.ready and not self.scoring_mode:
                    self._set_status("● KataGo 就绪", "green")
                # 取回结果
                for rid, resp in self.client.poll():
                    if rid in self._analysis_queue_pending:
                        self._handle_analysis_queue_result(rid, resp)
                        continue
                    if rid in self._style_verification_pending:
                        self._handle_style_verification_result(rid, resp)
                        continue
                    if rid in self._problem_compare_pending:
                        self._handle_problem_compare_result(rid, resp)
                        continue
                    if rid in self._drill_forced_pending:
                        self._handle_drill_forced_result(rid, resp)
                        continue
                    if rid in self._human_sl_pending:
                        self._handle_human_sl_result(rid, resp)
                        continue
                    if rid in self._mistake_forced_pending:
                        self._handle_mistake_forced_result(rid, resp)
                        continue
                    if rid in self._training_prefetch_pending:
                        self._handle_training_prefetch_result(rid, resp)
                        continue
                    if rid in self._training_cache_bg_pending:
                        self._handle_training_cache_bg_result(rid, resp)
                        continue
                    if rid in self._library_bg_pending:
                        self._handle_library_bg_result(rid, resp)
                        continue
                    node, ok = self.guard.take(rid)
                    if not ok:
                        continue   # 过期（来自旧引擎实例的残留结果），丢弃
                    if "error" in resp:
                        if node is self.tree.current:
                            self._clear_candidate_module(
                                "分析失败，请检查引擎设置后重试")
                            self.redraw()
                        self._set_msg("KataGo 报错：%s" % resp.get("error"))
                        continue
                    node.analysis = resp
                    self._apply_analysis_result(node, resp)
                # 就绪后若当前节点没分析也没挂账，补一个
                if self.client.ready:
                    self._drain_training_deferred()
                    self._maybe_start_selected_problem_comparison()
                    cur = self.tree.current
                    if cur.analysis is None and not self.guard.has_pending(cur):
                        self._request_analysis(cur, training=bool(self._training and self._training.get("active")))
                    self._kick_analysis_queue()
                    self._maybe_prepare_library_training_background()
            elif self.client and not self.client.is_alive():
                err = "\n".join(self.client.recent_stderr(8)) or "进程已退出"
                self._set_status("● KataGo 已退出", "red")
                self._set_msg("KataGo 异常退出：%s" % err)
                self.client = None
                self.btn_start.configure(text="启动 KataGo")
                self._library_bg_pending = {}
                self._library_bg_current = None
                self._training_cache_bg_pending = {}
                self._training_cache_bg_current = None
                self._problem_compare_pending = {}
                self._style_verification_pending = {}
                self._style_verification_queue = []
                self._interrupt_analysis_queue("KataGo 异常退出，等待重新启动后继续")
                self._reset_batch_state()      # 引擎死亡：中止批量计数，避免 stale nid 误计
                self._clear_candidate_module(
                    "KataGo 已退出，重新启动后可继续分析")
                self.redraw()
        finally:
            self.after(POLL_MS, self._poll_loop)

    def _render_analysis(self, resp):
        root = resp.get("rootInfo", {})
        wr = root.get("winrate")
        score = root.get("scoreLead")
        self._update_situation_metrics(wr, score)
        self._render_score_judgment(score)   # 形势判断大字
        self._render_territory(resp)         # 领地统计（实地 / 倾向）
        self._apply_auto_hint(resp)          # 自动 AI 首选（全模式默认开；不允许时清空）
        mis = sorted(resp.get("moveInfos", []), key=lambda m: m.get("order", 99))
        self._candidate_actions = []
        self._pv_candidates = []                      # 实际显示的候选（过滤 pass/无效后，供主变切换索引）
        self._clear_candidate_module("正在整理推荐点…")
        if self._hide_ai_for_training():
            self._show_candidate_state("训练中暂不显示 AI 推荐")
            self.redraw()
            return
        valid = []
        for m in mis:
            mv = m.get("move")
            if mv in (None, "pass") or not mv:
                continue
            try:
                x, y = point_to_xy(mv, self.size)
            except Exception:
                continue
            valid.append((m, x, y))
            if len(valid) >= self._candidate_count:
                break
        # 新分析结果默认聚焦首选；随后用户在同一结果内切换候选时保持选择。
        self._pv_idx = 0
        current_player = str(root.get("currentPlayer") or "B").upper()
        for idx, (m, x, y) in enumerate(valid):
            if idx >= len(self._candidate_buttons):
                break   # 面板固定 3 个大按钮；设置里更大的数量只用于别处
            mv = m.get("move")
            self._pv_candidates.append(m)
            w = m.get("winrate", 0)
            player_wr = w if current_player == "B" else 1.0 - w
            pv = " ".join(m.get("pv", [])[:6])
            self._candidate_actions.append((x, y, pv, mv))
            # v2：按钮=第几选+坐标（单击落子），右侧只留胜率数字
            self._candidate_buttons[idx].configure(
                text="%d  %s" % (idx + 1, mv), state=tk.NORMAL)
            self._candidate_win_labels[idx].config(
                text="%.1f%%" % (player_wr * 100))
        if valid:
            self._show_candidate_rows(len(valid))
        else:
            self._show_candidate_state("当前局面没有可用候选")
        self._sync_candidate_selection()
        self.redraw()
        if self._show_pv and not self.scoring_mode:
            self._show_pv_sequence()           # 主变模式：刷新后重发序列（避免被通用消息覆盖）
        else:
            self._set_msg("已更新分析（手数 %d，轮 %s）" %
                          (self.tree.current.depth, root.get("currentPlayer", "?")))

    def _hide_ai_for_training(self):
        if self._mistake_review and self._mistake_review.get("active"):
            return True
        tr = self._training
        if not tr or not tr.get("active") or tr.get("finished"):
            return False
        if tr.get("ai_playing"):
            return False
        return True

    def _update_situation_metrics(self, wr, score):
        """同步形势概览的文字、指标卡和胜率条。"""
        if wr is None:
            self.lbl_wr.config(text="黑 —  ·  白 —  ｜ 目差 —")
            for attr in ("lbl_metric_black", "lbl_metric_white", "lbl_metric_lead"):
                label = getattr(self, attr, None)
                if label is not None:
                    label.config(text="—")
            self._draw_winrate_bar(0.5)
            return
        black_wr = float(wr) * 100
        white_wr = (1.0 - float(wr)) * 100
        lead_text = "—" if score is None else "%+.1f目" % float(score)
        self.lbl_wr.config(
            text="黑 %.1f%%  ·  白 %.1f%%  ｜ 目差 %s"
            % (black_wr, white_wr, lead_text))
        if hasattr(self, "lbl_metric_black"):
            self.lbl_metric_black.config(text="%.1f%%" % black_wr)
        if hasattr(self, "lbl_metric_white"):
            self.lbl_metric_white.config(text="%.1f%%" % white_wr)
        if hasattr(self, "lbl_metric_lead"):
            self.lbl_metric_lead.config(text=lead_text)
        self._draw_winrate_bar(float(wr))

    def _clear_analysis(self):
        self._candidate_actions = []
        self._pv_candidates = []
        self._update_situation_metrics(None, None)
        self._render_score_judgment(None)
        self._render_territory(None)
        self._clear_candidate_module()

    def _clear_candidate_module(self, message="启动 KataGo 后显示推荐点与变化图"):
        self._candidate_actions = []
        for idx, btn in enumerate(getattr(self, "_candidate_buttons", [])):
            btn.configure(text="%d  —" % (idx + 1), state=tk.DISABLED)
            self._style_candidate_row(idx, selected=False, active=False)
        for label in getattr(self, "_candidate_win_labels", []):
            label.config(text="—")
        for label in getattr(self, "_candidate_pv_labels", []):
            label.config(text="")
        self._show_candidate_state(message)

    def _show_candidate_state(self, message):
        """用单一状态提示替代一排不可用候选，减少空界面噪音。"""
        for row in getattr(self, "_candidate_rows", []):
            row.grid_remove()
        label = getattr(self, "_candidate_empty_label", None)
        if label is not None:
            label.config(text=message)
            label.grid()

    def _show_candidate_rows(self, count):
        """仅显示本次分析实际返回且符合设置数量的候选。"""
        label = getattr(self, "_candidate_empty_label", None)
        if label is not None:
            label.grid_remove()
        visible = min(int(count), self._candidate_count)
        for idx, row in enumerate(getattr(self, "_candidate_rows", [])):
            if idx < visible:
                row.grid()
            else:
                row.grid_remove()
            self._style_candidate_row(idx, selected=False, active=idx < visible)

    def _style_candidate_row(self, index, selected=False, active=True):
        """候选卡片选中态：边框、排名徽章和文字底色同步变化。

        兼容 CTkFrame：CTkFrame 用 fg_color 而非 bg/highlightbackground。
        """
        rows = getattr(self, "_candidate_rows", [])
        if not 0 <= index < len(rows):
            return
        bg = COLORS["accent_s"] if selected else COLORS["card2"]
        border = COLORS["accent"] if selected else COLORS["muted"]
        row = rows[index]
        if _HAS_CTK and isinstance(row, ctk.CTkFrame):
            try:
                row.configure(fg_color=bg)
            except Exception:
                pass
        else:
            try:
                row.configure(bg=bg, highlightbackground=border)
            except tk.TclError:
                pass
        rank_labels = getattr(self, "_candidate_rank_labels", [])
        if index < len(rank_labels):
            try:
                rank_labels[index].configure(
                    bg=COLORS["accent"] if selected else COLORS["card"],
                    fg="#ffffff" if selected else COLORS["accent"],
                    highlightbackground=border)
            except tk.TclError:
                pass
        for collection, fg in (
                (getattr(self, "_candidate_win_labels", []), COLORS["text"]),
                (getattr(self, "_candidate_pv_labels", []), COLORS["subtext"])):
            if index < len(collection):
                try:
                    collection[index].configure(bg=bg, fg=fg if active else COLORS["subtext"])
                except tk.TclError:
                    pass

    def _sync_candidate_selection(self):
        """推荐列表与棋盘推荐圆点使用同一个选择态。

        v2 按钮是 CTkButton（无 ttk style）：选中态用强调描边表达。
        """
        for index, button in enumerate(getattr(self, "_candidate_buttons", [])):
            active = index < len(self._candidate_actions)
            selected = active and index == self._pv_idx
            try:
                if _HAS_CTK and isinstance(button, ctk.CTkButton):
                    button.configure(
                        border_width=2 if selected else 1,
                        border_color=(COLORS["accent"] if selected
                                      else COLORS["muted"]))
                else:                                             # ttk.Button
                    button.configure(
                        style="Accent.TButton" if selected else "TButton")
            except (tk.TclError, ValueError):
                pass
            self._style_candidate_row(index, selected=selected, active=active)

    def _render_territory(self, resp):
        """领地统计：基于 ownership 拆「实地(高置信) / 倾向(影响)」。None/无 ownership→空。"""
        own = resp.get("ownership") if resp else None
        if not own:
            self.lbl_territory.config(text="")
            return
        split = ownership_territory_split(own, size=self.size)
        self.lbl_territory.config(
            text="实地：黑 %d · 白 %d   倾向：黑 %d · 白 %d" % (
                split["b_strong"], split["w_strong"],
                split["b_lean"], split["w_lean"]))

    def _refresh_situation_labels(self):
        """形势卡（胜率 / 目差 / 领地）随当前节点分析刷新。

        普通导航走 _render_analysis；点目等不调用 _render_analysis 的模式用它保持常驻，
        这样「形势判断」在所有模式下都与当前局面同步。
        """
        if not hasattr(self, "lbl_score"):
            return
        resp = getattr(self.tree.current, "analysis", None)
        root = (resp or {}).get("rootInfo") or {}
        wr = root.get("winrate")
        score = root.get("scoreLead")
        self._update_situation_metrics(wr, score)
        self._render_score_judgment(score)
        self._render_territory(resp)

    def _render_score_judgment(self, score):
        """形势判断大字：score=黑视角目差（+黑领先 / −白领先）。None=无数据。"""
        if score is None:
            self.lbl_score.config(text="等待分析", fg=COLORS["subtext"])
            return
        if score > 0.05:
            self.lbl_score.config(text="黑 +%.1f 目" % score, fg=COLORS["text"])
        elif score < -0.05:
            self.lbl_score.config(text="白 +%.1f 目" % abs(score), fg=COLORS["purple"])
        else:
            self.lbl_score.config(text="形势均衡", fg=COLORS["subtext"])

    def _select_candidate(self, index):
        """点击候选（v2）：普通模式直接落子；主变模式下切换看该选变化。

        推荐理由/PV 文字说明已按用户需求从面板移除——胜率就是按钮
        右侧的数字；想研究某选的后续变化请先开【主变】再点它。
        """
        if not 0 <= index < len(self._candidate_actions):
            return
        if self._show_pv and not self.scoring_mode:
            self._pv_idx = index
            self._sync_candidate_selection()
            self.redraw()
            self._show_pv_sequence()
            return
        self._play_candidate(index)

    def _play_candidate(self, index):
        """双击推荐按钮：落该子。"""
        if self.scoring_mode:
            return "break"
        if not 0 <= index < len(self._candidate_actions):
            return "break"
        x, y, _pv, _move = self._candidate_actions[index]
        self.play(x, y)
        return "break"

    # ===================== 全局提示 =====================
    def _clear_hint(self, redraw=False):
        self._hint_point = None
        self._hint_pending_nid = None
        self._hint_auto = False
        if redraw and hasattr(self, "canvas"):
            self.redraw()

    def _hint_move_info(self, resp):
        for item in sorted(
                (resp or {}).get("moveInfos") or [],
                key=lambda value: value.get("order", 999)):
            move = str(item.get("move") or "pass")
            if move.lower() == "pass":
                return item
            try:
                x, y = point_to_xy(move, self.size)
                self.tree.current.board.try_play(x, y)
                return item
            except (ValueError, IllegalMove):
                continue
        return None

    def show_hint(self):
        """在所有模式提供主动提示；训练中只有用户请求时才揭示答案。"""
        if self.scoring_mode:
            self._clear_hint(redraw=True)
            node = self.tree.current
            own = node.analysis.get("ownership") if node.analysis else None
            suggestions = set()
            if own and self.score_estimator is not None:
                suggestions = self.score_estimator.suggest_dead_stones_from_ownership(
                    own, threshold=0.75)
            if suggestions:
                self._set_msg("点目提示：优先核对疑似死子 %s" % ", ".join(sorted(suggestions)))
            elif self._scoring_result is not None:
                self._set_msg("点目提示：当前估算 %s；请继续核对死子和双活。" %
                              self._scoring_result.result_text)
            else:
                self._set_msg("点目提示：请先标记死子，再确认地盘归属。")
            return
        tr = self._training
        if tr and tr.get("active") and not tr.get("finished"):
            user_color = normalize_player_color(tr.get("user_color"))
            if user_color in ("B", "W") and color_letter(
                    self.tree.current.board.to_move) != user_color:
                self._set_msg("训练提示：当前是 AI 回合，请等待 AI 应手。")
                return
        node = self.tree.current
        if node.analysis is None and tr and tr.get("active"):
            self._apply_cached_training_analysis(node)
        if node.analysis is None:
            self._hint_pending_nid = node.nid
            if self.client and self.client.is_alive() and self.client.ready:
                if self.guard.has_pending(node):
                    self._set_msg("当前局面正在分析，完成后会自动显示提示…")
                else:
                    self._request_analysis(node, training=bool(tr and tr.get("active")))
                    self._set_msg("正在计算当前局面的提示…")
            else:
                if not (self.client and self.client.is_alive()):
                    self._start_katago(quiet=True)
                if self.client and self.client.is_alive():
                    self._set_msg("正在启动 KataGo，提示会在分析完成后自动显示…")
                else:
                    self._set_msg("当前没有可用缓存，请先配置 KataGo 引擎和模型。")
            return
        self._show_hint_from_analysis(node, node.analysis)

    def _show_hint_from_analysis(self, node, resp):
        if node is not self.tree.current:
            return False
        item = self._hint_move_info(resp)
        if not item:
            self._clear_hint(redraw=True)
            self._set_msg("当前局面没有可用提示")
            return False
        move = str(item.get("move") or "pass")
        self._hint_pending_nid = None
        self._hint_point = None
        self._hint_auto = False     # 手动提示（F1 / 按钮），标注用「提示」
        if move.lower() != "pass":
            try:
                self._hint_point = point_to_xy(move, self.size)
            except ValueError:
                pass
        root = (resp or {}).get("rootInfo") or {}
        player = str(root.get("currentPlayer") or color_letter(node.board.to_move)).upper()
        winrate = item.get("winrate")
        detail = ""
        if winrate is not None:
            player_wr = float(winrate) if player == "B" else 1.0 - float(winrate)
            detail = "，%s方胜率 %.1f%%" % ("黑" if player == "B" else "白", player_wr * 100)
        prefix = "训练提示" if self._training and self._training.get("active") else "AI 提示"
        if self._training and self._training.get("active"):
            if not self._training.get("current_turn_hint_used"):
                self._training["hint_used_count"] = int(
                    self._training.get("hint_used_count") or 0) + 1
            self._training["current_turn_hint_used"] = True
        self.redraw()
        self._set_msg("%s：建议 %s%s" % (prefix, move, detail))
        return True

    # ===================== 自动 AI 首选（全模式默认开）=====================
    def _auto_hint_context_allowed(self):
        """自动提示是否允许在当前模式揭示 AI 首选。

        全模式默认开（普通 / 复盘 / 浏览 / 训练-AI 回合）；
        保留盲下训练与问题手 quiz 的意义——训练用户回合、quiz 作答阶段默认不揭示，
        用户可用设置 auto_hint_training 强制在训练也揭示；点目模式走单独的死子提示。
        """
        if self.scoring_mode:
            return False
        if self._mistake_review and self._mistake_review.get("active"):
            return False     # 错题测验为盲下：默认不揭示（F1 可主动请求）
        if self._drill_active() and not getattr(self, "_drill_revealed", False):
            return False
        tr = self._training
        if tr and tr.get("active") and not tr.get("finished") and not tr.get("ai_playing"):
            # 训练用户回合：默认不揭示（保留训练），可用设置强制开
            return bool(self.cfg.get("auto_hint_training", False))
        return True

    def _apply_auto_hint(self, resp):
        """分析回流后自动把 AI 首选标到棋盘（_auto_hint 开且当前模式允许时）。

        在 _render_analysis 早期调用，确保训练用户回合（提前 return）也能按设置揭示。
        不允许时清空 _hint_point，避免上一手的提示残留。
        """
        if not getattr(self, "_auto_hint", True):
            # 自动关：仅清「自动首选」标记，保留用户 F1 手动提示不被同节点重绘清掉
            if getattr(self, "_hint_auto", False):
                self._hint_point = None
                self._hint_auto = False
            return
        if not self._auto_hint_context_allowed():
            if getattr(self, "_hint_auto", False):
                self._hint_point = None
                self._hint_auto = False
            return
        item = self._hint_move_info(resp)
        if not item:
            self._hint_point = None
            self._hint_auto = False
            return
        move = str(item.get("move") or "pass")
        if move.lower() == "pass":
            self._hint_point = None
            self._hint_auto = False
            return
        try:
            self._hint_point = point_to_xy(move, self.size)
            self._hint_auto = True
        except ValueError:
            self._hint_point = None
            self._hint_auto = False

    def toggle_auto_hint(self):
        """切换「自动 AI 首选」：开则分析回流即在棋盘标出 AI 下一手，关则仅 F1 手动。"""
        self._auto_hint = not getattr(self, "_auto_hint", True)
        self.cfg.update(auto_hint=self._auto_hint)
        btn = self.__dict__.get("btn_auto_hint")
        if btn is not None:
            btn.config(text="AI首选 ✓" if self._auto_hint else "AI首选 ✗")
            self._set_toggle(btn, self._auto_hint)
        # 立即按当前分析应用 / 清除首选标记
        node = self.tree.current
        if self._auto_hint and node.analysis and not self.scoring_mode:
            self._apply_auto_hint(node.analysis)
        else:
            self._hint_point = None
            self._hint_auto = False
        self.redraw()
        self._set_msg("自动 AI 首选已%s" % ("开启" if self._auto_hint else "关闭"))

    def toggle_candidates(self):
        """切换棋盘候选点叠加层（A/B/C/D/E 圆圈）：默认关闭，用户主动开启。

        与「AI首选」不同：AI首选只标一个点（_hint_point），候选点显示前 N 个推荐（_draw_candidate_overlay）。
        两者独立控制，用户可只开其一。
        """
        self._show_candidates = not getattr(self, "_show_candidates", False)
        btn = self.__dict__.get("btn_candidates")
        if btn is not None:
            btn.configure(text="候选点 ✓" if self._show_candidates else "候选点 ✗")
            self._set_toggle(btn, self._show_candidates)
        self.redraw()
        self._set_msg("棋盘候选点叠加已%s" % ("开启" if self._show_candidates else "关闭"))

    def _draw_hint_overlay(self):
        if self._hint_point is None or self.scoring_mode:
            return
        x, y = self._hint_point
        board = self.tree.current.board
        if board.stone_at(x, y) != EMPTY:
            return
        cx = self.MARGIN + x * self.CELL
        cy = self.MARGIN + y * self.CELL
        # 若首选点同时是棋盘已显示的候选项，画外环强调（不画中心点，避免遮字母）
        cand_pts = {(ax, ay) for ax, ay, _pv, _mv in getattr(self, "_candidate_actions", [])}
        on_candidate = (x, y) in cand_pts
        radius = self.CELL * (0.56 if on_candidate else 0.42)
        self.canvas.create_oval(
            cx - radius, cy - radius, cx + radius, cy + radius,
            outline=COLORS["green"], width=4, dash=(5, 3), tags=("hint-marker",))
        if not on_candidate:
            self.canvas.create_oval(
                cx - 4, cy - 4, cx + 4, cy + 4,
                fill=COLORS["green"], outline="#ffffff", width=1, tags=("hint-marker",))
        text = "AI首选" if getattr(self, "_hint_auto", False) else "提示"
        # 标注避让正上方被棋子/候选占据的交叉点，间距随 CELL 缩放（不与上方字母重叠）
        gap = self.CELL * 0.5
        above_free = (y - 1 >= 0) and board.stone_at(x, y - 1) == EMPTY and (x, y - 1) not in cand_pts
        if above_free and (cy - radius - gap) > self.MARGIN:
            label_y = cy - radius - gap
        else:
            label_y = cy + radius + gap
        self.canvas.create_text(
            cx, label_y, text=text, fill=COLORS["green"],
            font=("Microsoft YaHei UI", 8, "bold"), tags=("hint-marker",))

    def _draw_winrate_bar(self, wr_black):
        """胜率条：PIL 圆角预渲染（黑白段 + accent 分割线），替代直角 create_rectangle。"""
        self.wr_canvas.delete("all")
        w = max(240, self.wr_canvas.winfo_width())
        h = max(12, self.wr_canvas.winfo_height())
        wr_black = max(0.0, min(1.0, float(wr_black)))
        bx = int(w * wr_black)
        radius = min(h // 2, 6)   # 圆角半径（胶囊形）
        # PIL 路径：圆角底 + 黑白段 + 分割线，整体真 AA 圆角
        if _HAS_PIL:
            SS = 3
            img = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            # 圆角底（muted 色）
            muted_rgb = self._hex_to_rgb(COLORS["muted"])
            draw.rounded_rectangle([0, 0, w * SS - 1, h * SS - 1],
                                   radius=radius * SS, fill=muted_rgb + (255,))
            # 黑段（左半，按胜率宽度）
            bx_ss = bx * SS
            if bx_ss > SS:
                black_rgb = self._hex_to_rgb(COLORS["black"])
                draw.rectangle([SS, SS, bx_ss, (h - 1) * SS], fill=black_rgb + (255,))
            # 白段（右半）
            if bx_ss < (w - 1) * SS:
                white_rgb = self._hex_to_rgb(COLORS["white"])
                draw.rectangle([bx_ss, SS, (w - 1) * SS, (h - 1) * SS], fill=white_rgb + (255,))
            # accent 分割线
            accent_rgb = self._hex_to_rgb(COLORS["accent"])
            draw.line([bx_ss, SS, bx_ss, (h - 1) * SS], fill=accent_rgb + (255,), width=max(1, SS))
            # 重新画圆角底裁剪（让黑白段也呈圆角）
            mask = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.rounded_rectangle([0, 0, w * SS - 1, h * SS - 1],
                                        radius=radius * SS, fill=(255, 255, 255, 255))
            img = Image.alpha_composite(Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0)), img)
            # 用 mask 裁剪
            img.putalpha(mask.split()[3])
            img = img.resize((w, h), Image.LANCZOS)
            self._wr_bar_img = ImageTk.PhotoImage(img)   # 持有引用防 GC
            self.wr_canvas.create_image(0, 0, anchor=tk.NW, image=self._wr_bar_img)
        else:
            # 降级：create_rectangle 直角条
            inner_left = 1
            inner_right = max(inner_left + 1, w - 1)
            split = max(inner_left, min(inner_right, bx))
            self.wr_canvas.create_rectangle(0, 0, w, h, fill=COLORS["muted"], outline=COLORS["muted"])
            if split > inner_left:
                self.wr_canvas.create_rectangle(inner_left, 1, split, h - 1, fill=COLORS["black"], outline="")
            if split < inner_right:
                self.wr_canvas.create_rectangle(split, 1, inner_right, h - 1, fill=COLORS["white"], outline="")
            self.wr_canvas.create_line(split, 1, split, h - 1, fill=COLORS["accent"], width=2)
            self.wr_canvas.create_rectangle(0, 0, w, h, outline=COLORS["muted"], width=1)

    # ===================== 绘制 =====================
    def _init_event_bus(self):
        """初始化轻量事件总线：各域订阅事件，解耦 _after_navigate 的刷新扇出。

        事件类型：
        - "navigated"：每次落子/导航后（原 _after_navigate 的扇出）
        - "analysis_applied"：分析结果回流后
        listener 按注册顺序调用（OrderedDict 保证顺序）。
        """
        from collections import OrderedDict
        self._listeners = OrderedDict()
        self._listeners["navigated"] = []
        self._listeners["analysis_applied"] = []
        # 注册 navigated 的 listener（保持原 _after_navigate 的执行顺序）
        self._subscribe("navigated", self._refresh_treeview)
        self._subscribe("navigated", self._on_navigated_analysis)      # render/clear + request
        self._subscribe("navigated", self._check_double_pass)
        self._subscribe("navigated", self._update_review_state)        # 复盘重活
        self._subscribe("navigated", self._update_scale)
        self._subscribe("navigated", self._refresh_game_context)
        self._subscribe("navigated", self._on_navigated_training_feedback)

    def _subscribe(self, event, fn):
        """订阅事件（按订阅顺序调用）。"""
        self._listeners.setdefault(event, []).append(fn)

    def _emit(self, event):
        """触发事件，按顺序调用所有 listener。单个 listener 异常不中断后续。"""
        for fn in self._listeners.get(event, []):
            try:
                fn()
            except Exception:
                pass

    def _on_navigated_analysis(self):
        """navigated listener：渲染当前节点分析，或清空并请求。"""
        node = self.tree.current
        if node.analysis:
            self._render_analysis(node.analysis)
        else:
            self._clear_analysis()
            if self.client and self.client.is_alive() and self.client.ready:
                self._request_analysis(node, training=bool(self._training and self._training.get("active")))
            elif self.client and self.client.is_alive():
                self._show_candidate_state("KataGo 正在加载模型…")
                self._set_msg("KataGo 加载中，就绪后将自动分析…")

    def _on_navigated_training_feedback(self):
        """navigated listener：训练激活时刷新训练反馈。"""
        if self._training and self._training.get("active"):
            self._refresh_training_feedback()

    def _init_overlay_layers(self):
        """初始化棋盘叠加图层注册表（z-order + 条件 + 绘制函数）。

        新增图层只需在此注册一行，无需改 redraw。互斥语义在 condition 里集中表达。
        基础层（木纹/网格/星位/坐标/棋子）不注册，在 redraw 里硬编码。
        z_order 小的先画（在下层），大的后画（在上层）。
        """
        self._overlay_layers = [
            # (z, name, condition_fn, draw_fn)
            # z=10 热力图（画在棋子之下）
            (10, "heatmap", self._layer_heatmap_cond, self._layer_heatmap_draw),
            # z=15 空盘提示
            (15, "empty_state", self._layer_empty_state_cond, self._layer_empty_state_draw),
            # z=20 悬停预览子
            (20, "hover", self._layer_hover_cond, self._layer_hover_draw),
            # z=25 最后一手红三角
            (25, "last_move", self._layer_last_move_cond, self._layer_last_move_draw),
            # z=26 精细评价色圈
            (26, "quality_ring", self._layer_quality_ring_cond, self._layer_quality_ring_draw),
            # z=30 点目地盘图（独占，与候选/PV/drill 互斥）
            (30, "scoring", self._layer_scoring_cond, self._draw_scoring_overlay),
            # z=35 主变序列（与候选互斥）
            (35, "pv", self._layer_pv_cond, self._draw_pv_overlay),
            # z=35 候选点（与 PV 同 z，互斥——condition 保证只有一方胜出）
            (35, "candidate", self._layer_candidate_cond, self._draw_candidate_overlay),
            # z=40 问题手对比分支
            (40, "problem_branch", self._layer_problem_branch_cond, self._draw_problem_branch_overlay),
            # z=45 问题手训练 quiz 字母
            (45, "drill", self._layer_drill_cond, self._draw_drill_overlay),
            # z=50 AI 首选提示
            (50, "hint", lambda: True, self._draw_hint_overlay),
            # z=90 形势判断 HUD（右上角）
            (90, "situation", lambda: True, self._draw_situation_overlay),
            # z=95 训练 banner（左上角，最上层）
            (95, "training", lambda: True, self._draw_training_overlay),
        ]
        # 按 z_order 排序（稳定的：同 z 按注册顺序）
        self._overlay_layers.sort(key=lambda item: item[0])

    # ---- 图层 condition 函数（集中管理互斥逻辑，替代散落的 if）----

    def _layer_heatmap_cond(self):
        return self._heat_mode != 0 and not self.scoring_mode

    def _layer_heatmap_draw(self):
        node = self.tree.current
        if node.analysis:
            if self._heat_mode == 1 and node.analysis.get("ownership"):
                self._draw_ownership(node.analysis["ownership"])
            elif self._heat_mode == 2 and node.analysis.get("policy"):
                self._draw_policy(node.analysis["policy"])

    def _layer_empty_state_cond(self):
        board = self.tree.current.board
        stone_count = sum(1 for y in range(self.size) for x in range(self.size)
                          if board.stone_at(x, y) != 0)
        return (stone_count == 0 and not self.scoring_mode
                and not self.tree.current.analysis)

    def _layer_empty_state_draw(self):
        self._draw_board_empty_state()

    def _layer_hover_cond(self):
        return (self._hover_point is not None and not self.scoring_mode
                and not self._show_pv)

    def _layer_hover_draw(self):
        c = self.canvas
        M, D = self.MARGIN, self.CELL
        R = D * 0.46
        board = self.tree.current.board
        hx, hy = self._hover_point
        if board.stone_at(hx, hy) == EMPTY:
            self._draw_hover_stone(c, M + hx * D, M + hy * D, R, board.to_move)

    def _layer_last_move_cond(self):
        return bool(self.tree.current.board.last_move)

    def _layer_last_move_draw(self):
        c = self.canvas
        M, D = self.MARGIN, self.CELL
        R = D * 0.46
        lm = self.tree.current.board.last_move
        cx, cy = M + lm[0] * D, M + lm[1] * D
        ts = R * 0.38
        c.create_polygon(cx, cy - ts, cx - ts * 0.87, cy + ts * 0.5,
                         cx + ts * 0.87, cy + ts * 0.5,
                         fill=COLORS["red"], outline="")

    def _layer_quality_ring_cond(self):
        return (not self.scoring_mode
                and self.tree.current.board.last_move is not None
                and getattr(self, "_current_quality_result", None) is not None)

    def _layer_quality_ring_draw(self):
        c = self.canvas
        M, D = self.MARGIN, self.CELL
        R = D * 0.46
        qr = self._current_quality_result
        _ring = {"best": COLORS.get("accent"), "good": COLORS.get("green"),
                 "inaccuracy": COLORS.get("amber"), "blunder": COLORS.get("red")}.get(qr.quality_key)
        if _ring:
            lm = self.tree.current.board.last_move
            cx, cy = M + lm[0] * D, M + lm[1] * D
            c.create_oval(cx - R - 3, cy - R - 3, cx + R + 3, cy + R + 3,
                          outline=_ring, width=3)

    def _layer_scoring_cond(self):
        return self.scoring_mode

    def _layer_pv_cond(self):
        return self._show_pv and not self.scoring_mode

    def _layer_candidate_cond(self):
        # 与 PV 互斥：PV 开时不画候选；用户未开启候选点叠加层时不画
        return (self._show_candidates
                and not self.scoring_mode and not self._show_pv
                and not self._hide_ai_for_training()
                and not self._drill_active())

    def _layer_problem_branch_cond(self):
        return bool(getattr(self, "_problem_branch_overlay", None)) and not self.scoring_mode

    def _layer_drill_cond(self):
        return bool(getattr(self, "_drill_overlay", None)) and not self.scoring_mode

    def redraw(self):
        """节流重绘：同步执行但合并连续调用。

        第一次调用立即执行 _do_redraw；执行途中若再次调用（递归触发），标记 dirty，
        执行完后检查 dirty 再补一次。避免 after_idle 在无头测试环境不触发的问题。
        """
        if getattr(self, "_redrawing", False):
            # 递归重入：标记 dirty，当前 redraw 完成后再补一次
            self._redraw_dirty = True
            return
        self._redrawing = True
        self._redraw_dirty = False
        try:
            self._do_redraw()
            while self._redraw_dirty:
                self._redraw_dirty = False
                self._do_redraw()
        finally:
            self._redrawing = False

    def _do_redraw(self):
        """实际执行重绘（由 redraw 节流入口调用）。"""
        c = self.canvas
        c.delete("all")
        S = self.size
        M, D = self.MARGIN, self.CELL
        # PIL 锐利化棋盘底图：木纹+网格+星位（3x超采样+Lanczos=真抗锯齿），贴为 board-bg
        bg = self._get_board_bg()
        if bg is not None:
            c.create_image(0, 0, anchor=tk.NW, image=bg, tags=("board-bg",))
        else:
            # 降级：无 PIL 时用 Tk 原生绘制（无 AA）
            for offset in (0.18, 0.39, 0.63, 0.84):
                y = int(self.BOARD_PIX * offset)
                c.create_line(2, y, self.BOARD_PIX - 2, y, fill=COLORS["board2"], width=1)
            for i in range(S):
                c.create_line(M, M + i * D, M + (S - 1) * D, M + i * D, fill=COLORS["grid"])
                c.create_line(M + i * D, M, M + i * D, M + (S - 1) * D, fill=COLORS["grid"])
            for sx in (3, 9, 15):
                for sy in (3, 9, 15):
                    px, py = M + sx * D, M + sy * D
                    c.create_oval(px - 4, py - 4, px + 4, py + 4, outline=COLORS["star"], width=1.5)
                    c.create_oval(px - 1.6, py - 1.6, px + 1.6, py + 1.6, fill=COLORS["star"], outline="")
        # 坐标（Tk create_text，文字 AA 由系统 ClearType/FreeType 处理）
        for i in range(S):
            c.create_text(M + i * D, M - 13, text=COLS[i], font=("Consolas", 9), fill=COLORS["coord"])
            c.create_text(M - 13, M + i * D, text=str(S - i), font=("Consolas", 9), fill=COLORS["coord"])
        # 热力图层（z=10，画在棋子之下）——先于棋子绘制
        if self._overlay_layers and self._overlay_layers[0][1] == "heatmap":
            if self._layer_heatmap_cond():
                self._layer_heatmap_draw()
        # 棋子（带左上高光，瓷质感）——基础层
        board = self.tree.current.board
        R = D * 0.46
        for y in range(S):
            for x in range(S):
                st = board.stone_at(x, y)
                if st == 0:
                    continue
                cx, cy = M + x * D, M + y * D
                self._draw_stone(c, cx, cy, R, st)
        # 叠加图层：按 z-order 遍历注册表（棋子之上的所有图层）
        # 新增图层只需 _init_overlay_layers 注册一行，互斥语义在 condition 里集中管理
        for _z, _name, cond_fn, draw_fn in self._overlay_layers:
            if _name == "heatmap":
                continue   # 热力图已在棋子之前单独处理（画在棋子下）
            try:
                if cond_fn():
                    draw_fn()
            except Exception:
                pass   # 单个图层异常不中断整体 redraw（鲁棒性）
        # 信息标签
        to_move = "黑" if board.to_move == BLACK else "白"
        sibs = self.tree.siblings()
        br = "  ｜分支 %d/%d" % (self.tree.sibling_index() + 1, len(sibs)) if len(sibs) > 1 else ""
        self.lbl_info.config(
            text="当前：%s方\n第 %d 手%s"
            % (to_move, self.tree.current.depth, br))
        if hasattr(self, "lbl_move_num"):
            qr = getattr(self, '_current_quality_result', None)
            qlabel = (" · %s" % qr.quality_label) if qr and qr.quality_label != "未评价" else ""
            self.lbl_move_num.config(text="第 %d 手 · %s方%s" % (self.tree.current.depth, to_move, qlabel))
        # situation/training HUD 已由 _overlay_layers 注册表按 z-order 绘制（z=90/95）

    def _draw_board_empty_state(self):
        """空棋盘给出明确下一步；已有棋谱回到根节点时改为浏览提示。"""
        has_mainline = bool(self.tree.root.children)
        title = "棋谱起点" if has_mainline else "开始一盘研究"
        detail = (
            "点击 → 或播放浏览主线"
            if has_mainline else "点击棋盘落子，或按 Ctrl+O 导入 SGF")
        cx = self.MARGIN + (self.size - 1) * self.CELL / 2
        cy = self.MARGIN + (self.size - 1) * self.CELL / 2
        half_width = min(self.CELL * 5.8, self.BOARD_PIX / 2 - 12)
        half_height = max(38, self.CELL * 1.55)
        left = cx - half_width
        top = cy - half_height
        right = cx + half_width
        bottom = cy + half_height
        self.canvas.create_rectangle(
            left + 4, top + 5, right + 4, bottom + 5,
            fill=COLORS["grid"], outline="", stipple="gray50",
            tags=("board-empty-state",))
        self.canvas.create_rectangle(
            left, top, right, bottom,
            fill=COLORS["board2"], outline=COLORS["accent_m"], width=1,
            tags=("board-empty-state",))
        self.canvas.create_rectangle(
            left, top, left + 6, bottom,
            fill=COLORS["accent"], outline="",
            tags=("board-empty-state",))
        self.canvas.create_text(
            left + 28, cy - 11, text=title, anchor="w",
            fill=COLORS["text"], font=FONTS["title"],
            tags=("board-empty-state",))
        self.canvas.create_text(
            left + 28, cy + 15, text=detail, anchor="w",
            fill=COLORS["coord"], font=FONTS["small"],
            tags=("board-empty-state",))

    def _draw_stone(self, c, cx, cy, R, st):
        """棋子 + 两层高光（光源左上的球感瓷质）+ 落子投影，st=BLACK/WHITE。

        色温对标主流平台（KaTrain/OGS）：黑子用暖深灰高光而非冷蓝灰，
        避免在暖木棋盘上显塑料感；白子用微暖白高光，更接近云子质感。

        PIL 锐利化：有 PIL 时用预渲染 PNG（真抗锯齿 + 真 alpha 阴影），
        否则降级为 Tk create_oval（无 AA 但保证可用）。
        """
        # PIL 路径：预渲染棋子 PNG，create_image 贴图（真 AA + 真透明阴影）
        if _HAS_PIL:
            img = self._get_stone_image(st, R)
            if img is not None:
                c.create_image(int(cx), int(cy), image=img, tags=("stone",))
                return
        # 降级路径：Tk create_oval（无抗锯齿，无高光——按用户要求取消反光）
        so = max(1.5, R * 0.14)
        c.create_oval(cx - R + so, cy - R + so, cx + R + so, cy + R + so,
                      fill=COLORS["shadow"], outline="", stipple="gray50")
        if st == BLACK:
            c.create_oval(cx - R, cy - R, cx + R, cy + R, fill=COLORS["black"], outline="#000", width=1)
        else:
            c.create_oval(cx - R, cy - R, cx + R, cy + R, fill=COLORS["white"], outline="#c2c6ce", width=1)

    def _hex_to_rgb(self, hex_color):
        """#rrggbb → (r, g, b) 元组。"""
        h = hex_color.lstrip("#")
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

    def _render_stone_image(self, color, radius):
        """用 PIL 预渲染单颗棋子（2x 超采样 + Lanczos 缩小 = 真抗锯齿）。

        含真 alpha 阴影（替代 Tk stipple 抖动）、径向高光（球感）、暖色温。
        返回 ImageTk.PhotoImage（调用方需持有引用防 GC）。
        """
        if not _HAS_PIL or radius < 4:
            return None
        SS = 3   # 超采样倍数（3x：棋子边缘更锐利精细，对标主流平台棋子分辨率）
        R = int(radius)
        pad = max(4, int(R * 0.3))   # 阴影边距
        size = (R * 2 + pad * 2) * SS
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        cx = cy = size // 2
        r = R * SS
        # 落子阴影：右下偏移，真 alpha 模糊（替代 stipple）
        so = max(2, int(R * 0.14)) * SS
        shadow_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow_layer)
        shadow_rgb = self._hex_to_rgb(COLORS["shadow"])
        shadow_draw.ellipse([cx - r + so, cy - r + so, cx + r + so, cy + r + so],
                            fill=(shadow_rgb[0], shadow_rgb[1], shadow_rgb[2], 110))
        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=max(1, SS)))
        img = Image.alpha_composite(img, shadow_layer)
        draw = ImageDraw.Draw(img)
        if color == BLACK:
            # 黑子主体（纯色平面，无反光高光——按用户要求取消）
            main_rgb = self._hex_to_rgb(COLORS["black"])
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=main_rgb + (255,))
        else:
            # 白子主体（纯色平面，无反光高光——按用户要求取消）
            main_rgb = self._hex_to_rgb(COLORS["white"])
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=main_rgb + (255,))
        # Lanczos 缩小（超采样 → 真抗锯齿）
        img = img.resize((size // SS, size // SS), Image.LANCZOS)
        return ImageTk.PhotoImage(img)

    def _get_stone_image(self, color, radius):
        """从缓存取棋子 PNG，缓存未命中则渲染。缓存键=(color, int(radius), 主题)。"""
        if not _HAS_PIL:
            return None
        theme_key = COLORS.get("black", "") + COLORS.get("white", "")
        R = int(radius)
        cache_key = (color, R, theme_key)
        if self._stone_cache_key != theme_key:
            self._stone_image_cache.clear()
            self._stone_cache_key = theme_key
        if cache_key not in self._stone_image_cache:
            try:
                self._stone_image_cache[cache_key] = self._render_stone_image(color, R)
            except Exception:
                self._stone_image_cache[cache_key] = None
        return self._stone_image_cache.get(cache_key)

    def _render_candidate_marker(self, fill_hex, outline_hex, radius, alpha, width):
        """PIL 预渲染候选点圆圈（3x 超采样 + Lanczos = 真抗锯齿 + 真透明）。

        替代 create_oval(stipple="gray75") 的锯齿 + 假透明。
        返回 ImageTk.PhotoImage（调用方持有引用防 GC）。
        """
        if not _HAS_PIL or radius < 3:
            return None
        SS = 3
        R = int(radius)
        pad = max(3, int(R * 0.2))
        size = (R * 2 + pad * 2) * SS
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        cx = cy = size // 2
        r = R * SS
        fill_rgb = self._hex_to_rgb(fill_hex)
        outline_rgb = self._hex_to_rgb(outline_hex)
        w = max(1, int(width * SS))
        # 填充圆（真 alpha 透明，替代 stipple 抖动）
        draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                     fill=fill_rgb + (alpha,), outline=outline_rgb + (255,), width=w)
        img = img.resize((size // SS, size // SS), Image.LANCZOS)
        return ImageTk.PhotoImage(img)

    def _get_candidate_marker(self, fill_hex, outline_hex, radius, alpha, width):
        """从缓存取候选点标记 PNG，缓存未命中则渲染。"""
        if not _HAS_PIL:
            return None
        cache_key = ("cand", fill_hex, outline_hex, int(radius), alpha, int(width))
        if cache_key not in self._stone_image_cache:
            try:
                self._stone_image_cache[cache_key] = self._render_candidate_marker(
                    fill_hex, outline_hex, radius, alpha, width)
            except Exception:
                self._stone_image_cache[cache_key] = None
        return self._stone_image_cache.get(cache_key)

    def _render_rounded_rect(self, w, h, radius, fill_hex, alpha=255):
        """PIL 预渲染圆角矩形（3x 超采样 + Lanczos = 真抗锯齿圆角 + 真透明）。

        用于 HUD 卡片底（形势判断/训练 banner/空状态），替代 create_rectangle 直角 + stipple 假透明。
        """
        if not _HAS_PIL or w < 4 or h < 4:
            return None
        SS = 3
        img = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        rgb = self._hex_to_rgb(fill_hex)
        draw.rounded_rectangle(
            [0, 0, w * SS - 1, h * SS - 1],
            radius=radius * SS, fill=rgb + (alpha,))
        img = img.resize((w, h), Image.LANCZOS)
        return ImageTk.PhotoImage(img)

    def _get_rounded_rect(self, w, h, radius, fill_hex, alpha=255):
        """从缓存取圆角矩形 PNG，缓存未命中则渲染。"""
        if not _HAS_PIL:
            return None
        cache_key = ("rect", w, h, radius, fill_hex, alpha)
        if cache_key not in self._stone_image_cache:
            try:
                self._stone_image_cache[cache_key] = self._render_rounded_rect(
                    w, h, radius, fill_hex, alpha)
            except Exception:
                self._stone_image_cache[cache_key] = None
        return self._stone_image_cache.get(cache_key)

    def _render_board_bg(self):
        """用 PIL 预渲染棋盘底图（木纹+网格+星位+坐标），2x 超采样 + Lanczos = 真抗锯齿。

        替代 redraw 里的 create_line 网格 + create_oval 星位 + create_text 坐标。
        缓存键=(BOARD_PIX, CELL, 主题)；尺寸/主题变化时重渲染。
        """
        if not _HAS_PIL:
            return None
        W = self.BOARD_PIX
        M, D, S = self.MARGIN, self.CELL, self.size
        SS = 3   # 超采样倍数（3x：网格/星位边缘更锐利，对标主流平台棋盘清晰度）
        img = Image.new("RGBA", (W * SS, W * SS), self._hex_to_rgb(COLORS["board"]) + (255,))
        draw = ImageDraw.Draw(img)
        # 木纹横线（极轻，消除纯色塑料感）
        board2_rgb = self._hex_to_rgb(COLORS["board2"])
        for offset in (0.18, 0.39, 0.63, 0.84):
            y = int(W * offset) * SS
            draw.line([(2 * SS, y), ((W - 2) * SS, y)], fill=board2_rgb, width=SS)
        # 网格线（2x 下 2px → 1x 下 1px，AA 锐利）
        grid_rgb = self._hex_to_rgb(COLORS["grid"])
        for i in range(S):
            x = (M + i * D) * SS
            draw.line([(M * SS, x), ((M + (S - 1) * D) * SS, x)], fill=grid_rgb, width=max(1, SS))
            draw.line([(x, M * SS), (x, (M + (S - 1) * D) * SS)], fill=grid_rgb, width=max(1, SS))
        # 星位（双圈）
        star_rgb = self._hex_to_rgb(COLORS["star"])
        for sx in (3, 9, 15):
            for sy in (3, 9, 15):
                px = (M + sx * D) * SS
                py = (M + sy * D) * SS
                draw.ellipse([px - 4 * SS, py - 4 * SS, px + 4 * SS, py + 4 * SS],
                             outline=star_rgb, width=max(1, int(1.5 * SS)))
                draw.ellipse([px - 1.6 * SS, py - 1.6 * SS, px + 1.6 * SS, py + 1.6 * SS],
                             fill=star_rgb)
        # 坐标：保留 Tk create_text 渲染（文字 AA 由系统 ClearType/FreeType 处理，非锯齿问题）
        # PIL 底图只画木纹+网格+星位，坐标在 redraw 里用 create_text 叠加
        # Lanczos 缩小（超采样 → 真抗锯齿）
        img = img.resize((W, W), Image.LANCZOS)
        return ImageTk.PhotoImage(img)

    def _get_board_bg(self):
        """从缓存取棋盘底图 PhotoImage，缓存未命中则渲染。"""
        if not _HAS_PIL:
            return None
        key = (self.BOARD_PIX, self.CELL, COLORS.get("board", "") + COLORS.get("grid", ""))
        if self._board_bg_key != key:
            try:
                self._board_bg_image = self._render_board_bg()
                self._board_bg_key = key
            except Exception:
                self._board_bg_image = None
                self._board_bg_key = None
        return self._board_bg_image

    def _draw_hover_stone(self, c, cx, cy, R, color):
        """半透明落点预览：PIL 真透明（替代 stipple 抖动假透明）。"""
        fill = COLORS["black"] if color == BLACK else COLORS["white"]
        outline = COLORS["muted"]
        # PIL 路径：半透明预览子（alpha≈110，真透明而非 stipple）
        hover_img = self._get_candidate_marker(fill, outline, R, 110, 2)
        if hover_img is not None:
            c.create_image(int(cx), int(cy), image=hover_img, tags=("hover-stone",))
        else:
            # 降级：create_oval + stipple
            c.create_oval(
                cx - R, cy - R, cx + R, cy + R,
                fill=fill, outline=outline, width=2, dash=(4, 3), stipple="gray50",
                tags=("hover-stone",))

    def _draw_candidate_overlay(self):
        """把前三个 AI 推荐放回棋盘，让列表数据与落点位置直接对应。"""
        candidates = getattr(self, "_pv_candidates", None) or []
        actions = getattr(self, "_candidate_actions", None) or []
        if not candidates or not actions:
            return
        board = self.tree.current.board
        for index, (action, info) in enumerate(zip(actions[:3], candidates[:3])):
            x, y = action[0], action[1]
            if board.stone_at(x, y) != EMPTY:
                continue
            cx = self.MARGIN + x * self.CELL
            cy = self.MARGIN + y * self.CELL
            radius = self.CELL * (0.40 if index == self._pv_idx else 0.34)
            selected = index == self._pv_idx
            fill = COLORS["accent"] if selected else COLORS["accent_s"]
            outline = COLORS["accent"] if index < 2 else COLORS["accent_m"]
            # PIL 锐利化：真抗锯齿 + 真透明（替代 create_oval + stipple 锯齿抖动）
            alpha = 255 if selected else 180
            marker_img = self._get_candidate_marker(
                fill, outline, radius, alpha, 2 if selected else 1)
            if marker_img is not None:
                self.canvas.create_image(
                    int(cx), int(cy), image=marker_img,
                    tags=("candidate-marker", "candidate-%d" % index))
            else:
                # 降级：无 PIL 时用 create_oval
                self.canvas.create_oval(
                    cx - radius, cy - radius, cx + radius, cy + radius,
                    fill=fill, outline=outline, width=2 if selected else 1,
                    stipple="" if selected else "gray75",
                    tags=("candidate-marker", "candidate-%d" % index))
            # v2：红色数字=第几选择（与右栏序号一致；胜率只在右栏显示）
            label = str(index + 1)
            self.canvas.create_text(
                cx, cy, text=label,
                fill=COLORS["red"],
                font=("Microsoft YaHei UI", max(9, int(self.CELL * 0.30)), "bold"),
                tags=("candidate-marker", "candidate-%d" % index))

    # ---- 主变（长度可在设置中调整）----
    def _reset_pv_state(self):
        """清空/导入新局时关闭主变模式（避免按钮✓残留但无数据）。"""
        if self._show_pv:
            self._show_pv = False
            self.btn_pv.configure(text="主变 %d 步" % self._pv_length)

    def toggle_pv(self):
        """切换主变显示；开启时默认第 1 选，可用推荐按钮切换。"""
        # 盲测模式（drill 未揭示 / 错题复习）下禁止开主变：主变第一步正是 AI 首选，等于公布答案。
        if self._show_pv is False:   # 即将开启时才检查（关闭不受限）
            drill_blind = self._drill_active() and not getattr(self, "_drill_revealed", False)
            mr_active = self._mistake_review and self._mistake_review.get("active")
            if drill_blind or mr_active:
                self._set_msg("盲测中不能显示主变（会泄露答案），请先揭示或关闭训练/复习")
                return
        self._hover_point = None
        self._show_pv = not self._show_pv
        self.btn_pv.configure(
            text=("主变 %d 步 ✓" if self._show_pv else "主变 %d 步")
            % self._pv_length)
        self._set_toggle(self.btn_pv, self._show_pv)
        self.redraw()
        if self._show_pv:
            self._show_pv_sequence()

    def _pv_pick(self):
        """返回 (idx, mi)：当前主变显示的候选。

        索引基准 = 右侧实际显示的推荐按钮列表（_pv_candidates）；
        若新分析候选变少，clamp 后写回 _pv_idx 归一化（避免 stale）。
        """
        cands = getattr(self, "_pv_candidates", None) or []
        if not cands:
            return 0, None
        idx = max(0, min(self._pv_idx, len(cands) - 1))
        self._pv_idx = idx            # 写回归一化（候选变少时不再 stale）
        return idx, cands[idx]

    def _show_pv_sequence(self):
        idx, mi = self._pv_pick()
        if mi is None:
            self._set_msg("当前无分析——点【重新分析】后再看主变")
            return
        pv = [m for m in (mi.get("pv", []) or [])][:self._pv_length]
        if not pv:
            self._set_msg("第 %d 选主变为空" % (idx + 1))
            return
        self._set_msg("主变（第%d选 %s）：%s" % (
            idx + 1, mi.get("move", "?"),
            "  ".join("%d.%s" % (i + 1, m) for i, m in enumerate(pv))))

    def _draw_pv_overlay(self):
        """选定候选的主变：棋盘带圈数字（黑白底按当前轮次交替）。"""
        node = self.tree.current
        _idx, mi = self._pv_pick()
        self._pv_marker_fills = []
        if mi is None:
            return
        pv = (mi.get("pv", []) or [])[:self._pv_length]
        c, S, M, D = self.canvas, self.size, self.MARGIN, self.CELL
        R = D * 0.46
        black_first = (node.board.to_move == BLACK)
        for i, mv in enumerate(pv):
            if not mv or mv == "pass":
                continue
            try:
                x, y = point_to_xy(mv, S)
            except Exception:
                continue
            if node.board.stone_at(x, y) != EMPTY:
                continue      # pv 落在已有棋子上（罕见：提子重填/陈旧分析），跳过避免盖棋子
            cx, cy = M + x * D, M + y * D
            is_black = black_first if (i % 2 == 0) else (not black_first)
            fill = COLORS["black"] if is_black else COLORS["white"]
            tcol = "#ffffff" if is_black else COLORS["text"]
            rr = R * 0.52
            self._pv_marker_fills.append(fill)
            # PIL 锐利化：真 AA 圆圈（替代 create_oval 锯齿）
            pv_img = self._get_candidate_marker(fill, COLORS["accent"], rr, 255, 2)
            if pv_img is not None:
                c.create_image(int(cx), int(cy), image=pv_img, tags=("pv-marker",))
            else:
                c.create_oval(cx - rr, cy - rr, cx + rr, cy + rr,
                              fill=fill, outline=COLORS["accent"], width=2)
            c.create_text(cx, cy, text=str(i + 1), fill=tcol, font=("Consolas", 9, "bold"))

    def _draw_problem_branch_overlay(self):
        """在问题手父局面显示实战或 AI 分支的编号变化。"""
        overlay = self._problem_branch_overlay or {}
        moves = overlay.get("pv") or []
        if not moves:
            return
        c, M, D = self.canvas, self.MARGIN, self.CELL
        board = self.tree.current.board
        black_first = board.to_move == BLACK
        outline = COLORS["red"] if overlay.get("kind") == "actual" else COLORS["accent"]
        radius = D * 0.29
        for index, move in enumerate(moves[:14]):
            if not move or str(move).lower() == "pass":
                continue
            try:
                x, y = point_to_xy(move, self.size)
            except Exception:
                continue
            if board.stone_at(x, y) != EMPTY:
                continue
            cx, cy = M + x * D, M + y * D
            is_black = black_first if index % 2 == 0 else not black_first
            fill = COLORS["black"] if is_black else COLORS["white"]
            text_color = "#ffffff" if is_black else COLORS["text"]
            # PIL 锐利化：真 AA 圆圈（替代 create_oval 锯齿）
            branch_img = self._get_candidate_marker(fill, outline, radius, 255, 3)
            if branch_img is not None:
                c.create_image(int(cx), int(cy), image=branch_img, tags=("problem-branch",))
            else:
                c.create_oval(
                    cx - radius, cy - radius, cx + radius, cy + radius,
                    fill=fill, outline=outline, width=3, tags=("problem-branch",))
            c.create_text(
                cx, cy, text=str(index + 1), fill=text_color,
                font=("Consolas", 8, "bold"), tags=("problem-branch",))

    # ---- 热力图 ----
    def cycle_heatmap(self):
        """0 关 → 1 地盘(ownership) → 2 策略(policy) → 0。"""
        self._heat_mode = (self._heat_mode + 1) % 3
        self.btn_heat.configure(text="热力图: %s" % HEAT_LABELS[self._heat_mode])
        self._set_toggle(self.btn_heat, self._heat_mode != 0)
        self.cfg.update(heatmap_mode=HEAT_KEYS[self._heat_mode])   # 持久化
        node = self.tree.current
        if (self._heat_mode != 0 and node.analysis is None
                and self.client and self.client.is_alive() and self.client.ready):
            self._request_analysis(node)   # 当前节点还没分析，补一个
        else:
            self.redraw()

    def _draw_ownership(self, ownership):
        """地盘图：PIL 预渲染半透明圆点（真 AA + 真透明），按归属强度渐变。

        美观优化：替代原 create_oval 硬编码色 + 锯齿；用调色板 black/white 色 +
        alpha 按归属强度渐变（强归属深、弱归属淡），视觉更柔和通透。
        """
        c, S, M, D = self.canvas, self.size, self.MARGIN, self.CELL
        board = self.tree.current.board
        for i, v in enumerate(ownership[:S * S]):
            if not v or abs(v) <= 0.05:
                continue
            x, y = i % S, i // S
            if board.stone_at(x, y) != EMPTY:
                continue
            av = abs(v)
            cx, cy = M + x * D, M + y * D
            # 半径按归属强度渐变（强=大、弱=小）
            radius = D * (0.12 + 0.16 * av)
            # alpha 按归属强度渐变（强=不透明、弱=半透明）
            alpha = int(80 + 140 * av)
            fill = COLORS["black"] if ownership_is_black(v) else COLORS["white"]
            # PIL 锐利化路径：真 AA + 真透明
            dot_img = self._get_candidate_marker(fill, fill, radius, alpha, 0)
            if dot_img is not None:
                c.create_image(int(cx), int(cy), image=dot_img, tags=("heatmap-own",))
            else:
                # 降级：create_oval
                c.create_oval(cx - radius, cy - radius, cx + radius, cy + radius,
                              fill=fill, outline="", stipple="gray75")

    def _draw_policy(self, policy):
        """策略热力图：PIL 预渲染半透明圆点（真 AA + 真透明），按权重渐变。

        美观优化：替代原 create_oval + stipple 抖动；颜色用 accent 系（和主题统一），
        透明度按 policy 权重平滑渐变。
        """
        c, S, M, D = self.canvas, self.size, self.MARGIN, self.CELL
        entries = policy_board_entries(policy, S)
        if not entries:
            return
        mx = max(v for _, _, v in entries)
        if mx <= 0:
            return
        for x, y, p in entries:
            frac = p / mx
            if frac < 0.02:
                continue
            cx, cy = M + x * D, M + y * D
            r = (D * 0.46) * (frac ** 0.5)
            # alpha 按权重平滑渐变（高权重=不透明、低权重=淡）
            alpha = int(60 + 160 * frac)
            fill = COLORS.get("accent") if frac >= 0.5 else COLORS.get("accent_m")
            dot_img = self._get_candidate_marker(fill, fill, r, alpha, 0)
            if dot_img is not None:
                c.create_image(int(cx), int(cy), image=dot_img, tags=("heatmap-pol",))
            else:
                c.create_oval(cx - r, cy - r, cx + r, cy + r,
                              fill=fill, outline="", stipple="gray75")

    # ===================== 点目 / 终局数棋 =====================
    def toggle_scoring(self):
        """【点目】按钮：进入 / 退出点目模式。"""
        if self.scoring_mode:
            self.exit_scoring()
        else:
            self.enter_scoring()

    def enter_scoring(self):
        """进入点目模式：以当前局面建 ScoreEstimator，显示地盘与初始结果。

        若当前节点已有 ownership → 弹窗询问是否采用 AI 死子建议；
        否则请求一次分析（includeOwnership 默认开），返回后补建议。
        """
        if self._training and self._training.get("active") and not self._training.get("finished"):
            self._set_msg("请先结束阶段训练，再进入点目模式")
            return
        if self._drill_active():
            self._set_msg("请先关闭问题手训练，再进入点目模式")
            return
        if self._mistake_review and self._mistake_review.get("active"):
            self._set_msg("请先完成或关闭错题复习，再进入点目模式")
            return
        self._stop_auto_play()   # 点目锁定当前局面估分，自动播放不得继续推进棋盘
        self._hover_point = None
        self._clear_hint()
        board = self.tree.current.board
        self.score_estimator = ScoreEstimator(board, komi=self.komi)
        self.scoring_mode = True
        self.dead_points = set()
        self._await_scoring_ownership = False
        self._show_scoring_widgets()
        self._refresh_scoring()
        node = self.tree.current
        own = node.analysis.get("ownership") if node.analysis else None
        if own:
            self._maybe_offer_ai_suggestion()
            self._set_msg("点目模式：点击棋子标记/取消死子；空点显示归属")
        else:
            self._await_scoring_ownership = True
            if self.client and self.client.is_alive() and self.client.ready:
                self.tree.current.analysis = None
                self._request_analysis(self.tree.current)
                self._set_msg("点目模式：等待 KataGo ownership 返回后给出死子建议…")
            else:
                self._set_msg("点目模式：未启动 KataGo，可手动点击棋子标记死子")
        self._set_status("● 点目模式（按 Esc 或【点目】退出）", "amber")

    def exit_scoring(self):
        """退出点目模式：清空临时状态，恢复正常落子/分析。"""
        if not self.scoring_mode:
            return
        self.scoring_mode = False
        self.score_estimator = None
        self.dead_points = set()
        self._await_scoring_ownership = False
        self._hide_scoring_widgets()
        self.redraw()
        # 退出点目后同步右侧分析面板（点目期间可能收到了带 ownership 的新分析）
        node = self.tree.current
        if node.analysis:
            self._render_analysis(node.analysis)
        else:
            self._clear_analysis()
        if self.client and self.client.is_alive():
            if self.client.ready:
                self._set_status("● KataGo 就绪", "green")
            else:
                self._set_status("● 模型加载中", "amber")
        else:
            self._set_status("● 未启动", "subtext")
        self._set_msg("已退出点目模式")

    def _maybe_offer_ai_suggestion(self):
        """进入点目时：若 ownership 给出死子建议，弹窗询问是否采用（每个节点只问一次）。"""
        if self.score_estimator is None:
            return
        node = self.tree.current
        own = node.analysis.get("ownership") if node.analysis else None
        if not own:
            return
        # 同一节点只问一次：退出点目再进入同一局面不重复弹窗
        if self._scoring_suggestion_prompted == node.nid:
            return
        self._scoring_suggestion_prompted = node.nid
        sug = self.score_estimator.suggest_dead_stones_from_ownership(own, threshold=0.75)
        if not sug:
            self._set_msg("点目模式：KataGo 暂无死子建议（可手动点击棋子标记）")
            return
        if messagebox.askyesno("采用 AI 死子建议",
                               "KataGo 建议 %d 颗死子：%s\n是否采用？" % (
                                   len(sug), ", ".join(sorted(sug)))):
            self.dead_points |= sug
            self._refresh_scoring()

    def apply_ai_dead_suggestion(self):
        """【AI 死子建议】按钮：把当前 ownership 的死子建议合并进已标记集合。"""
        if self.score_estimator is None:
            return
        node = self.tree.current
        own = node.analysis.get("ownership") if node.analysis else None
        if not own:
            self._set_msg("当前无 ownership，无法建议死子（请启动 KataGo 分析）")
            return
        sug = self.score_estimator.suggest_dead_stones_from_ownership(own, threshold=0.75)
        if not sug:
            self._set_msg("KataGo 暂无死子建议")
            return
        new = sug - self.dead_points
        self.dead_points |= sug
        self._refresh_scoring()
        self._set_msg("已采用 AI 死子建议：%s" % ", ".join(sorted(new or sug)))

    def confirm_score(self):
        """【确认结果】按钮：锁定当前点目结果，写回 tree.score_result（导出 SGF 用）。"""
        if self._scoring_result is None:
            return
        self.tree.score_result = self._scoring_result
        r = self._scoring_result
        if r.winner == "Draw":
            msg = "和棋（RE[0]）"
        else:
            side = "黑" if r.winner == "B" else "白"
            msg = "%s胜（RE[%s]）" % (side, r.result_text)
        messagebox.showinfo("点目结果已确认",
                            msg + "\n\n导出 SGF 时将写入 RE[] 与点目摘要。")
        self.exit_scoring()

    def _on_scoring_click(self, x, y):
        """点目模式点击：棋子 → 切换死子；空点 → 显示该点归属。不修改 MoveTree。"""
        if self.score_estimator is None:
            return
        board = self.tree.current.board
        st = board.stone_at(x, y)
        pt = xy_to_point(x, y, self.size)
        if st != EMPTY:
            self.score_estimator.toggle_dead_stone(pt)
            self.dead_points = self.score_estimator.get_dead_stones()
            self._refresh_scoring()
        else:
            r = self._scoring_result
            if r is None:
                return
            if pt in r.black_territory_points:
                attr = "黑地"
            elif pt in r.white_territory_points:
                attr = "白地"
            elif pt in r.neutral_points_list:
                attr = "中立"
            else:
                attr = "—"
            self._set_msg("%s：%s（点目模式下空点不落子）" % (pt, attr))

    def _refresh_scoring(self):
        """死子变更后重算结果、刷新右侧面板与棋盘叠加。"""
        if not self.scoring_mode or self.score_estimator is None:
            return
        self.score_estimator.set_dead_stones(self.dead_points)
        self._scoring_result = self.score_estimator.compute_chinese_area_score()
        self._render_scoring_panel(self._scoring_result)
        self._refresh_situation_labels()   # 形势卡在点目模式也随当前局面常驻
        self.redraw()

    def _render_scoring_panel(self, r):
        """右侧点目结果面板：活子/地/贴目/最终结果/死子清单。"""
        if r is None:
            self.lbl_scoring.config(text="")
            return
        if r.winner == "Draw":
            result_line = "结果：和棋"
        else:
            side = "黑" if r.winner == "B" else "白"
            result_line = "结果：%s胜 %.1f 目（%s）" % (side, abs(r.margin), r.result_text)
        lines = [
            "点目结果：中国规则 / 面积数法",
            "",
            "黑方：活子 %d + 地 %d = %g" % (r.black_stones, r.black_territory, r.black_area),
            "白方：活子 %d + 地 %d + 贴目 %g = %g" % (
                r.white_stones, r.white_territory, r.komi, r.white_area),
            "中立空点：%d" % r.neutral_points,
            "",
            result_line,
            "",
        ]
        if r.dead_black or r.dead_white:
            lines.append("死子：")
            if r.dead_black:
                lines.append("  黑：%s" % ", ".join(r.dead_black))
            if r.dead_white:
                lines.append("  白：%s" % ", ".join(r.dead_white))
        else:
            lines.append("死子：无")
        self.lbl_scoring.config(text="\n".join(lines))

    def _draw_scoring_overlay(self):
        """点目叠加：黑地/白地/中立大标记 + 死子红色 X（画在棋子之上）。"""
        if self._scoring_result is None:
            return
        c = self.canvas
        S, M, D = self.size, self.MARGIN, self.CELL
        r = self._scoring_result
        mark_r = max(5, D * 0.23)
        neutral_r = max(4, D * 0.16)
        # 地盘归属点：比旧版 3px 更醒目，贴近围棋平台的领地标记阅读方式。
        for pt in r.black_territory_points:
            x, y = point_to_xy(pt, S)
            cx, cy = M + x * D, M + y * D
            c.create_oval(cx - mark_r, cy - mark_r, cx + mark_r, cy + mark_r,
                          fill=COLORS["black"], outline="#2f3542", width=2)
        for pt in r.white_territory_points:
            x, y = point_to_xy(pt, S)
            cx, cy = M + x * D, M + y * D
            c.create_oval(cx - mark_r, cy - mark_r, cx + mark_r, cy + mark_r,
                          fill=COLORS["white"], outline="#5f6673", width=2)
        for pt in r.neutral_points_list:
            x, y = point_to_xy(pt, S)
            cx, cy = M + x * D, M + y * D
            c.create_oval(cx - neutral_r, cy - neutral_r, cx + neutral_r, cy + neutral_r,
                          fill=COLORS.get("amber"), outline=COLORS.get("accent_h"), width=1)
        # 死子：红色 X（覆盖在棋子上）
        for pt in self.dead_points:
            try:
                x, y = point_to_xy(pt, S)
            except Exception:
                continue
            cx, cy = M + x * D, M + y * D
            off = D * 0.30
            c.create_line(cx - off, cy - off, cx + off, cy + off, fill=COLORS["red"], width=3)
            c.create_line(cx - off, cy + off, cx + off, cy - off, fill=COLORS["red"], width=3)

    def _show_scoring_widgets(self):
        if not self._scoring_inner.winfo_ismapped():
            self._scoring_inner.pack(fill="x")
        self._scoring_frame.grid()          # 恢复 grid 单元（grid_remove 后重新显示）

    def _hide_scoring_widgets(self):
        self._scoring_inner.pack_forget()
        self._scoring_frame.grid_remove()   # 收缩 grid 单元（避免退出点目后留空隙）

    # ===================== 复盘三件套：批量分析 / 失误榜 / 胜率曲线 =====================
    def _review_focus_color(self):
        if self._review_scope_mode != "profile":
            return None
        side = getattr(self.tree, "_profile_side", "unknown")
        return side if side in ("B", "W") else None

    def _refresh_review_scope_button(self):
        if not hasattr(self, "btn_review_scope"):
            return
        color = self._review_focus_color()
        if self._review_scope_mode == "both" or color is None:
            text = "复盘范围：双方"
        else:
            name = (
                getattr(self.tree, "_sgf_pb", "黑方")
                if color == "B" else getattr(self.tree, "_sgf_pw", "白方"))
            text = "复盘范围：我方（%s·%s）" % (
                "黑" if color == "B" else "白", name)
        self.btn_review_scope.configure(text=text)

    def toggle_review_scope(self):
        self._review_scope_mode = (
            "both" if self._review_scope_mode == "profile" else "profile")
        self.cfg.update(review_scope=self._review_scope_mode)
        self._reset_problem_comparison_state()
        self._refresh_review_scope_button()
        self._update_review_state()
        self._set_msg(
            "复盘范围已切换为%s" % (
                "双方" if self._review_scope_mode == "both"
                else ("我方" if self._review_focus_color() else "双方（尚未识别我方）")))

    def _maybe_auto_analyze_library_game(self, attempts=240):
        """导入入库后自动分析整盘，随后由分析完成钩子生成训练题。"""
        if not self._library_record_id:
            return
        if self.client and self.client.is_alive() and self.client.ready:
            self.analyze_mainline()
            self._set_msg("已入库，正在自动分析整盘并生成阶段训练题…")
            return
        if not (self.client and self.client.is_alive()):
            self._start_katago(quiet=True)
        if attempts > 0:
            if self.client and self.client.is_alive():
                self._set_msg("已入库，KataGo 正在加载模型；OpenCL 首次启动可能需要调优，请稍候…")
            self.after(1000, lambda: self._maybe_auto_analyze_library_game(attempts - 1))
        else:
            self._set_msg("已入库，但 KataGo 暂未就绪；可稍后点【开始阶段训练】自动继续准备。")

    def analyze_mainline(self, training=False, quick=False):
        """【分析整盘】：主线全部尚无 analysis 且无 pending 的节点一次性发给 KataGo。

        结果异步回流、逐个写入 node.analysis 并【实时】刷新曲线/失误榜；完成后导航零等待。
        计数只针对【本次新请求】的 todo 节点，避免 force_analyze 重分析已计数节点时双重计数。

        visits 选择（对标 LizzieYzy 轻量模型快速补曲线的思路，但用单引擎 visits 分档实现）：
        - 默认 / training 走各自档位（_training_visits 或配置 max_visits）。
        - quick=True 用低 visits（80）快速预扫全盘，先定位问题手分布与胜率曲线，
          随后可对关键问题手单独 force_analyze 深算——避免整盘高 visits 的等待。
        """
        if not self._ensure_ready():
            return
        rr = ReviewReport(self.tree)
        nodes = rr.mainline_nodes()
        todo = [nd for nd in nodes if nd.analysis is None and not self.guard.has_pending(nd)]
        pending = [nd for nd in nodes if self.guard.has_pending(nd)]
        done0, total = rr.analyze_progress()
        if not todo:
            if pending:
                self._set_msg("整盘分析正在进行（%d 个请求待返回）" % len(pending))
            else:
                self._set_msg("主线已全部分析（%d/%d）" % (done0, total))
            self._update_review_state()
            return
        self._reset_batch_state()
        self._batch_target_nids = set(nd.nid for nd in todo)   # 只含本次新请求（防双重计数）
        self._batch_total = len(todo)
        self._batch_done = 0
        self._batch_done0 = done0
        self._batch_mainline_total = total
        # 快速预扫用固定低 visits（与 TRAINING_SPEED_MODES fast 档一致），先补曲线和定位问题手
        quick_visits = TRAINING_SPEED_MODES["fast"] if quick else None
        for nd in todo:
            self._request_analysis(nd, training=training, visits=quick_visits)
        mode = "（快速预扫 %dv）" % TRAINING_SPEED_MODES["fast"] if quick else (
            "（训练快速版）" if training else "")
        self._set_msg("批量分析整盘%s：%d / %d …" % (mode, done0, total))

    def quick_scan_mainline(self):
        """【快速预扫】：用低 visits 快速跑完整盘，先定位问题手与曲线，再按需深算。

        对标 LizzieYzy 的轻量模型补曲线——区别是本工具用单引擎 visits 分档实现，
        不起双进程。预扫完成后，问题手仍可用「分析当前局面」或双分支对比深算。
        """
        self.analyze_mainline(quick=True)

    def _reset_batch_state(self):
        """清空批量分析计数（引擎死亡/停止/导入/reset 时调用，防 stale nid 误计）。"""
        self._batch_target_nids = set()
        self._batch_total = 0
        self._batch_done = 0
        self._batch_done0 = 0
        self._batch_mainline_total = 0

    def _apply_analysis_result(self, node, resp):
        """处理一个回流的分析结果（从 _poll_loop 抽出，便于无头测试）。

        批量结果（node 属本批 todo）逐个刷新曲线+失误榜+当前手 loss（验收：实时刷新），
        并更新进度消息（放最后，避免被 _render_analysis 的"已更新分析"覆盖）；
        force_analyze / 导航触发的单节点结果走非批量分支。
        """
        if self._training and self._training.get("active"):
            self._store_active_training_analysis(node=node, resp=resp)
        if node.nid in self._batch_target_nids:
            self._batch_done += 1
            completed = self._batch_done >= self._batch_total
            if completed:
                total = self._batch_mainline_total
                self._reset_batch_state()
            self._update_review_state()                  # 实时刷新曲线 + 失误榜 + 当前手 loss
            if node is self.tree.current:
                if self.scoring_mode:
                    self._refresh_scoring()
                else:
                    self._render_analysis(resp)          # 候选点/胜率条/redraw（用最新 loss 画红圈）
            if completed:
                if self._library_record_id:
                    self._refresh_review_summary_artifact()
                    update_project_snapshot(self._library_record_id, self.tree, rules=self.rules, komi=self.komi)
                    self._refresh_library_window()
                self._set_msg("批量分析完成（%d 节点）" % total)
                # 新棋分析完成后聚合数据已变：若画像/棋风窗口开着，重开重算避免 stale。
                # open_* 方法本身是"重开重算"语义，winfo_exists 守卫确保只在窗口开着时触发。
                if self._profile_win is not None and self._profile_win.winfo_exists():
                    self.open_player_profile()
                if self._style_win is not None and self._style_win.winfo_exists():
                    self.open_style_profile()
                rec = self._ensure_training_task()
                self._maybe_start_pending_training(rec)
            else:
                self._set_msg("批量分析整盘：%d / %d …" % (
                    self._batch_done0 + self._batch_done, self._batch_mainline_total))
        else:
            if node is self.tree.current:
                if self.scoring_mode:
                    if self._await_scoring_ownership:
                        self._await_scoring_ownership = False
                        self._maybe_offer_ai_suggestion()
                    else:
                        self._refresh_scoring()
                else:
                    self._render_analysis(resp)
            self._update_review_state()
            if self._library_record_id:
                update_project_snapshot(self._library_record_id, self.tree, rules=self.rules, komi=self.komi)
                self._refresh_library_window()
        self._training_on_analysis(node)
        if self._hint_pending_nid == node.nid and node is self.tree.current:
            self._show_hint_from_analysis(node, resp)

    # ===================== 棋局库后台训练题准备 =====================
    def _library_bg_should_pause(self):
        if self.scoring_mode:
            return True
        if self._problem_compare_pending:
            return True
        if self._pending_training_record_id:
            return True
        if self._training and self._training.get("active") and not self._training.get("finished"):
            return True
        if self._drill_active():
            return True
        if self._mistake_review and self._mistake_review.get("active"):
            return True
        if self._analysis_queue_current or self._analysis_queue_pending:
            return True
        return False

    def _training_cache_signature(self, rules=None, komi=None, visits=None):
        model = model_signature(self.model_file)
        if self.model_file and os.path.exists(self.model_file):
            try:
                stat = os.stat(self.model_file)
                model += ":%d:%d" % (int(stat.st_size), int(stat.st_mtime))
            except OSError:
                pass
        return {
            "model": model,
            "rules": rules if rules is not None else self.rules,
            "komi": float(self.komi if komi is None else komi),
            "visits": int(self._training_visits() if visits is None else visits),
            "boardSize": int(self.size),
        }

    def _training_position_key(self, tree, moves, rules, komi):
        return position_key(
            tree.initial_stones_list(), moves, rules=rules, komi=komi, board_size=self.size)

    @staticmethod
    def _cache_move_infos(resp):
        return sorted(
            (resp or {}).get("moveInfos") or [],
            key=lambda item: item.get("order", 999),
        )

    def _cache_candidate_moves(self, resp, limit=3):
        out = []
        for item in self._cache_move_infos(resp):
            move = str(item.get("move") or "pass")
            low = move.lower()
            if low != "pass":
                try:
                    point_to_xy(move, self.size)
                except Exception:
                    continue
            if low not in {m.lower() for m in out}:
                out.append(move)
            if len(out) >= limit:
                break
        return out

    @staticmethod
    def _cache_append_move(moves, color, move):
        return list(moves or []) + [[color, "pass" if str(move).lower() == "pass" else move]]

    def _record_training_cache_ready(self, rec, task, signature):
        package = load_training_cache(rec.get("id"))
        if not package_matches(package, task, signature):
            return False
        return (
            package.get("status") == "ready"
            and int(package.get("preparedRounds") or 0) >= int(package.get("plannedRounds") or 0)
        )

    def _maybe_prepare_library_training_background(self):
        """补齐训练题，并为已有训练题生成持久化 AI 应手缓存。"""
        if not (self.client and self.client.is_alive() and self.client.ready):
            return
        if (self._library_bg_pending or self._library_bg_current
                or self._training_cache_bg_pending or self._training_cache_bg_current):
            return
        if self._library_bg_should_pause():
            return
        for rec in search_records(""):
            rid = rec.get("id")
            if not rid or rid in self._library_bg_recent:
                continue
            path = rec.get("projectPath")
            if not path or not os.path.exists(path):
                self._library_bg_recent.add(rid)
                continue
            try:
                task = rec.get("trainingTask")
                if task:
                    signature = self._training_cache_signature(
                        rec.get("rules") or self.rules,
                        rec.get("komi") if rec.get("komi") is not None else self.komi,
                        self._training_visits(),
                    )
                    if self._record_training_cache_ready(rec, task, signature):
                        self._library_bg_recent.add(rid)
                        continue
                    if self._start_training_cache_background(rec, task, signature):
                        return
                    self._library_bg_recent.add(rid)
                    continue
                if rid == self._library_record_id:
                    continue
                tree, data = load_project(path)
                refreshed = refresh_training_task(rid, tree)
                if refreshed and refreshed.get("trainingTask"):
                    self._refresh_library_window()
                    signature = self._training_cache_signature(
                        data.get("rules", rec.get("rules") or self.rules),
                        data.get("komi", rec.get("komi") or self.komi),
                        self._training_visits(),
                    )
                    if self._start_training_cache_background(
                            refreshed, refreshed.get("trainingTask"), signature, tree=tree, data=data):
                        return
                    continue
                rr = ReviewReport(tree)
                nodes = rr.mainline_nodes()
                todo = [nd for nd in nodes if nd.analysis is None]
                rules = data.get("rules", rec.get("rules") or self.rules)
                komi = data.get("komi", rec.get("komi") or self.komi)
                if not todo:
                    update_project_snapshot(rid, tree, rules=rules, komi=komi)
                    self._refresh_library_window()
                    self._library_bg_recent.add(rid)
                    continue
                visits = int(self.cfg.get("library_training_visits", 120))
                self._library_bg_current = {
                    "record_id": rid,
                    "name": rec.get("name", ""),
                    "tree": tree,
                    "rules": rules,
                    "komi": komi,
                    "todo": todo,
                    "visits": visits,
                    "total": len(todo),
                    "done": 0,
                    "errors": 0,
                }
                self._send_next_library_bg_request()
                self._set_msg("棋局库后台生成训练题：%s（0/%d，visits %d）" % (
                    rec.get("name", ""), len(todo), visits))
                return
            except Exception as e:
                self._library_bg_recent.add(rid)
                self._set_msg("棋局库后台准备失败：%s" % e)
                return

    def _send_next_library_bg_request(self):
        ctx = self._library_bg_current
        if not ctx or self._library_bg_pending:
            return
        if self._library_bg_should_pause() or self.guard.pending_count() > 0:
            self.after(500, self._send_next_library_bg_request)
            return
        todo = ctx.get("todo") or []
        done = int(ctx.get("done", 0))
        if done >= len(todo):
            return
        node = todo[done]
        q = self._analysis_query_for(
            ctx.get("tree"), node, ctx.get("rules"), ctx.get("komi"), int(ctx.get("visits", 120)))
        qid = self.client.analyze(q)
        self._library_bg_pending[qid] = {"node": node, "ctx": ctx}

    def _handle_library_bg_result(self, rid, resp):
        item = self._library_bg_pending.pop(rid, None)
        if not item:
            return
        ctx = item.get("ctx") or {}
        if "error" in resp:
            ctx["errors"] = int(ctx.get("errors", 0)) + 1
        else:
            item["node"].analysis = resp
        ctx["done"] = int(ctx.get("done", 0)) + 1
        total = int(ctx.get("total", 0))
        done = int(ctx.get("done", 0))
        name = ctx.get("name", "")
        rec = None
        if done < total:
            self._set_msg("棋局库后台生成训练题：%s（%d/%d）" % (name, done, total))
            self.after(10, self._send_next_library_bg_request)
            return
        record_id = ctx.get("record_id")
        try:
            update_project_snapshot(record_id, ctx.get("tree"), rules=ctx.get("rules"), komi=ctx.get("komi"))
            rec = refresh_training_task(record_id, ctx.get("tree"))
            if rec and rec.get("trainingTask"):
                self._set_msg("棋局库后台已生成训练题：%s" % name)
            else:
                self._set_msg("棋局库后台已分析：%s，暂未形成训练题" % name)
        except Exception as e:
            self._set_msg("棋局库后台保存失败：%s" % e)
        finally:
            if record_id and not (rec and rec.get("trainingTask")):
                self._library_bg_recent.add(record_id)
            self._library_bg_current = None
            self._refresh_library_window()
            self.after(500, self._maybe_prepare_library_training_background)

    # ===================== 棋局库后台训练应手缓存 =====================
    def _start_training_cache_background(self, rec, task, signature, tree=None, data=None):
        try:
            if tree is None:
                tree, data = load_project(rec.get("projectPath"))
            data = data or {}
            rr = ReviewReport(tree)
            start_node = rr.node_at_move(int(task.get("startNodeMove", 0)))
            if start_node is None:
                return False
            rules = data.get("rules", rec.get("rules") or self.rules)
            komi = data.get("komi", rec.get("komi") if rec.get("komi") is not None else self.komi)
            configured = normalize_player_color(task.get("playerColor"))
            user_color = configured if configured in ("B", "W") else color_letter(start_node.board.to_move)
            planned_rounds = max(4, min(20, (int(task.get("targetMoves") or 36) + 1) // 2))
            package = {
                "version": CACHE_VERSION,
                "recordId": rec.get("id"),
                "taskId": task.get("id"),
                "taskPlayerColor": task.get("playerColor", "both"),
                "status": "building",
                "signature": signature,
                "playerColor": user_color,
                "plannedRounds": planned_rounds,
                "preparedRounds": 0,
                "entries": {},
                "updatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            start_moves = start_node.moves_list()
            if start_node.analysis:
                key = self._training_position_key(tree, start_moves, rules, komi)
                put_analysis(package, key, start_node.analysis)
            self._training_cache_bg_current = {
                "record_id": rec.get("id"),
                "name": rec.get("name", ""),
                "tree": tree,
                "rules": rules,
                "komi": komi,
                "visits": int(signature.get("visits") or self._training_visits()),
                "package": package,
                "current_moves": start_moves,
                "current_analysis": start_node.analysis,
                "to_move": color_letter(start_node.board.to_move),
                "user_color": user_color,
                "rounds": 0,
                "planned_rounds": planned_rounds,
                "jobs": [],
                "branches": {},
                "errors": 0,
            }
            self._set_msg("后台预生成训练应手：%s（0/%d 回合）" % (
                rec.get("name", ""), planned_rounds))
            self.after(10, self._advance_training_cache_background)
            return True
        except Exception as e:
            self._set_msg("训练应手缓存准备失败：%s" % e)
            return False

    def _advance_training_cache_background(self):
        ctx = self._training_cache_bg_current
        if not ctx:
            return
        if self._library_bg_should_pause():
            self.after(500, self._advance_training_cache_background)
            return
        if int(ctx.get("rounds", 0)) >= int(ctx.get("planned_rounds", 0)):
            self._finish_training_cache_background("ready")
            return
        analysis = ctx.get("current_analysis")
        if not analysis:
            ctx["jobs"] = [{
                "kind": "position",
                "moves": ctx.get("current_moves") or [],
            }]
            self._send_next_training_cache_bg_request()
            return
        to_move = ctx.get("to_move")
        if to_move != ctx.get("user_color"):
            moves = self._cache_candidate_moves(analysis, 1)
            if not moves:
                self._finish_training_cache_background("partial", "当前局面没有可用 AI 应手")
                return
            next_moves = self._cache_append_move(ctx.get("current_moves"), to_move, moves[0])
            ctx["jobs"] = [{"kind": "forced_ai", "moves": next_moves}]
            self._send_next_training_cache_bg_request()
            return
        candidates = self._cache_candidate_moves(analysis, 3)
        if not candidates:
            self._finish_training_cache_background("partial", "当前局面没有可预生成候选")
            return
        ctx["branches"] = {}
        ctx["jobs"] = []
        for index, move in enumerate(candidates):
            moves = self._cache_append_move(ctx.get("current_moves"), to_move, move)
            ctx["jobs"].append({
                "kind": "user",
                "branch": index,
                "userMove": move,
                "moves": moves,
            })
        self._send_next_training_cache_bg_request()

    def _send_next_training_cache_bg_request(self):
        ctx = self._training_cache_bg_current
        if not ctx or self._training_cache_bg_pending:
            return
        if self._library_bg_should_pause() or self.guard.pending_count() > 0:
            self.after(400, self._send_next_training_cache_bg_request)
            return
        jobs = ctx.get("jobs") or []
        if not jobs:
            self._complete_training_cache_round()
            return
        job = jobs.pop(0)
        q = self._analysis_query_from_moves(
            ctx.get("tree"), job.get("moves") or [], ctx.get("rules"), ctx.get("komi"),
            int(ctx.get("visits") or self._training_visits()))
        qid = self.client.analyze(q)
        self._training_cache_bg_pending[qid] = {"ctx": ctx, "job": job}

    def _handle_training_cache_bg_result(self, rid, resp):
        item = self._training_cache_bg_pending.pop(rid, None)
        if not item:
            return
        ctx = item.get("ctx") or {}
        job = item.get("job") or {}
        if "error" in resp:
            ctx["errors"] = int(ctx.get("errors", 0)) + 1
            if job.get("kind") in ("position", "forced_ai"):
                self._finish_training_cache_background("partial", resp.get("error"))
                return
        else:
            key = self._training_position_key(
                ctx.get("tree"), job.get("moves") or [], ctx.get("rules"), ctx.get("komi"))
            put_analysis(ctx.get("package"), key, resp)
            kind = job.get("kind")
            if kind == "position":
                ctx["current_analysis"] = resp
                self.after(10, self._advance_training_cache_background)
                return
            if kind == "forced_ai":
                ctx["current_moves"] = job.get("moves") or []
                ctx["current_analysis"] = resp
                ctx["to_move"] = ctx.get("user_color")
                self.after(10, self._advance_training_cache_background)
                return
            if kind == "user":
                branch = int(job.get("branch", 0))
                best = self._cache_candidate_moves(resp, 1)
                if best:
                    ai_color = "W" if ctx.get("user_color") == "B" else "B"
                    ai_moves = self._cache_append_move(job.get("moves"), ai_color, best[0])
                    ctx["jobs"].insert(0, {
                        "kind": "ai",
                        "branch": branch,
                        "userMove": job.get("userMove"),
                        "aiMove": best[0],
                        "moves": ai_moves,
                    })
            elif kind == "ai":
                ctx.setdefault("branches", {})[int(job.get("branch", 0))] = {
                    "moves": job.get("moves") or [],
                    "analysis": resp,
                }
        self.after(10, self._send_next_training_cache_bg_request)

    def _complete_training_cache_round(self):
        ctx = self._training_cache_bg_current
        if not ctx:
            return
        branches = ctx.get("branches") or {}
        chosen = branches.get(0)
        if chosen is None and branches:
            chosen = branches[sorted(branches)[0]]
        if not chosen:
            self._finish_training_cache_background("partial", "候选变化均未能完成")
            return
        ctx["current_moves"] = chosen.get("moves") or []
        ctx["current_analysis"] = chosen.get("analysis")
        ctx["to_move"] = ctx.get("user_color")
        ctx["rounds"] = int(ctx.get("rounds", 0)) + 1
        package = ctx.get("package")
        package["preparedRounds"] = ctx["rounds"]
        package["updatedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if ctx["rounds"] % 3 == 0:
            save_training_cache(ctx.get("record_id"), package)
            self._refresh_library_window()
        self._set_msg("后台预生成训练应手：%s（%d/%d 回合，%d 个局面）" % (
            ctx.get("name", ""), ctx["rounds"], ctx.get("planned_rounds"),
            len(package.get("entries") or {})))
        self.after(10, self._advance_training_cache_background)

    def _finish_training_cache_background(self, status, reason=None):
        ctx = self._training_cache_bg_current
        if not ctx:
            return
        package = ctx.get("package")
        package["status"] = status
        package["preparedRounds"] = int(ctx.get("rounds", 0))
        package["updatedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_training_cache(ctx.get("record_id"), package)
        if status == "ready":
            self._set_msg("训练应手已预生成：%s（%d 回合，%d 个局面）" % (
                ctx.get("name", ""), ctx.get("rounds"), len(package.get("entries") or {})))
        else:
            self._set_msg("训练应手已部分生成：%s（%s）" % (
                ctx.get("name", ""), reason or "部分变化不可用"))
        record_id = ctx.get("record_id")
        if record_id:
            self._library_bg_recent.add(record_id)
        self._training_cache_bg_current = None
        self._refresh_library_window()
        self.after(500, self._maybe_prepare_library_training_background)

    # ===================== 阶段训练模式 =====================
    def _clear_training_prefetch(self):
        self._training_prefetch_pending = {}
        self._training_prefetch_cache = {}
        self._training_prefetch_waiters = {}

    def _abandon_training_state(self):
        """切换棋谱 / 重置 / 导入新局时彻底终止阶段训练，避免训练缓存与新树 nid 错配。

        仅置空 _training 不够：预取缓存（_training_prefetch_*）和挂起节点
        （_training_deferred_nodes）用的是旧树的 nid，新树的 nid 计数器从 0 开始，
        可能 stale 误命中。这里一并清掉，保证切换后训练相关状态无残留。
        """
        self._training = None
        self._clear_training_prefetch()
        self._training_deferred_nodes = {}
        self._active_training_cache = None
        self._active_training_cache_dirty = 0

    def _reset_for_new_game(self):
        """换棋谱时的统一状态重置（消除三份不一致清理清单）。

        _load_project_from_path / do_import_sgf / do_reset 都调这个方法，
        保证换棋谱时所有临时模式/缓存/标志一次性清干净，新增清理项只改这一处。
        """
        self._stop_auto_play()
        if self.scoring_mode:
            self.exit_scoring()             # 点目：score_estimator 建在旧 board，必须退出
        self._reset_problem_comparison_state()
        if self._drill_win is not None:
            self._close_problem_drill()     # drill：旧 quiz 不残留到新棋盘
        self._abandon_training_state()      # 训练：彻底清（含 prefetch/deferred/cache）
        self._training_report = None
        self._mistake_review = None
        self._pending_training_record_id = None
        self._reset_batch_state()
        self._reset_pv_state()
        self._current_loss_val = None
        self._current_quality_result = None
        self._clear_candidate_module()
        self._clear_hint()
        self._double_pass_prompted = None
        self._scoring_suggestion_prompted = None

    def _load_active_training_cache(self, task):
        self._active_training_cache = None
        self._active_training_cache_dirty = 0
        if not self._library_record_id:
            return 0
        package = load_training_cache(self._library_record_id)
        signature = self._training_cache_signature(self.rules, self.komi, self._training_visits())
        if package_matches(package, task, signature):
            self._active_training_cache = package
        else:
            self._library_bg_recent.discard(self._library_record_id)
            self._active_training_cache = {
                "version": CACHE_VERSION,
                "recordId": self._library_record_id,
                "taskId": (task or {}).get("id"),
                "taskPlayerColor": (task or {}).get("playerColor", "both"),
                "status": "partial",
                "signature": signature,
                "playerColor": (task or {}).get("playerColor"),
                "plannedRounds": 0,
                "preparedRounds": 0,
                "entries": {},
                "updatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        return len(self._active_training_cache.get("entries") or {})

    def _cached_training_analysis(self, node):
        package = self._active_training_cache
        if not package or node is None:
            return None
        key = self._training_position_key(
            self.tree, node.moves_list(), self.rules, self.komi)
        return (package.get("entries") or {}).get(key)

    def _apply_cached_training_analysis(self, node):
        if node is None or node.analysis is not None:
            return bool(node and node.analysis)
        cached = self._cached_training_analysis(node)
        if not cached:
            return False
        node.analysis = cached
        return True

    def _store_active_training_analysis(self, node=None, resp=None, moves=None):
        package = self._active_training_cache
        if not package or not self._library_record_id or not resp or "error" in resp:
            return False
        if moves is None:
            if node is None:
                return False
            moves = node.moves_list()
        key = self._training_position_key(self.tree, moves, self.rules, self.komi)
        if not put_analysis(package, key, resp):
            return False
        package["updatedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._active_training_cache_dirty += 1
        if self._active_training_cache_dirty >= 6:
            self._flush_active_training_cache()
        return True

    def _flush_active_training_cache(self):
        if (not self._active_training_cache or not self._library_record_id
                or not self._active_training_cache_dirty):
            return
        save_training_cache(self._library_record_id, self._active_training_cache)
        self._active_training_cache_dirty = 0

    def _training_node_key(self, node):
        if node is None or node.parent is None or node.move is None:
            return None
        color, coord = node.move
        move = "pass" if coord is None else xy_to_point(coord[0], coord[1], self.size)
        return (node.parent.nid, color, move.lower())

    def _training_prefetch_key(self, parent, move):
        if parent is None:
            return None
        color = color_letter(parent.board.to_move)
        return (parent.nid, color, str(move or "pass").lower())

    def _cache_training_prefetch(self, key, resp):
        if not key or not resp or "error" in resp:
            return
        self._training_prefetch_cache[key] = resp
        if len(self._training_prefetch_cache) > 96:
            for old_key in list(self._training_prefetch_cache.keys())[:32]:
                self._training_prefetch_cache.pop(old_key, None)

    def _consume_training_prefetch(self, node):
        if self._apply_cached_training_analysis(node):
            self._set_msg("训练中：已命中棋局库预生成应手，AI 正在落子…")
            self.after(10, lambda n=node: self._training_play_ai_reply(n))
            return True
        key = self._training_node_key(node)
        if not key:
            return False
        cached = self._training_prefetch_cache.pop(key, None)
        if cached:
            node.analysis = cached
            self._set_msg("训练中：已命中预热缓存，AI 正在应手…")
            self.after(10, lambda n=node: self._training_play_ai_reply(n))
            return True
        if any(item.get("key") == key for item in self._training_prefetch_pending.values()):
            self._training_prefetch_waiters[key] = node
            self._set_msg("训练中：等待预热分析返回…")
            return True
        return False

    def _schedule_training_prefetch(self, delay=120):
        tr = self._training
        if not tr or not tr.get("active") or tr.get("finished"):
            return
        self.after(delay, self._training_prefetch_user_moves)

    def _training_prefetch_user_moves(self, limit=4):
        tr = self._training
        if not tr or not tr.get("active") or tr.get("finished") or tr.get("awaiting") != "user":
            return
        if not (self.client and self.client.is_alive() and self.client.ready):
            return
        if self.guard.pending_count() > 0:
            self.after(400, self._training_prefetch_user_moves)
            return
        parent = self.tree.current
        if not parent.analysis:
            return
        base_moves = parent.moves_list()
        sent = 0
        for mi in ReviewReport._move_infos(parent)[:8]:
            mv = mi.get("move") or "pass"
            if not mv:
                continue
            if str(mv).lower() != "pass":
                try:
                    point_to_xy(mv, self.size)
                except Exception:
                    continue
            key = self._training_prefetch_key(parent, mv)
            if not key or key in self._training_prefetch_cache:
                continue
            candidate_moves = base_moves + [[key[1], "pass" if key[2] == "pass" else mv]]
            package = self._active_training_cache or {}
            persistent_key = self._training_position_key(
                self.tree, candidate_moves, self.rules, self.komi)
            if persistent_key in (package.get("entries") or {}):
                continue
            if any(item.get("key") == key for item in self._training_prefetch_pending.values()):
                continue
            q = self._analysis_query_from_moves(
                self.tree, candidate_moves,
                self.rules, self.komi, self._training_visits())
            qid = self.client.analyze(q)
            self._training_prefetch_pending[qid] = {
                "key": key,
                "moves": candidate_moves,
            }
            sent += 1
            if sent >= limit:
                break

    def _handle_training_prefetch_result(self, rid, resp):
        item = self._training_prefetch_pending.pop(rid, None)
        if not item:
            return
        key = item.get("key")
        if not key or "error" in resp:
            return
        self._store_active_training_analysis(resp=resp, moves=item.get("moves"))
        node = self._training_prefetch_waiters.pop(key, None)
        if node is not None and node.analysis is None:
            node.analysis = resp
            if node is self.tree.current:
                self._apply_analysis_result(node, resp)
            return
        self._cache_training_prefetch(key, resp)

    def _ensure_training_task(self):
        """当前棋局来自棋局库时，自动刷新最差阶段训练题。"""
        if not self._library_record_id:
            return None
        rec = refresh_training_task(self._library_record_id, self.tree)
        if rec:
            self._library_bg_recent.discard(self._library_record_id)
            self._refresh_library_window()
        return rec

    def _maybe_start_pending_training(self, rec=None):
        """若用户已点训练且自动分析刚完成，则直接进入训练。"""
        rid = self._pending_training_record_id
        if not rid or rid != self._library_record_id:
            return False
        rec = rec or self._ensure_training_task()
        task = (rec or {}).get("trainingTask")
        if not task:
            return False
        self._pending_training_record_id = None
        self._start_stage_training(task)
        return True

    def _prepare_training_after_auto_analysis(self, attempts=240):
        """训练入口的自动准备：启动引擎、分析整盘、生成题，完成后进训练。"""
        rid = self._pending_training_record_id
        if not rid or rid != self._library_record_id:
            return
        rec = self._ensure_training_task()
        if self._maybe_start_pending_training(rec):
            return
        if self._batch_target_nids:
            self._set_msg("正在自动分析整盘，完成后会直接进入阶段训练…")
            return
        if not (self.client and self.client.is_alive()):
            self._start_katago(quiet=True)
        if self.client and self.client.is_alive() and self.client.ready:
            self.analyze_mainline(training=True)   # 训练准备用快速 visits，不再卡整盘高 visits
            if self._batch_target_nids:
                self._set_msg("正在快速分析（训练用），完成后直接进入阶段训练…")
                return
            rec = self._ensure_training_task()
            if self._maybe_start_pending_training(rec):
                return
            self._pending_training_record_id = None
            messagebox.showinfo(
                "暂无训练题",
                "这盘棋已自动检查，但暂未生成可训练的问题阶段。通常是棋局过短，或主线分析数据还不足。"
            )
            return
        if attempts <= 0:
            self._pending_training_record_id = None
            messagebox.showinfo("等待 KataGo", "KataGo 还没有就绪，暂时无法自动生成训练题。请检查引擎/模型设置。")
            return
        self._set_msg("正在启动/等待 KataGo，准备自动分析并生成阶段训练题；OpenCL 首次启动可能需要调优…")
        self.after(1000, lambda: self._prepare_training_after_auto_analysis(attempts - 1))

    def _selected_library_record(self):
        if self._lib_tv is None:
            return None
        sel = self._lib_tv.selection()
        if not sel:
            return None
        return self._lib_map.get(sel[0])

    def _start_training_for_record(self):
        rec = self._selected_library_record()
        if not rec:
            self._set_msg("请先在棋局库中选中一盘棋")
            return
        path = rec.get("projectPath")
        if not path or not os.path.exists(path):
            messagebox.showerror("训练失败", "项目快照不存在：%s" % path)
            return
        if self._library_record_id != rec.get("id"):
            self._load_project_from_path(path, rec.get("name", ""), library_record_id=rec.get("id"))
            rec = refresh_training_task(rec.get("id"), self.tree) or rec
        if not rec.get("trainingTask"):
            rec = refresh_training_task(rec.get("id"), self.tree) or rec
        task = rec.get("trainingTask")
        if not task:
            self._pending_training_record_id = rec.get("id")
            self._set_msg("暂无训练题，已开始自动分析整盘；完成后会直接进入阶段训练…")
            self.after(10, self._prepare_training_after_auto_analysis)
            return
        self._start_stage_training(task)

    def _refresh_training_feedback(self):
        """训练模式逐手即时反馈：当前用户落子节点算 quality 并画等级环 + 目损。

        覆写 _update_review_state 对分支节点的清空（训练分支 on_main=False 时
        _current_quality_result 被置 None）——训练中用户每一手都要立刻看到好坏，
        只是这些评价不进复盘问题表（_render_review 仅统计主线）。
        """
        tr = self._training
        if not (tr and tr.get("active") and not tr.get("finished")):
            return
        n = self.tree.current
        if n is None or n.parent is None or n.move is None:
            self.redraw()
            return
        if n.move[0] != tr.get("user_color") or n.analysis is None:
            # AI 应手节点 / 用户手尚待分析：不画评价环，保留上一手反馈信息
            self._current_quality_result = None
            self._current_loss_val = None
            self.redraw()
            return
        rr = ReviewReport(self.tree)
        qr = rr.move_quality_for_node(
            n, stage=tr.get("phase") or "middle",
            visits=self._training_visits())
        self._current_quality_result = qr
        self._current_loss_val = qr.score_loss if qr.score_loss is not None else None
        coord = "pass" if n.move[1] is None else xy_to_point(
            n.move[1][0], n.move[1][1], self.size)
        self._training_last_feedback = {
            "move_no": n.depth,
            "coord": coord,
            "loss": qr.score_loss,
            "label": qr.quality_label,
            "key": qr.quality_key,
        }
        self.redraw()

    def _start_stage_training(self, task, retry_count=0):
        if self.scoring_mode:
            self._set_msg("请先退出点目模式再开始训练")
            return
        if self._drill_active():
            self._set_msg("请先关闭问题手训练再开始训练")
            return
        self._stop_auto_play()
        rr = ReviewReport(self.tree)
        start_node = rr.node_at_move(int(task.get("startNodeMove", 0)))
        if start_node is None:
            self._set_msg("训练起点不存在，无法开始")
            return
        self.tree.current = start_node
        configured_color = normalize_player_color(task.get("playerColor"))
        user_color = configured_color if configured_color in ("B", "W") else color_letter(self.tree.current.board.to_move)
        target = int(task.get("targetMoves") or 36)
        phase = task.get("phase") or "middle"
        start_move = int(task.get("startMove") or 1)
        end_move = int(task.get("endMove") or (start_move + target - 1))
        original_quality = []
        for result in rr.move_quality_results(
                visits=int(self.cfg.get("max_visits", 200)),
                include_unknown=True):
            if not (start_move <= result.move_no <= end_move):
                continue
            if result.color != user_color:
                continue
            original_node = rr.node_at_move(result.move_no)
            if original_node is not None and original_node.parent is not None:
                result.position_key = self._training_position_key(
                    self.tree, original_node.parent.moves_list(),
                    self.rules, self.komi)
            result.source_move_no = result.move_no
            original_quality.append(result)
        self._training = {
            "active": True,
            "task": task,
            "start_node": start_node,
            "user_color": user_color,
            "target_moves": max(8, min(40, target)),
            "nodes": [],
            "ai_playing": False,
            "finished": False,
            "awaiting": "user",
            "phase": phase,
            "source_game_id": self._library_record_id,
            "original_quality": original_quality,
            "attempt_steps": [],
            "hint_used_count": 0,
            "retry_count": int(retry_count or 0),
            "current_turn_hint_used": False,
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._training_report = None
        self._clear_training_prefetch()
        cached_positions = self._load_active_training_cache(task)
        self._apply_cached_training_analysis(start_node)
        self._after_navigate()
        cache_text = "，已载入 %d 个预生成局面" % cached_positions if cached_positions else ""
        self._set_msg("阶段训练开始%s：%s 请从当前局面重新下。" % (
            cache_text, describe_training_task(task)))
        self._training_last_feedback = None
        self._update_training_controls()
        self.after(20, self._training_drive_to_user_turn)

    def _training_after_user_move(self, node):
        tr = self._training
        if not tr or not tr.get("active") or tr.get("finished") or tr.get("ai_playing"):
            return
        parent = node.parent
        step_id = len(tr.get("attempt_steps") or []) + 1
        original = tr.get("original_quality") or []
        source_move_no = (
            original[step_id - 1].move_no
            if step_id - 1 < len(original) else None)
        tr.setdefault("attempt_steps", []).append({
            "step_id": step_id,
            "source_move_no": source_move_no,
            "position_key_before": self._training_position_key(
                self.tree, parent.moves_list(), self.rules, self.komi),
            "node_nid": node.nid,
            "played_move": (
                "pass" if node.move[1] is None
                else xy_to_point(node.move[1][0], node.move[1][1], self.size)),
            "hint_used": bool(tr.get("current_turn_hint_used")),
        })
        tr["current_turn_hint_used"] = False
        tr["nodes"].append(node)
        tr["awaiting"] = "user_analysis"
        if node.analysis is not None:
            self._store_active_training_analysis(node=node, resp=node.analysis)
        if node.analysis is None:
            if self._consume_training_prefetch(node):
                return
            self._request_analysis(node, training=True)
            self._set_msg("训练中：等待 AI 评价你的这手棋…")
        else:
            self._refresh_training_feedback()
            self.after(10, lambda n=node: self._training_play_ai_reply(n))

    def _training_on_analysis(self, node):
        tr = self._training
        if not tr or not tr.get("active") or tr.get("finished"):
            return
        if tr.get("finishing"):
            self._training_maybe_finish()
            return
        if tr.get("awaiting") == "user_analysis" and node is self.tree.current:
            self._refresh_training_feedback()   # 先画用户手评价环再让 AI 应手
            self._training_play_ai_reply(node)
        elif tr.get("awaiting") == "ai_analysis" and node in tr.get("nodes", []):
            tr["awaiting"] = "user"
            self._training_maybe_finish()
            self._schedule_training_prefetch()
        elif tr.get("awaiting") == "ai_analysis" and node is self.tree.current:
            self._training_drive_to_user_turn()

    def _training_best_move(self, node):
        mis = ReviewReport._move_infos(node)
        if not mis:
            return None
        return mis[0].get("move") or "pass"

    def _training_play_ai_reply(self, user_node):
        tr = self._training
        if not tr or not tr.get("active") or tr.get("finished"):
            return
        if user_node is not self.tree.current:
            return
        self._training_play_ai_move(user_node)

    def _training_play_ai_move(self, source_node=None):
        tr = self._training
        if not tr or not tr.get("active") or tr.get("finished"):
            return
        source_node = source_node or self.tree.current
        mv = self._training_best_move(source_node)
        if not mv:
            self._set_msg("训练中：当前局面没有 AI 候选手，训练暂停。")
            tr["awaiting"] = "user"
            return
        tr["ai_playing"] = True
        try:
            if str(mv).lower() == "pass":
                ok, reason = self.tree.play_pass()
            else:
                x, y = point_to_xy(mv, self.size)
                ok, reason = self.tree.play(x, y)
        except Exception as e:
            ok, reason = False, str(e)
        finally:
            tr["ai_playing"] = False
        if not ok:
            self._set_msg("训练中：AI 应手失败：%s" % reason)
            tr["awaiting"] = "user"
            return
        ai_node = self.tree.current
        tr["nodes"].append(ai_node)
        tr["awaiting"] = "ai_analysis"
        self._apply_cached_training_analysis(ai_node)
        self._after_navigate()
        if ai_node.analysis is None:
            self._request_analysis(ai_node, training=True)
        else:
            tr["awaiting"] = "user"
            self._schedule_training_prefetch()
        self._set_msg("训练中：AI 已应手 %s，轮到你继续。" % mv)
        self._training_maybe_finish()

    def _training_drive_to_user_turn(self):
        tr = self._training
        if not tr or not tr.get("active") or tr.get("finished"):
            return
        user_color = normalize_player_color(tr.get("user_color"))
        if user_color not in ("B", "W"):
            tr["awaiting"] = "user"
            self._schedule_training_prefetch()
            return
        if color_letter(self.tree.current.board.to_move) == user_color:
            tr["awaiting"] = "user"
            self._schedule_training_prefetch()
            return
        tr["awaiting"] = "ai_analysis"
        node = self.tree.current
        self._apply_cached_training_analysis(node)
        if node.analysis is None:
            self._request_analysis(node, training=True)
            self._set_msg("训练中：先由 AI 补到你的回合…")
            return
        self._training_play_ai_move(node)

    def _training_maybe_finish(self):
        tr = self._training
        if not tr or not tr.get("active") or tr.get("finished"):
            return
        if len(tr.get("nodes", [])) < int(tr.get("target_moves", 36)):
            return
        for node in tr["nodes"]:
            self._apply_cached_training_analysis(node)
        pending = [n for n in tr["nodes"] if n.analysis is None]
        if pending:
            tr["finishing"] = True
            for n in pending:
                if not self.guard.has_pending(n):
                    self._request_analysis(n, training=True)
            self._set_msg("训练已到目标手数，正在补齐评价…")
            return
        tr["finishing"] = False
        self._finish_training()

    def _finish_training(self):
        tr = self._training
        if not tr or tr.get("finished"):
            return
        rr = ReviewReport(self.tree)
        evals = [rr.eval_node(n) for n in tr.get("nodes", []) if n.parent is not None and n.move is not None]
        report = grade_training_session(tr.get("task") or {}, evals, tr.get("user_color", "B"))
        steps_by_nid = {
            item.get("node_nid"): item
            for item in (tr.get("attempt_steps") or [])
        }
        training_quality = []
        self._training_report_nodes = {}
        for node in tr.get("nodes", []):
            if not node.move or node.move[0] != tr.get("user_color"):
                continue
            result = rr.move_quality_for_node(
                node, stage=tr.get("phase") or "middle",
                visits=self._training_visits())
            step = steps_by_nid.get(node.nid) or {}
            result.position_key = step.get("position_key_before")
            result.source_move_no = step.get("source_move_no")
            training_quality.append(result)
            self._training_report_nodes[result.move_no] = node
        detailed = analyze_training(
            tr.get("original_quality") or [],
            training_quality,
            phase=tr.get("phase") or "middle",
            task_id=(tr.get("task") or {}).get("id"),
            source_game_id=tr.get("source_game_id"),
            hint_used_count=int(tr.get("hint_used_count") or 0),
            retry_count=int(tr.get("retry_count") or 0),
        )
        detailed_dict = detailed.to_dict()
        report["trainingAnalysis"] = detailed_dict
        report["training_analysis"] = detailed_dict
        report["trainingScore"] = detailed.training_score
        report["trainingLabel"] = detailed.training_label
        report["hintUsedCount"] = detailed.hint_used_count
        report["retryCount"] = detailed.retry_count
        report["finishedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if detailed.review_recommendations:
            report["summary"] = "\n".join(detailed.review_recommendations)
        tr["finished"] = True
        tr["active"] = False
        self._flush_active_training_cache()
        self._active_training_cache = None
        self._clear_training_prefetch()
        self._training_report = report
        if self._library_record_id:
            # 闭环回写：训练中重复犯错的手送回错题本重练，已改善的手标记掌握
            outcomes = []
            for comp in detailed.repeated_errors:
                if comp.original_move_no is not None:
                    outcomes.append((comp.original_move_no, comp.color, "again"))
            for comp in detailed.improved_moves:
                if comp.original_move_no is not None:
                    outcomes.append((comp.original_move_no, comp.color, "good"))
            if outcomes:
                apply_training_outcomes(self._library_record_id, outcomes)
            append_training_session(self._library_record_id, report)
            update_project_snapshot(self._library_record_id, self.tree, rules=self.rules, komi=self.komi)
            self._refresh_library_window()
        self._show_training_report(report)
        self._update_training_controls()

    def _show_training_report(self, report):
        """可交互训练报告：摘要 + 问题类型变化 + 逐手对比表（双击跳转）+ 建议。

        取代原来的 disabled Text 弹窗——把 training_analysis 已算好并落盘的富字段
        (comparisons / recommended_review_positions / problem_tag_changes) 真正渲染出来。
        """
        detailed = report.get("trainingAnalysis") or report.get("training_analysis") or {}

        def _num(v):
            return "—" if v is None else "%.2f" % v

        win = tk.Toplevel(self)
        self._prepare_child_window(win, "训练分析", 760, 640, minsize=(620, 480))
        frame = tk.Frame(win, bg=COLORS["card"])
        frame.pack(fill="both", expand=True, padx=12, pady=12)

        # ---- 顶部摘要 ----
        score = detailed.get("training_score", 0)
        label = detailed.get("training_label", "样本不足")
        score_col = {"优秀": COLORS.get("green"), "明显改善": COLORS.get("green"),
                     "基本合格": COLORS.get("accent"),
                     "仍需复习": COLORS.get("amber"), "建议重练": COLORS.get("red"),
                     "样本不足": COLORS["subtext"]}.get(label, COLORS["text"])
        summary = tk.Frame(frame, bg=COLORS["card"])
        summary.pack(fill="x", pady=(0, 6))
        tk.Label(summary, text="本次训练  %d 分 · %s" % (score, label),
                 font=FONTS["title"], fg=score_col,
                 bg=COLORS["card"]).pack(anchor="w")
        tk.Label(
            summary,
            text=("平均目损  原实战 %s → 本次 %s   改善 %s        "
                  "恶手 %s→%s  不佳 %s→%s        建议复习 %s 天") % (
                      _num(detailed.get("original_avg_score_loss")),
                      _num(detailed.get("training_avg_score_loss")),
                      _num(detailed.get("improvement_score_loss")),
                      detailed.get("original_blunder_count", 0),
                      detailed.get("training_blunder_count", 0),
                      detailed.get("original_inaccuracy_count", 0),
                      detailed.get("training_inaccuracy_count", 0),
                      detailed.get("suggested_review_after_days", "—")),
            font=FONTS["ui"], fg=COLORS["text"], bg=COLORS["card"],
            justify=tk.LEFT).pack(anchor="w", pady=(2, 0))

        # ---- 问题类型变化 ----
        changes = detailed.get("problem_tag_changes") or {}
        if changes:
            chips = tk.Frame(frame, bg=COLORS["card"])
            chips.pack(fill="x", pady=(0, 6))
            tk.Label(chips, text="问题类型变化：", font=FONTS["ui"],
                     fg=COLORS["subtext"], bg=COLORS["card"]).pack(side=tk.LEFT)
            for tag, vals in sorted(changes.items(), key=lambda kv: kv[1][2]):
                orig_n, train_n, delta = vals
                if delta == 0:
                    continue
                name = PROBLEM_TAGS.get(tag, tag)
                col = COLORS.get("green") if delta < 0 else COLORS.get("red")
                sign = "+" if delta > 0 else ""
                tk.Label(chips, text="%s %s→%s (%s%s)" % (name, orig_n, train_n, sign, delta),
                         font=FONTS["small"], fg=col,
                         bg=COLORS["card"]).pack(side=tk.LEFT, padx=(0, 10))

        # ---- 逐手对比表 ----
        table_wrap = tk.Frame(frame, bg=COLORS["card"])
        table_wrap.pack(fill="both", expand=True)
        tk.Label(table_wrap, text="逐手对比（双击跳到该训练手；标 ★ 为重点复盘位）",
                 font=FONTS["ui"], fg=COLORS["subtext"],
                 bg=COLORS["card"]).pack(anchor="w", pady=(0, 2))
        cols = ("move", "side", "played", "orig_q", "train_q", "loss", "improve", "cat")
        tv = ttk.Treeview(table_wrap, columns=cols, show="headings", height=14)
        headers = {"move": "手", "side": "方", "played": "实战下法",
                   "orig_q": "原评级", "train_q": "本次", "loss": "本次目损",
                   "improve": "改善", "cat": "分类"}
        for c in cols:
            tv.heading(c, text=headers[c])
            tv.column(c, width=72, anchor=tk.CENTER)
        tv.column("played", width=92, anchor=tk.W)
        tv.column("cat", width=78)
        review_move_nos = {
            int(c.get("move_no") or 0)
            for c in (detailed.get("recommended_review_positions") or [])}
        comparisons = sorted(
            detailed.get("comparisons") or [],
            key=lambda c: (c.get("training_score_loss") is None,
                           -(c.get("training_score_loss") or 0)))
        cat_cn = {"improved": "已改善", "repeated_error": "重复错误",
                  "new_error": "新错误", "neutral": "—"}
        self._training_report_tv_map = {}
        for comp in comparisons:
            mn = int(comp.get("move_no") or 0)
            side = "黑" if comp.get("color") == "B" else "白"
            star = "★ " if mn in review_move_nos else ""
            cat = comp.get("category") or "neutral"
            cat_tag = cat if cat in ("improved", "repeated_error", "new_error") else ""
            iid = tv.insert("", "end", values=(
                "%s%d" % (star, mn), side, comp.get("played_move") or "?",
                self._quality_cn(comp.get("original_quality")),
                self._quality_cn(comp.get("training_quality")),
                _num(comp.get("training_score_loss")),
                _num(comp.get("score_loss_improvement")),
                cat_cn.get(cat, cat)), tags=(cat_tag,) if cat_tag else ())
            self._training_report_tv_map[iid] = mn
        tv.tagconfigure("improved", foreground=COLORS.get("green"))
        tv.tagconfigure("repeated_error", foreground=COLORS.get("red"))
        tv.tagconfigure("new_error", foreground=COLORS.get("amber"))
        vsb = ttk.Scrollbar(table_wrap, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=vsb.set)
        tv.pack(side=tk.LEFT, fill="both", expand=True)
        vsb.pack(side=tk.RIGHT, fill="y")
        self._training_report_tv = tv
        tv.bind("<Double-Button-1>", self._on_training_report_double_click)

        # ---- 建议 ----
        recs = detailed.get("review_recommendations") or []
        if recs:
            tk.Label(frame, text="建议：" + "  ".join(recs), font=FONTS["small"],
                     fg=COLORS["subtext"], bg=COLORS["card"], wraplength=720,
                     justify=tk.LEFT).pack(anchor="w", pady=(6, 0))
        btns = self._dialog_button_bar(win)
        self._make_button(btns, "关闭", win.destroy, variant="default").pack(side=tk.RIGHT, padx=8)
        self._set_msg(report.get("summary", "训练完成。"))

    @staticmethod
    def _quality_cn(key):
        return {"best": "最佳", "good": "好手", "normal": "一般",
                "inaccuracy": "不佳", "blunder": "恶手",
                "unknown": "未评价", None: "—"}.get(key, key or "—")

    def _on_training_report_double_click(self, event=None):
        """报告表双击：跳到对应训练手（节点仍保留在树中，训练结束后可回看）。"""
        tv = getattr(self, "_training_report_tv", None)
        if tv is None:
            return
        sel = tv.selection()
        if not sel:
            return
        mn = self._training_report_tv_map.get(sel[0])
        node = (getattr(self, "_training_report_nodes", None) or {}).get(mn)
        if node is None:
            return
        if self._block_jump("跳转"):
            return
        self.tree.current = node
        self._after_navigate()
        self._set_msg("已跳到训练第 %d 手，可在棋盘结合右侧分析查看差异" % mn)

    @staticmethod
    def _display_number(value):
        return "—" if value is None else "%.1f" % float(value)

    def training_hint(self):
        """兼容旧训练页入口；实际使用全局提示实现。"""
        self.show_hint()

    def training_finish_now(self):
        tr = self._training
        if not tr or not tr.get("active"):
            self._set_msg("当前没有进行中的阶段训练")
            return
        if not tr.get("nodes"):
            self._set_msg("还没有训练落子，无法评价")
            return
        pending = [n for n in tr["nodes"] if n.analysis is None]
        if pending:
            tr["finishing"] = True
            for n in pending:
                if not self.guard.has_pending(n):
                    self._request_analysis(n, training=True)
            self._set_msg("正在补齐训练评价…")
            return
        self._finish_training()

    def training_restart(self):
        tr = self._training
        task = tr.get("task") if tr else None
        if not task and self._training_report:
            self._set_msg("训练已结束，请从棋谱库重新开始该训练题")
            return
        if not task:
            self._set_msg("当前没有可重来的训练题")
            return
        retry_count = int((tr or {}).get("retry_count") or 0) + 1
        self._start_stage_training(task, retry_count=retry_count)

    def training_show_original(self):
        tr = self._training
        task = tr.get("task") if tr else None
        if not task:
            self._set_msg("当前没有训练题")
            return
        lines = [describe_training_task(task)]
        problems = task.get("topProblems") or []
        if problems:
            lines.append("")
            lines.append("原实战重点问题：")
            for p in problems[:5]:
                lines.append("第 %s 手 %s%s，目损 %.1f，AI 建议 %s" % (
                    p.get("move"), "黑" if p.get("color") == "B" else "白",
                    p.get("coord"), float(p.get("loss") or 0.0), p.get("bestMove")))
        messagebox.showinfo("原实战阶段", "\n".join(lines), parent=self)

    # ===================== 问题手双分支深度对比 =====================
    def _reset_problem_comparison_state(self):
        self._problem_compare_pending = {}
        self._problem_compare_queue = []
        self._review_selected_move_no = None
        self._selected_problem_eval = None
        self._problem_compare_mode = "summary"
        self._problem_branch_overlay = None

    def _deep_comparisons(self):
        if not hasattr(self.tree, "_deep_comparisons"):
            self.tree._deep_comparisons = {}
        return self.tree._deep_comparisons

    def _deep_compare_visits(self):
        base = int(self.cfg.get("max_visits", 200))
        return max(300, min(800, base * 2))

    def _deep_compare_signature(self, visits=None):
        return self._training_cache_signature(
            self.rules, self.komi, visits if visits is not None else self._deep_compare_visits())

    def _comparison_for(self, evaluation):
        if evaluation is None:
            return None
        key = str(evaluation.move_number)
        comparison = self._deep_comparisons().get(key)
        if not comparison:
            return None
        if comparison.get("signature") != self._deep_compare_signature(comparison.get("visits")):
            self._deep_comparisons().pop(key, None)
            return None
        return comparison

    def _problem_comparison_inflight(self, move_number):
        return any(
            (item.get("ctx") or {}).get("move_number") == move_number
            for item in self._problem_compare_pending.values())

    def _ensure_problem_comparison(self, evaluation, auto_start=False):
        if evaluation is None or evaluation.loss is None or evaluation.loss < GRADE_BAD:
            return False
        if self._comparison_for(evaluation):
            return True
        if self._problem_comparison_inflight(evaluation.move_number):
            return False
        if self._problem_compare_pending:
            if not any(
                    item.move_number == evaluation.move_number
                    for item in self._problem_compare_queue):
                self._problem_compare_queue.insert(0, evaluation)
            return False
        if self.scoring_mode or (self._training and self._training.get("active")):
            return False
        if self.client and self.client.is_alive() and self.client.ready:
            return self._start_problem_comparison(evaluation)
        if auto_start and not (self.client and self.client.is_alive()):
            self._start_katago(quiet=True)
        return False

    def _maybe_start_selected_problem_comparison(self):
        if self._problem_compare_pending:
            return
        candidates = []
        if self._selected_problem_eval is not None:
            candidates.append(self._selected_problem_eval)
        candidates.extend(self._problem_compare_queue)
        for evaluation in candidates:
            if self._comparison_for(evaluation):
                continue
            if self._ensure_problem_comparison(evaluation, auto_start=False):
                self._problem_compare_queue = [
                    item for item in self._problem_compare_queue
                    if item.move_number != evaluation.move_number]
                return

    def _start_problem_comparison(self, evaluation):
        rr = ReviewReport(self.tree)
        node = rr.node_at_move(evaluation.move_number)
        if node is None or node.parent is None:
            return False
        visits = self._deep_compare_visits()
        ai_move = evaluation.best_move or "pass"
        actual_moves = node.moves_list()
        ai_moves = list(node.parent.moves_list()) + [[evaluation.color, ai_move]]
        ctx = {
            "move_number": evaluation.move_number,
            "evaluation": evaluation,
            "visits": visits,
            "responses": {},
            "errors": [],
            "done": 0,
        }
        for branch, moves in (("actual", actual_moves), ("ai", ai_moves)):
            query = self._analysis_query_from_moves(
                self.tree, moves, self.rules, self.komi, visits)
            query["includePolicy"] = False
            qid = self.client.analyze(query)
            self._problem_compare_pending[qid] = {
                "ctx": ctx,
                "branch": branch,
            }
        self._set_msg("正在深度比较第 %d 手：实战变化 / AI 变化（%d visits）…" % (
            evaluation.move_number, visits))
        self._show_problem_intent(evaluation, rr, ensure=False)
        return True

    def _handle_problem_compare_result(self, rid, resp):
        item = self._problem_compare_pending.pop(rid, None)
        if not item:
            return
        ctx = item.get("ctx") or {}
        branch = item.get("branch")
        ctx["done"] = int(ctx.get("done", 0)) + 1
        if "error" in resp:
            ctx.setdefault("errors", []).append("%s：%s" % (branch, resp.get("error")))
        else:
            ctx.setdefault("responses", {})[branch] = resp
        if ctx["done"] < 2:
            return
        evaluation = ctx.get("evaluation")
        responses = ctx.get("responses") or {}
        if ctx.get("errors") or not responses.get("actual") or not responses.get("ai"):
            self._set_msg("第 %d 手深度对比失败：%s" % (
                ctx.get("move_number", 0), "；".join(ctx.get("errors") or ["响应不完整"])))
            if (self._selected_problem_eval
                    and self._selected_problem_eval.move_number == evaluation.move_number):
                self._show_problem_intent(evaluation, ReviewReport(self.tree), ensure=False)
            return
        rr = ReviewReport(self.tree)
        total_moves = max(0, len(rr.mainline_nodes()) - 1)
        phase = rr.phase_label(rr.phase_of_move(evaluation.move_number, total_moves))
        comparison = build_branch_comparison(
            evaluation, responses["actual"], responses["ai"],
            phase_label=phase, board_size=self.size, visits=ctx.get("visits"))
        comparison["signature"] = self._deep_compare_signature(ctx.get("visits"))
        self._deep_comparisons()[str(evaluation.move_number)] = comparison
        if self._library_record_id:
            update_project_snapshot(
                self._library_record_id, self.tree, rules=self.rules, komi=self.komi)
            self._refresh_library_window()
        self._set_msg("第 %d 手双分支深度对比已完成" % evaluation.move_number)
        if (self._selected_problem_eval
                and self._selected_problem_eval.move_number == evaluation.move_number):
            self._show_problem_intent(evaluation, rr, ensure=False)

    def _update_review_state(self):
        """导航/分析回流后统一刷新：当前手目损缓存 + 失误榜 + 胜率曲线。

        仅对【主线】当前节点算 loss 并画等级环——评价表/概览只统计主线，
        分支节点不画环（避免环与表语义不一致：分支环在表里查不到、无法跳转）。
        """
        if self._library_record_id:
            self._ensure_profile_identity()
        self._refresh_review_scope_button()
        rr = ReviewReport(self.tree)
        quality_results = rr.move_quality_results(
            visits=int(self.cfg.get("max_visits", 200)),
            include_unknown=True)
        self._quality_by_move = {
            result.move_no: result for result in quality_results
        }
        n = self.tree.current
        on_main = n is not None and any(nd is n for nd in rr.mainline_nodes())
        if n is not None and n.parent is not None and n.move is not None and on_main:
            ev = rr.eval_node(n)
            self._current_loss_val = ev.loss if (ev.analyzed and ev.loss is not None) else None
            self._current_quality_result = self._quality_by_move.get(ev.move_number)
        else:
            self._current_loss_val = None
            self._current_quality_result = None
        self._render_review(rr)
        self._render_rating(rr)
        self._render_profile(rr)
        self._refresh_graph(rr)
        self._refresh_strength_eval(rr)

    def _render_review(self, rr=None):
        """刷新问题棋列表、阶段概览和自动文字分析。"""
        if self._tv_review is None:
            return
        preferred_move_no = self._review_selected_move_no
        self._tv_review.delete(*self._tv_review.get_children())
        self._review_map = {}
        self._problem_eval_map = {}
        if rr is None:
            rr = ReviewReport(self.tree)
        focus_color = self._review_focus_color()
        problem_moves = rr.meaningful_problems(
            n=REVIEW_TOP_N, min_loss=LOSS_THRESHOLD, min_winrate_loss=0.03,
            color=focus_color)
        queued_moves = {item.move_number for item in self._problem_compare_queue}
        for evaluation in [
                item for item in problem_moves if item.loss >= GRADE_BAD][:3]:
            if (not self._comparison_for(evaluation)
                    and not self._problem_comparison_inflight(evaluation.move_number)
                    and evaluation.move_number not in queued_moves):
                self._problem_compare_queue.append(evaluation)
                queued_moves.add(evaluation.move_number)
        timeline_points = []
        for e in problem_moves:
            if e.loss is not None and e.loss >= 1.0:
                timeline_points.append((e.move_number, float(e.loss)))
            side = "黑" if e.color == "B" else "白"
            coord = "pass" if e.is_pass else (e.coord or "?")
            node = rr.node_at_move(e.move_number)
            if node is None:
                continue
            quality = self._quality_by_move.get(e.move_number)
            quality_label = quality.quality_label if quality is not None else "未评价"
            tag_text = "、".join(
                PROBLEM_TAGS.get(tag, tag)
                for tag in ((quality.problem_tags if quality else [])[:2])
            ) or "—"
            # 学习类别（大纲 §58：第82手 弱棋 6.3目）：九类技术错误主类，
            # 证据不足时明确"待分类"而不是猜
            try:
                from taxonomy import category_label, classify_problem
                category_text = category_label(classify_problem(
                    {"problem_tags": (quality.problem_tags if quality else [])}
                )["primary_category"])
            except Exception:
                category_text = "待分类"
            quality_key = quality.quality_key if quality is not None else "unknown"
            row_tag = (
                "bad" if quality_key == "blunder" else
                "inaccuracy" if quality_key == "inaccuracy" else
                "unknown" if quality_key == "unknown" else "")
            iid = self._tv_review.insert("", "end", values=(
                e.move_number, side, coord, quality_label, "%.1f" % e.loss,
                "%.1f%%" % rr.winrate_loss_pct(e), e.best_move,
                category_text, tag_text),
                tags=(row_tag,) if row_tag else ())
            self._review_map[iid] = node
            self._problem_eval_map[iid] = e
        # 学习时间轴数据（V6 §38-43）：目损色杆 + 学习价值紫圈
        if hasattr(self, "timeline"):
            try:
                from learning_store import get_events_by_game
                game_id = str(getattr(self, "_library_record_id", "") or "")
                pri_by_move = {
                    evt.move_no: evt.learning_priority
                    for evt in (get_events_by_game(game_id) if game_id else [])
                    if evt.learning_priority}
            except Exception:
                pri_by_move = {}
            line_nodes = rr.mainline_nodes()
            self.timeline.set_data(
                max(1, len(line_nodes) - 1),
                [{"move": mv, "loss": ls, "priority": pri_by_move.get(mv)}
                 for mv, ls in timeline_points],
                current=self.tree.current.depth)

        if hasattr(self, "lbl_review_summary"):
            phases = []
            for ps in rr.phase_summary(color=focus_color):
                loss = "—" if ps["avg_loss"] is None else "%.1f" % ps["avg_loss"]
                phases.append("%s %s/%s" % (ps["label"], ps["quality"], loss))
            coverage = rr.analysis_coverage(focus_color)
            whole_coverage = rr.analysis_coverage()
            scope = (
                "我方·%s" % ("黑" if focus_color == "B" else "白")
                if focus_color in ("B", "W") else "双方")
            self.lbl_review_summary.config(
                text="%s｜分析覆盖 %d/%d（%.0f%%）｜关键问题 %d 手\n%s" % (
                    scope, coverage["analyzed"], coverage["total"],
                    coverage["percent"], len(problem_moves),
                    "  |  ".join(phases)))
            if hasattr(self, "review_coverage_bar"):
                self.review_coverage_bar.configure(value=coverage["percent"])
            if hasattr(self, "lbl_review_coverage"):
                coverage_text = (
                    "完整 · %.0f%%" % coverage["percent"]
                    if coverage["complete"] else
                    "待补 %d 手 · %.0f%%" % (
                        coverage["missing"], coverage["percent"]))
                self.lbl_review_coverage.config(
                    text=coverage_text,
                    fg=COLORS["green"] if coverage["complete"] else COLORS["amber"])
            # 同步常驻形势卡进度条（所有标签页可见，解决研究模式下进度不可见）
            if hasattr(self, "review_coverage_bar_top"):
                self.review_coverage_bar_top.configure(value=coverage["percent"])
            if hasattr(self, "lbl_review_coverage_top"):
                self.lbl_review_coverage_top.config(
                    text=coverage_text,
                    fg=COLORS["green"] if coverage["complete"] else COLORS["amber"])
            if hasattr(self, "btn_complete_analysis"):
                pending_count = sum(
                    1 for node in rr.mainline_nodes()
                    if self.guard.has_pending(node))
                if pending_count:
                    self.btn_complete_analysis.configure(
                        text="正在分析（%d）" % pending_count,
                        state=tk.DISABLED)
                elif whole_coverage["complete"] or whole_coverage["total"] == 0:
                    self.btn_complete_analysis.configure(
                        text="整盘分析已完整", state=tk.DISABLED)
                else:
                    self.btn_complete_analysis.configure(
                        text="补全整盘（缺%d）" % whole_coverage["missing"],
                        state=tk.NORMAL)
        if hasattr(self, "txt_game_commentary"):
            commentary = rr.game_commentary(
                getattr(self.tree, "_sgf_pb", "黑方"),
                getattr(self.tree, "_sgf_pw", "白方"),
                focus_color=focus_color)
            self.txt_game_commentary.configure(state=tk.NORMAL)
            self.txt_game_commentary.delete("1.0", tk.END)
            self.txt_game_commentary.insert("1.0", commentary)
            self.txt_game_commentary.configure(state=tk.DISABLED)
        current_move_no = None
        if (self.tree.current is not None
                and self.tree.current.parent is not None
                and any(node is self.tree.current for node in rr.mainline_nodes())):
            current_eval = rr.eval_node(self.tree.current)
            if current_eval.move_number in [
                    e.move_number for e in self._problem_eval_map.values()]:
                current_move_no = current_eval.move_number
        target_move_no = current_move_no or preferred_move_no
        selected_iid = next(
            (iid for iid, e in self._problem_eval_map.items()
             if e.move_number == target_move_no), None)
        if selected_iid is None:
            selected_iid = next(
                (iid for iid, e in self._problem_eval_map.items()
                 if e.loss >= GRADE_BAD), None)
        if selected_iid is None and self._problem_eval_map:
            selected_iid = next(iter(self._problem_eval_map))
        if selected_iid:
            self._tv_review.selection_set(selected_iid)
            self._tv_review.focus(selected_iid)
            self._tv_review.see(selected_iid)
            self._on_problem_select()
        else:
            self._review_selected_move_no = None
            self._selected_problem_eval = None
            self._problem_branch_overlay = None
            self.btn_compare_actual.configure(state=tk.DISABLED)
            self.btn_compare_ai.configure(state=tk.DISABLED)
            self._update_problem_position_label()
            self._set_problem_intent_text("暂无达到恶手阈值的意图分析。")

    def _render_rating(self, rr=None):
        """显示排除胜负已定局面的稳健单局表现估计。"""
        if not hasattr(self, "_tv_rating"):
            return
        if rr is None:
            rr = ReviewReport(self.tree)
        self._tv_rating.delete(*self._tv_rating.get_children())
        players = (
            ("B", getattr(self.tree, "_sgf_pb", "黑方")),
            ("W", getattr(self.tree, "_sgf_pw", "白方")),
        )
        for color, name in players:
            s = rr.player_performance(color)
            if s is None:
                values = (name, "—", "—", "—", "—", "0/0", "—")
                self._tv_rating.insert("", "end", values=values)
                continue
            elo = s.get("elo")
            elo_txt = "—" if elo is None else "%d-%d" % (elo[0], elo[1])
            if s["rank"] == "—":
                values = (name, "样本不足", "—", "—", "—",
                          "%d/%d" % (s["rated_moves"], s["moves"]), "—")
            else:
                agree = s.get("agree1")
                agree_txt = "—" if agree is None else "%.0f%%" % (agree * 100)
                values = (
                    name, s["rank_short"], elo_txt, s["rank_range"],
                    agree_txt,
                    "%d/%d" % (s["rated_moves"], s["moves"]), s["confidence"])
            self._tv_rating.insert("", "end", values=values)

    def _render_profile(self, rr=None):
        """长期画像摘要：从当前局 move_quality 评价构建 GameProfileSummary，
        显示质量分布 + 阶段弱点 + 趋势方向。"""
        if not hasattr(self, "lbl_profile"):
            return
        if rr is None:
            rr = ReviewReport(self.tree)
        quality_results = list(self._quality_by_move.values())
        if not quality_results:
            self.lbl_profile.config(text="尚未生成精细评价；完成整局分析后显示画像摘要。")
            return
        # 构建单局摘要
        summary = build_game_profile_summary(
            quality_results,
            game_id=(
                self._library_record_id
                or getattr(self.tree, "_sgf_re", "")
                or str(id(self.tree))),
            game_name=getattr(self.tree, "_sgf_pb", "黑方"),
            profile_side=(
                getattr(self.tree, "_profile_side", "both")
                if getattr(self.tree, "_profile_side", "both") != "unknown"
                else "both"),
            model=os.path.basename(self.model_file or ""),
            visits=int(self.cfg.get("max_visits", 200)),
            analysis_signature=(
                getattr(self.tree, "_analysis_signature", None)
                or self._analysis_signature()))
        # 构建画像（单局，但能出质量分布 + 阶段统计）
        profile = build_profile([{"summary": summary}], user_side="both")
        # 显示摘要
        parts = []
        # 质量分布
        qd = profile.quality_distribution
        qd_parts = []
        label_map = {"best": "最佳", "good": "好手", "normal": "一般",
                     "inaccuracy": "不佳", "blunder": "恶手"}
        for k in ("best", "good", "normal", "inaccuracy", "blunder"):
            cnt = qd.get(k, 0)
            if cnt > 0:
                qd_parts.append("%s%d" % (label_map.get(k, k), cnt))
        if qd_parts:
            parts.append("  ".join(qd_parts))
        # 阶段统计
        for phase_key, phase_label in (("opening", "布局"), ("middle", "中盘"), ("endgame", "官子")):
            ps = getattr(profile, phase_key)
            if ps.moves >= 3:
                parts.append("%s %.1f目/%d手" % (phase_label, ps.avg_score_loss, ps.moves))
        # 弱点
        if profile.weaknesses:
            parts.append("弱：" + profile.weaknesses[0][:30])
        if self._library_record_id:
            profile_cfg = self.cfg.get("profile", {}) or {}
            recent = get_recent_profile_summaries(
                int(profile_cfg.get("profile_window_games", 30) or 30))
            benchmark = compare_game_to_baseline(summary, recent)
            if benchmark.status in ("better", "similar", "worse"):
                label = {
                    "better": "优于",
                    "similar": "接近",
                    "worse": "低于",
                }[benchmark.status]
                parts.append("本局%s个人基线（历史%d盘，%s置信）" % (
                    label, benchmark.prior_games,
                    {"low": "低", "medium": "中", "high": "高"}.get(
                        benchmark.confidence, benchmark.confidence)))
        text = "\n".join(parts) if parts else ""
        self.lbl_profile.config(text=text)

    def _on_review_double_click(self, event):
        """双击失误榜行 → 跳转到该手节点。"""
        sel = self._tv_review.selection()
        if not sel:
            return
        evaluation = self._problem_eval_map.get(sel[0])
        if evaluation is not None:
            self._review_selected_move_no = evaluation.move_number
        node = self._review_map.get(sel[0])
        if node is None or node is self.tree.current:
            return
        if self._block_jump("跳转"):
            return
        self.tree.current = node
        self._after_navigate()

    def _navigate_problem(self, delta):
        """在高价值问题手之间循环跳转，同时刷新解释和棋盘。"""
        rows = list(self._tv_review.get_children()) if self._tv_review else []
        if not rows:
            self._set_msg("当前还没有可导航的问题手")
            return
        selected = self._tv_review.selection()
        current = rows.index(selected[0]) if selected and selected[0] in rows else (-1 if delta > 0 else 0)
        iid = rows[(current + delta) % len(rows)]
        self._tv_review.selection_set(iid)
        self._tv_review.focus(iid)
        self._tv_review.see(iid)
        evaluation = self._problem_eval_map.get(iid)
        if evaluation is not None:
            self._review_selected_move_no = evaluation.move_number
        node = self._review_map.get(iid)
        if node is not None and not self.scoring_mode:
            self.tree.current = node
            self._after_navigate()
        self._on_problem_select()

    def _update_problem_position_label(self, evaluation=None):
        """显示当前问题在本局问题队列中的位置，帮助连续复盘不迷路。"""
        if not hasattr(self, "lbl_problem_position"):
            return
        rows = list(self._tv_review.get_children()) if self._tv_review else []
        if not rows:
            self.lbl_problem_position.config(text="暂无问题手")
            return
        selected = self._tv_review.selection()
        iid = selected[0] if selected and selected[0] in rows else None
        if evaluation is None and iid is not None:
            evaluation = self._problem_eval_map.get(iid)
        if evaluation is None:
            self.lbl_problem_position.config(text="共 %d 个关键问题" % len(rows))
            return
        index = rows.index(iid) + 1 if iid in rows else 1
        self.lbl_problem_position.config(
            text="问题 %d/%d · 第%d手 · 目损 %.1f · AI建议 %s" % (
                index, len(rows), evaluation.move_number,
                evaluation.loss, evaluation.best_move or "—"),
            fg=COLORS["red"] if evaluation.loss >= GRADE_BAD else COLORS["amber"])

    def _on_problem_select(self, _event=None):
        sel = self._tv_review.selection()
        if not sel:
            return
        evaluation = self._problem_eval_map.get(sel[0])
        if evaluation is None:
            return
        self._review_selected_move_no = evaluation.move_number
        self._update_problem_position_label(evaluation)
        quality = self._quality_by_move.get(evaluation.move_number)
        if evaluation.loss < GRADE_BAD:
            self._selected_problem_eval = None
            self._problem_branch_overlay = None
            self.btn_compare_actual.configure(state=tk.DISABLED)
            self.btn_compare_ai.configure(state=tk.DISABLED)
            detail = self._format_quality_detail(quality)
            self._set_problem_intent_text(
                detail + "\n\n该手未达到恶手阈值，因此不生成双分支意图分析。")
            return
        self._problem_compare_mode = "summary"
        self._problem_branch_overlay = None
        self.redraw()
        self._show_problem_intent(evaluation, ReviewReport(self.tree), auto_start=True)

    def _show_problem_intent(self, evaluation, rr, auto_start=False, ensure=True):
        self._selected_problem_eval = evaluation
        quality_text = self._format_quality_detail(
            self._quality_by_move.get(evaluation.move_number))
        intent = rr.bad_move_intent(evaluation)
        if not intent:
            self.btn_compare_actual.configure(state=tk.DISABLED)
            self.btn_compare_ai.configure(state=tk.DISABLED)
            self._set_problem_intent_text(
                quality_text + "\n\n暂无可用的恶手意图分析。")
            return
        comparison = self._comparison_for(evaluation)
        ready_state = tk.NORMAL if comparison else tk.DISABLED
        self.btn_compare_actual.configure(state=ready_state)
        self.btn_compare_ai.configure(state=ready_state)
        side = "黑" if intent["color"] == "B" else "白"
        quality = self._quality_by_move.get(evaluation.move_number)
        explanation = build_evidence_explanation(
            evaluation, intent=intent, quality=quality, comparison=comparison)
        explanation_text = format_evidence_explanation(explanation)
        intent_text = (
            "实战意图（%s）：%s\n"
            "AI意图（%s）：%s" % (
                intent.get("actualMove", "?"), intent.get("actualIntent", "—"),
                intent.get("aiMove", "?"), intent.get("aiIntent", "—")))
        if comparison and self._problem_compare_mode in ("actual", "ai"):
            branch = comparison[self._problem_compare_mode]
            title = "实战变化" if self._problem_compare_mode == "actual" else "AI 推荐变化"
            text = quality_text + "\n\n" + intent_text + "\n\n" + (
                "%s：首着 %s\n"
                "预测：%s方胜率 %.1f%%，局面价值 %+.1f 目，稳定控制点 %d\n"
                "主变：%s\n\n"
                "对比结论：%s"
                % (title,
                   comparison["actualMove"] if self._problem_compare_mode == "actual"
                   else comparison["aiMove"],
                   side, float(branch.get("winrate") or 0.0) * 100,
                   float(branch.get("score") or 0.0),
                   int(branch.get("controlPoints") or 0),
                   " → ".join(branch.get("pv") or []) or "—",
                   comparison.get("summary", "")))
        elif comparison:
            text = quality_text + "\n\n" + intent_text + "\n\n" + (
                "%s\n\n"
                "实战主变：%s\n"
                "AI主变：%s"
                % (explanation_text,
                   " → ".join((comparison.get("actual") or {}).get("pv") or []),
                   " → ".join((comparison.get("ai") or {}).get("pv") or [])))
        else:
            if self._problem_comparison_inflight(evaluation.move_number):
                status = "正在计算实战与 AI 两条变化…"
            elif self._problem_compare_pending:
                status = "已加入深度分析队列。"
            else:
                status = "等待 KataGo 就绪后自动生成。"
            text = "%s\n\n%s\n\n%s\n\n深算状态：%s" % (
                quality_text, intent_text, explanation_text, status)
        self._set_problem_intent_text(text)
        if ensure:
            self._ensure_problem_comparison(evaluation, auto_start=auto_start)

    @staticmethod
    def _format_quality_detail(result):
        """问题手选中后的结构化 AI 参考评价。"""
        if result is None:
            return "AI 参考评价：尚未生成精细评价。"
        confidence = {
            "high": "高", "medium": "中", "low": "低", "unknown": "未知"
        }.get(result.confidence, result.confidence)
        ai_rank = (
            "第 %d 选" % result.ai_rank
            if result.ai_rank is not None else "未进入返回候选")
        tags = "、".join(
            PROBLEM_TAGS.get(tag, tag) for tag in result.problem_tags) or "无"
        lines = [
            "AI 参考评价：%s（%d 分）" % (
                result.quality_label, result.quality_score),
            "置信度：%s" % confidence,
            "目损：%s" % (
                "—" if result.score_loss is None else "%.1f" % result.score_loss),
            "胜率损失：%s" % (
                "—" if result.winrate_drop is None
                else "%.1f 个百分点" % result.winrate_drop),
            "AI 排名：%s" % ai_rank,
            "问题标签：%s" % tags,
            "原因：",
        ]
        lines.extend("  - " + reason for reason in (result.reasons or ["暂无可用原因。"]))
        return "\n".join(lines)

    def _set_problem_compare_mode(self, mode):
        evaluation = self._selected_problem_eval
        if evaluation is None:
            self._set_msg("请先选择一手恶手")
            return
        comparison = self._comparison_for(evaluation)
        if mode in ("actual", "ai") and not comparison:
            self._set_msg("这手棋的双分支深度对比尚未完成")
            return
        self._problem_compare_mode = mode
        for key, btn in (("summary", getattr(self, "btn_compare_summary", None)),
                         ("actual", getattr(self, "btn_compare_actual", None)),
                         ("ai", getattr(self, "btn_compare_ai", None))):
            if btn is not None and str(btn.cget("state")) != "disabled":
                self._set_button_variant(btn, key == mode)
        if mode in ("actual", "ai"):
            self._show_problem_branch_on_board(evaluation, comparison, mode)
        else:
            self._problem_branch_overlay = None
            self.redraw()
        self._show_problem_intent(
            evaluation, ReviewReport(self.tree), ensure=False)

    def _show_problem_branch_on_board(self, evaluation, comparison, mode):
        rr = ReviewReport(self.tree)
        node = rr.node_at_move(evaluation.move_number)
        if node is None or node.parent is None:
            return
        self._reset_pv_state()
        self.tree.current = node.parent
        self._hover_point = None
        self._hint_point = None
        self._hint_pending_nid = None
        self._problem_branch_overlay = {
            "kind": mode,
            "pv": list((comparison.get(mode) or {}).get("pv") or []),
        }
        self.redraw()
        self._refresh_treeview()
        self._update_scale()
        if self.tree.current.analysis:
            self._render_analysis(self.tree.current.analysis)
        label = "实战变化" if mode == "actual" else "AI 推荐变化"
        self._set_msg("棋盘正在显示第 %d 手的%s（编号 1-%d）" % (
            evaluation.move_number, label,
            len(self._problem_branch_overlay.get("pv") or [])))

    def _set_problem_intent_text(self, text):
        if not hasattr(self, "txt_problem_intent"):
            return
        self.txt_problem_intent.configure(state=tk.NORMAL)
        self.txt_problem_intent.delete("1.0", tk.END)
        self.txt_problem_intent.insert("1.0", text)
        self.txt_problem_intent.configure(state=tk.DISABLED)

    # ===================== 问题手训练（涨棋网风格 quiz 钻取）=====================
    def _drill_active(self):
        # GoAnalyzer 继承自 Tk，未完整初始化的测试替身上使用 getattr()
        # 会落入 tkinter.Misc.__getattr__ 并递归访问 self.tk。这里直接读
        # __dict__，让“尚未创建问题手训练窗口”的状态安全返回 False。
        win = self.__dict__.get("_drill_win")
        if win is None:
            return False
        try:
            return bool(win.winfo_exists())
        except Exception:
            return False

    def _player_side_for_drill(self):
        side = getattr(self.tree, "_profile_side", "unknown")
        return side if side in ("B", "W") else "both"

    # ---- Human SL 主链（大纲 §6-8、§14）----
    def _request_human_sl_priors(self, drill):
        """为训练题的实战手发起双档 humanPolicy 查询（本人档 + 更高档）。

        prior 来自策略头，低 visits 即可（用 60 封顶，不占深算额度）；
        双档结果缓存进 LearningEvent 持久化，下次训练排序的 level_gap
        分量直接复用，不重复计算。引擎/Human 模型未就绪时静默跳过。
        """
        if not (self.client and self.client.is_alive() and self.client.ready
                and getattr(self.client, "human_model_active", False)):
            return
        from human_sl import human_query
        profiles = (self.cfg.get("human_sl_profile") or "rank_1d",
                    self.cfg.get("human_sl_reference_profile") or "rank_3d")
        rr = ReviewReport(self.tree)
        # humanPrior 来自策略头，1 visit 即可获取（KataGo 官方建议），
        # 不占用深算额度
        visits = 1
        queued = 0
        for dm in drill.moves or []:
            if str(dm.played_move).lower() == "pass":
                continue
            node = rr.node_at_move(dm.move_number)
            parent = node.parent if node is not None else None
            if parent is None or not parent.analysis:
                continue
            base = self._analysis_query_for(
                self.tree, parent, self.rules, self.komi, visits)
            for kind, profile in (("current", profiles[0]),
                                  ("stronger", profiles[1])):
                qid = self.client.analyze(human_query(base, profile))
                self._human_sl_pending[qid] = (dm.move_number, kind, profile)
                queued += 1
        if queued:
            self._set_msg("Human SL：正在计算 %d 道题的双档选点概率（缓存后长期复用）…"
                          % len(drill.moves))

    def _handle_human_sl_result(self, rid, resp):
        """Human SL 查询回流：解析实战手双档概率，齐套后写入事件缓存。"""
        from human_sl import parse_human_prior
        ctx = self._human_sl_pending.pop(rid, None)
        if ctx is None:
            return
        move_number, kind, profile = ctx
        drill = self.__dict__.get("_drill")
        dm = next((m for m in (drill.moves if drill else [])
                   if m.move_number == move_number), None)
        if dm is None:
            return
        prior = parse_human_prior(resp, dm.played_move)
        if prior is None:
            return    # 实战手不在返回候选（极冷门）→ 没有证据不给 level_gap
        entry = self._human_sl_cache.setdefault(move_number, {})
        entry[kind] = prior
        entry["profile" if kind == "current" else "stronger_profile"] = profile
        if "current" in entry and "stronger" in entry:
            self._cache_human_priors_to_event(dm, entry)

    def _cache_human_priors_to_event(self, dm, entry):
        """双档概率齐套 → 持久缓存到 LearningEvent（human 字段不入进度合并）。"""
        game_id = str(getattr(self, "_library_record_id", "") or "")
        if not game_id:
            return
        try:
            from learning_event import event_id
            from learning_store import get_event, save_event
            evt = get_event(event_id(game_id, dm.move_number, dm.color))
            if evt is None:
                return
            evt.human_profile = str(entry.get("profile") or "")
            evt.human_prior_current = float(entry.get("current") or 0.0)
            evt.human_prior_stronger = float(entry.get("stronger") or 0.0)
            save_event(evt)
        except Exception:
            pass

    def _learning_priority_context(self, evaluations):
        """学习排序上下文：跨盘复发 + 掌握状态 + Human SL 双档概率 + 对局情境。"""
        context = {"recurrence_by_move": {}, "mastery_by_move": {},
                   "human_priors_by_move": {},
                   "game_type": str(self.cfg.get("default_game_type") or "").strip() or None}
        try:
            from learning_priority import build_recurrence_index
            from learning_store import get_events, get_events_by_game
            from taxonomy import classify_problem
            game_id = str(getattr(self, "_library_record_id", "") or "")
            index = build_recurrence_index(get_events(), exclude_game_id=game_id)
            for evt in (get_events_by_game(game_id) if game_id else []):
                context["mastery_by_move"][evt.move_no] = evt.mastery_state
                # Human SL 概率已缓存进事件（human_profile 非空 = 有数据）
                if evt.human_profile:
                    context["human_priors_by_move"][evt.move_no] = {
                        "current": evt.human_prior_current,
                        "stronger": evt.human_prior_stronger,
                    }
            for e in evaluations or []:
                quality = (getattr(self, "_quality_by_move", {}) or {}).get(
                    e.move_number)
                tags = list(getattr(quality, "problem_tags", None) or [])
                category = classify_problem({
                    "problem_tags": tags,
                    "score_loss": e.loss})["primary_category"]
                if category and category != "unclassified":
                    context["recurrence_by_move"][e.move_number] = index.get(
                        category, 0)
        except Exception:
            pass
        return context

    def open_problem_drill(self):
        """把本局用户方问题手组织成逐题 quiz + 选点对比表 + 4 变化图（对标涨棋网）。"""
        if self._drill_active():
            self._drill_win.lift()
            self._drill_win.focus_set()
            return
        if self.scoring_mode:
            self._set_msg("请先退出点目模式，再开始问题手训练")
            return
        if self._training and self._training.get("active") and not self._training.get("finished"):
            self._set_msg("请先结束阶段训练，再开始问题手训练")
            return
        rr = ReviewReport(self.tree)
        evaluations = rr.evaluate()
        mainline = rr.mainline_nodes()
        total = max(1, len(mainline) - 1)
        parent_infos = {}
        for e in evaluations:
            node = rr.node_at_move(e.move_number)
            parent = node.parent if node is not None else None
            if parent is not None and parent.analysis:
                parent_infos[e.move_number] = parent.analysis.get("moveInfos") or []
        quality_by_move = {}
        for mn, qr in getattr(self, "_quality_by_move", {}).items():
            if getattr(qr, "quality_label", None):
                quality_by_move[int(mn)] = qr.quality_label

        drill = build_problem_drill(
            evaluations, parent_infos,
            user_color=self._player_side_for_drill(),
            phase_label_of=lambda mn: ReviewReport.phase_label(
                ReviewReport.phase_of_move(mn, total)),
            quality_by_move=quality_by_move,
            ranking="learning",
            priority_context=self._learning_priority_context(evaluations))
        if drill.is_empty:
            messagebox.showinfo(
                "问题手训练",
                "暂无可训练的问题手。\n\n" + "\n".join(drill.warnings)
                + "\n\n请先对整盘做分析（复盘 → 补全分析），并确认已识别你的执棋方"
                "（可在棋谱库 / 个人画像里设置）。",
                parent=self)
            return
        self._drill = drill
        self._drill_result = new_drill_result(drill)
        self._drill_index = 0
        self._drill_revealed = False
        self._drill_user_color = drill.user_color
        self._request_human_sl_priors(drill)
        self._build_drill_window()

    def _build_drill_window(self):
        win = tk.Toplevel(self)
        self._prepare_child_window(
            win, "问题手训练 · 涨棋网风格", 740, 600, minsize=(640, 500))
        win.protocol("WM_DELETE_WINDOW", self._close_problem_drill)

        top = tk.Frame(win, bg=COLORS["card"], padx=12, pady=9,
                       highlightthickness=1, highlightbackground=COLORS["muted"])
        top.pack(fill="x", padx=10, pady=(10, 4))
        self._drill_header = tk.Label(
            top, text="", font=FONTS["title"], bg=COLORS["card"], fg=COLORS["text"])
        self._drill_header.pack(anchor="w")
        self._drill_sub = tk.Label(
            top, text="", font=FONTS["small"], bg=COLORS["card"], fg=COLORS["subtext"])
        self._drill_sub.pack(anchor="w")

        # 题号进度条：拖动直接跳到任意一题（不必逐题翻）
        self._drill_scale_row = tk.Frame(win, bg=COLORS["bg"])
        self._drill_scale_row.pack(fill="x", padx=12, pady=(2, 2))
        self._drill_scale = ttk.Scale(
            self._drill_scale_row, from_=1, to=1, orient=tk.HORIZONTAL,
            command=self._on_drill_scale)
        self._drill_scale.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._drill_scale_label = tk.Label(
            self._drill_scale_row, text="第 1 / 1 题", width=12, anchor="e",
            bg=COLORS["bg"], fg=COLORS["subtext"], font=FONTS["data"])
        self._drill_scale_label.pack(side=tk.LEFT, padx=(8, 0))

        self._drill_instruction = tk.Label(
            win, text="", font=FONTS["ui"], bg=COLORS["bg"], fg=COLORS["text"],
            justify=tk.LEFT, wraplength=700, anchor="w")
        self._drill_instruction.pack(fill="x", padx=12, pady=(4, 2))

        self._drill_letter_frame = tk.Frame(win, bg=COLORS["bg"])
        self._drill_letter_frame.pack(fill="x", padx=12, pady=(0, 2))

        tv = ttk.Treeview(
            win,
            columns=("eval", "quality", "coord", "visits", "policy",
                     "winrate", "score", "wrloss", "sloss"),
            show="headings", height=5)
        for col, title, width, anchor in [
                ("eval", "评价", 54, "center"), ("quality", "评级", 54, "center"),
                ("coord", "坐标", 60, "center"), ("visits", "计算量", 68, "e"),
                ("policy", "选点概率", 74, "e"), ("winrate", "胜率", 62, "e"),
                ("score", "领先目", 62, "e"), ("wrloss", "胜率损失", 76, "e"),
                ("sloss", "目数损失", 76, "e")]:
            tv.heading(col, text=title)
            tv.column(col, width=width, minwidth=width, stretch=False, anchor=anchor)
        tv.tag_configure("best", foreground=COLORS["green"])
        tv.tag_configure("actual", foreground=COLORS["red"])
        tv.tag_configure("muted", foreground=COLORS["subtext"])
        tv.pack(fill="x", padx=12, pady=(2, 2))
        self._drill_tv = tv

        var_frame = tk.Frame(win, bg=COLORS["bg"])
        var_frame.pack(fill="x", padx=12, pady=(2, 2))
        self._drill_var_buttons = {}
        for key in ("正解图", "失败图", "二选", "三选"):
            b = self._make_button(var_frame, key,
                                  lambda k=key: self._drill_show_variation(k),
                                  variant="default")
            b.pack(side=tk.LEFT, padx=4)
            self._drill_var_buttons[key] = b
        tk.Label(var_frame, text="  ← 点按钮在棋盘显示对应变化图",
                 bg=COLORS["bg"], fg=COLORS["subtext"], font=FONTS["small"]).pack(side=tk.LEFT)

        nav = tk.Frame(win, bg=COLORS["bg"])
        nav.pack(fill="x", padx=12, pady=(2, 4))
        self._make_button(nav, "◀ 上一题", self._drill_prev, variant="default").pack(side=tk.LEFT)
        self._drill_reveal_btn = self._make_button(
            nav, "查看答案",
            lambda: self._drill_reveal(answered_letter=None), variant="accent")
        self._drill_reveal_btn.pack(side=tk.LEFT, padx=8)
        self._make_button(nav, "结束并查看总结",
                          self._drill_show_summary, variant="default").pack(side=tk.RIGHT, padx=8)
        self._make_button(nav, "下一题 ▶",
                          self._drill_next, variant="accent").pack(side=tk.RIGHT)

        self._drill_summary = tk.Label(
            win, text="", font=FONTS["small"], bg=COLORS["bg"], fg=COLORS["text"],
            justify=tk.LEFT, wraplength=700, anchor="w")
        self._drill_summary.pack(fill="both", expand=True, padx=12, pady=(0, 10))

        self._drill_win = win
        self._drill_show_question()

    # ---- 单题渲染 ----
    def _drill_show_question(self):
        drill = self._drill
        if drill is None or not drill.moves or self._drill_index >= len(drill.moves):
            self._drill_show_summary()
            return
        dm = drill.moves[self._drill_index]
        rr = ReviewReport(self.tree)
        node = rr.node_at_move(dm.move_number)
        if node is None or node.parent is None:
            self._set_msg("第 %d 手节点不可用，跳过" % dm.move_number)
            self._drill_next()
            return
        side = "黑" if dm.color == "B" else "白"
        self._drill_header.config(
            text="第 %d / %d 题   ·   %s方 第 %d 手   ·   %s   ·   实战：%s" % (
                self._drill_index + 1, len(drill.moves), side, dm.move_number,
                dm.phase_label, dm.played_quality))
        self._drill_refresh_score()
        self._drill_sync_scale()
        if dm.move_number in self._drill_result.answers:
            self._drill_revealed = True
            self._drill_enter_board(dm, node.parent, reveal=True)
            self._drill_apply_reveal(dm, self._drill_result.answers[dm.move_number].get("letter"))
            return
        self._drill_revealed = False
        self._drill_var_key = None
        self._drill_enter_board(dm, node.parent, reveal=False)
        for w in self._drill_letter_frame.winfo_children():
            w.destroy()
        for key in dm.quiz_order:
            letter = dm.letter_of(key)
            btn = self._make_button(
                self._drill_letter_frame, str(letter),
                lambda L=letter: self._drill_answer(L),
                variant="default", width=36 if _HAS_CTK else 3)
            btn.pack(side=tk.LEFT, padx=4, pady=4)
        tk.Label(self._drill_letter_frame,
                 text="  ← 点字母选候选，或直接在棋盘任意空点自由落子作答（推荐）",
                 bg=COLORS["bg"], fg=COLORS["subtext"], font=FONTS["small"]).pack(side=tk.LEFT)
        self._drill_instruction.config(
            text="如果现在重新下一次，%s方第 %d 手你会走哪里？棋盘 A/B/C… 是打乱的候选与实战落点，"
                 "也可以自由落子——不在候选内的选点会自动送 KataGo 强制分析后判定。" % (
                     side, dm.move_number),
            fg=COLORS["text"])
        self._drill_clear_table("（查看答案后显示选点对比表）")
        for b in self._drill_var_buttons.values():
            self._set_button_variant(b, False)
            b.configure(state=tk.DISABLED)
        self._drill_reveal_btn.configure(text="查看答案", state=tk.NORMAL)
        self._drill_summary.config(text="")

    def _drill_refresh_score(self):
        res = self._drill_result
        self._drill_sub.config(text="已作答 %d / %d，答对 %d" % (
            res.answered, res.total, res.correct))

    def _drill_answer(self, letter):
        if self._drill_revealed:
            return
        dm = self._drill.moves[self._drill_index]
        self._drill_result.record(dm, letter)
        # 字母作答与自由落子共用同一判分与记账（大纲 §20-23：
        # 判分逻辑只能有一个，字母只是输入方式）
        try:
            from candidate_assessment import (
                assessment_for_loss, classify_retry, srs_result,
            )
            chosen = dm.candidate(dm.key_of(letter))
            if chosen is not None:
                level, _ok = assessment_for_loss(chosen.score_loss)
                rr = ReviewReport(self.tree)
                node = rr.node_at_move(dm.move_number)
                parent = node.parent if node is not None else None
                mis = (parent.analysis or {}).get("moveInfos") or [] \
                    if parent is not None else []
                self._drill_persist_free_answer(
                    dm, chosen.move,
                    {"score_loss": chosen.score_loss, "assessment": level,
                     "ai_rank": dm.candidates.index(chosen) + 1},
                    classify_retry(dm.loss, level, chosen.score_loss),
                    srs_result(level), mis, None)
        except Exception:
            pass
        self._drill_reveal(answered_letter=letter)

    # ---- 主动复盘：自由落子作答（大纲 §23-25）----
    def _drill_free_answer(self, x, y):
        """quiz 阶段棋盘自由落子：在候选表内直接判分，榜外手强制分析后判分。"""
        drill = self.__dict__.get("_drill")
        if drill is None or self._drill_index >= len(drill.moves):
            return
        dm = drill.moves[self._drill_index]
        if self._drill_revealed or dm.move_number in self._drill_result.answers:
            return
        rr = ReviewReport(self.tree)
        node = rr.node_at_move(dm.move_number)
        parent = node.parent if node is not None else None
        if parent is None or parent.board.stone_at(x, y) != EMPTY:
            self._set_msg("该点不能落子")
            return
        coord = "%s%d" % (COLS[x], self.size - y)
        mis = (parent.analysis or {}).get("moveInfos") or []
        in_infos = any(str(m.get("move") or "").lower() == coord.lower()
                       for m in mis)
        if in_infos or not (self.client and self.client.is_alive()
                            and self.client.ready):
            # 候选表内：直接按实际目损判定；引擎未就绪且榜外：数据不足保守判定
            self._drill_record_free_answer(dm, coord, parent, move_infos=mis)
            return
        # 榜外手：绝不直接判错——用 allowMoves 强制分析这手再判定（大纲 §23）
        from candidate_assessment import forced_move_query
        visits = int(self.cfg.get("max_visits", 200))
        q = forced_move_query(
            self._analysis_query_for(self.tree, parent, self.rules, self.komi, visits),
            coord, player=dm.color)
        qid = self.client.analyze(q)
        self._drill_forced_pending[qid] = (dm.move_number, coord)
        self._set_msg("你的选点 %s 不在已有候选，正在强制分析…" % coord)

    def _handle_drill_forced_result(self, rid, resp):
        from candidate_assessment import forced_move_result
        ctx = self._drill_forced_pending.pop(rid, None)
        if ctx is None:
            return
        move_number, coord = ctx
        drill = self.__dict__.get("_drill")
        if drill is None:
            return
        dm = next((m for m in drill.moves if m.move_number == move_number), None)
        if dm is None or self._drill_revealed:
            return
        # 用户可能已切到别的题：结果仍记录到原题，但只揭示当前正在看的题
        current = (drill.moves[self._drill_index]
                   if self._drill_index < len(drill.moves) else None)
        still_current = current is not None and current.move_number == move_number
        rr = ReviewReport(self.tree)
        node = rr.node_at_move(move_number)
        parent = node.parent if node is not None else None
        if parent is None:
            return
        score, winrate, _order = forced_move_result(resp, coord)
        self._drill_record_free_answer(
            dm, coord, parent, forced=(score, winrate), reveal=still_current)

    def _drill_record_free_answer(self, dm, coord, parent,
                                  move_infos=None, forced=None, reveal=True):
        """按实际目损判定一次自由作答并揭示（大纲 §25 四分类 + §60 三行对比）。

        reveal=False 用于强制分析延迟返回时用户已切题：只记录结果，
        不动当前题的揭示状态。
        """
        from candidate_assessment import assess_candidate, classify_retry, srs_result
        mis = move_infos if move_infos is not None else \
            (parent.analysis or {}).get("moveInfos") or []
        ordered = sorted(mis, key=lambda m: m.get("order", 999))
        best_info = ordered[0] if ordered else {}
        kwargs = {}
        if forced is not None:
            score, winrate = forced
            kwargs = dict(forced_score_lead=score, forced_winrate=winrate,
                          best_score_lead=best_info.get("scoreLead"),
                          best_winrate=best_info.get("winrate"))
        assessment = assess_candidate(
            coord, mis, dm.color,
            performance_label=self._assessment_context()["performance_label"],
            complexity=0.0, **kwargs)
        reasonable = assessment["assessment"] in ("best", "excellent", "acceptable")
        retry_status = classify_retry(
            dm.loss, assessment["assessment"], assessment.get("score_loss"))

        # 训练窗口计分（自由作答：达到合理标准计答对；重下实战计 picked_actual）
        res = self._drill_result
        if dm.move_number not in res.answers:
            res.answered += 1
            if reasonable:
                res.correct += 1
            if coord.upper() == str(dm.played_move).upper():
                res.picked_actual += 1
        res.answers[dm.move_number] = {
            "letter": None, "chosenMove": coord,
            "chosenQuality": assessment["assessment_label"],
            "bestMove": dm.best_move, "isCorrect": reasonable,
            "isActual": coord.upper() == str(dm.played_move).upper(),
            "assessment": assessment, "retryStatus": retry_status,
            "forced": bool(forced),
        }
        self._drill_refresh_score()

        # 回写错题本（按实际目损判分）+ LearningEvent（作答历史与掌握状态）
        self._drill_persist_free_answer(
            dm, coord, assessment, retry_status,
            srs_result(assessment["assessment"]), mis, forced)
        if reveal:
            self._drill_reveal(answered_letter=None)

    def _drill_persist_free_answer(self, dm, coord, assessment, retry_status,
                                   srs, move_infos, forced):
        """自由作答结果回写错题本与 LearningEvent（存在才写，不凭空造）。"""
        game_id = str(getattr(self, "_library_record_id", "") or "")
        if not game_id:
            return
        kwargs = {}
        if forced is not None:
            score, winrate = forced
            kwargs = dict(forced_score_lead=score, forced_winrate=winrate,
                          best_score_lead=None, best_winrate=None)
            ordered = sorted(move_infos or [], key=lambda m: m.get("order", 999))
            if ordered:
                kwargs["best_score_lead"] = ordered[0].get("scoreLead")
                kwargs["best_winrate"] = ordered[0].get("winrate")
        try:
            from learning_event import event_id
            from learning_store import get_event, save_event
            eid = event_id(game_id, dm.move_number, dm.color)
            from mistake_book import get_item, record_graded_attempt
            if get_item(eid) is not None:
                record_graded_attempt(
                    eid, coord, move_infos, dm.color, dm.best_move, **kwargs)
                return
            evt = get_event(eid)
            if evt is not None:
                evt.add_attempt(
                    coord, score_loss=assessment.get("score_loss"),
                    assessment=assessment.get("assessment"),
                    ai_rank=assessment.get("ai_rank"))
                evt.record_retry(coord, assessment.get("score_loss") or 0.0,
                                 retry_status)
                save_event(evt)
        except Exception:
            pass

    def _drill_reveal(self, answered_letter=None):
        dm = self._drill.moves[self._drill_index]
        self._drill_revealed = True
        rr = ReviewReport(self.tree)
        node = rr.node_at_move(dm.move_number)
        parent = node.parent if node is not None else None
        self._drill_enter_board(dm, parent, reveal=True)
        if answered_letter is None:
            ans = self._drill_result.answers.get(dm.move_number)
            answered_letter = ans["letter"] if ans else None
        self._drill_apply_reveal(dm, answered_letter)

    def _drill_apply_reveal(self, dm, answered_letter):
        ans = self._drill_result.answers.get(dm.move_number)
        if answered_letter:
            g = grade_quiz(dm, answered_letter,
                           context=self._assessment_context())
            loss_text = ("损失 %.1f 目" % g["chosenLoss"]
                         if g.get("chosenLoss") is not None else "数据不足")
            if g["isCorrect"]:
                msg = "✓ 合理：%s（%s，%s）。AI 一选 %s。" % (
                    g["chosenMove"], g["assessmentLabel"], loss_text, dm.best_move)
                fg = COLORS["green"]
            else:
                who = "（你选的是实战 %s）" % (g["chosenMove"] or "") if g["isActual"] \
                    else "（你选的是 %s）" % (g["chosenMove"] or "—")
                msg = "✗ 超出合理范围%s：%s，%s。AI 一选 %s。" % (
                    who, g["assessmentLabel"], loss_text, dm.best_move)
                fg = COLORS["red"]
        elif ans and ans.get("letter") is None and ans.get("chosenMove"):
            # 自由落子作答：三行对比 + 四分类结论（大纲 §25/§60）
            assessment = ans.get("assessment") or {}
            status_text = {
                "corrected": "已自行修正——你现在能独立纠正这个问题。",
                "alternative_correct": "合理方案——没选 AI 第一选，但完全可行。",
                "improved": "有进步，但尚未达到合理标准。",
                "repeated": "再次出现同类错误——这是真正的知识盲区。",
            }.get(ans.get("retryStatus"), "")
            retry_loss = assessment.get("score_loss")
            msg = "你的实战 %s：损失 %.1f 目 ｜ 你的重选 %s：%s%s\nAI 首选 %s：损失 0 目。%s" % (
                dm.played_move, dm.loss, ans["chosenMove"],
                "损失 %s 目" % ("%.1f" % retry_loss if retry_loss is not None else "？"),
                "（%s ✓）" % assessment.get("assessment_label", "")
                if ans.get("isCorrect") else "（%s）" % assessment.get("assessment_label", "数据不足"),
                dm.best_move,
                ("\n" + status_text) if status_text else "")
            fg = COLORS["green"] if ans.get("isCorrect") else COLORS["red"]
        else:
            msg = "已揭示答案：正解是 AI 一选 %s。" % dm.best_move
            fg = COLORS["accent"]
        self._drill_instruction.config(text=msg, fg=fg)
        self._drill_populate_table(dm)
        for b in self._drill_var_buttons.values():
            b.configure(state=tk.NORMAL)
        self._drill_show_variation("正解图")
        self._drill_reveal_btn.configure(text="已揭示答案", state=tk.DISABLED)
        for w in self._drill_letter_frame.winfo_children():
            if isinstance(w, ttk.Button) or (_HAS_CTK and isinstance(w, ctk.CTkButton)):
                w.configure(state=tk.DISABLED)
        self._drill_refresh_score()

    def _drill_populate_table(self, dm):
        tv = self._drill_tv
        tv.delete(*tv.get_children())
        for c in dm.candidates:
            tag = "actual" if c.is_actual else ("best" if c.key == "c0" else "")
            tv.insert("", tk.END, values=(
                c.eval_label, c.quality_label, c.coord,
                ("%d" % c.visits) if c.visits else "0",
                "%.1f%%" % (c.policy * 100.0),
                "%.1f%%" % (c.winrate * 100.0),
                "%+.1f" % c.score_lead,
                ("%.1f" % c.winrate_loss) if c.winrate_loss else "0",
                ("%.1f" % c.score_loss) if c.score_loss else "0",
            ), tags=(tag,))

    def _drill_clear_table(self, placeholder):
        tv = self._drill_tv
        tv.delete(*tv.get_children())
        if placeholder:
            tv.insert("", tk.END,
                      values=(placeholder, "", "", "", "", "", "", "", ""),
                      tags=("muted",))

    def _drill_show_variation(self, key):
        if not self._drill_revealed or self._drill is None:
            return
        dm = self._drill.moves[self._drill_index]
        pv = dm.variations.get(key) or []
        self._drill_var_key = key
        for k, b in self._drill_var_buttons.items():
            self._set_button_variant(b, k == key)
        self._problem_branch_overlay = (
            {"kind": "ai" if key != "失败图" else "actual", "pv": list(pv)}
            if pv else None)
        self.redraw()
        self._set_msg("问题手训练 · %s：%s" % (key, " ".join(pv[:10]) or "（该选无变化数据）"))

    def _drill_enter_board(self, dm, parent_node, reveal=False):
        self._reset_pv_state()
        self.tree.current = parent_node
        self._hover_point = None
        self._hint_point = None
        self._hint_pending_nid = None
        if reveal:
            self._drill_overlay = None
        else:
            letters = {}
            for key in dm.quiz_order:
                cand = dm.candidate(key)
                if cand is None or cand.move == "pass":
                    continue
                try:
                    x, y = point_to_xy(cand.move, self.size)
                except Exception:
                    continue
                if parent_node.board.stone_at(x, y) == EMPTY:
                    letters[(x, y)] = dm.letter_of(key)
            self._drill_overlay = {"letters": letters} if letters else None
            self._problem_branch_overlay = None
        self.redraw()
        self._refresh_treeview()
        self._update_scale()
        if self.tree.current.analysis:
            self._render_analysis(self.tree.current.analysis)
        if not reveal:
            # quiz 阶段：隐藏右侧 AI 候选，避免提前泄露答案
            self._show_candidate_state("问题手训练中：AI 推荐已隐藏，请在棋盘辨认 A/B/C…")

    def _drill_next(self):
        if self._drill is None:
            return
        if self._drill_index < len(self._drill.moves) - 1:
            self._drill_index += 1
            self._drill_show_question()
        else:
            self._drill_show_summary()

    def _drill_prev(self):
        if self._drill_index > 0:
            self._drill_index -= 1
            self._drill_show_question()

    def _on_drill_scale(self, value):
        """拖动题号进度条 → 直接跳到对应题。"""
        if self._drill_scale_suppress or self._drill is None:
            return
        try:
            pos = int(round(float(value)))
        except (TypeError, ValueError):
            return
        idx = max(0, min(len(self._drill.moves) - 1, pos - 1))
        if idx != self._drill_index:
            self._drill_index = idx
            self._drill_show_question()

    def _drill_sync_scale(self):
        """把进度条范围/位置/文案同步到当前题号（抑制回调避免反馈环）。"""
        if self._drill_scale is None or self._drill is None:
            return
        n = max(1, len(self._drill.moves))
        self._drill_scale_suppress = True
        try:
            self._drill_scale.config(from_=1, to=n)
            self._drill_scale.set(self._drill_index + 1)
            self._drill_scale_label.config(
                text="第 %d / %d 题" % (self._drill_index + 1, n))
        finally:
            self._drill_scale_suppress = False

    def _drill_show_summary(self):
        drill = self._drill
        res = self._drill_result
        if drill is None or res is None:
            return
        lines = ["【训练总结】%s 问题手钻取：%s" % (
            drill.user_color_label, "已结束" if self._drill_index >= len(drill.moves)
            else "已中途结束")]
        lines.append("作答 %d / %d，答对 %d，得分 %d（%s）。" % (
            res.answered, res.total, res.correct, res.score_pct, res.label))
        # 主动复盘四分类统计（大纲 §25）：自由落子作答的学习价值画像
        retry_counts = {"corrected": 0, "alternative_correct": 0,
                        "improved": 0, "repeated": 0}
        for ans in res.answers.values():
            status = (ans or {}).get("retryStatus")
            if status in retry_counts:
                retry_counts[status] += 1
        if any(retry_counts.values()):
            lines.append("主动复盘：已修正 %d ｜ 合理替代 %d ｜ 有改善 %d ｜ 重复错误 %d" % (
                retry_counts["corrected"], retry_counts["alternative_correct"],
                retry_counts["improved"], retry_counts["repeated"]))
        if drill.other_problems:
            lines.append("其它问题手（建议自行复盘）：%s" % "、".join(
                "第%d手(%s)" % (p["move"], p["quality"]) for p in drill.other_problems[:24]))
        if drill.out_of_reach:
            lines.append("超纲问题手（同水平难以掌握，可暂不强求）：%s" % "、".join(
                "第%d手(%s)" % (p["move"], p["quality"]) for p in drill.out_of_reach))
        self._drill_summary.config(text="\n".join(lines), fg=COLORS["text"])
        self._drill_instruction.config(
            text="训练结束。可关闭窗口，或用「上一题」回看具体题目。", fg=COLORS["subtext"])
        for w in self._drill_letter_frame.winfo_children():
            w.destroy()
        self._drill_clear_table("（训练结束）")
        for b in self._drill_var_buttons.values():
            self._set_button_variant(b, False)
            b.configure(state=tk.DISABLED)
        self._drill_reveal_btn.configure(state=tk.DISABLED)
        self._drill_header.config(text="问题手训练 · 训练总结")
        self._drill_overlay = None
        self._problem_branch_overlay = None
        self.redraw()
        self._set_msg("问题手训练结束：%s（得分 %d）" % (res.label, res.score_pct))

    def _close_problem_drill(self):
        win = self._drill_win
        self._drill_win = None
        self._drill = None
        self._drill_result = None
        self._drill_overlay = None
        self._drill_forced_pending = {}
        self._human_sl_pending = {}
        self._problem_branch_overlay = None
        if win is not None:
            try:
                win.destroy()
            except tk.TclError:
                pass
        # 同步复盘面板（曲线/失误榜/候选）到当前节点：仅 redraw 不够，复盘状态需 _after_navigate 刷新。
        try:
            self._after_navigate()
        except tk.TclError:
            pass

    def _draw_drill_overlay(self):
        """quiz 阶段：在候选点 / 实战点上画统一字母标记，不揭示哪个是正解。"""
        ov = self._drill_overlay or {}
        letters = ov.get("letters") or {}
        c, M, D = self.canvas, self.MARGIN, self.CELL
        r = D * 0.42
        for (x, y), letter in letters.items():
            cx, cy = M + x * D, M + y * D
            c.create_oval(cx - r, cy - r, cx + r, cy + r,
                          fill=COLORS["accent_s"], outline=COLORS["accent"], width=2,
                          tags=("drill-marker",))
            c.create_text(cx, cy, text=str(letter), fill=COLORS["accent_h"],
                          font=("Microsoft YaHei UI", max(9, int(D * 0.34)), "bold"),
                          tags=("drill-marker",))

    # ===================== 常驻形式判断（棋盘 HUD）=====================
    def toggle_situation(self):
        """切换棋盘常驻形式判断 HUD（胜率 / 目差 / 双方实地 / 提子）。

        开启时自动把热力图切到「地盘」模式（配合看地盘归属），并记住开启前的热力图状态；
        关闭时恢复开启前的热力图（通常为关）——符合「形式判断 ↔ 地盘热力图」联动直觉。
        """
        self._show_situation = not getattr(self, "_show_situation", True)
        if hasattr(self, "btn_situation"):
            self.btn_situation.configure(
                text="形式判断 ✓" if self._show_situation else "形式判断 ✗")
            self._set_toggle(self.btn_situation, self._show_situation, "Quiet.TButton")
        if self._show_situation and getattr(self, "_heat_mode", 0) != 1:
            self._heat_mode_before_situation = getattr(self, "_heat_mode", 0)
            self._heat_mode = 1   # 地盘（ownership）模式，辅助看双方实地
            if hasattr(self, "btn_heat"):
                self.btn_heat.configure(text="热力图: %s" % HEAT_LABELS[self._heat_mode])
                self._set_toggle(self.btn_heat, self._heat_mode != 0)
            self.cfg.update(heatmap_mode=HEAT_KEYS[self._heat_mode])
        elif not self._show_situation and hasattr(self, "_heat_mode_before_situation"):
            # 关闭形式判断：恢复开启前的热力图（取消自动切的地盘模式）
            self._heat_mode = self._heat_mode_before_situation
            del self._heat_mode_before_situation
            if hasattr(self, "btn_heat"):
                self.btn_heat.configure(text="热力图: %s" % HEAT_LABELS[self._heat_mode])
                self._set_toggle(self.btn_heat, self._heat_mode != 0)
            self.cfg.update(heatmap_mode=HEAT_KEYS[self._heat_mode])
        self.redraw()

    def _draw_situation_overlay(self):
        """棋盘右上角常驻 HUD：胜率条 + 目差 + 双方实地 + 提子，跨所有模式可见。

        数据取当前节点 KataGo 分析（rootInfo.winrate/scoreLead + ownership 实地估算）
        与盘面累计提子（board.captures）。只显示局面评估，不揭示该下哪，训练期可常驻。
        """
        if not getattr(self, "_show_situation", True):
            return
        if not getattr(self, "canvas", None):
            return
        node = self.tree.current
        resp = getattr(node, "analysis", None)
        root = (resp or {}).get("rootInfo") or {}
        wr = root.get("winrate")
        score = root.get("scoreLead")
        own = (resp or {}).get("ownership")
        caps_black = caps_white = 0
        try:
            bd = node.board
            caps_black = int(bd.captures.get(BLACK, 0))    # 黑方提走的白子累计
            caps_white = int(bd.captures.get(WHITE, 0))     # 白方提走的黑子累计
        except Exception:
            pass
        c = self.canvas
        w, h = 204, 64
        x0 = self.BOARD_PIX - w - 6
        y0 = 6
        # PIL 圆角卡片底（替代 create_rectangle 直角）：真 AA 圆角 + 真透明浮层感
        rect_img = self._get_rounded_rect(w, h, 10, COLORS["card"], 235)
        if rect_img is not None:
            c.create_image(int(x0 + w / 2), int(y0 + h / 2), image=rect_img, tags=("situation",))
        else:
            c.create_rectangle(
                x0, y0, x0 + w, y0 + h, fill=COLORS["card"], outline=COLORS["muted"],
                width=1, tags=("situation",))
        c.create_rectangle(
            x0, y0, x0 + 3, y0 + h, fill=COLORS["accent"], outline="",
            tags=("situation",))
        c.create_text(
            x0 + 10, y0 + 11, text="形势判断", anchor="w",
            fill=COLORS["subtext"], font=("Microsoft YaHei UI", 8),
            tags=("situation",))
        cap_txt = "提子 黑%d · 白%d" % (caps_black, caps_white)
        if wr is None:
            c.create_text(
                x0 + w / 2, y0 + 27, text="等待分析", fill=COLORS["subtext"],
                font=FONTS["small"], tags=("situation",))
            c.create_text(
                x0 + 10, y0 + 50, text=cap_txt, anchor="w",
                fill=COLORS["subtext"], font=FONTS["small"], tags=("situation",))
            return
        wrb = max(0.0, min(1.0, float(wr)))           # 黑胜率
        # 行1 右：目差文案
        if score is None:
            stxt = "黑 %.0f%% · 白 %.0f%%" % (wrb * 100, (1 - wrb) * 100)
        elif float(score) > 0.05:
            stxt = "黑 %.0f%% · 黑+%.1f目" % (wrb * 100, float(score))
        elif float(score) < -0.05:
            stxt = "白 %.0f%% · 白+%.1f目" % ((1 - wrb) * 100, abs(float(score)))
        else:
            stxt = "均衡 · 黑 %.0f%%" % (wrb * 100)
        col = COLORS["text"] if (score is None or float(score) >= 0) else COLORS["purple"]
        c.create_text(
            x0 + w - 10, y0 + 11, text=stxt, anchor="e", fill=col,
            font=FONTS["small"], tags=("situation",))
        # 行2：黑白胜率条
        bar_x, bar_y, bar_w, bar_h = x0 + 10, y0 + 22, w - 20, 8
        bx = bar_x + bar_w * wrb
        c.create_rectangle(
            bar_x, bar_y, bx, bar_y + bar_h, fill=COLORS["black"], outline="",
            tags=("situation",))
        c.create_rectangle(
            bx, bar_y, bar_x + bar_w, bar_y + bar_h, fill=COLORS["white"],
            outline=COLORS["muted"], tags=("situation",))
        # 行3：双方实地（ownership 高置信估算）+ 提子
        terr_txt = ""
        if own:
            split = ownership_territory_split(own, size=self.size)
            terr_txt = "实地 黑%d·白%d  " % (split["b_strong"], split["w_strong"])
        c.create_text(
            x0 + 10, y0 + 50, text=terr_txt + cap_txt, anchor="w",
            fill=COLORS["subtext"], font=FONTS["small"], tags=("situation",))

    def _draw_training_overlay(self):
        """训练模式常驻目标 banner：阶段/手数范围/原目损→目标/进度 + 上一手反馈。

        仿 _draw_situation_overlay 的 HUD 形态，放左上避免与形势 HUD 冲突；训练
        激活时常驻，回答"我在练哪个阶段、目标是什么、上一手好坏"——补足目标感缺失。
        """
        tr = self._training
        if not (tr and tr.get("active") and not tr.get("finished")):
            return
        if not getattr(self, "canvas", None):
            return
        task = tr.get("task") or {}
        c = self.canvas
        w = 150 if self.BOARD_PIX < 440 else 206
        h = 88
        x0, y0 = 6, 6
        # PIL 圆角卡片底（替代 create_rectangle 直角）
        rect_img = self._get_rounded_rect(w, h, 10, COLORS["card"], 235)
        if rect_img is not None:
            c.create_image(int(x0 + w / 2), int(y0 + h / 2), image=rect_img, tags=("training_banner",))
        else:
            c.create_rectangle(
                x0, y0, x0 + w, y0 + h, fill=COLORS["card"], outline=COLORS["muted"],
                width=1, tags=("training_banner",))
        c.create_rectangle(
            x0, y0, x0 + 3, y0 + h, fill=COLORS["accent"], outline="",
            tags=("training_banner",))
        c.create_text(
            x0 + 10, y0 + 12,
            text="阶段训练 · %s" % (task.get("phaseLabel") or "中盘"),
            anchor="w", fill=COLORS["subtext"],
            font=("Microsoft YaHei UI", 8, "bold"), tags=("training_banner",))
        start_m = int(task.get("startMove") or 1)
        end_m = int(task.get("endMove") or (start_m + 35))
        orig_loss = task.get("avgLoss")
        try:
            orig_txt = "%.1f" % float(orig_loss) if orig_loss is not None else "—"
            target_txt = ("%.1f" % max(1.0, float(orig_loss) * 0.5)
                          if orig_loss is not None else "—")
        except (TypeError, ValueError):
            orig_txt = target_txt = "—"
        c.create_text(
            x0 + 10, y0 + 31,
            text="手数 %d-%d  原目损 %s → 目标 %s" % (
                start_m, end_m, orig_txt, target_txt),
            anchor="w", fill=COLORS["text"], font=FONTS["small"],
            tags=("training_banner",))
        target_moves = int(tr.get("target_moves") or 36)
        user_target = max(1, target_moves // 2)
        user_done = sum(
            1 for n in tr.get("nodes", [])
            if n.move and n.move[0] == tr.get("user_color"))
        c.create_text(
            x0 + 10, y0 + 50, text="进度 %d/%d 手" % (user_done, user_target),
            anchor="w", fill=COLORS["text"], font=FONTS["small"],
            tags=("training_banner",))
        fb = getattr(self, "_training_last_feedback", None)
        if fb:
            loss_txt = "—" if fb.get("loss") is None else "%.1f 目" % fb["loss"]
            fb_col = {"best": COLORS.get("accent"), "good": COLORS.get("green"),
                      "inaccuracy": COLORS.get("amber"),
                      "blunder": COLORS.get("red")}.get(
                          fb.get("key"), COLORS["text"])
            c.create_text(
                x0 + 10, y0 + 69,
                text="上一手 %s · %s · %s" % (
                    fb.get("coord"), fb.get("label"), loss_txt),
                anchor="w", fill=fb_col, font=FONTS["small"],
                tags=("training_banner",))
        else:
            c.create_text(
                x0 + 10, y0 + 69, text="轮到你落子，下完即显示这手好坏",
                anchor="w", fill=COLORS["subtext"], font=FONTS["small"],
                tags=("training_banner",))

    def toggle_graph(self):
        if self._graph_win is not None and self._graph_win.winfo_exists():
            self._close_graph()
            return
        win = tk.Toplevel(self)
        self._prepare_child_window(
            win, "胜率 / 目差曲线 + 问题手分布", 560, 458,
            resizable=(False, False))
        ttk.Label(win, text="上：x=手数  y=黑视角目差（上=黑好/下=白好）每手色=质量  点击跳转",
                  foreground=COLORS["subtext"]).pack(anchor="w", padx=6, pady=(4, 0))
        self._graph_canvas = tk.Canvas(win, width=540, height=250, bg=COLORS["card"],
                                       highlightthickness=1, highlightbackground=COLORS["muted"])
        self._graph_canvas.pack(padx=8, pady=4)
        self._graph_canvas.bind("<Button-1>", self._on_graph_click)
        ttk.Label(win, text="中：整盘问题手热力概览（色块=该手有问题，红=恶手/橙=疑问/蓝=轻微，一眼定位最该看的手）",
                  foreground=COLORS["subtext"]).pack(anchor="w", padx=8)
        self._graph_heat_canvas = tk.Canvas(win, width=540, height=20, bg=COLORS["card"],
                                            highlightthickness=1, highlightbackground=COLORS["muted"])
        self._graph_heat_canvas.pack(padx=8, pady=(0, 2))
        self._graph_heat_canvas.bind("<Button-1>", self._on_heat_click)
        ttk.Label(win, text="下：问题手目损分布（柱高=目损，红=恶手，橙=疑问）",
                  foreground=COLORS["subtext"]).pack(anchor="w", padx=8)
        self._graph_loss_canvas = tk.Canvas(win, width=540, height=70, bg=COLORS["card"],
                                            highlightthickness=1, highlightbackground=COLORS["muted"])
        self._graph_loss_canvas.pack(padx=8, pady=(0, 4))
        self._graph_legend_label = tk.Label(
            win, text="", justify=tk.LEFT, anchor="w", wraplength=540,
            bg=COLORS["card"], fg=COLORS["subtext"], font=FONTS["small"])
        self._graph_legend_label.pack(fill="x", padx=8, pady=(0, 6))
        self._graph_win = win
        win.protocol("WM_DELETE_WINDOW", self._close_graph)
        self._refresh_graph()

    def _refresh_graph(self, rr=None):
        """重绘胜率曲线（scoreLead 折线 + 失误红点 + 当前进度竖线）。"""
        if self._graph_canvas is None or self._graph_win is None or not self._graph_win.winfo_exists():
            return
        if rr is None:
            rr = ReviewReport(self.tree)
        c = self._graph_canvas
        c.delete("all")
        W, H = 540, 250
        pad_l, pad_r, pad_t, pad_b = 34, 12, 14, 22
        plot_w = W - pad_l - pad_r
        plot_h = H - pad_t - pad_b
        series = rr.score_lead_series()
        if not series:
            self._graph_pts = []
            c.create_text(W / 2, H / 2, text="（尚无分析数据——点【分析整盘】）", fill=COLORS["subtext"])
            return
        max_move = max((m for m, _ in series), default=1) or 1
        span = max((max(abs(v) for _, v in series) if series else 0.0), 5.0) * 1.15   # 对称量程

        def xy(move, val):
            x = pad_l + (move / max_move) * plot_w
            y = pad_t + plot_h / 2 - (val / span) * (plot_h / 2)
            return x, y

        zy = pad_t + plot_h / 2
        c.create_line(pad_l, zy, W - pad_r, zy, fill=COLORS["muted"], dash=(3, 3))   # 0 目中线
        c.create_text(8, zy, text="0", font=FONTS["small"], fill=COLORS["subtext"])
        c.create_text(8, pad_t + 8, text="+%g" % span, font=FONTS["small"], fill=COLORS["subtext"])
        c.create_text(8, H - pad_b, text="-%g" % span, font=FONTS["small"], fill=COLORS["subtext"])
        # 高光时刻 / 疑似AI 区间背景标注（对标涨棋网 P3-4）
        focus = self._review_focus_color()
        for iv in highlight_intervals(rr.evaluate(), color=focus):
            x0 = pad_l + (iv["start"] / max_move) * plot_w
            x1 = pad_l + (iv["end"] / max_move) * plot_w
            fill = COLORS["green"] if iv["kind"] == "highlight" else COLORS["purple"]
            c.create_rectangle(x0, pad_t, x1, H - pad_b, fill=fill,
                               outline="", stipple="gray25", tags=("graph-highlight",))
        pts = [xy(m, v) for m, v in series]
        for i in range(len(pts) - 1):
            c.create_line(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1], fill=COLORS["accent"])
        # 每手命中分级标注（对标涨棋网 P3：一选/好手/疑问/恶手 色块）
        focus = self._review_focus_color()
        eval_by_move = {e.move_number: e for e in rr.evaluate()}
        self._graph_pts = []
        for (m, _v), (px, py) in zip(series, pts):
            self._graph_pts.append((m, px, py))
            e = eval_by_move.get(m)
            color, r = COLORS["muted"], 2            # 默认灰（未分析 / 一般 / 非聚焦方）
            if e is not None and e.analyzed and (focus is None or e.color == focus):
                loss = e.loss if e.loss is not None else 0.0
                if loss >= GRADE_BAD:
                    color, r = COLORS["red"], 3       # 恶手
                elif loss >= GRADE_DOUBT:
                    color, r = COLORS.get("amber"), 3           # 疑问
                elif e.agreement_rank == 0:
                    color, r = COLORS["green"], 3     # AI 一选
                elif e.agreement_rank is not None and e.agreement_rank < 3:
                    color, r = COLORS["accent"], 2.5  # 前3好手
            c.create_oval(px - r, py - r, px + r, py + r, fill=color, outline="")
        # 图例（右上角）
        lx = W - pad_r
        for label, col in reversed([("恶手", COLORS["red"]), ("疑问", COLORS.get("amber")),
                                    ("好手", COLORS["accent"]), ("一选", COLORS["green"])]):
            c.create_text(lx, pad_t + 2, text=label, anchor="e",
                          fill=col, font=FONTS["small"])
            lx -= 30
        cur_move = self.tree.current.depth
        if 0 <= cur_move <= max_move:
            cx = pad_l + (cur_move / max_move) * plot_w
            c.create_line(cx, pad_t, cx, H - pad_b, fill="#fb0", dash=(2, 2))         # 当前手竖线
        # 高光/疑似AI 区间详情列表（对标涨棋网 P4）
        if hasattr(self, "_graph_legend_label") and self._graph_legend_label is not None:
            intervals = highlight_intervals(rr.evaluate(), color=self._review_focus_color())
            if intervals:
                parts = ["%s 第%d-%d手" % (
                    "高光" if iv["kind"] == "highlight" else "疑似AI",
                    iv["start"], iv["end"]) for iv in intervals]
                self._graph_legend_label.config(
                    text="连续区间：" + "；".join(parts)
                         + "（绿=连续好手，紫=连续一选疑似AI）",
                    fg=COLORS["text"])
            else:
                self._graph_legend_label.config(
                    text="暂无连续高光 / 疑似AI 区间（≥5 手连续）", fg=COLORS["subtext"])
        self._refresh_loss_bar(rr)
        self._refresh_heat_bar(rr)

    def _refresh_loss_bar(self, rr):
        """问题手目损分布柱图（对标涨棋网 P5）：横坐标手数，柱高=loss，恶手红/疑问橙。"""
        c = getattr(self, "_graph_loss_canvas", None)
        if c is None:
            return
        c.delete("all")
        W, H = 540, 70
        pad_l, pad_r, pad_b, pad_t = 34, 12, 12, 6
        plot_w = W - pad_l - pad_r
        plot_h = H - pad_t - pad_b
        c.create_line(pad_l, H - pad_b, W - pad_r, H - pad_b, fill=COLORS["muted"])
        focus = self._review_focus_color()
        evs = [e for e in rr.evaluate()
               if e.analyzed and e.loss is not None
               and (focus is None or e.color == focus)]
        if not evs:
            c.create_text(W / 2, H / 2, text="（无问题手目损数据）",
                          fill=COLORS["subtext"], font=FONTS["small"])
            return
        max_move = max(e.move_number for e in evs) or 1
        max_loss = max(max(e.loss for e in evs), GRADE_BAD) * 1.15
        for e in evs:
            x = pad_l + (e.move_number / max_move) * plot_w
            h = (e.loss / max_loss) * plot_h
            col = (COLORS["red"] if e.loss >= GRADE_BAD
                   else COLORS.get("amber") if e.loss >= GRADE_DOUBT else COLORS["accent_m"])
            c.create_line(x, H - pad_b, x, H - pad_b - h, fill=col, width=2)
            if e.loss >= GRADE_DOUBT:
                c.create_text(x, H - pad_b - h - 4, text=str(e.move_number),
                              fill=col, font=FONTS["small"])

    def _refresh_heat_bar(self, rr=None):
        """整盘问题手热力概览色带：每个问题手在对应手数位置画色块，按严重度着色。

        对标 LizzieYzy 底部热力条——一眼定位整盘哪里有雷。与上方曲线、下方柱状图
        共用同一手数轴（max_move 来自 score_lead_series，和曲线一致），三者竖向对齐。
        """
        if (self._graph_heat_canvas is None or self._graph_win is None
                or not self._graph_win.winfo_exists()):
            return
        if rr is None:
            rr = ReviewReport(self.tree)
        c = self._graph_heat_canvas
        c.delete("all")
        W, H = 540, 20
        pad_l, pad_r = 34, 12
        plot_w = W - pad_l - pad_r
        series = rr.score_lead_series()
        if not series:
            c.create_text(W / 2, H / 2, text="（尚无分析数据）",
                          fill=COLORS["subtext"], font=FONTS["small"])
            return
        max_move = max((m for m, _ in series), default=1) or 1
        # 每手占的像素宽度（用于色块尺寸），最小 5px 保证可见
        cell_w = max(plot_w / max(max_move, 1), 5)
        focus = self._review_focus_color()
        evs = [e for e in rr.evaluate()
               if e.analyzed and e.loss is not None and e.loss >= GRADE_GOOD
               and (focus is None or e.color == focus)]
        for e in evs:
            x = pad_l + (e.move_number / max_move) * plot_w
            if e.loss >= GRADE_BAD:
                col = COLORS["red"]
            elif e.loss >= GRADE_DOUBT:
                col = COLORS.get("amber")
            else:
                col = COLORS["accent_m"]
            # 色块：以该手位置为中心，宽度=cell_w，高度近满
            c.create_rectangle(x - cell_w / 2, 2, x + cell_w / 2, H - 2,
                               fill=col, outline="", tags=("heat-cell",))
        # 当前进度竖线（与曲线、柱状图保持同步指示）
        cur_move = self.tree.current.depth
        if 0 <= cur_move <= max_move:
            cx = pad_l + (cur_move / max_move) * plot_w
            c.create_line(cx, 0, cx, H, fill=COLORS["amber"], width=1, dash=(2, 2))

    def _on_heat_click(self, event):
        """点击热力概览色带 → 跳到最近的那一手（与曲线点击同语义）。"""
        rr = ReviewReport(self.tree)
        series = rr.score_lead_series()
        if not series:
            return
        if self._block_jump("跳转"):
            return
        W = 540
        pad_l, pad_r = 34, 12
        plot_w = W - pad_l - pad_r
        max_move = max((m for m, _ in series), default=1) or 1
        # 由点击 x 反推最近手数
        if event.x < pad_l:
            target_move = 0
        elif event.x > W - pad_r:
            target_move = max_move
        else:
            target_move = round((event.x - pad_l) / plot_w * max_move)
            target_move = max(0, min(target_move, max_move))
        node = rr.node_at_move(target_move)
        if node is None or node is self.tree.current:
            return
        self.tree.current = node
        self._after_navigate()

    def _on_graph_click(self, event):
        """点击曲线 → 跳到最近的那一手。"""
        if not self._graph_pts:
            return
        if self._block_jump("跳转"):
            return
        x = event.x
        move_number = min(self._graph_pts, key=lambda p: abs(p[1] - x))[0]
        node = ReviewReport(self.tree).node_at_move(move_number)
        if node is None or node is self.tree.current:
            return
        if self.scoring_mode:
            self._set_msg("点目模式下不能跳转，请先【退出点目】")
            return
        self.tree.current = node
        self._after_navigate()

    def _close_graph(self):
        if self._graph_win is not None:
            try:
                self._graph_win.destroy()
            except Exception:
                pass
        self._graph_win = None
        self._graph_canvas = None
        self._graph_heat_canvas = None
        self._graph_pts = []

    # ===================== 棋力评估（阶段进度条标亮）=====================
    def toggle_strength_eval(self):
        """棋力评估子功能：把整盘按布局/中盘/关子摊成进度条，下得好的阶段标亮。"""
        if self._strength_win is not None and self._strength_win.winfo_exists():
            self._strength_win.lift(); self._strength_win.focus_set()
            return
        win = tk.Toplevel(self)
        self._prepare_child_window(
            win, "棋力评估 · 阶段进度（下得好的阶段标亮）", 560, 340,
            resizable=(False, False))
        top = tk.Frame(win, bg=COLORS["card"])
        top.pack(fill="x", padx=8, pady=(6, 2))
        tk.Label(top, text="视角：", bg=COLORS["card"], fg=COLORS["text"]).pack(side=tk.LEFT)
        focus = self._review_focus_color()
        default = {"B": "黑方", "W": "白方"}.get(focus, "双方")
        self._strength_side_var = tk.StringVar(value=default)
        ttk.OptionMenu(top, self._strength_side_var, default, "双方", "黑方", "白方",
                       command=lambda _v: self._refresh_strength_eval()).pack(side=tk.LEFT, padx=4)
        tk.Label(
            top,
            text="绿=下得不错(标亮) · 橙=有波动 · 红=问题较多 · 灰=无数据 · 点击跳到该阶段",
            bg=COLORS["card"], fg=COLORS["subtext"], font=FONTS["small"]).pack(side=tk.LEFT, padx=8)
        # 进度条：Canvas 绘制，显示用（不拖动），点击可跳到该阶段起始手
        self._strength_canvas = tk.Canvas(
            win, width=540, height=48, bg=COLORS["card"],
            highlightthickness=1, highlightbackground=COLORS["muted"])
        self._strength_canvas.pack(padx=8, pady=4)
        self._strength_canvas.bind("<Button-1>", self._on_strength_bar_click)
        tv = ttk.Treeview(win, columns=("range", "moves", "loss", "rank", "quality", "gnd"),
                          show="headings", height=3, selectmode="none")
        for col, title, w, anchor in [
                ("range", "手数区间", 90, "center"),
                ("moves", "已分析", 60, "center"),
                ("loss", "平均目损", 80, "center"),
                ("rank", "档位", 70, "center"),
                ("quality", "质量", 80, "center"),
                ("gnd", "好·疑·恶", 110, "center")]:
            tv.heading(col, text=title)
            tv.column(col, width=w, minwidth=w, stretch=False, anchor=anchor)
        tv.pack(fill="x", padx=8, pady=(0, 4))
        tv.tag_configure("good", background="#dff5e1")
        self._strength_tv = tv
        self._strength_summary_lbl = tk.Label(
            win, text="", bg=COLORS["card"], fg=COLORS["text"],
            font=FONTS["ui"], justify=tk.LEFT, wraplength=520)
        self._strength_summary_lbl.pack(anchor="w", padx=10, pady=(2, 8))
        self._strength_win = win
        win.protocol("WM_DELETE_WINDOW", self._close_strength_eval)
        self._refresh_strength_eval()

    def _refresh_strength_eval(self, rr=None):
        if (self._strength_canvas is None or self._strength_win is None
                or not self._strength_win.winfo_exists()):
            return
        if rr is None:
            rr = ReviewReport(self.tree)
        color = self._strength_side_var.get() if self._strength_side_var else "双方"
        color_arg = None if color == "双方" else ("B" if color == "黑方" else "W")
        total = max(0, len(rr.mainline_nodes()) - 1)
        segs = rr.phase_bar_segments(total=total, color=color_arg)
        c = self._strength_canvas
        c.delete("all")
        W, H, pad = 540, 48, 8
        bar_y, bar_h = 16, 24
        if not any(s["moves"] > 0 for s in segs):
            # 无数据：不画段，也不武装点击跳转（避免空状态点击误跳棋盘）
            self._strength_segs = []
            c.create_text(W / 2, H / 2,
                          text="（尚无分析数据——点【分析整盘】后这里标亮下得好的阶段）",
                          fill=COLORS["subtext"], font=FONTS["small"])
            self._render_strength_breakdown(segs, color_arg)
            return
        self._strength_segs = segs
        for s in segs:
            lo_f, hi_f = s["frac"]
            x0 = pad + lo_f * (W - 2 * pad)
            x1 = pad + hi_f * (W - 2 * pad)
            if s["is_good"]:                       # 下得不错 → 实绿标亮
                fill, outline, width, stip, lblcol = (
                    COLORS["green"], COLORS["green"], 3, "", "#ffffff")
            elif s["moves"] > 0:                    # 有数据但非优秀 → 按质量着色并减淡
                fill = {"有波动": COLORS.get("amber"), "问题较多": COLORS["red"]}.get(
                    s["quality"], COLORS["muted"])
                outline, width, stip, lblcol = (
                    COLORS["muted"], 1, "gray75", COLORS["text"])
            else:                                   # 无数据 → 灰
                fill, outline, width, stip, lblcol = (
                    COLORS["muted"], COLORS["muted"], 1, "gray50", COLORS["subtext"])
            c.create_rectangle(x0, bar_y, x1, bar_y + bar_h, fill=fill, outline=outline,
                               width=width, stipple=stip, tags=("strength-seg",))
            c.create_text((x0 + x1) / 2, bar_y + bar_h / 2,
                          text="%s %s" % (s["label"], s["rank"]),
                          fill=lblcol, font=("Microsoft YaHei UI", 8, "bold"),
                          tags=("strength-seg",))
        if total > 0:
            cur = self.tree.current.depth
            if 0 <= cur <= total:
                cx = pad + (cur / total) * (W - 2 * pad)
                c.create_line(cx, bar_y - 3, cx, bar_y + bar_h + 3,
                              fill="#fb0", dash=(2, 2), tags=("strength-seg",))
        self._render_strength_breakdown(segs, color_arg)

    def _render_strength_breakdown(self, segs, color_arg):
        tv = self._strength_tv
        if tv is not None:
            tv.delete(*tv.get_children())
            for s in segs:
                lo, hi = s["range"]
                rng = "%d–%d" % (lo, hi) if hi >= lo else str(lo)
                loss = "—" if s["avg_loss"] is None else "%.2f" % s["avg_loss"]
                gnd = "%d·%d·%d" % (s["good"], s["doubt"], s["bad"])
                tv.insert("", "end",
                          values=(rng, s["moves"], loss, s["rank"], s["quality"], gnd),
                          tags=("good",) if s["is_good"] else ())
        if self._strength_summary_lbl is not None:
            good = [s for s in segs if s["is_good"]]
            analyzed = [s for s in segs if s["moves"] > 0]
            side_txt = {"B": "黑方", "W": "白方"}.get(color_arg, "双方")
            if not analyzed:
                txt = "（%s）尚无已分析阶段，点【分析整盘】后这里会标亮下得好的阶段。" % side_txt
            elif good:
                names = "、".join("%s（%s，均损 %s）" % (
                    s["label"], s["quality"], "%.2f" % s["avg_loss"]) for s in good)
                txt = "（%s）下得不错的阶段：%s — 已在进度条标亮。" % (side_txt, names)
            else:
                best = min(analyzed, key=lambda s: (s["avg_loss"] if s["avg_loss"] is not None else 9))
                txt = "（%s）本局没有明显占优的阶段；相对最稳的是 %s（%s，均损 %s）。" % (
                    side_txt, best["label"], best["quality"],
                    "—" if best["avg_loss"] is None else "%.2f" % best["avg_loss"])
            self._strength_summary_lbl.config(text=txt)

    def _on_strength_bar_click(self, event):
        """点击阶段进度条 → 跳到该阶段起始手（进度条为显示用、非拖动；点击为额外便利）。"""
        if not self._strength_segs:
            return
        if self._block_jump("跳转"):
            return
        pad, W = 8, 540
        for s in self._strength_segs:
            lo_f, hi_f = s["frac"]
            x0 = pad + lo_f * (W - 2 * pad)
            x1 = pad + hi_f * (W - 2 * pad)
            if x0 <= event.x <= x1:
                lo, _hi = s["range"]
                node = ReviewReport(self.tree).node_at_move(max(1, lo))
                if node is not None and node is not self.tree.current:
                    self.tree.current = node
                    self._after_navigate()
                return

    def _close_strength_eval(self):
        if self._strength_win is not None:
            try:
                self._strength_win.destroy()
            except Exception:
                pass
        self._strength_win = None
        self._strength_canvas = None
        self._strength_tv = None
        self._strength_summary_lbl = None
        self._strength_segs = []

    # ---- 分支 / 树视图 ----
    def do_prev_branch(self):
        if self._block_in_scoring("切换分支"):
            return
        if self.tree.goto_sibling(-1):
            self._after_navigate()

    def do_next_branch(self):
        if self._block_in_scoring("切换分支"):
            return
        if self.tree.goto_sibling(1):
            self._after_navigate()

    def toggle_treeview(self):
        if self._tv_win is not None and self._tv_win.winfo_exists():
            self._tv_win.lift(); self._tv_win.focus_set()
            return
        win = tk.Toplevel(self)
        self._prepare_child_window(
            win, "棋局树", 280, 420, minsize=(240, 320))
        tv = ttk.Treeview(win)
        tv.pack(fill="both", expand=True)
        tv.bind("<<TreeviewSelect>>", self._on_tv_select)
        self._tv_win, self._tv, self._tv_map = win, tv, {}
        win.protocol("WM_DELETE_WINDOW", self._close_treeview)
        self._populate_treeview()
        self.btn_tree.configure(text="树视图 ✓")
        self._set_toggle(self.btn_tree, True)

    def _close_treeview(self):
        if self._tv_win is not None:
            try:
                self._tv_win.destroy()
            except tk.TclError:
                pass
        self._tv_win, self._tv, self._tv_map = None, None, {}
        self.btn_tree.configure(text="树视图")
        self._set_toggle(self.btn_tree, False)

    def _populate_treeview(self):
        tv = self._tv
        if tv is None:
            return
        tv.delete(*tv.get_children())
        self._tv_map = {}
        def add(node, parent_iid):
            if node.move is None:
                label = "开始"
            else:
                cl, coord = node.move
                label = ("%s pass" % cl) if coord is None else (
                    "%s %s" % (cl, xy_to_point(coord[0], coord[1], self.size)))
            if len(node.children) > 1:
                label += "  (%d 分支)" % len(node.children)
            iid = tv.insert(parent_iid, "end", text=label, open=True)
            self._tv_map[iid] = node
            for ch in node.children:
                add(ch, iid)
        add(self.tree.root, "")
        for iid, node in self._tv_map.items():
            if node is self.tree.current:
                tv.selection_set(iid)
                tv.focus(iid)
                tv.see(iid)
                break

    def _on_tv_select(self, event):
        if self._tv is None:
            return
        if self._block_jump("跳转节点"):
            return
        sel = self._tv.selection()
        if not sel:
            return
        node = self._tv_map.get(sel[0])
        if node is None or node is self.tree.current:
            return
        self.tree.current = node
        self._after_navigate()

    def _refresh_treeview(self):
        if self._tv_win is not None and self._tv_win.winfo_exists() and self._tv is not None:
            self._populate_treeview()

    # ---- 棋谱库（导入 SGF 后自动入库；双击打开项目快照）----
    def _enqueue_records_for_analysis(self, records):
        added = 0
        for rec in records or []:
            if not rec or not rec.get("id"):
                continue
            _task, created = self._analysis_queue.enqueue(
                rec.get("id"), rec.get("name", ""), rec.get("totalNodes", 0))
            added += bool(created)
        self._refresh_analysis_queue_window()
        return added

    def _queue_has_active_work(self):
        return any(
            task.get("status") in ("queued", "running", "paused")
            for task in self._analysis_queue.tasks())

    def _kick_analysis_queue(self):
        """在前台空闲时领取一盘棋，逐节点分析；任务间相互隔离。"""
        if self._analysis_queue.is_paused() or self._analysis_queue_current:
            return
        if not (self.client and self.client.is_alive() and self.client.ready):
            if self._queue_has_active_work() and not (self.client and self.client.is_alive()):
                self._start_katago(quiet=True)
            return
        if (self.scoring_mode or self.guard.pending_count() > 0
                or self._problem_compare_pending or self._style_verification_pending
                or self._training_cache_bg_pending or self._training_cache_bg_current
                or self._library_bg_pending or self._library_bg_current
                or (self._training and self._training.get("active"))
                or self._drill_active() or getattr(self, "_drill_overlay", None) is not None
                or (self._mistake_review and self._mistake_review.get("active"))):
            return
        task = self._analysis_queue.claim_next()
        if not task:
            return
        rec = get_record(task.get("recordId"))
        try:
            if not rec or not rec.get("projectPath") or not os.path.exists(rec.get("projectPath")):
                raise ValueError("棋谱库项目快照不存在")
            tree, data = load_project(rec.get("projectPath"))
            rr = ReviewReport(tree)
            nodes = rr.mainline_nodes()
            todo = [node for node in nodes if node.analysis is None]
            done = len(nodes) - len(todo)
            ctx = {
                "task_id": task.get("id"), "record_id": rec.get("id"),
                "name": rec.get("name", ""), "tree": tree,
                "rules": data.get("rules", rec.get("rules") or self.rules),
                "komi": data.get("komi", rec.get("komi") if rec.get("komi") is not None else self.komi),
                "visits": int(self.cfg.get("max_visits", 200)),
                "todo": todo, "index": 0, "done": done, "total": len(nodes),
            }
            self._analysis_queue_current = ctx
            self._analysis_queue.update(
                task.get("id"), done, len(nodes), "正在分析 %d/%d" % (done, len(nodes)))
            if not todo:
                update_project_snapshot(
                    rec.get("id"), tree, rules=ctx["rules"], komi=ctx["komi"])
                self._analysis_queue.finish(task.get("id"))
                self._analysis_queue_current = None
                self._refresh_analysis_queue_window()
                self.after(30, self._kick_analysis_queue)
                return
            self._set_msg("分析队列：%s（%d/%d）" % (ctx["name"], done, len(nodes)))
            self._send_next_analysis_queue_request()
        except Exception as exc:
            self._analysis_queue.fail(task.get("id"), str(exc))
            self._analysis_queue_current = None
            self._refresh_analysis_queue_window()
            self.after(30, self._kick_analysis_queue)

    def _send_next_analysis_queue_request(self):
        ctx = self._analysis_queue_current
        if not ctx or self._analysis_queue_pending:
            return
        if self._analysis_queue.is_paused():
            self._finish_paused_analysis_queue_task()
            return
        if (self.guard.pending_count() > 0 or self.scoring_mode
                or self._problem_compare_pending
                or (self._training and self._training.get("active"))
                or self._drill_active()
                or (self._mistake_review and self._mistake_review.get("active"))):
            self.after(300, self._send_next_analysis_queue_request)
            return
        index = int(ctx.get("index") or 0)
        todo = ctx.get("todo") or []
        if index >= len(todo):
            self._complete_analysis_queue_task()
            return
        node = todo[index]
        query = self._analysis_query_for(
            ctx["tree"], node, ctx["rules"], ctx["komi"], ctx["visits"])
        try:
            if not (self.client and self.client.is_alive() and self.client.ready):
                raise RuntimeError("KataGo 当前不可用")
            qid = self.client.analyze(query)
        except Exception as exc:
            self._fail_analysis_queue_task("请求发送失败：%s" % exc)
            return
        self._analysis_queue_pending[qid] = {"ctx": ctx, "node": node}

    def _handle_analysis_queue_result(self, rid, resp):
        item = self._analysis_queue_pending.pop(rid, None)
        if not item:
            return
        ctx = item.get("ctx") or {}
        if "error" in resp:
            self._fail_analysis_queue_task(resp.get("error") or "KataGo 返回错误")
            return
        item["node"].analysis = resp
        ctx["index"] = int(ctx.get("index") or 0) + 1
        ctx["done"] = int(ctx.get("done") or 0) + 1
        self._analysis_queue.update(
            ctx.get("task_id"), ctx["done"], ctx["total"],
            "正在分析 %d/%d" % (ctx["done"], ctx["total"]))
        # 周期性写回项目快照。正常暂停/停止仍会立即保存；异常断电时最多重算少量节点。
        if ctx["done"] % 10 == 0:
            try:
                self._save_analysis_queue_snapshot(ctx)
            except Exception as exc:
                self._fail_analysis_queue_task("进度保存失败：%s" % exc)
                return
        self._refresh_analysis_queue_window()
        if self._analysis_queue.is_paused():
            self._finish_paused_analysis_queue_task()
        elif ctx["index"] >= len(ctx.get("todo") or []):
            self._complete_analysis_queue_task()
        else:
            self.after(10, self._send_next_analysis_queue_request)

    def _save_analysis_queue_snapshot(self, ctx):
        if not ctx:
            return
        update_project_snapshot(
            ctx.get("record_id"), ctx.get("tree"),
            rules=ctx.get("rules"), komi=ctx.get("komi"))

    def _complete_analysis_queue_task(self):
        ctx = self._analysis_queue_current
        if not ctx:
            return
        try:
            self._save_analysis_queue_snapshot(ctx)
            self._analysis_queue.update(
                ctx["task_id"], ctx["total"], ctx["total"], "分析完成")
            self._analysis_queue.finish(ctx["task_id"])
            self._set_msg("分析队列完成：%s（%d/%d）" % (
                ctx["name"], ctx["total"], ctx["total"]))
        except Exception as exc:
            self._analysis_queue.fail(ctx["task_id"], "保存失败：%s" % exc)
        self._analysis_queue_current = None
        self._refresh_library_window()
        self._refresh_analysis_queue_window()
        self.after(30, self._kick_analysis_queue)

    def _finish_paused_analysis_queue_task(self):
        ctx = self._analysis_queue_current
        if not ctx:
            return
        try:
            self._save_analysis_queue_snapshot(ctx)
            # pause() 已把 running 状态变为 paused；只补进度信息。
            self._analysis_queue.update(
                ctx["task_id"], ctx["done"], ctx["total"],
                "已暂停并保存 %d/%d" % (ctx["done"], ctx["total"]))
        except Exception as exc:
            self._analysis_queue.fail(ctx["task_id"], "暂停保存失败：%s" % exc)
        self._analysis_queue_current = None
        self._refresh_library_window()
        self._refresh_analysis_queue_window()

    def _fail_analysis_queue_task(self, message):
        ctx = self._analysis_queue_current
        if not ctx:
            return
        try:
            self._save_analysis_queue_snapshot(ctx)
        except Exception:
            pass
        self._analysis_queue.fail(ctx["task_id"], str(message))
        self._set_msg("分析队列失败但已继续下一盘：%s｜%s" % (ctx["name"], message))
        self._analysis_queue_current = None
        self._refresh_library_window()
        self._refresh_analysis_queue_window()
        self.after(30, self._kick_analysis_queue)

    def _interrupt_analysis_queue(self, message="引擎已停止，等待继续"):
        ctx = self._analysis_queue_current
        self._analysis_queue_pending = {}
        if not ctx:
            return
        try:
            self._save_analysis_queue_snapshot(ctx)
        except Exception:
            pass
        self._analysis_queue.release(ctx["task_id"], message)
        self._analysis_queue_current = None
        self._refresh_analysis_queue_window()

    def open_analysis_queue(self):
        if self._analysis_queue_win is not None and self._analysis_queue_win.winfo_exists():
            self._analysis_queue_win.lift()
            self._refresh_analysis_queue_window()
            return
        win = tk.Toplevel(self)
        self._prepare_child_window(win, "批量分析队列", 820, 440, minsize=(700, 360))
        top = tk.Frame(win, bg=COLORS["bg"])
        top.pack(fill="x", padx=10, pady=(10, 6))
        tk.Label(top, text="本地批量分析队列", font=FONTS["title"],
                 bg=COLORS["bg"], fg=COLORS["text"]).pack(side=tk.LEFT)
        tk.Label(top, text="失败不会阻塞后续棋谱；队列与进度会自动保存",
                 bg=COLORS["bg"], fg=COLORS["subtext"], font=FONTS["small"]).pack(
                     side=tk.LEFT, padx=(12, 0))
        frame = tk.Frame(win, bg=COLORS["card"])
        frame.pack(fill="both", expand=True, padx=10, pady=4)
        tv = ttk.Treeview(
            frame, columns=("name", "status", "progress", "attempts", "message", "time"),
            show="headings", height=12)
        for col, label, width, anchor in [
                ("name", "棋谱", 220, "w"), ("status", "状态", 72, "center"),
                ("progress", "进度", 82, "center"), ("attempts", "尝试", 48, "center"),
                ("message", "说明", 250, "w"), ("time", "更新时间", 140, "w")]:
            tv.heading(col, text=label); tv.column(col, width=width, anchor=anchor)
        tv.pack(fill="both", expand=True, padx=6, pady=6)
        bar = self._dialog_button_bar(win)
        self._make_button(bar, "关闭", win.destroy, variant="default").pack(side=tk.RIGHT)
        self._make_button(bar, "重试失败", self._retry_failed_analysis_queue, variant="default").pack(
            side=tk.RIGHT, padx=8)
        self._make_button(bar, "暂停", self._pause_analysis_queue, variant="default").pack(
            side=tk.RIGHT, padx=8)
        self._make_button(bar, "继续", self._resume_analysis_queue, variant="accent").pack(side=tk.RIGHT, padx=8)
        self._analysis_queue_win = win
        self._analysis_queue_tv = tv
        win.protocol("WM_DELETE_WINDOW", self._close_analysis_queue_window)
        self._refresh_analysis_queue_window()

    def _close_analysis_queue_window(self):
        if self._analysis_queue_win is not None:
            try:
                self._analysis_queue_win.destroy()
            except tk.TclError:
                pass
        self._analysis_queue_win = None
        self._analysis_queue_tv = None

    def _refresh_analysis_queue_window(self):
        tv = self._analysis_queue_tv
        if tv is None or not (self._analysis_queue_win and self._analysis_queue_win.winfo_exists()):
            return
        tv.delete(*tv.get_children())
        labels = {"queued": "等待", "running": "分析中", "paused": "已暂停",
                  "completed": "已完成", "failed": "失败"}
        for task in reversed(self._analysis_queue.tasks()):
            tv.insert("", "end", values=(
                task.get("name", ""), labels.get(task.get("status"), task.get("status", "")),
                "%d/%d" % (int(task.get("done") or 0), int(task.get("total") or 0)),
                int(task.get("attempts") or 0), task.get("message", ""), task.get("updatedAt", "")))

    def _pause_analysis_queue(self):
        self._analysis_queue.pause()
        if not self._analysis_queue_pending:
            self._finish_paused_analysis_queue_task()
        self._refresh_analysis_queue_window()
        self._set_msg("批量分析队列已暂停；当前请求返回后会保存进度")

    def _resume_analysis_queue(self):
        self._analysis_queue.resume()
        self._refresh_analysis_queue_window()
        self._set_msg("批量分析队列已继续")
        self.after(20, self._kick_analysis_queue)

    def _retry_failed_analysis_queue(self):
        count = self._analysis_queue.retry_failed()
        self._refresh_analysis_queue_window()
        self._set_msg("已将 %d 个失败任务放回队列" % count)
        self.after(20, self._kick_analysis_queue)

    def scan_library_inbox(self, silent=False):
        """自动扫描棋谱库收件箱，把新 SGF 转成可继续复盘的项目快照。"""
        try:
            result = scan_inbox(rules=self.rules, komi=self.komi)
        except Exception as e:
            if not silent:
                messagebox.showerror("扫描失败", str(e))
            else:
                self._set_msg("棋谱库自动扫描失败：%s" % e)
            return None
        imported = len(result.get("imported") or [])
        failed = len(result.get("failed") or [])
        if self._lib_tv is not None and self._lib_win and self._lib_win.winfo_exists():
            self._refresh_library_window()
        if imported or failed:
            msg = "棋谱库已自动入库 %d 盘" % imported
            if failed:
                msg += "，失败 %d 个文件" % failed
            self._set_msg(msg)
            if failed and not silent:
                detail = "\n".join("%s：%s" % (os.path.basename(x["path"]), x["error"])
                                   for x in result["failed"][:8])
                messagebox.showerror("部分棋谱导入失败", detail)
        elif not silent:
            self._set_msg("收件箱没有新棋谱：%s" % inbox_dir())
        return result

    def _open_library_inbox(self):
        path = inbox_dir()
        try:
            os.startfile(path)
        except Exception:
            messagebox.showinfo("棋谱库收件箱", path)

    # ---- 学习中心（大纲 §55-56：学习模式四入口）----
    def open_learning_center(self):
        """学习模式的四个核心入口 + 学习状态摘要（研究功能仍走原界面）。"""
        if (self._learning_center_win is not None
                and self._learning_center_win.winfo_exists()):
            self._learning_center_win.lift()
            return
        try:
            from learning_profile import summarize_learning
            from learning_store import get_events
            from mistake_book import book_stats
            summary = summarize_learning(get_events())
            stats = book_stats()
        except Exception:
            summary, stats = {}, {"due": 0}
        mastery = summary.get("mastery_distribution") or {}
        theme = summary.get("top_training_theme") or {}

        win = tk.Toplevel(self)
        self._prepare_child_window(win, "学习中心", 700, 540, minsize=(620, 460))
        self._learning_center_win = win
        win.protocol("WM_DELETE_WINDOW", self._close_learning_center)

        tk.Label(win, text="学习中心", font=FONTS["title"], bg=COLORS["bg"],
                 fg=COLORS["text"]).pack(anchor="w", padx=16, pady=(14, 2))
        tk.Label(win, text="从自己的历史实战出发：先复盘重下，再间隔复习，长期追踪错误是否真正改掉。",
                 font=FONTS["ui"], bg=COLORS["bg"], fg=COLORS["subtext"],
                 wraplength=650, justify=tk.LEFT).pack(anchor="w", padx=16, pady=(0, 10))

        # ---- 学习状态摘要条 ----
        status = tk.Frame(win, bg=COLORS["card"], highlightthickness=1,
                          highlightbackground=COLORS["muted"])
        status.pack(fill="x", padx=16, pady=(0, 10))
        due = int(stats.get("due") or 0)
        parts = ["今日复习 %d 题%s" % (due, "（有待办）" if due else "（已清）"),
                 "学习事件 %d 条 / %d 盘" % (
                     summary.get("events_total", 0), summary.get("games_total", 0))]
        unstable = mastery.get("unstable", 0) or 0
        if unstable:
            parts.append("⚠ %d 个问题实战复发中" % unstable)
        if theme:
            parts.append("第一训练主题：%s" % theme.get("category", ""))
        tk.Label(status, text="　｜　".join(parts), font=FONTS["ui"],
                 bg=COLORS["card"], fg=(COLORS["red"] if due else COLORS["text"]),
                 anchor="w", justify=tk.LEFT, wraplength=640).pack(
                     anchor="w", padx=12, pady=10)

        # ---- 四入口（大纲 §56：我的棋谱 / 本盘复盘 / 今日复习 / 我的学习）----
        grid = tk.Frame(win, bg=COLORS["bg"])
        grid.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        for i in range(2):
            grid.columnconfigure(i, weight=1)
            grid.rowconfigure(i, weight=1)
        entries = [
            ("我的棋谱", "导入与管理历史对局：分析状态、重点问题数、训练记录。",
             self.open_game_library, "topbar", ""),
            ("本盘复盘", "本局学习节点（学习优先级排序，每盘聚焦 5 处）："
             "棋盘自由落子重下，榜外选点自动送 KataGo 强制分析。",
             self.open_problem_drill, "accent", ""),
            ("今日复习", "错题到期队列。按实际目损判分，复习结果回写学习曲线。",
             self._start_next_due_mistake_review,
             "accent" if due else "default",
             "今日 %d 题" % due),
            ("我的学习", "重复类别率 / 主动纠正率 / 保持率 / 第一训练主题 / 实战复发。",
             self.open_player_profile, "topbar", ""),
        ]
        for index, (title, desc, command, variant, badge) in enumerate(entries):
            card = tk.Frame(grid, bg=COLORS["card"], highlightthickness=1,
                            highlightbackground=COLORS["muted"])
            card.grid(row=index // 2, column=index % 2,
                      sticky="nsew", padx=(0 if index % 2 == 0 else 5,
                                           5 if index % 2 == 0 else 0),
                      pady=(0 if index < 2 else 5, 5 if index < 2 else 0))
            head = tk.Frame(card, bg=COLORS["card"])
            head.pack(fill="x", padx=12, pady=(10, 2))
            tk.Label(head, text=title, font=FONTS["section"], bg=COLORS["card"],
                     fg=COLORS["accent"]).pack(side=tk.LEFT)
            if badge:
                tk.Label(head, text=badge, font=FONTS["small"], bg=COLORS["card"],
                         fg=COLORS["red"]).pack(side=tk.RIGHT)
            tk.Label(card, text=desc, font=FONTS["ui"], bg=COLORS["card"],
                     fg=COLORS["subtext"], wraplength=280, justify=tk.LEFT).pack(
                         anchor="w", padx=12, pady=(0, 6))
            self._make_button(card, "进入", command,
                              variant=variant).pack(anchor="e", padx=12, pady=(0, 10))

        tk.Label(win, text="研究功能（候选对比 / 双分支深算 / 热力图 / 棋力评估等）仍在主界面与各子窗口。",
                 font=FONTS["small"], bg=COLORS["bg"], fg=COLORS["subtext"],
                 wraplength=650, justify=tk.LEFT).pack(anchor="w", padx=16, pady=(0, 12))

    def _close_learning_center(self):
        win = self._learning_center_win
        self._learning_center_win = None
        if win is not None:
            try:
                win.destroy()
            except tk.TclError:
                pass

    def open_game_library(self):
        """棋谱库入口（V6 Phase 4）：一级页面优先，无 Shell 时回退 Toplevel。"""
        if getattr(self, "shell", None) is not None:
            self.router.go("library")
            return
        self._build_library_toplevel()

    def _build_library_toplevel(self):
        win = tk.Toplevel(self)
        self._prepare_child_window(
            win, "棋谱库", 1040, 520, minsize=(900, 440))
        self._build_library_into(win, toplevel=True)

    def _build_library_into(self, win, toplevel=False):
        if toplevel:
            win.protocol("WM_DELETE_WINDOW", self._close_library_window)
        top = tk.Frame(win, bg=COLORS["bg"])
        top.pack(fill="x", padx=10, pady=(10, 4))
        tk.Label(top, text="本地棋谱库", font=FONTS["title"], bg=COLORS["bg"],
                 fg=COLORS["text"]).pack(side=tk.LEFT)
        self._lib_search_var = tk.StringVar()
        ent = ttk.Entry(top, textvariable=self._lib_search_var, width=24)
        ent.pack(side=tk.LEFT, padx=(14, 6))
        ent.bind("<KeyRelease>", lambda _e: self._refresh_library_window())
        self._make_button(top, "搜索", self._refresh_library_window, variant="default").pack(side=tk.LEFT)
        self._make_button(top, "打开收件箱", self._open_library_inbox, variant="default").pack(side=tk.RIGHT)
        self._make_button(top, "扫描收件箱", lambda: self.scan_library_inbox(silent=False), variant="default").pack(side=tk.RIGHT, padx=(0, 6))
        self._make_button(top, "刷新", lambda: self.scan_library_inbox(silent=True), variant="default").pack(side=tk.RIGHT, padx=(0, 6))
        lib_list = tk.Frame(win, bg=COLORS["card"], highlightthickness=1,
                            highlightbackground=COLORS["muted"])
        lib_list.pack(fill="both", expand=True, padx=10, pady=4)
        tv = ttk.Treeview(lib_list, columns=("name", "moves", "analyzed", "side", "training", "cache", "last_training", "rules", "komi", "time"),
                          show="headings", height=12)
        for col, txt, w, anch in [
            ("name", "棋谱", 230, "w"), ("moves", "手数", 52, "e"),
            ("analyzed", "已分析", 76, "e"), ("side", "我方", 56, "center"),
            ("training", "训练题", 190, "w"), ("cache", "快速训练准备", 150, "w"),
            ("last_training", "最近训练", 88, "w"), ("rules", "规则", 76, "w"),
            ("komi", "贴目", 52, "e"), ("time", "最近", 150, "w"),
        ]:
            tv.heading(col, text=txt)
            tv.column(col, width=w, anchor=anch)
        tv.pack(fill="both", expand=True, padx=6, pady=6)
        tv.bind("<Double-1>", lambda _e: self._open_selected_library_record())
        btns = self._dialog_button_bar(win)
        tk.Label(
            btns, text="双击棋谱也可直接打开",
            bg=COLORS["card"], fg=COLORS["subtext"],
            font=FONTS["small"]).pack(side=tk.LEFT)
        self._make_button(
            btns, "删除记录",
            self._delete_selected_library_record, variant="default").pack(side=tk.RIGHT, padx=8)
        self._make_button(
            btns, "开始阶段训练",
            self._start_training_for_record, variant="default").pack(side=tk.RIGHT, padx=8)
        self._make_button(
            btns, "打开选中棋谱",
            self._open_selected_library_record, variant="accent").pack(side=tk.RIGHT, padx=8)

        library_tools = tk.Frame(win, bg=COLORS["bg"])
        library_tools.pack(fill="x", padx=10, pady=(0, 4))
        tk.Label(
            library_tools, text="训练方：", bg=COLORS["bg"],
            fg=COLORS["subtext"], font=FONTS["small"]).pack(side=tk.LEFT)
        for text, side in (("黑", "B"), ("白", "W"), ("双方", "both")):
            self._make_button(
                library_tools, text,
                lambda value=side: self._set_selected_training_side(value),
                variant="default").pack(side=tk.LEFT, padx=(0, 3))
        self._make_button(
            library_tools, "训练历史",
            self._show_selected_training_history, variant="default").pack(
                side=tk.LEFT, padx=(8, 0))
        self._make_button(
            library_tools, "错题本",
            self.open_mistake_book, variant="default").pack(side=tk.LEFT, padx=(6, 0))
        self._make_button(
            library_tools, "个人画像",
            self.open_player_profile, variant="default").pack(side=tk.LEFT, padx=(6, 0))
        profile_row = tk.Frame(win, bg=COLORS["bg"])
        profile_row.pack(fill="x", padx=10, pady=(0, 8))
        tk.Label(profile_row, text="画像身份（独立于训练方）：",
                 bg=COLORS["bg"], fg=COLORS["subtext"]).pack(side=tk.LEFT)
        self._make_button(profile_row, "我执黑",
                          lambda: self._set_selected_profile_side("B"),
                          variant="default").pack(side=tk.LEFT, padx=(4, 0))
        self._make_button(profile_row, "我执白",
                          lambda: self._set_selected_profile_side("W"),
                          variant="default").pack(side=tk.LEFT, padx=(4, 0))
        self._make_button(profile_row, "双方",
                          lambda: self._set_selected_profile_side("both"),
                          variant="default").pack(side=tk.LEFT, padx=(4, 0))
        self._lib_win = win
        self._lib_tv = tv
        self._lib_map = {}
        self.scan_library_inbox(silent=True)
        self._refresh_library_window()

    def _close_library_window(self):
        """关闭棋谱库（双击打开棋谱后自动调用，避免库继续挡在前面）。

        内嵌页模式：切回复盘工作区即可，页面容器保留复用；
        Toplevel 模式：销毁并清引用。
        """
        shell = getattr(self, "shell", None)
        page = shell.pages.get("library") if shell is not None else None
        if self._lib_win is not None and self._lib_win is page:
            self.router.go("review")
            return
        if self._lib_win is not None:
            try:
                self._lib_win.destroy()
            except tk.TclError:
                pass
        self._lib_win = None
        self._lib_tv = None
        self._lib_map = {}

    def _refresh_library_window(self):
        if self._lib_tv is None or not (self._lib_win and self._lib_win.winfo_exists()):
            return
        self._lib_tv.delete(*self._lib_tv.get_children())
        self._lib_map = {}
        query = self._lib_search_var.get() if self._lib_search_var is not None else ""
        for rec in search_records(query):
            analyzed = "%s/%s" % (rec.get("analyzed", 0), rec.get("totalNodes", 0))
            task = rec.get("trainingTask") or {}
            training = "未生成"
            if task:
                training = "%s %s-%s手 目损%.1f" % (
                    task.get("phaseLabel", "阶段"), task.get("startMove", "?"),
                    task.get("endMove", "?"), float(task.get("avgLoss") or 0.0))
            cache_meta = rec.get("trainingCache") or {}
            cache_status = "待生成" if task else "等待训练题"
            if cache_meta:
                status_label = {
                    "ready": "已就绪",
                    "building": "生成中",
                    "partial": "部分就绪",
                }.get(cache_meta.get("status"), "准备中")
                cache_status = "%s %s回合/%s局面" % (
                    status_label,
                    int(cache_meta.get("rounds") or 0),
                    int(cache_meta.get("entries") or 0),
                )
            sessions = rec.get("trainingSessions") or []
            last_training = "未练"
            if sessions:
                last = sessions[-1]
                last_training = "%s %.1f" % (last.get("grade", "?"), float(last.get("avgLoss") or 0.0))
            iid = self._lib_tv.insert("", "end", values=(
                rec.get("name", ""),
                rec.get("moves", 0),
                analyzed,
                player_color_label(rec.get("playerColor")),
                training,
                cache_status,
                last_training,
                rec.get("rules", ""),
                rec.get("komi", ""),
                rec.get("lastOpenedAt") or rec.get("updatedAt") or rec.get("importedAt", ""),
            ))
            self._lib_map[iid] = rec

    def _sync_mistake_book_library(self):
        """用现有轻量摘要同步错题；完整旧项目缺摘要时就地补建。"""
        from learning_store import sync_profile_summary as sync_learning_summary
        for rec in search_records(""):
            summary = rec.get("profileSummary")
            if isinstance(summary, dict):
                try:
                    sync_mistake_summary(rec, summary)
                except Exception:
                    pass
                try:
                    sync_learning_summary(rec, summary)
                except Exception:
                    pass
                continue
            path = rec.get("projectPath")
            if (path and os.path.exists(path)
                    and int(rec.get("totalNodes") or 0) > 1
                    and int(rec.get("analyzed") or 0) >= int(rec.get("totalNodes") or 0)):
                try:
                    tree, _data = load_project(path)
                    update_project_snapshot(
                        rec.get("id"), tree,
                        rules=rec.get("rules", self.rules),
                        komi=rec.get("komi", self.komi))
                except Exception:
                    pass

    def open_mistake_book(self):
        """打开跨棋局错题队列；双击题目即可进入隐藏答案测验。"""
        if self._mistake_book_win is not None and self._mistake_book_win.winfo_exists():
            self._mistake_book_win.lift()
            self._refresh_mistake_book_window()
            return
        self._sync_mistake_book_library()
        win = tk.Toplevel(self)
        self._prepare_child_window(
            win, "错题本 · 间隔复习", 940, 500, minsize=(820, 420))

        top = tk.Frame(win, bg=COLORS["bg"])
        top.pack(fill="x", padx=10, pady=(10, 4))
        tk.Label(top, text="错题本", font=FONTS["title"], bg=COLORS["bg"],
                 fg=COLORS["text"]).pack(side=tk.LEFT)
        self._mistake_book_stats_label = tk.Label(
            top, text="", bg=COLORS["bg"], fg=COLORS["subtext"], font=FONTS["ui"])
        self._mistake_book_stats_label.pack(side=tk.LEFT, padx=(12, 0))
        self._mistake_due_only_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            top, text="只看今日到期", variable=self._mistake_due_only_var,
            command=self._refresh_mistake_book_window,
            bg=COLORS["bg"], fg=COLORS["text"], activebackground=COLORS["bg"],
            selectcolor=COLORS["card"]).pack(side=tk.RIGHT)

        content = tk.Frame(win, bg=COLORS["card"])
        content.pack(fill="both", expand=True, padx=10, pady=4)
        tv = ttk.Treeview(
            content,
            columns=("due", "game", "move", "side", "played", "best",
                     "quality", "loss", "tags", "progress"),
            show="headings", height=15)
        for col, text, width, anchor in [
                ("due", "下次复习", 88, "center"), ("game", "棋局", 210, "w"),
                ("move", "手数", 48, "e"), ("side", "方", 32, "center"),
                ("played", "实战", 50, "center"), ("best", "AI首选", 56, "center"),
                ("quality", "评价", 58, "center"), ("loss", "目损", 52, "e"),
                ("tags", "弱点标签", 120, "w"), ("progress", "复习进度", 92, "center")]:
            tv.heading(col, text=text)
            tv.column(col, width=width, anchor=anchor)
        tv.pack(fill="both", expand=True)
        tv.bind("<Double-1>", lambda _e: self._start_selected_mistake_review())
        tv.tag_configure("due", foreground=COLORS["red"])
        tv.tag_configure("future", foreground=COLORS["text"])
        self._mistake_book_empty = self._empty_card(
            content, "错题本暂无内容",
            "请先在棋谱库为棋局设置「我方」身份并完成整盘分析，"
            "问题手会自动进入错题本用于间隔复习。")

        btns = self._dialog_button_bar(win)
        tk.Label(
            btns,
            text="按实际目损判分（与主动复盘同一条链）；判定未达标回到题面重试，榜外选点自动送 AI 强制分析。",
            bg=COLORS["card"], fg=COLORS["subtext"], font=FONTS["small"]).pack(side=tk.LEFT)
        self._make_button(btns, "暂不复习",
                          self._master_selected_mistake, variant="default").pack(side=tk.RIGHT, padx=(6, 0))
        self._make_button(btns, "明天再练",
                          lambda: self._postpone_selected_mistake(1), variant="default").pack(side=tk.RIGHT, padx=8)
        self._make_button(btns, "开始复习",
                          self._start_selected_mistake_review, variant="accent").pack(side=tk.RIGHT, padx=8)

        self._mistake_book_win = win
        self._mistake_book_tv = tv
        self._mistake_book_map = {}
        win.protocol("WM_DELETE_WINDOW", self._close_mistake_book)
        self._refresh_mistake_book_window()

    def _close_mistake_book(self):
        if self._mistake_book_win is not None:
            try:
                self._mistake_book_win.destroy()
            except tk.TclError:
                pass
        self._mistake_book_win = None
        self._mistake_book_tv = None
        self._mistake_book_map = {}
        # 关闭错题本窗口时终止进行中的复习，避免 _mistake_review.active 残留：
        # 否则后续任意落子会触发 _mistake_review_after_user_move 对失效题目做"回题面"，
        # 其引用的 parent 节点可能已属于切换后的旧棋局，导致跨树跳转或崩溃。
        if self._mistake_review and self._mistake_review.get("active"):
            self._mistake_review = None
            self._set_msg("已关闭错题本，进行中的复习已终止")

    def _refresh_mistake_book_window(self):
        tv = self._mistake_book_tv
        if tv is None or not (
                self._mistake_book_win and self._mistake_book_win.winfo_exists()):
            return
        tv.delete(*tv.get_children())
        self._mistake_book_map = {}
        due_only = bool(
            self._mistake_due_only_var and self._mistake_due_only_var.get())
        items = list_mistake_items(due_only=due_only)
        for item in items:
            tags = "、".join(
                PROBLEM_TAGS.get(tag, tag) for tag in item.get("problemTags") or [])
            progress = "%d次 · 错%d" % (
                int(item.get("repetitions") or 0), int(item.get("lapses") or 0))
            iid = tv.insert("", "end", values=(
                item.get("dueDate") or "—",
                item.get("gameName") or item.get("gameId") or "",
                item.get("moveNo") or "",
                "黑" if item.get("color") == "B" else "白",
                item.get("playedMove") or "—",
                item.get("bestMove") or "—",
                QUALITY_LABELS.get(item.get("qualityKey"), item.get("qualityKey") or "—"),
                "—" if item.get("scoreLoss") is None
                else "%.1f" % float(item.get("scoreLoss")),
                tags or "—", progress),
                tags=("due" if item.get("isDue") else "future",))
            self._mistake_book_map[iid] = item
        stats = book_stats()
        if self._mistake_book_stats_label is not None:
            self._mistake_book_stats_label.config(
                text="共 %d 题 · 今日到期 %d · 已掌握 %d" % (
                    stats["total"], stats["due"], stats["mastered"]))
        empty = getattr(self, "_mistake_book_empty", None)
        if not items:
            tv.pack_forget()
            if empty is not None:
                empty.pack(fill="both", expand=True)
            if not due_only:
                self._set_msg("错题本为空：请先在棋谱库设置画像身份并完成整盘分析")
        else:
            if empty is not None and empty.winfo_ismapped():
                empty.pack_forget()
            if not tv.winfo_ismapped():
                tv.pack(fill="both", expand=True)

    def _selected_mistake_item(self):
        if self._mistake_book_tv is None:
            return None
        selected = self._mistake_book_tv.selection()
        if not selected:
            rows = self._mistake_book_tv.get_children()
            if not rows:
                self._set_msg("当前没有可复习的错题")
                return None
            selected = (rows[0],)
            self._mistake_book_tv.selection_set(selected[0])
        return self._mistake_book_map.get(selected[0])

    def _postpone_selected_mistake(self, days):
        item = self._selected_mistake_item()
        if not item:
            return
        postpone_mistake_item(item.get("id"), days)
        self._refresh_mistake_book_window()
        self._set_msg("已将第 %s 手错题推迟 %d 天" % (item.get("moveNo"), days))

    def _master_selected_mistake(self):
        item = self._selected_mistake_item()
        if not item:
            return
        set_mistake_mastered(item.get("id"), True)  # 暂不复习：仅推迟调度，不改掌握状态
        self._refresh_mistake_book_window()
        self._set_msg("已暂不复习（一年内不再排队）：%s 第 %s 手" % (
            item.get("gameName") or "", item.get("moveNo")))

    def _start_next_due_mistake_review(self):
        """从个人画像直接进入今日第一道到期错题。"""
        self.open_mistake_book()
        if self._mistake_due_only_var is not None:
            self._mistake_due_only_var.set(True)
        self._refresh_mistake_book_window()
        rows = (
            self._mistake_book_tv.get_children()
            if self._mistake_book_tv is not None else [])
        if not rows:
            self._set_msg("今日没有到期错题")
            return
        self._mistake_book_tv.selection_set(rows[0])
        self._mistake_book_tv.focus(rows[0])
        self._mistake_book_tv.see(rows[0])
        self._start_selected_mistake_review()

    def _start_selected_mistake_review(self):
        if self.scoring_mode:
            self._set_msg("请先退出点目模式，再开始错题复习")
            return
        if self._drill_active() or getattr(self, "_drill_overlay", None) is not None:
            self._set_msg("请先关闭问题手训练，再开始错题复习")
            return
        tr = self._training
        if tr and tr.get("active") and not tr.get("finished"):
            self._set_msg("请先结束阶段训练，再开始错题复习")
            return
        self._stop_auto_play()
        item = self._selected_mistake_item()
        if not item:
            return
        path = item.get("projectPath")
        if not path or not os.path.exists(path):
            messagebox.showerror("无法开始复习", "项目快照不存在：%s" % (path or "—"))
            return
        self._load_project_from_path(
            path, item.get("gameName") or os.path.basename(path),
            library_record_id=item.get("gameId"))
        node = ReviewReport(self.tree).node_at_move(int(item.get("moveNo") or 0))
        if node is None or node.parent is None:
            messagebox.showerror("无法开始复习", "项目中找不到第 %s 手" % item.get("moveNo"))
            return
        self._training = None
        self._reset_pv_state()
        self._problem_branch_overlay = None
        self._mistake_review = {
            "active": True,
            "item": dict(item),
            "parent": node.parent,
            "attempts": 0,
        }
        self.tree.current = node.parent
        self._clear_candidate_module()
        self._after_navigate()
        side = "黑" if item.get("color") == "B" else "白"
        self._set_msg(
            "错题测验：%s第 %s 手，轮到%s。请在棋盘落子；F1 可请求提示。"
            % (item.get("gameName") or "", item.get("moveNo"), side))

    def _mistake_review_after_user_move(self, node):
        """复习测验即时判分（审查 P0-1：与主动复盘同一条判分链）。

        实际目损 → CandidateAssessment（统一上下文）→ srs_result；
        榜外选点送 KataGo allowMoves 强制分析后判定，绝不"不在前三=错"。
        """
        from candidate_assessment import assess_candidate
        ctx = self._mistake_review
        if not ctx or not ctx.get("active") or node.parent is None:
            return False
        item = ctx.get("item") or {}
        coord = node.move[1] if node.move else None
        played = "pass" if coord is None else xy_to_point(coord[0], coord[1], self.size)
        move_infos = sorted(
            (node.parent.analysis or {}).get("moveInfos") or [],
            key=lambda value: value.get("order", 999))
        color = str(item.get("color") or "B").upper()
        in_infos = any(
            str(m.get("move") or "").lower() == played.lower()
            for m in move_infos)
        if not in_infos and self.client and self.client.is_alive() and self.client.ready:
            from candidate_assessment import forced_move_query
            visits = int(self.cfg.get("max_visits", 200))
            q = forced_move_query(
                self._analysis_query_for(
                    self.tree, node.parent, self.rules, self.komi, visits),
                played, player=color)
            qid = self.client.analyze(q)
            self._mistake_forced_pending[qid] = (item.get("id"), played, color)
            self._set_msg("你的选点 %s 不在已有候选，正在强制分析…" % played)
            self.tree.current = ctx.get("parent")
            self._after_navigate()
            return True
        self._mistake_review_settle(
            item, played, move_infos, color,
            assessment=(assess_candidate(
                played, move_infos, color,
                performance_label=self._assessment_context()["performance_label"],
                complexity=0.0) if in_infos else None))
        return True

    def _handle_mistake_forced_result(self, rid, resp):
        from candidate_assessment import assess_candidate, forced_move_result
        pend = self._mistake_forced_pending.pop(rid, None)
        if pend is None or not (self._mistake_review or {}).get("active"):
            return
        item_id, played, color = pend
        item = next((it for it in list_mistake_items()
                     if it.get("id") == item_id), None)
        if item is None:
            return
        node = self.tree.current
        move_infos = []
        if node is not None and node.parent is not None:
            move_infos = sorted(
                (node.parent.analysis or {}).get("moveInfos") or [],
                key=lambda value: value.get("order", 999))
        score, winrate, _o = forced_move_result(resp, played)
        ordered = sorted(move_infos, key=lambda m: m.get("order", 999))
        best = ordered[0] if ordered else {}
        assessment = assess_candidate(
            played, move_infos, color,
            forced_score_lead=score, forced_winrate=winrate,
            best_score_lead=best.get("scoreLead"),
            best_winrate=best.get("winrate"),
            performance_label=self._assessment_context()["performance_label"],
            complexity=0.0)
        self._mistake_review_settle(item, played, move_infos, color,
                                    assessment=assessment)

    def _mistake_review_settle(self, item, played, move_infos, color,
                               assessment):
        """判分落账：与主动复盘同一 record_graded_attempt（P0-1 单链）。"""
        from candidate_assessment import srs_result
        result = srs_result((assessment or {}).get("assessment"))
        updated = record_graded_attempt_mb(
            item.get("id"), played, move_infos, color,
            item.get("bestMove"), assessment=assessment)
        label = (assessment or {}).get("assessment_label") or "数据不足"
        loss = (assessment or {}).get("score_loss")
        loss_text = "" if loss is None else "（亏 %.1f 目）" % loss
        ctx = self._mistake_review or {}
        if result == "again":
            ctx["attempts"] = int(ctx.get("attempts") or 0) + 1
            self.tree.current = ctx.get("parent")
            self._after_navigate()
            self._set_msg(
                "%s%s，回到题面再试一次（第 %d 次）；按 F1 可看提示。" % (
                    label, loss_text, ctx["attempts"]))
            return
        ctx["active"] = False
        self._mistake_review = None
        self._refresh_mistake_book_window()
        self._set_msg("判定：%s%s；下次复习：%s" % (
            label, loss_text, (updated or {}).get("dueDate") or "—"))

    def _open_selected_library_record(self):
        if self._lib_tv is None:
            return
        sel = self._lib_tv.selection()
        if not sel:
            self._set_msg("请先在棋谱库中选中一盘棋")
            return
        rec = self._lib_map.get(sel[0])
        if not rec:
            return
        path = rec.get("projectPath")
        if not path or not os.path.exists(path):
            messagebox.showerror("打开失败", "项目快照不存在：%s" % path)
            return
        self._load_project_from_path(path, rec.get("name", ""), library_record_id=rec.get("id"))

    def _set_selected_training_side(self, color):
        rec = self._selected_library_record()
        if not rec:
            self._set_msg("请先在棋谱库中选中一盘棋")
            return
        rec = update_training_settings(rec.get("id"), color) or rec
        path = rec.get("projectPath")
        try:
            if self._library_record_id == rec.get("id"):
                refresh_training_task(rec.get("id"), self.tree)
            elif path and os.path.exists(path):
                t, _data = load_project(path)
                refresh_training_task(rec.get("id"), t)
        except Exception:
            pass
        self._library_bg_recent.discard(rec.get("id"))
        self._refresh_library_window()
        self._set_msg("已设置训练方：%s" % player_color_label(color))
        self.after(50, self._maybe_prepare_library_training_background)

    def _set_selected_profile_side(self, side):
        rec = self._selected_library_record()
        if not rec:
            self._set_msg("请先在棋谱库中选中一盘棋")
            return
        update_profile_side(rec.get("id"), side)
        if self._library_record_id == rec.get("id"):
            self.tree._profile_side = side
            self._refresh_review_summary_artifact()
            update_project_snapshot(
                rec.get("id"), self.tree, rules=self.rules, komi=self.komi)
        else:
            path = rec.get("projectPath")
            if path and os.path.exists(path):
                try:
                    tree, _data = load_project(path)
                    tree._profile_side = side
                    update_project_snapshot(
                        rec.get("id"), tree,
                        rules=rec.get("rules", self.rules),
                        komi=rec.get("komi", self.komi))
                except Exception:
                    pass
        self._refresh_library_window()
        label = {"B": "我执黑", "W": "我执白", "both": "双方"}.get(side, "未知")
        self._set_msg("已设置画像身份：%s（与训练方设置相互独立）" % label)
        if self._profile_win is not None and self._profile_win.winfo_exists():
            self._profile_win.destroy()
            self._profile_win = None
            self.open_player_profile()

    def open_player_profile(self):
        """显示最近 N 盘的本地长期画像；空数据和身份未知均给明确说明。"""
        if self._profile_win is not None and self._profile_win.winfo_exists():
            self._profile_win.destroy()   # 重开重算数据（画像数据可能已变）
        profile_cfg = self.cfg.get("profile", {}) or {}
        window_games = int(profile_cfg.get("profile_window_games", 30) or 30)
        records = get_recent_profile_summaries(window_games)
        profile = get_or_rebuild(window_games=window_games)
        benchmark = self._latest_game_benchmark(records)
        mistakes = list_mistake_items(include_mastered=False)
        mistake_stats = book_stats()
        trends = weakness_trends(
            records, tags=profile.problem_tag_distribution.keys())
        priorities = prioritize_weaknesses(
            profile, mistakes, trends=trends)

        win = tk.Toplevel(self)
        self._prepare_child_window(
            win, "个人画像 · AI 参考评价", 900, 820,
            minsize=(800, 680))
        self._profile_win = win
        win.protocol("WM_DELETE_WINDOW", self._close_profile_window)

        tk.Label(
            win, text="个人画像", font=FONTS["title"], bg=COLORS["bg"],
            fg=COLORS["text"]).pack(anchor="w", padx=14, pady=(12, 2))
        tk.Label(
            win,
            text="基于本机已分析棋局生成，不代表正式段位；结果受模型、visits、规则和棋局复杂度影响。",
            font=FONTS["ui"], bg=COLORS["bg"], fg=COLORS["subtext"],
            wraplength=850, justify=tk.LEFT).pack(anchor="w", padx=14, pady=(0, 8))

        metrics = tk.Frame(win, bg=COLORS["bg"])
        metrics.pack(fill="x", padx=14, pady=(0, 8))
        trend_label = {
            "improving": "改善", "stable": "稳定",
            "declining": "需关注", "insufficient": "样本不足",
        }.get(profile.recent_trend.direction, "样本不足")
        metric_items = [
            ("纳入棋局", "%d 盘" % profile.games_count, COLORS["text"]),
            ("有效评价", "%d 手" % profile.evaluated_moves_count, COLORS["text"]),
            ("平均目损", (
                "—" if profile.overall.avg_score_loss is None
                else "%.2f" % profile.overall.avg_score_loss), COLORS["accent"]),
            ("总体趋势", trend_label, (
                COLORS["green"] if profile.recent_trend.direction == "improving"
                else COLORS["red"] if profile.recent_trend.direction == "declining"
                else COLORS["text"])),
            ("今日复习", "%d 题" % mistake_stats["due"], (
                COLORS["red"] if mistake_stats["due"] else COLORS["green"])),
        ]
        for index, (label, value, color) in enumerate(metric_items):
            card = tk.Frame(
                metrics, bg=COLORS["card"], highlightthickness=1,
                highlightbackground=COLORS["muted"], padx=10, pady=7)
            card.pack(
                side=tk.LEFT, fill="x", expand=True,
                padx=(0, 6) if index < len(metric_items) - 1 else 0)
            tk.Label(
                card, text=label, bg=COLORS["card"], fg=COLORS["subtext"],
                font=FONTS["small"]).pack(anchor="w")
            tk.Label(
                card, text=value, bg=COLORS["card"], fg=color,
                font=FONTS["score"]).pack(anchor="w", pady=(2, 0))

        actions = tk.Frame(win, bg=COLORS["card"], highlightthickness=1,
                           highlightbackground=COLORS["muted"])
        actions.pack(fill="x", padx=14, pady=(0, 6))
        inner = tk.Frame(actions, bg=COLORS["card"])
        inner.pack(fill="x", padx=12, pady=8)
        tk.Label(
            inner, text="逐盘平均目损越低越好；仅比较相同分析口径。",
            bg=COLORS["card"], fg=COLORS["subtext"], font=FONTS["small"]).pack(side=tk.LEFT)
        self._make_button(
            inner, "棋风与成长路线",
            self.open_style_profile, variant="default").pack(side=tk.RIGHT, padx=8)
        self._make_button(
            inner, "打开错题本",
            self.open_mistake_book, variant="default").pack(side=tk.RIGHT, padx=8)
        self._make_button(
            inner, "导出个人分析报告",
            lambda: self._export_profile_report(
                profile, records, benchmark), variant="default").pack(side=tk.RIGHT, padx=8)
        review_btn = self._make_button(
            inner, "开始今日复习",
            self._start_next_due_mistake_review, variant="accent")
        review_btn.pack(side=tk.RIGHT, padx=8)
        if mistake_stats["due"] <= 0:
            review_btn.configure(state=tk.DISABLED)

        # 学习系统摘要（大纲 §43/§64）：重复错误率/主动纠正率/延迟保留率/第一训练主题
        try:
            from learning_profile import format_learning_summary, summarize_learning
            from learning_store import get_events
            learning_summary = summarize_learning(get_events())
            if learning_summary["events_total"] > 0:
                learn_card = tk.Frame(win, bg=COLORS["card"], highlightthickness=1,
                                      highlightbackground=COLORS["muted"])
                learn_card.pack(fill="x", padx=14, pady=(0, 8))
                tk.Label(
                    learn_card, text="学习系统 · 我的学习",
                    font=FONTS["ui"], bg=COLORS["card"],
                    fg=COLORS["accent"]).pack(anchor="w", padx=12, pady=(8, 2))
                tk.Label(
                    learn_card,
                    text="基于错题作答与复盘重选的学习画像（与 AI 单局评价相互独立）。",
                    font=FONTS["small"], bg=COLORS["card"], fg=COLORS["subtext"]
                ).pack(anchor="w", padx=12)
                tk.Label(
                    learn_card, text=format_learning_summary(learning_summary),
                    font=FONTS["ui"], bg=COLORS["card"], fg=COLORS["text"],
                    justify=tk.LEFT, anchor="w").pack(
                        anchor="w", padx=12, pady=(4, 10), fill="x")
        except Exception:
            pass

        trend_canvas = tk.Canvas(
            win, height=150, bg=COLORS["card"],
            highlightthickness=1, highlightbackground=COLORS["muted"])
        trend_canvas.pack(fill="x", padx=14, pady=(0, 8))
        self._draw_profile_trend(trend_canvas, profile)

        priority_frame = ttk.LabelFrame(
            win, text=" 优先训练 ", style="Section.TLabelframe")
        priority_frame.pack(fill="x", padx=14, pady=(0, 8))
        priority_tv = ttk.Treeview(
            priority_frame,
            columns=("focus", "occurrences", "due", "loss", "trend"),
            show="headings", height=max(1, min(5, len(priorities))))
        for column, title, width, anchor in [
                ("focus", "训练重点", 150, "w"),
                ("occurrences", "跨局出现", 90, "center"),
                ("due", "今日到期", 80, "center"),
                ("loss", "错题目损", 90, "center"),
                ("trend", "近期变化", 180, "w")]:
            priority_tv.heading(column, text=title)
            priority_tv.column(column, width=width, anchor=anchor)
        for item in priorities:
            priority_tv.insert("", "end", values=(
                item["label"],
                "%d 次" % item["occurrences"],
                "%d 题" % item["due_mistakes"],
                "—" if item["avg_mistake_loss"] is None
                else "%.1f" % item["avg_mistake_loss"],
                self._weakness_trend_text(item)))
        if not priorities:
            priority_tv.insert("", "end", values=(
                "继续积累完整棋局", "—", "—", "—", "样本不足"))
        priority_tv.pack(fill="x")
        tk.Label(
            priority_frame,
            text="近期变化按每百手问题频率比较；前后各少于 2 盘时不下趋势结论。",
            bg=COLORS["card"], fg=COLORS["subtext"],
            font=FONTS["small"]).pack(anchor="w", pady=(5, 0))

        detail = ttk.LabelFrame(
            win, text=" 详细分析 ", style="Section.TLabelframe")
        detail.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        frame = tk.Frame(detail, bg=COLORS["card"])
        frame.pack(fill="both", expand=True)
        text = tk.Text(
            frame, wrap=tk.WORD, relief="flat", bg=COLORS["card"],
            fg=COLORS["text"], font=FONTS["ui"], padx=12, pady=10)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side=tk.LEFT, fill="both", expand=True)
        scroll.pack(side=tk.RIGHT, fill="y")

        lines = self._profile_display_lines(
            profile, records, window_games,
            benchmark=benchmark, priorities=priorities)
        text.insert("1.0", "\n".join(lines))
        text.configure(state=tk.DISABLED)

    @staticmethod
    def _weakness_trend_text(item):
        trend = (item or {}).get("trend") or {}
        status = trend.get("status")
        delta = trend.get("delta_per_100")
        if status == "improving" and delta is not None:
            return "改善 ↓ %.1f/百手" % abs(float(delta))
        if status == "worsening" and delta is not None:
            return "反复 ↑ %.1f/百手" % abs(float(delta))
        if status == "stable":
            return "基本持平"
        return "样本不足"

    def _build_style_artifacts(self):
        profile_cfg = self.cfg.get("profile", {}) or {}
        window_games = int(profile_cfg.get("profile_window_games", 30) or 30)
        records = get_recent_style_records(window_games)
        style_profile = build_style_profile(records)
        costs = build_style_costs(style_profile)
        attach_style_costs(style_profile, costs)
        growth_path = build_growth_path(style_profile, costs)
        store = load_store()
        findings = list(store.get("verifiedFindings") or [])
        apply_verified_findings(growth_path, findings)
        save_style_cache(style_profile, growth_path)
        self._style_profile = style_profile
        self._growth_path = growth_path
        return style_profile, growth_path, store

    def open_style_profile(self):
        """打开独立棋风窗口；计算和报告逻辑均位于独立模块。"""
        if self._style_win is not None and self._style_win.winfo_exists():
            try:
                self._style_win.destroy()
            except tk.TclError:
                pass
            self._style_win = None   # 重开重算棋风数据（可能已变）
        style_profile, growth_path, store = self._build_style_artifacts()
        win = StyleProfileWindow(
            self, style_profile, growth_path, store.get("tasks") or [],
            COLORS, FONTS,
            on_export=self._export_style_report,
            on_generate=self._generate_style_verification_tasks,
            on_verify=self._start_style_verifications)
        self._style_win = win
        win.protocol("WM_DELETE_WINDOW", self._close_style_window)

    def _close_style_window(self):
        if self._style_win is not None:
            try:
                self._style_win.destroy()
            except tk.TclError:
                pass
        self._style_win = None

    def _export_style_report(self):
        style_profile, growth_path, store = self._build_style_artifacts()
        report = render_style_report(
            style_profile, growth_path,
            verification=store.get("verifiedFindings") or [])
        path = filedialog.asksaveasfilename(
            defaultextension=".md",
            filetypes=[("Markdown", "*.md"), ("文本文件", "*.txt"),
                       ("所有文件", "*.*")],
            initialfile="个人棋风与成长路线报告.md",
            parent=self._style_win or self)
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(report)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self._style_win or self)
            return
        self._set_msg("已导出棋风与成长路线报告：%s" % os.path.basename(path))

    def _deep_verification_config(self):
        raw = self.cfg.get("deep_verification", {}) or {}
        return {
            "enabled": bool(raw.get("enabled", True)),
            "target_visits": max(200, int(raw.get("target_visits", 800) or 800)),
            "max_samples_per_finding": max(
                1, min(5, int(raw.get("max_samples_per_finding", 3) or 3))),
        }

    def _generate_style_verification_tasks(self):
        if self._style_profile is None or self._growth_path is None:
            self._build_style_artifacts()
        config = self._deep_verification_config()
        tasks = build_verification_tasks(
            self._style_profile, self._growth_path,
            max_samples_per_finding=config["max_samples_per_finding"],
            target_visits=config["target_visits"])
        data = merge_and_save_tasks(tasks)
        if self._style_win is not None and self._style_win.winfo_exists():
            self._style_win.refresh_tasks(data.get("tasks") or [])
        self._set_msg(
            "已生成 %d 个高强度复核任务" % len(tasks)
            if tasks else
            "当前没有达到样本与成本门槛的关键结论，无需生成复核任务")
        return tasks

    def _start_style_verifications(self, tasks):
        pending = [
            DeepVerificationTask.from_dict(item)
            for item in (tasks or [])
            if (item.get("status") or "pending") in ("pending", "failed")]
        if not pending:
            self._set_msg("当前没有待复核任务")
            return
        if not self._ensure_ready():
            self._set_msg("请先启动并等待 KataGo 就绪，再开始高强度复核")
            return
        if self._style_verification_pending:
            self._set_msg("已有高强度复核正在进行")
            return
        self._style_verification_queue = pending
        self._start_next_style_verification()

    def _start_next_style_verification(self):
        if self._style_verification_pending:
            return
        if not self._style_verification_queue:
            self._set_msg("棋风关键样本高强度复核已完成")
            if self._style_win is not None and self._style_win.winfo_exists():
                self._close_style_window()
                self.open_style_profile()
            return
        task = self._style_verification_queue.pop(0)
        path = task.project_path
        if not path or not os.path.exists(path):
            record = get_record(task.game_id) or {}
            path = record.get("projectPath")
        if not path or not os.path.exists(path):
            update_task_result(task.task_id, error="项目快照不存在")
            self._start_next_style_verification()
            return
        try:
            tree, project = load_project(path)
            rr = ReviewReport(tree)
            node = rr.node_at_move(task.move_no)
            if node is None or node.parent is None:
                raise ValueError("项目中找不到第 %d 手" % task.move_no)
        except Exception as exc:
            update_task_result(task.task_id, error=str(exc))
            self._start_next_style_verification()
            return
        set_task_status(task.task_id, "running")
        ctx = {
            "task": task,
            "tree": tree,
            "node": node,
            "rules": project.get("rules", self.rules),
            "komi": project.get("komi", self.komi),
            "responses": {},
            "errors": [],
            "done": 0,
        }
        for role, target in (("before", node.parent), ("after", node)):
            query = self._analysis_query_for(
                tree, target, ctx["rules"], ctx["komi"], task.target_visits)
            query["includeOwnership"] = False
            query["includePolicy"] = False
            qid = self.client.analyze(query)
            self._style_verification_pending[qid] = {
                "ctx": ctx, "role": role}
        self._set_msg(
            "正在复核“%s”第 %d 手（%d visits）…"
            % (task.conclusion_label, task.move_no, task.target_visits))

    def _handle_style_verification_result(self, rid, resp):
        item = self._style_verification_pending.pop(rid, None)
        if not item:
            return
        ctx = item["ctx"]
        role = item["role"]
        ctx["done"] += 1
        if "error" in resp:
            ctx["errors"].append("%s：%s" % (role, resp.get("error")))
        else:
            ctx["responses"][role] = resp
        if ctx["done"] < 2:
            return
        task = ctx["task"]
        if ctx["errors"] or len(ctx["responses"]) < 2:
            update_task_result(
                task.task_id,
                error="；".join(ctx["errors"] or ["响应不完整"]))
            self._start_next_style_verification()
            return
        node = ctx["node"]
        node.parent.analysis = ctx["responses"]["before"]
        node.analysis = ctx["responses"]["after"]
        quality = ReviewReport(ctx["tree"]).move_quality_for_node(
            node, visits=task.target_visits)
        result = quality.to_dict()
        result["verified_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        result["target_visits"] = task.target_visits
        update_task_result(task.task_id, result=result)
        if self._style_win is not None and self._style_win.winfo_exists():
            self._style_win.refresh_tasks(load_store().get("tasks") or [])
        self._set_msg(
            "第 %d 手复核完成：%s，目损 %s"
            % (task.move_no, quality.quality_label,
               "—" if quality.score_loss is None
               else "%.1f" % quality.score_loss))
        self._start_next_style_verification()

    @staticmethod
    def _latest_game_benchmark(records):
        valid = [
            GameProfileSummary.from_dict(record.get("profileSummary") or {})
            for record in records
            if isinstance(record.get("profileSummary"), dict)
        ]
        if not valid:
            return None
        return compare_game_to_baseline(valid[-1], valid[:-1])

    def _export_profile_report(self, profile, records, benchmark):
        path = filedialog.asksaveasfilename(
            defaultextension=".md",
            filetypes=[("Markdown", "*.md"), ("文本文件", "*.txt"), ("所有文件", "*.*")],
            initialfile="个人分析报告.md", parent=self)
        if not path:
            return
        report = generate_profile_markdown(
            profile, records=records, benchmark=benchmark,
            mistakes=list_mistake_items(include_mastered=False))
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(report)
        except Exception as e:
            messagebox.showerror("导出失败", str(e), parent=self)
            return
        self._set_msg("已导出个人分析报告：%s" % os.path.basename(path))

    @staticmethod
    def _draw_profile_trend(canvas, profile):
        """绘制小型逐盘平均目损曲线；它表达数据，不承担装饰作用。"""
        canvas.update_idletasks()
        width = max(600, int(canvas.winfo_width() or 740))
        height = 150
        canvas.delete("all")
        points = [
            point for point in (getattr(profile, "trend_points", None) or [])
            if point.avg_score_loss is not None
        ]
        canvas.create_text(
            12, 12, anchor="w", text="逐盘平均目损（越低越好）",
            fill=COLORS["subtext"], font=FONTS["ui"])
        if len(points) < 2:
            canvas.create_text(
                width / 2, height / 2, text="至少需要两盘同口径棋局才能绘制趋势",
                fill=COLORS["subtext"], font=FONTS["ui"])
            return
        left, right, top, bottom = 42, width - 18, 30, height - 24
        values = [float(point.avg_score_loss) for point in points]
        lo, hi = min(values), max(values)
        span = max(hi - lo, 0.5)
        lo -= span * 0.15
        hi += span * 0.15

        def xy(index, value):
            x = left + index * (right - left) / max(1, len(points) - 1)
            y = top + (hi - value) / (hi - lo) * (bottom - top)
            return x, y

        canvas.create_line(left, bottom, right, bottom, fill=COLORS["muted"])
        canvas.create_text(
            left - 6, top, anchor="e", text="%.1f" % hi,
            fill=COLORS["subtext"], font=FONTS["small"])
        canvas.create_text(
            left - 6, bottom, anchor="e", text="%.1f" % lo,
            fill=COLORS["subtext"], font=FONTS["small"])
        coords = []
        for idx, value in enumerate(values):
            coords.extend(xy(idx, value))
        canvas.create_line(*coords, fill=COLORS["accent"], width=2)
        for idx, value in enumerate(values):
            x, y = xy(idx, value)
            color = COLORS["red"] if idx == len(values) - 1 else COLORS["accent"]
            canvas.create_oval(x - 4, y - 4, x + 4, y + 4,
                               fill=color, outline=COLORS["card"])
            canvas.create_text(
                x, bottom + 12, text=str(idx + 1),
                fill=COLORS["subtext"], font=FONTS["small"])

    def _close_profile_window(self):
        if self._profile_win is not None:
            try:
                self._profile_win.destroy()
            except Exception:
                pass
        self._profile_win = None

    @staticmethod
    def _profile_display_lines(profile, records, window_games,
                               benchmark=None, priorities=None):
        if not records:
            return [
                "暂无画像数据",
                "",
                "棋局库中还没有完成精细评价的棋局。请先打开一盘棋并完成整局分析。",
            ]
        if profile is None or profile.games_count == 0:
            return [
                "已有 %d 盘分析摘要，但尚未识别“我的执棋方”。" % len(records),
                "",
                "请在棋谱库中选中棋局，再用底部“画像身份”按钮标记我执黑、我执白或双方。",
            ]

        overall = profile.overall
        trend_label = {
            "improving": "上升", "stable": "稳定",
            "declining": "下降", "insufficient": "样本不足",
        }.get(profile.recent_trend.direction, "样本不足")
        lines = [
            "总览",
            "最近 %d/%d 盘 · 有效评价 %d 手" % (
                profile.games_count, window_games, profile.evaluated_moves_count),
            "平均目损 %s · 恶手率 %s · 不佳率 %s · AI 前3吻合 %s · 趋势 %s" % (
                "—" if overall.avg_score_loss is None else "%.2f" % overall.avg_score_loss,
                "—" if overall.blunder_rate is None else "%.1f%%" % overall.blunder_rate,
                "—" if overall.inaccuracy_rate is None else "%.1f%%" % overall.inaccuracy_rate,
                "—" if overall.top3_match_rate is None else "%.1f%%" % overall.top3_match_rate,
                trend_label),
        ]
        if profile.excluded_incompatible_games:
            lines.append(
                "另有 %d 盘因模型/visits/评价版本口径不同，未混入本次趋势。"
                % profile.excluded_incompatible_games)

        lines.extend(["", "最近一盘 vs 个人基线"])
        if benchmark is None or benchmark.status == "insufficient":
            evidence = (
                benchmark.evidence if benchmark is not None
                else ["暂无可用的最近一盘基线比较。"])
            lines.extend(evidence)
        else:
            status_label = {
                "better": "优于个人基线",
                "similar": "接近个人基线",
                "worse": "低于个人基线",
            }.get(benchmark.status, "基线不足")
            lines.append("%s（%s置信，历史 %d 盘）" % (
                status_label,
                {"low": "低", "medium": "中", "high": "高"}.get(
                    benchmark.confidence, benchmark.confidence),
                benchmark.prior_games))
            lines.extend(benchmark.evidence)
            for phase, label in (
                    ("opening", "布局"), ("middle", "中盘"), ("endgame", "官子")):
                item = benchmark.stage_comparisons.get(phase) or {}
                delta = item.get("loss_improvement")
                if delta is None:
                    continue
                lines.append("%s：本局 %s / 基线 %s，%s %.2f 目" % (
                    label,
                    "—" if item.get("current_avg_loss") is None
                    else "%.2f" % item["current_avg_loss"],
                    "—" if item.get("baseline_avg_loss") is None
                    else "%.2f" % item["baseline_avg_loss"],
                    "改善" if delta >= 0 else "变差",
                    abs(delta)))

        lines.extend(["", "阶段表现"])
        for key, label in (("opening", "布局"), ("middle", "中盘"), ("endgame", "官子")):
            stat = getattr(profile, key)
            lines.append("%s：%d 手，平均目损 %s，恶手率 %s" % (
                label, stat.moves,
                "—" if stat.avg_score_loss is None else "%.2f" % stat.avg_score_loss,
                "—" if stat.blunder_rate is None else "%.1f%%" % stat.blunder_rate))

        lines.extend(["", "黑白表现"])
        for key, label in (("black", "执黑"), ("white", "执白")):
            stat = getattr(profile, key)
            lines.append("%s：%d 手，平均目损 %s，AI 前3吻合 %s" % (
                label, stat.moves,
                "—" if stat.avg_score_loss is None else "%.2f" % stat.avg_score_loss,
                "—" if stat.top3_match_rate is None else "%.1f%%" % stat.top3_match_rate))

        lines.extend(["", "常见问题"])
        tags = sorted(
            profile.problem_tag_distribution.items(),
            key=lambda item: item[1], reverse=True)[:5]
        if tags:
            lines.extend(
                "%d. %s（%d 次）" % (index, PROBLEM_TAGS.get(tag, tag), count)
                for index, (tag, count) in enumerate(tags, start=1))
        else:
            lines.append("暂无达到统计门槛的问题标签。")

        lines.extend(["", "优先训练"])
        if priorities:
            for index, item in enumerate(priorities[:5], start=1):
                lines.append("%d. %s：%s" % (
                    index, item.get("label", item.get("tag", "问题")),
                    item.get("reason", "")))
        else:
            lines.append("当前没有足够的跨局问题与错题证据可排序。")

        lines.extend(["", "建议"])
        suggestions = (
            profile.weaknesses[:2] + profile.recommendations[:5])
        lines.extend(
            "• " + item for item in (
                suggestions or ["样本仍少，继续完成几盘整局分析后再判断趋势。"]))
        recent_positions = []
        for record in reversed(records):
            summary = record.get("profileSummary") or {}
            for move in summary.get("top_problem_moves") or []:
                recent_positions.append((record.get("name", ""), move))
        lines.extend(["", "最近值得训练的局面"])
        if recent_positions:
            for name, move in recent_positions[:5]:
                lines.append("• %s 第%s手 %s：%s，目损 %s" % (
                    name or "棋局",
                    move.get("move_no", "?"),
                    move.get("played_move", "?"),
                    QUALITY_LABELS.get(
                        move.get("quality_key"), move.get("quality_key", "未评价")),
                    "—" if move.get("score_loss") is None
                    else "%.1f" % float(move.get("score_loss"))))
        else:
            lines.append("暂无达到筛选阈值的问题局面。")
        return lines

    def _show_selected_training_history(self):
        """训练历史：评分趋势折线 + 明细表，取代原 messagebox 纯文本。"""
        rec = self._selected_library_record()
        if not rec:
            self._set_msg("请先在棋谱库中选中一盘棋")
            return
        sessions = rec.get("trainingSessions") or []
        if not sessions:
            messagebox.showinfo("训练历史", "这盘棋还没有训练记录。", parent=self)
            return
        win = tk.Toplevel(self)
        self._prepare_child_window(
            win, "训练历史 · %s" % rec.get("name", ""), 660, 520, minsize=(540, 380))
        frame = tk.Frame(win, bg=COLORS["card"])
        frame.pack(fill="both", expand=True, padx=12, pady=12)

        seq = list(sessions)  # 旧 → 新
        scores = []
        for s in seq:
            d = s.get("trainingAnalysis") or s.get("training_analysis") or {}
            try:
                scores.append(int(d.get("training_score") or 0))
            except (TypeError, ValueError):
                scores.append(0)
        if len(scores) >= 2:
            cv = tk.Canvas(frame, height=92, bg=COLORS["card"], highlightthickness=0)
            cv.pack(fill="x", pady=(0, 8))
            self._draw_history_spark(cv, scores)

        cols = ("time", "score", "label", "loss", "improve", "rep", "new", "days")
        tv = ttk.Treeview(frame, columns=cols, show="headings", height=10)
        headers = {"time": "时间", "score": "评分", "label": "标签",
                   "loss": "本次目损", "improve": "改善", "rep": "重复",
                   "new": "新错", "days": "复习(天)"}
        for c in cols:
            tv.heading(c, text=headers[c])
            tv.column(c, width=72, anchor=tk.CENTER)
        tv.column("time", width=150, anchor=tk.W)
        for s in reversed(seq):
            d = s.get("trainingAnalysis") or s.get("training_analysis") or {}
            if d:
                values = (
                    s.get("createdAt", "") or s.get("finishedAt", ""),
                    d.get("training_score", 0), d.get("training_label", "—"),
                    self._display_number(d.get("training_avg_score_loss")),
                    self._display_number(d.get("improvement_score_loss")),
                    len(d.get("repeated_errors") or []),
                    len(d.get("new_errors") or []),
                    d.get("suggested_review_after_days", "—"))
            else:  # 兼容 v3.x 旧训练记录（仅有 grade/avgLoss）
                values = (
                    s.get("createdAt", "") or s.get("finishedAt", ""),
                    s.get("grade", "?"), "",
                    "%.1f" % float(s.get("avgLoss") or 0.0),
                    "%.1f" % float(s.get("improvement") or 0.0),
                    "", "", "—")
            tv.insert("", "end", values=values)
        tv.pack(fill="both", expand=True)
        self._set_msg("%s 共 %d 次训练" % (rec.get("name", ""), len(sessions)))

    def _draw_history_spark(self, cv, scores):
        """简易评分趋势折线（最近 N 次训练的 training_score，0-100）。"""
        cv.delete("spark")
        w = max(360, cv.winfo_width())
        h = 92
        pad = 10
        n = len(scores)
        if n < 2:
            return

        def xy(i, v):
            x = pad + (w - 2 * pad) * (i / (n - 1))
            y = (h - pad) - (h - 2 * pad) * (max(0, min(100, v)) / 100.0)
            return x, y

        pts = [xy(i, v) for i, v in enumerate(scores)]
        for i in range(len(pts) - 1):
            cv.create_line(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1],
                           fill=COLORS.get("accent"), width=2, tags=("spark",))
        for x, y in pts:
            cv.create_oval(x - 3, y - 3, x + 3, y + 3,
                           fill=COLORS.get("accent"), outline="", tags=("spark",))
        cv.create_text(pad, 6, text="训练评分趋势（0-100）", anchor="w",
                       fill=COLORS["subtext"], font=FONTS["small"], tags=("spark",))

    def _delete_selected_library_record(self):
        if self._lib_tv is None:
            return
        sel = self._lib_tv.selection()
        if not sel:
            self._set_msg("请先在棋谱库中选中一盘棋")
            return
        rec = self._lib_map.get(sel[0])
        if not rec:
            return
        if not messagebox.askyesno("删除棋谱", "从棋谱库删除「%s」？\n不会删除原始来源文件。" % rec.get("name", "")):
            return
        if delete_record(rec.get("id")):
            if self._library_record_id == rec.get("id"):
                self._library_record_id = None
            self._refresh_library_window()
            self._set_msg("已从棋谱库删除：%s" % rec.get("name", ""))

    # ---- 引擎 / 模型 / 规则设置（持久化到 user_settings.json）----
    def open_settings(self):
        if self._settings_win is not None and self._settings_win.winfo_exists():
            self._settings_win.lift()
            self._settings_win.focus_set()
            return
        win = tk.Toplevel(self)
        self._settings_win = win
        self._prepare_child_window(
            win, "系统设置", 840, 690, minsize=(760, 600))
        win.protocol("WM_DELETE_WINDOW", self._close_settings_window)
        win.columnconfigure(0, weight=1)
        win.rowconfigure(0, weight=1)
        exe_var = tk.StringVar(value=self.katago_exe)
        model_var = tk.StringVar(value=self.model_file)
        rules_var = tk.StringVar(value=str(self.rules))
        komi_var = tk.StringVar(value=str(self.komi))
        visits_var = tk.StringVar(value=str(self.cfg.get("max_visits", 200)))
        candidate_count_var = tk.StringVar(value=str(self._candidate_count))
        pv_length_var = tk.StringVar(value=str(self._pv_length))
        style_labels = [UI_STYLE_LABELS["simple"]]
        style_label_to_key = {label: key for key, label in UI_STYLE_LABELS.items()}
        ui_style_label_var = tk.StringVar(
            value=UI_STYLE_LABELS.get(self._ui_style, UI_STYLE_LABELS["simple"]))
        training_mode_var = tk.StringVar(value=str(self.cfg.get("training_speed_mode", "fast")))
        library_visits_var = tk.StringVar(value=str(self.cfg.get("library_training_visits", 120)))
        profile_cfg = self.cfg.get("profile", {}) or {}
        profile_names_var = tk.StringVar(
            value="，".join(profile_cfg.get("my_player_names") or []))
        profile_side_var = tk.StringVar(
            value=str(profile_cfg.get("default_profile_side", "unknown")))
        profile_window_var = tk.StringVar(
            value=str(profile_cfg.get("profile_window_games", 30)))
        engines = list_engine_paths(self.cfg.runtime_dir)
        models = list_model_paths(self.cfg.runtime_dir)

        content = tk.Frame(win, bg=COLORS["bg"], padx=12, pady=10)
        content.grid(row=0, column=0, sticky="nsew")
        content.columnconfigure(0, weight=1)
        content.columnconfigure(1, weight=1)

        def section(title, row, column=0, columnspan=1, hint=""):
            box = ttk.LabelFrame(content, text=" %s " % title, style="Card.TLabelframe")
            box.grid(row=row, column=column, columnspan=columnspan, sticky="nsew",
                     padx=(0, 8) if column == 0 and columnspan == 1 else 0,
                     pady=(0, 9))
            box.columnconfigure(1, weight=1)
            start_row = 0
            if hint:
                tk.Label(
                    box, text=hint, bg=COLORS["card"], fg=COLORS["subtext"],
                    font=FONTS["small"], justify=tk.LEFT, wraplength=700
                ).grid(row=0, column=0, columnspan=3, sticky="ew", padx=8, pady=(2, 7))
                start_row = 1
            return box, start_row

        def field(parent, row, label, widget, extra=None):
            ttk.Label(parent, text=label).grid(
                row=row, column=0, sticky="w", padx=8, pady=5)
            widget.grid(row=row, column=1, sticky="ew", padx=6, pady=5)
            if extra is not None:
                extra.grid(row=row, column=2, sticky="w", padx=(0, 8), pady=5)

        appearance, ar = section(
            "外观", 0, 0, 2,
            "统一深色主题：各区域亮度平滑过渡，对比度合理，适合长时间复盘。")
        appearance.columnconfigure(1, weight=0)
        appearance.columnconfigure(2, weight=1)
        ttk.Label(appearance, text="界面风格：").grid(
            row=ar, column=0, sticky="w", padx=8, pady=6)
        ttk.OptionMenu(
            appearance, ui_style_label_var, ui_style_label_var.get(),
            *style_labels).grid(row=ar, column=1, sticky="w", padx=6, pady=6)
        preview = tk.Canvas(
            appearance, width=270, height=92, bg=COLORS["bg"],
            highlightthickness=1, highlightbackground=COLORS["muted"])
        preview.grid(row=ar, column=2, rowspan=2, sticky="e", padx=8, pady=3)
        self._draw_style_preview(
            preview, style_label_to_key.get(ui_style_label_var.get(), "simple"))
        ui_style_label_var.trace_add(
            "write",
            lambda *_: self._draw_style_preview(
                preview, style_label_to_key.get(ui_style_label_var.get(), "simple")))
        tk.Label(
            appearance,
            text="提示：当前为统一深色主题，Ctrl+T 可刷新视觉。",
            bg=COLORS["card"], fg=COLORS["subtext"], font=FONTS["small"],
            justify=tk.LEFT, wraplength=430
        ).grid(row=ar + 1, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 5))

        engine, er = section(
            "引擎与规则", 1, 0, 2,
            "这里决定 KataGo 分析进程、模型、规则和贴目；修改引擎或模型后会自动重启分析进程。")
        field(
            engine, er, "引擎 (.exe)：",
            ttk.Combobox(engine, textvariable=exe_var, values=engines, width=62),
            ttk.Button(engine, text="…", width=3,
                       command=lambda: self._pick_file(
                           exe_var, [("可执行文件", "*.exe")], self.katago_exe)))
        er += 1
        field(
            engine, er, "模型 (.bin.gz)：",
            ttk.Combobox(engine, textvariable=model_var, values=models, width=62),
            ttk.Button(engine, text="…", width=3,
                       command=lambda: self._pick_file(
                           model_var,
                           [("KataGo 模型", "*.bin.gz"), ("所有文件", "*.*")],
                           self.model_file)))
        er += 1
        field(
            engine, er, "规则：",
            ttk.OptionMenu(
                engine, rules_var, self.rules, "chinese", "japanese", "korean",
                "tromp-taylor", "aga", "new-zealand"))
        er += 1
        field(engine, er, "贴目 komi：", ttk.Entry(engine, textvariable=komi_var, width=10))

        analysis, rr = section("分析参数", 2, 0, 1)
        analysis.columnconfigure(1, weight=1)
        field(analysis, rr, "复盘 maxVisits：",
              ttk.Entry(analysis, textvariable=visits_var, width=10))
        rr += 1
        ttk.Label(analysis, text="复盘预设：").grid(
            row=rr, column=0, sticky="w", padx=8, pady=5)
        preset = tk.Frame(analysis, bg=COLORS["card"])
        preset.grid(row=rr, column=1, sticky="w", padx=6, pady=5)
        for text, value in (("快 80", "80"), ("标准 200", "200"), ("深入 800", "800")):
            self._make_button(
                preset, text,
                lambda v=value: visits_var.set(v), variant="default"
            ).pack(side=tk.LEFT, padx=(0, 6))
        rr += 1
        field(analysis, rr, "推荐点数量：",
              ttk.Spinbox(analysis, from_=1, to=MAX_CANDIDATES,
                          textvariable=candidate_count_var, width=8))
        rr += 1
        field(analysis, rr, "主变显示长度：",
              ttk.Spinbox(analysis, from_=1, to=30,
                          textvariable=pv_length_var, width=8))
        rr += 1
        ttk.Label(analysis, text="训练速度：").grid(
            row=rr, column=0, sticky="w", padx=8, pady=5)
        mode_labels = ["%s（%d visits）" % (label, visits) for _key, (label, visits) in TRAINING_SPEED_MODES.items()]
        label_to_mode = {
            "%s（%d visits）" % (label, visits): key
            for key, (label, visits) in TRAINING_SPEED_MODES.items()
        }
        current_mode = training_mode_var.get()
        current_label = "%s（%d visits）" % TRAINING_SPEED_MODES.get(current_mode, TRAINING_SPEED_MODES["fast"])
        training_mode_label_var = tk.StringVar(value=current_label)
        ttk.OptionMenu(
            analysis, training_mode_label_var, current_label, *mode_labels
        ).grid(row=rr, column=1, sticky="w", padx=6, pady=5)
        rr += 1
        field(analysis, rr, "棋局库后台 visits：",
              ttk.Entry(analysis, textvariable=library_visits_var, width=10))
        rr += 1
        ttk.Label(analysis, text="训练揭示首选：").grid(
            row=rr, column=0, sticky="w", padx=8, pady=5)
        auto_hint_training_var = tk.BooleanVar(
            value=bool(self.cfg.get("auto_hint_training", False)))
        ttk.Checkbutton(
            analysis, text="训练中也自动揭示 AI 首选（默认关闭，保留盲下训练）",
            variable=auto_hint_training_var
        ).grid(row=rr, column=1, sticky="w", padx=6, pady=5)

        profile, pr = section("个人画像", 2, 1, 1)
        profile.columnconfigure(1, weight=1)
        field(profile, pr, "我的棋手名：",
              ttk.Entry(profile, textvariable=profile_names_var, width=32))
        pr += 1
        field(
            profile, pr, "默认画像方：",
            ttk.OptionMenu(
                profile, profile_side_var, profile_side_var.get(),
                "unknown", "B", "W", "both"))
        pr += 1
        field(profile, pr, "画像最近棋局数：",
              ttk.Entry(profile, textvariable=profile_window_var, width=10))
        pr += 1
        tk.Label(
            profile,
            text="棋手名可用中文逗号或英文逗号分隔；如果不确定执棋方，可保持 unknown，再在棋谱库中逐盘标记。",
            bg=COLORS["card"], fg=COLORS["subtext"], font=FONTS["small"],
            justify=tk.LEFT, wraplength=330
        ).grid(row=pr, column=0, columnspan=2, sticky="ew", padx=8, pady=(7, 2))

        def apply_and_close():
            training_mode_var.set(label_to_mode.get(training_mode_label_var.get(), "fast"))
            self._apply_settings(exe_var.get().strip(), model_var.get().strip(),
                                 rules_var.get().strip(), komi_var.get().strip(),
                                 visits_var.get().strip(), training_mode_var.get().strip(),
                                 library_visits_var.get().strip(),
                                 profile_names_var.get().strip(),
                                 profile_side_var.get().strip(),
                                 profile_window_var.get().strip(),
                                 candidate_count_var.get().strip(),
                                 pv_length_var.get().strip(),
                                 style_label_to_key.get(
                                     ui_style_label_var.get(), "simple"),
                                 bool(auto_hint_training_var.get()))
            self._close_settings_window()
        btns = tk.Frame(win, bg=COLORS["card"], highlightthickness=1,
                        highlightbackground=COLORS["muted"])
        btns.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))
        inner = tk.Frame(btns, bg=COLORS["card"])
        inner.pack(fill="x", padx=12, pady=9)
        tk.Label(
            inner, text="设置保存到 user_settings.json",
            bg=COLORS["card"], fg=COLORS["subtext"], font=FONTS["small"]
        ).pack(side=tk.LEFT)
        self._make_button(inner, "检测配置",
                   lambda: self._check_settings(exe_var.get().strip(), model_var.get().strip(),
                                                        rules_var.get().strip(), komi_var.get().strip(),
                                                        visits_var.get().strip(),
                                                        label_to_mode.get(training_mode_label_var.get(), "fast"),
                                                        library_visits_var.get().strip(),
                                                        profile_names_var.get().strip(),
                                                        profile_side_var.get().strip(),
                                                        profile_window_var.get().strip(),
                                                        candidate_count_var.get().strip(),
                                                        pv_length_var.get().strip()),
                   variant="default"
                   ).pack(side=tk.RIGHT, padx=8)
        self._make_button(inner, "应用（持久化；引擎/模型变更时自动重启）",
                   apply_and_close, variant="accent"
                   ).pack(side=tk.RIGHT, padx=8)

    def _close_settings_window(self):
        if self._settings_win is not None:
            try:
                self._settings_win.destroy()
            except tk.TclError:
                pass
        self._settings_win = None

    def _pick_file(self, var, filetypes, initial):
        path = filedialog.askopenfilename(
            filetypes=filetypes, initialdir=(os.path.dirname(initial) or None))
        if path:
            var.set(path)

    def _check_settings(self, exe, model, rules, komi, visits, training_mode=None,
                        library_visits=None, profile_names=None,
                        profile_side=None, profile_window=None,
                        candidate_count=None, pv_length=None):
        temp = ConfigManager(path=self.cfg.path)
        temp.data = dict(self.cfg.data)
        temp.data.update(engine_path=exe, model_path=model, rules=rules,
                         komi=komi, max_visits=visits,
                         training_speed_mode=training_mode or "fast",
                         library_training_visits=library_visits or 120,
                         candidate_count=candidate_count or self._candidate_count,
                         pv_length=pv_length or self._pv_length)
        pre = temp.preflight()
        if pre["ok"]:
            msg = "配置可用"
            if pre["warnings"]:
                msg += "\n\n警告：\n" + "\n".join(pre["warnings"])
            messagebox.showinfo("配置检测", msg, parent=self)
        else:
            messagebox.showerror("配置检测失败", "\n".join(pre["errors"]), parent=self)

    def _apply_settings(self, exe, model, rules, komi, visits, training_mode=None,
                        library_visits=None, profile_names=None,
                        profile_side=None, profile_window=None,
                        candidate_count=None, pv_length=None, ui_style=None,
                        auto_hint_training=None):
        try:
            komi_v = float(komi)
        except Exception:
            komi_v = self.komi
        try:
            visits_v = int(visits)
        except Exception:
            visits_v = self.cfg.get("max_visits", 200)
        try:
            library_visits_v = int(library_visits)
        except Exception:
            library_visits_v = self.cfg.get("library_training_visits", 120)
        try:
            candidate_count_v = max(1, min(MAX_CANDIDATES, int(candidate_count)))
        except Exception:
            candidate_count_v = self._candidate_count
        try:
            pv_length_v = max(1, min(30, int(pv_length)))
        except Exception:
            pv_length_v = self._pv_length
        old_profile = dict(self.cfg.get("profile", {}) or {})
        names_v = (
            [
                item.strip()
                for item in str(profile_names).replace("，", ",").split(",")
                if item.strip()
            ]
            if profile_names is not None
            else list(old_profile.get("my_player_names") or []))
        side_v = (
            profile_side if profile_side in ("B", "W", "both", "unknown")
            else old_profile.get("default_profile_side", "unknown"))
        try:
            window_v = max(1, min(200, int(profile_window)))
        except Exception:
            window_v = int(old_profile.get("profile_window_games", 30) or 30)
        profile_v = {
            "my_player_names": names_v,
            "default_profile_side": side_v,
            "profile_window_games": window_v,
        }
        training_mode_v = training_mode if training_mode in TRAINING_SPEED_MODES else "fast"
        auto_hint_training_v = (
            bool(auto_hint_training)
            if auto_hint_training is not None
            else bool(self.cfg.get("auto_hint_training", False)))
        old_exe, old_model = self.katago_exe, self.model_file
        old_rules, old_komi = self.rules, self.komi
        old_training_mode = self.cfg.get("training_speed_mode", "fast")
        ui_style_v = ui_style if ui_style in UI_STYLE_LABELS else self._ui_style
        old_ui_style = self._ui_style
        # 持久化（ConfigManager.update 立即写 user_settings.json）
        self.cfg.update(engine_path=exe or None, model_path=model or None,
                        rules=rules or None, komi=komi_v, max_visits=visits_v,
                        training_speed_mode=training_mode_v,
                        library_training_visits=library_visits_v,
                        candidate_count=candidate_count_v,
                        pv_length=pv_length_v,
                        ui_style=ui_style_v,
                        auto_hint_training=auto_hint_training_v,
                        profile=profile_v)
        # 同步实例属性
        self.rules = self.cfg.get("rules")
        self.komi = self.cfg.get("komi")
        self.katago_exe = self.cfg.get("engine_path")
        self.model_file = self.cfg.get("model_path")
        self._candidate_count = candidate_count_v
        self._pv_length = pv_length_v
        if ui_style_v != old_ui_style:
            self._ui_style = ui_style_v
            self._refresh_visual_palette()
        self.btn_pv.configure(
            text=("主变 %d 步 ✓" if self._show_pv else "主变 %d 步")
            % self._pv_length)
        if self.tree.current.analysis and not self.scoring_mode:
            self._render_analysis(self.tree.current.analysis)
        exe_or_model_changed = (
            os.path.abspath(self.katago_exe or "") != os.path.abspath(old_exe or "")
            or os.path.abspath(self.model_file or "") != os.path.abspath(old_model or ""))
        cache_signature_changed = (
            exe_or_model_changed
            or self.rules != old_rules
            or float(self.komi) != float(old_komi)
            or training_mode_v != old_training_mode
        )
        if cache_signature_changed:
            self._library_bg_recent.clear()
            self._reset_problem_comparison_state()
        if exe_or_model_changed and self.client and self.client.is_alive():
            self._stop_katago()
            self._start_katago()
            self._set_msg("已切换引擎/模型并持久化，重新加载中…")
        else:
            self._set_msg("已保存设置：界面和推荐显示已立即生效；规则/贴目/visits 下次分析生效")

    # ===================== 辅助 =====================
    def _pixel_to_xy(self, px, py):
        x = round((px - self.MARGIN) / self.CELL)
        y = round((py - self.MARGIN) / self.CELL)
        if 0 <= x < self.size and 0 <= y < self.size:
            cx = self.MARGIN + x * self.CELL
            cy = self.MARGIN + y * self.CELL
            if (px - cx) ** 2 + (py - cy) ** 2 <= (self.CELL * 0.55) ** 2:
                return (x, y)
        return None

    def _set_status(self, text, color_key="subtext"):
        """统一状态药丸更新：文字 + 语义色（文字色用 color_key，底色用对应的 *_s 柔和色）。

        替代散落的 lbl_status.config(text=, fg=)，保证药丸视觉一致。
        """
        fg = COLORS.get(color_key, COLORS["subtext"])
        # 底色映射：红→red_s，琥珀→amber_s，绿/accent→accent_s，其它→accent_s
        bg_key = {"red": "red_s", "amber": "amber_s", "green": "accent_s"}.get(color_key, "accent_s")
        bg = COLORS.get(bg_key, COLORS["accent_s"])
        try:
            self.lbl_status.config(text=text, fg=fg, bg=bg)
        except Exception:
            pass

    def _set_msg(self, text, kind=None):
        """统一状态反馈的语义色，不依赖弹窗也能辨认成功、进行中和错误。"""
        value = str(text or "")
        if kind is None:
            kind = semantic_message_kind(value)
        palette = {
            "error": (COLORS["red_s"], COLORS["red"], COLORS["red"]),
            "warning": (COLORS["amber_s"], COLORS["amber"], COLORS["amber"]),
            "progress": (COLORS["accent_s"], COLORS["accent"], COLORS["accent_m"]),
            "success": (COLORS["accent_s"], COLORS["green"], COLORS["accent_m"]),
            "neutral": (COLORS["card2"], COLORS["subtext"], COLORS["muted"]),
        }
        background, foreground, border = palette.get(kind, palette["neutral"])
        self.lbl_msg.config(
            text=value, bg=background, fg=foreground,
            highlightbackground=border)

    # ---- 全屏 + 自适应缩放 ----
    def _toggle_fullscreen(self):
        """F11：切换全屏。"""
        self._fullscreen = not self._fullscreen
        self.attributes('-fullscreen', self._fullscreen)

    def _exit_fullscreen(self):
        """ESC：退出全屏。"""
        if self._fullscreen:
            self._fullscreen = False
            self.attributes('-fullscreen', False)

    def _on_configure(self, event):
        """窗口尺寸变 → 防抖重算 CELL/MARGIN（棋盘自适应缩放）。"""
        if event.widget is not self:
            return
        if self._resize_job is not None:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(80, self._do_resize, event.width, event.height)

    def _do_resize(self, win_w, win_h):
        """按棋盘面板的宽高共同计算正方形尺寸，避免窄窗口横向溢出。"""
        self._resize_job = None
        panel_w = None
        if hasattr(self, "_board_panel"):
            mapped_w = self._board_panel.winfo_width()
            if self._board_panel.winfo_ismapped() and mapped_w > 50:
                panel_w = mapped_w
        if panel_w is None:
            # 复盘页未显示（用户在今日学习页）时按窗口宽可靠估算：
            # 窗口 - 左导航(按断点) - 右面板 - workspace 内边距
            try:
                from ui.tokens import nav_metrics
                nav_w, _right = nav_metrics(win_w)
            except Exception:
                nav_w = 64
            panel_w = win_w - nav_w - RIGHT_PANEL_WIDTH - 40
        avail_w = panel_w - 28
        # 预留应用栏(67) + 顶部彩条(3) + workspace padding(20) + 传输栏(56) + 棋盘边距(16) + 余量(20) ≈ 182
        # 原 250 扣除过多导致最大化后棋盘被高度压小；减到 190 让棋盘更大（目标占窗口 2/3）
        avail_h = win_h - 190
        # 用户反馈：棋盘整体缩小一档——先满足上下按钮/进度条占高后按比例缩放
        avail = int(min(avail_w, avail_h) * BOARD_SCALE)
        if avail < 200:
            return
        new_cell = max(12, (avail - 38) // (self.size - 1))
        if new_cell == self.CELL:
            return
        self.CELL = new_cell
        self.MARGIN = max(13, int(new_cell * 0.78))
        self.BOARD_PIX = self.MARGIN * 2 + self.CELL * (self.size - 1)
        self._hover_point = None
        self.canvas.config(width=self.BOARD_PIX, height=self.BOARD_PIX)
        self.redraw()

    def _refresh_visual_palette(self):
        """把当前调色板应用到已存在控件；用于亮暗切换和风格切换。"""
        old_pal = dict(COLORS)
        new_pal = self._current_palette()
        cmap = {
            old_pal[k]: new_pal[k]
            for k in new_pal
            if k in old_pal and old_pal[k] != new_pal[k]
        }
        COLORS.clear()
        COLORS.update(new_pal)
        self._setup_style()
        self.configure(bg=COLORS["bg"])
        # 联动 CustomTkinter 外观模式：深色调 dark，浅色调 light，保证 CTkButton/Frame 跟随主题
        if _HAS_CTK:
            ctk.set_appearance_mode("dark" if self._theme_dark else "light")
        self._remap_widget_colors(self, cmap)
        self._draw_brand_mark()
        self.redraw()
        if getattr(self, "scale", None) is not None:
            try:
                self.scale.redraw()
            except tk.TclError:
                pass
        if hasattr(self, "btn_theme"):
            self.btn_theme.configure(text=self._theme_button_text())
        if getattr(self.tree.current, "analysis", None) and not self.scoring_mode:
            self._refresh_situation_labels()
            self._sync_candidate_selection()

    # ---- 主题：已统一为单一深色主题，Ctrl+T 仅刷新视觉 ----
    def _toggle_theme(self):
        """Ctrl+T：当前为统一深色主题，按一下刷新调色板（兼容旧快捷键习惯）。"""
        self._theme_dark = True
        self._refresh_visual_palette()
        self._set_msg("当前为统一深色主题（对比度已优化，各区域亮度平滑过渡）")

    def _remap_widget_colors(self, parent, cmap):
        """递归遍历所有子控件，把 bg/fg/highlightbackground 按 cmap 映射到新色值。"""
        for child in parent.winfo_children():
            for prop in ("bg", "fg", "highlightbackground", "selectbackground",
                         "activebackground", "disabledforeground"):
                try:
                    v = str(child.cget(prop))
                    if v in cmap:
                        child.configure(**{prop: cmap[v]})
                except (tk.TclError, TypeError):
                    pass
            self._remap_widget_colors(child, cmap)

    def _on_close(self):
        self.destroy()


if __name__ == "__main__":
    GoAnalyzer().mainloop()
