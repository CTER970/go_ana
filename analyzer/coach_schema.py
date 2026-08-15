"""coach_schema —— CoachExplanation 强制结构与校验（项目大纲 §35）。

所有教练输出（确定性教练 / 未来接入的 LLM Provider）必须符合本 schema：
字段齐全、类型正确、分类在枚举内。结构不合法直接拒绝，走确定性回退，
不允许"半合法"输出流向用户。
"""
from __future__ import annotations

SCHEMA_VERSION = 1

CONFIDENCE_VALUES = ("high", "medium", "low")
UNCERTAINTY_VALUES = ("high", "medium", "low")

# 字段名 → (必须, 类型)
_FIELDS = {
    "summary": (True, str),
    "mistake_category": (True, str),
    "category_confidence": (True, str),
    "what_happened": (True, str),
    "why_problematic": (True, str),
    "likely_reason": (True, str),
    "transferable_rule": (True, str),
    "reasonable_moves": (True, list),
    "short_variation": (True, list),
    "uncertainty": (True, str),
    "evidence_refs": (True, list),
}


def empty_explanation():
    """合法的空解释（确定性回退在证据不足时使用）。"""
    return {
        "summary": "",
        "mistake_category": "unclassified",
        "category_confidence": "low",
        "what_happened": "",
        "why_problematic": "",
        "likely_reason": "",
        "transferable_rule": "",
        "reasonable_moves": [],
        "short_variation": [],
        "uncertainty": "high",
        "evidence_refs": [],
        "schema_version": SCHEMA_VERSION,
        "source": "deterministic",
    }


def validate_explanation(data):
    """结构校验。返回 (ok, errors)；errors 为空列表表示合法。"""
    errors = []
    if not isinstance(data, dict):
        return False, ["解释必须是 dict，得到 %s" % type(data).__name__]
    for field, (required, kind) in _FIELDS.items():
        if field not in data or data[field] is None:
            if required:
                errors.append("缺少必填字段：%s" % field)
            continue
        value = data[field]
        if kind is str and not isinstance(value, str):
            errors.append("%s 必须是字符串" % field)
        elif kind is list and not isinstance(value, list):
            errors.append("%s 必须是列表" % field)
    if data.get("category_confidence") not in CONFIDENCE_VALUES:
        errors.append("category_confidence 必须是 %s 之一" % (CONFIDENCE_VALUES,))
    if data.get("uncertainty") not in UNCERTAINTY_VALUES:
        errors.append("uncertainty 必须是 %s 之一" % (UNCERTAINTY_VALUES,))
    for field in ("reasonable_moves", "short_variation"):
        if isinstance(data.get(field), list) and \
                not all(isinstance(x, str) for x in data[field]):
            errors.append("%s 的每一项必须是字符串" % field)
    return (not errors), errors
