"""v4.38 三项个人分析能力的纯逻辑测试。"""
import os
import shutil
import sys
import tempfile
from types import SimpleNamespace

from analysis_queue import AnalysisQueue
from candidate_recommendation import build_candidate_recommendations
from evidence_explanation import build_evidence_explanation, format_evidence_explanation

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def check(name, cond):
    print("[CHECK] %-30s %s" % (name, "OK" if cond else "FAIL"))
    if not cond:
        raise AssertionError(name)


def test_explanation():
    ev = SimpleNamespace(loss=7.2, best_move="Q16")
    quality = SimpleNamespace(problem_tags=["opening_direction"], winrate_drop=11.4)
    intent = {"actualMove": "D16", "aiMove": "Q16", "difference": "方向不同。",
              "aiIntent": "优先处理右上角，参考主变 Q16 → D4。"}
    comparison = {"diagnosis": "实战处理左上，AI优先右上。", "visits": 500,
                  "scoreGain": 6.8, "winrateGainPct": 10.2, "controlGain": 4,
                  "actual": {"pv": ["D16", "Q4"]}, "ai": {"pv": ["Q16", "D4"]}}
    result = build_evidence_explanation(ev, intent, quality, comparison)
    text = format_evidence_explanation(result)
    check("讲解三段完整", all(x in text for x in ("问题根因", "AI 手目的", "实战后果")))
    check("讲解带可追溯证据", result["verified"] and "500 visits" in text and "6.8 目" in text)
    partial = build_evidence_explanation(ev, intent, quality, None)
    check("缺深算时诚实降级", not partial["verified"] and "基础分析" in partial["disclaimer"])


def test_recommendation():
    candidates = [
        {"move": "Q16", "order": 0, "scoreLead": 2.0, "prior": .12, "pv": ["Q16"] * 12},
        {"move": "D4", "order": 1, "scoreLead": 1.6, "prior": .32, "pv": ["D4"] * 4},
        {"move": "C4", "order": 2, "scoreLead": -.5, "prior": .45, "pv": ["C4"] * 2},
    ]
    rows = build_candidate_recommendations(candidates, "B", "4-6级")
    check("AI首选明确", "AI最优" in rows[0]["badges"])
    check("易懂建议不必等于一选", "稳健易懂" in rows[1]["badges"])
    check("当前棋力建议有依据", any("当前棋力参考" in r["badges"] for r in rows))
    check("不冒充人类模型", all(not r["humanModel"] and "prior策略信号" in r["basis"] for r in rows))
    unknown = build_candidate_recommendations(candidates, "B", "—")
    check("样本不足不显示破折号档位", any(
        "按样本不足的" in r["reason"] for r in unknown))
    legacy = build_candidate_recommendations([
        {"move": "Q16", "order": 0, "scoreLead": 2.0, "policy": .2, "pv": []},
    ], "B", None)
    check("旧 policy 缓存仍兼容", legacy[0]["policy"] == .2)


def test_queue():
    root = tempfile.mkdtemp()
    try:
        path = os.path.join(root, "queue.json")
        q = AnalysisQueue(path)
        _, added1 = q.enqueue("g1", "第一盘", 3)
        _, added2 = q.enqueue("g1", "重复", 3)
        check("队列按棋谱去重", added1 and not added2 and len(q.tasks()) == 1)
        task = q.claim_next()
        q.update(task["id"], 1, 3, "1/3")
        recovered = AnalysisQueue(path)
        check("运行中断自动恢复等待", recovered.tasks()[0]["status"] == "queued")
        task = recovered.claim_next()
        q = recovered
        q.pause()
        check("暂停状态持久化", AnalysisQueue(path).is_paused())
        q.resume()
        task = q.claim_next()
        q.fail(task["id"], "模拟失败")
        check("失败任务可重试", q.retry_failed() == 1 and q.tasks()[0]["status"] == "queued")
        task = q.claim_next()
        q.finish(task["id"])
        check("任务完成闭环", q.tasks()[0]["status"] == "completed")
    finally:
        shutil.rmtree(root)


if __name__ == "__main__":
    test_explanation(); test_recommendation(); test_queue()
    print("test_analysis_product 全部通过 ✅")
