"""human_sl —— KataGo Human SL 人类选点模型封装（项目大纲 §6-8、M7）。

用途不是"模拟一个1D AI"，而是判断某个错误是否属于当前棋力特别值得解决的
问题：比较同一手在本人档位与更高档位的 humanPolicy——本人档常下、高档明显
少下 = 优质学习点。

架构约束（大纲 §79）：
- 主模型负责棋力事实（胜率/目差）；
- Human SL 只负责人类选点概率，其分数绝不替代主模型数值。
"""
from __future__ import annotations

# KataGo b18 Human SL 模型支持的档位（rank_20k … rank_9d）
PROFILES = (
    "rank_20k", "rank_15k", "rank_10k", "rank_5k", "rank_1k",
    "rank_1d", "rank_2d", "rank_3d", "rank_4d", "rank_5d",
    "rank_6d", "rank_7d", "rank_8d", "rank_9d",
)
DEFAULT_PROFILE = "rank_1d"
DEFAULT_REFERENCE_PROFILE = "rank_3d"

# 判定阈值（第一版产品参数）：当前档概率 - 高档概率 ≥ POSITIVE_MIN 记为
# 优质学习点；两档都 ≥ COMMON_MIN 记为"常见手"（不适合用水平差异解释）。
POSITIVE_MIN = 0.10
COMMON_MIN = 0.15

# humanPolicy 响应字段的历史兼容（b18 human 模型为 humanPolicy）
_PRIOR_KEYS = ("humanPolicy", "humanPrior", "prior")


def normalize_profile(profile):
    text = str(profile or "").strip().lower()
    if text in PROFILES:
        return text
    return DEFAULT_PROFILE


def human_query(query, profile):
    """在普通分析查询上加 Human SL 档位（overrideSettings.humanSLProfile）。

    includePolicy 保持开启（humanPolicy 随 moveInfos 返回）。
    """
    q = dict(query or {})
    overrides = dict(q.get("overrideSettings") or {})
    overrides["humanSLProfile"] = normalize_profile(profile)
    q["overrideSettings"] = overrides
    q["includePolicy"] = True
    return q


def parse_human_prior(resp, move):
    """从响应 moveInfos 里取某手的 human 概率；无数据返回 None。"""
    move = str(move or "").lower()
    for info in (resp or {}).get("moveInfos") or []:
        if str(info.get("move") or "").lower() != move:
            continue
        for key in _PRIOR_KEYS:
            value = info.get(key)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return None
    return None


def compare_profiles(move, prior_current, prior_stronger, *,
                      current_profile=DEFAULT_PROFILE,
                      stronger_profile=DEFAULT_REFERENCE_PROFILE):
    """同一手在两档位下的概率对比 → HumanMoveProfile（大纲 §7、§14）。

    verdict：
    - "level_gap"   本人档常下、高档明显少下 → 优质学习点
    - "common_both" 两档都常下 → 不是水平差异问题
    - "rare_both"   两档都少下 → 可能只是随机失误
    - "unknown"     数据不足，不做结论
    """
    current_profile = normalize_profile(current_profile)
    stronger_profile = normalize_profile(stronger_profile)
    result = {
        "move": str(move or ""),
        "current_profile": current_profile,
        "stronger_profile": stronger_profile,
        "prior_current": prior_current,
        "prior_stronger": prior_stronger,
        "delta": None,
        "verdict": "unknown",
    }
    if prior_current is None or prior_stronger is None:
        return result
    delta = float(prior_current) - float(prior_stronger)
    result["delta"] = round(delta, 4)
    if prior_current >= COMMON_MIN and prior_stronger >= COMMON_MIN:
        result["verdict"] = "common_both"
    elif delta >= POSITIVE_MIN:
        result["verdict"] = "level_gap"
    elif prior_current < POSITIVE_MIN and prior_stronger < POSITIVE_MIN:
        result["verdict"] = "rare_both"
    return result


def level_gap_component(profile_result):
    """HumanMoveProfile → learning_priority 的 level_gap 分量（0-1）。

    只有明确 level_gap（本人常下/高档少下）才给分；common/rare/unknown
    一律 0——没有证据不给分。
    """
    if not profile_result or profile_result.get("verdict") != "level_gap":
        return 0.0
    delta = profile_result.get("delta")
    if delta is None:
        return 0.0
    return max(0.0, min(float(delta) * 2.0, 1.0))
