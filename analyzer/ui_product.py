"""产品化 UI 的纯逻辑：棋局上下文、文本收缩和状态语义。

不依赖 tkinter，便于在无 Tcl 环境中验证产品文案与状态规则。
"""
from __future__ import annotations


RULE_LABELS = {
    "chinese": "中国规则",
    "japanese": "日本规则",
    "aga": "AGA",
    "tromp-taylor": "Tromp-Taylor",
}


def compact_text(text, limit):
    value = str(text or "").strip()
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return value
    if limit <= 0:
        return ""
    if len(value) <= limit:
        return value
    if limit == 1:
        return "…"
    return value[:limit - 1] + "…"


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def build_game_context(label, black, white, rules, komi,
                       current_depth, total_moves, result=""):
    black = str(black or "黑方")
    white = str(white or "白方")
    title = str(label or "新棋局")
    lowered = title.lower()
    for suffix in (".kga.json", ".sgf", ".json"):
        if lowered.endswith(suffix):
            title = title[:-len(suffix)]
            break
    if title == "新棋局" and (black != "黑方" or white != "白方"):
        title = "%s vs %s" % (black, white)
    rule_label = RULE_LABELS.get(str(rules).lower(), str(rules))
    meta = "● %s  vs  ○ %s · %s · 贴 %g · %d/%d 手" % (
        black, white, rule_label, _safe_float(komi, 7.5),
        _safe_int(current_depth), _safe_int(total_moves))
    if result:
        meta += " · %s" % result
    return compact_text(title, 30), compact_text(meta, 54)


def semantic_message_kind(text):
    value = str(text or "")
    if any(token in value for token in (
            "失败", "错误", "异常", "非法", "不存在", "无法")):
        return "error"
    if any(token in value for token in (
            "正在", "加载中", "等待", "分析中", "准备", "自动播放中")):
        return "progress"
    if any(token in value for token in (
            "请先", "暂无", "未配置", "不能", "跳过", "不足")):
        return "warning"
    if any(token in value for token in (
            "已保存", "已导出", "已导入", "已打开", "已完成",
            "分析完成", "已更新", "已加入", "已采用")):
        return "success"
    return "neutral"


def fit_window_size(saved_size, screen_width, screen_height,
                    default=(1240, 760), minimum=(1040, 640),
                    screen_margin=(32, 80)):
    """把保存尺寸限制在当前屏幕可用范围，给标题栏和任务栏预留空间。"""
    screen_width = max(1, int(screen_width))
    screen_height = max(1, int(screen_height))
    max_width = max(1, screen_width - int(screen_margin[0]))
    max_height = max(1, screen_height - int(screen_margin[1]))
    min_width = min(int(minimum[0]), max_width)
    min_height = min(int(minimum[1]), max_height)

    width, height = int(default[0]), int(default[1])
    try:
        parsed_width, parsed_height = (
            int(value) for value in str(saved_size or "").lower().split("x", 1))
        if parsed_width > 0 and parsed_height > 0:
            width, height = parsed_width, parsed_height
    except (TypeError, ValueError):
        pass
    return (
        max(min_width, min(width, max_width)),
        max(min_height, min(height, max_height)),
    )
