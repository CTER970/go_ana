"""test_taxonomy —— 九类技术错误分类、证据保留与克制性测试。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from taxonomy import (
    CATEGORY_UNCLASSIFIED, TAXONOMY_VERSION, category_label,
    classify_problem,
)


def check(name, cond, extra=""):
    print("[CHECK] %-40s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        raise AssertionError(name)


def run():
    # 单标签 → 主类别 + medium 置信
    r = classify_problem({"problem_tags": ["tenuki_timing"], "score_loss": 3.0})
    check("脱先时机 → sente_tenuki",
          r["primary_category"] == "sente_tenuki"
          and r["category_confidence"] == "medium")
    check("证据保留标签来源",
          r["category_evidence"] == ["deterministic_tag:tenuki_timing"]
          and r["taxonomy_version"] == TAXONOMY_VERSION)

    # 过分 → 主 attack_defense，副 weak_groups
    r = classify_problem({"problem_tags": ["overplay"], "score_loss": 7.0})
    check("过分 → 攻防主类+弱棋副类",
          r["primary_category"] == "attack_defense"
          and r["secondary_categories"] == ["weak_groups"])

    # 多标签 → 低置信，主类别按优先序（死活最具体）
    r = classify_problem({"problem_tags": ["overplay", "life_and_death"],
                          "score_loss": 8.0})
    check("多标签主类按优先序",
          r["primary_category"] == "life_death"
          and r["category_confidence"] == "low"
          and "reading" in r["secondary_categories"])

    # 意图佐证与主类一致 → high
    r = classify_problem(
        {"problem_tags": ["overplay"], "score_loss": 6.0},
        intent={"difference": "实战把手段投向攻击，AI则优先处理防守"})
    check("意图佐证一致提升置信",
          r["primary_category"] == "attack_defense"
          and r["category_confidence"] == "high"
          and any(e.startswith("intent_") for e in r["category_evidence"]))

    # 无标签无意图 → 明确待分类，不猜
    r = classify_problem({"problem_tags": [], "score_loss": 2.5})
    check("证据不足明确待分类",
          r["primary_category"] == CATEGORY_UNCLASSIFIED
          and r["category_confidence"] == "low")

    # 无标签但有意图 → 低置信方向
    r = classify_problem({"problem_tags": []},
                         intent={"actualIntent": "想在右边围地"})
    check("意图兜底给方向",
          r["primary_category"] == "direction"
          and r["category_confidence"] == "low")

    check("中文标签", category_label("weak_groups") == "弱棋/轻重"
          and category_label(CATEGORY_UNCLASSIFIED) == "待分类")

    print("test_taxonomy: 全部通过")


if __name__ == "__main__":
    run()
