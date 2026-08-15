"""error_chain —— 错误链与问题簇（项目大纲 §48-49、M11）。

区分"最大爆炸点"与"根源错误"：AI 目损最大的一手往往是早期失误的后果
（63手留弱棋 → 151手崩盘，真正值得学的是 63 手）。

第一版不用机器学习，纯确定性聚类：
- 局部连续性：同色问题手间隔 ≤ max_gap 视为同一场战斗；
- 类别连续性：同 taxonomy 主类强化归簇（间隔放宽到 category_gap）；
- 时间距离：间隔越大越不可能同簇。
每个簇输出 root_problem（最早根源）/ largest_loss（最大损失）/
representative（建议学习节点）。
"""
from __future__ import annotations

CHAIN_VERSION = 1

# 同色问题手间隔 ≤ 24 手（约 12 个回合）视为同一场战斗——一场缠斗常跨 80 手
MAX_SAME_COLOR_GAP = 24
# 类别相同时放宽到 40 手（同主题的错误链往往跨更长时间）
MAX_SAME_CATEGORY_GAP = 40


def _num(value, default=0.0):
    try:
        return default if value is None else float(value)
    except (TypeError, ValueError):
        return default


def build_problem_clusters(problems, *, max_gap=MAX_SAME_COLOR_GAP,
                           category_gap=MAX_SAME_CATEGORY_GAP):
    """把问题手序列聚成问题簇（按手数升序处理）。

    problems: [{move_no, color, score_loss, category, ...}]（dict 或对象）。
    返回簇列表（按 root move_no 升序），每簇：
      {cluster_id, move_nos, root, largest_loss, representative,
       categories, total_loss, span}
    root = 簇内最早一手（根源候选）；representative = 学习价值最高的一手
    （损失最大且非 root 时优先 root——根源优先于爆炸点，大纲 §48）。
    """
    items = []
    for p in (problems or []):
        g = (lambda k, d=None: p.get(k, d)) if isinstance(p, dict) \
            else (lambda k, d=None: getattr(p, k, d))
        items.append({
            "move_no": int(g("move_no", 0) or 0),
            "color": str(g("color", "") or "").upper(),
            "score_loss": _num(g("score_loss")),
            "category": str(g("primary_category", "") or g("category", "") or ""),
        })
    items = [i for i in items if i["move_no"] > 0]
    items.sort(key=lambda i: i["move_no"])
    if not items:
        return []

    clusters = []
    current = None
    for item in items:
        if current is not None and _belongs(current, item, max_gap, category_gap):
            current["members"].append(item)
        else:
            current = {"members": [item]}
            clusters.append(current)

    out = []
    for cluster in clusters:
        members = cluster["members"]
        root = members[0]
        largest = max(members, key=lambda m: m["score_loss"])
        total = sum(m["score_loss"] for m in members)
        # 代表学习节点 = 根源手（大纲 §48：AI 目损最大的常是根源的后果，
        # 真正值得学的是埋下根源的那一手；爆炸点经 largest_loss 单独提供）
        representative = root
        categories = []
        for m in members:
            if m["category"] and m["category"] not in categories:
                categories.append(m["category"])
        out.append({
            "cluster_id": "chain-%d" % root["move_no"],
            "move_nos": [m["move_no"] for m in members],
            "root": root,
            "largest_loss": largest,
            "representative": representative,
            "categories": categories,
            "total_loss": round(total, 2),
            "span": members[-1]["move_no"] - root["move_no"],
        })
    return out


def _belongs(current, item, max_gap, category_gap):
    last = current["members"][-1]
    if item["color"] and last["color"] and item["color"] != last["color"]:
        gap = item["move_no"] - last["move_no"]
        # 异色问题手：双方在缠斗中互相受损——间隔 ≤ 6 手且类别相同才并簇
        return gap <= 6 and bool(item["category"]) and \
            item["category"] == last["category"] and item["category"] != "unclassified"
    gap = item["move_no"] - last["move_no"]
    if gap > max_gap:
        # 间隔超出但类别相同且都非 unclassified → 弱关联并入
        return gap <= category_gap and bool(item["category"]) and \
            item["category"] == last["category"] and \
            item["category"] != "unclassified"
    return True


def chain_summary(clusters):
    """错误链的人类可读摘要（UI/报告用）。

    当前聚类是启发式（时间邻接 + 类别连续），没有证明因果，
    因此措辞用"可能的根源"，待接入 ownership 恶化/棋块关联等证据后再升级。
    """
    out = []
    for cluster in clusters or []:
        root = cluster["root"]
        largest = cluster["largest_loss"]
        if root["move_no"] == largest["move_no"]:
            text = "第%d手问题（损失 %.1f 目）" % (
                root["move_no"], root["score_loss"])
        else:
            text = "第%d手（可能的根源）→ 第%d手爆发（最大损失 %.1f 目，链上共 %d 手）" % (
                root["move_no"], largest["move_no"], largest["score_loss"],
                len(cluster["move_nos"]))
        out.append({
            "cluster_id": cluster["cluster_id"],
            "text": text,
            "learn_move": cluster["representative"]["move_no"],
            "total_loss": cluster["total_loss"],
        })
    return out
