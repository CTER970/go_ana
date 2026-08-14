"""project_store —— 保存/打开带分析缓存的复盘项目文件。

项目文件是 UTF-8 JSON，默认扩展名 ``.kga.json``。它保存整棵 MoveTree、
每个节点的 KataGo analysis 缓存、评论、终局点目结果和规则/贴目等元数据。
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, fields, MISSING
from datetime import datetime

from board import BLACK, WHITE, color_letter
from movetree import MoveTree, MoveNode, xy_to_point, point_to_xy
from score_estimator import ScoreResult

PROJECT_FORMAT = "katago-analyzer-project"
PROJECT_VERSION = 1


def _color_int(cl):
    return BLACK if str(cl).upper() == "B" else WHITE


def _color_letter_from_int(c):
    return color_letter(c)


def _node_move_to_json(node):
    if node.move is None:
        return None
    cl, coord = node.move
    point = "pass" if coord is None else xy_to_point(coord[0], coord[1], node.board.size)
    return [cl, point]


def _node_to_json(node):
    return {
        "move": _node_move_to_json(node),
        "comment": node.comment,
        "analysis": node.analysis,
        "children": [_node_to_json(ch) for ch in node.children],
    }


def _current_path(tree):
    path = []
    n = tree.current
    while n is not None and n.parent is not None:
        parent = n.parent
        path.append(parent.children.index(n))
        n = parent
    path.reverse()
    return path


def _score_to_json(score_result):
    return None if score_result is None else asdict(score_result)


def _score_from_json(data):
    """兼容读取点目结果。

    项目文件里的 ``scoreResult`` 是可重建的附加数据，不应该因为旧版缺少
    新增可选字段，或新版多出未知字段，就阻断整盘棋打开。
    """
    if not isinstance(data, dict):
        return None
    payload = {}
    try:
        for item in fields(ScoreResult):
            if item.name in data:
                payload[item.name] = data[item.name]
            elif item.default_factory is not MISSING:
                payload[item.name] = item.default_factory()
            elif item.default is not MISSING:
                payload[item.name] = item.default
            else:
                return None
        return ScoreResult(**payload)
    except Exception:
        return None


def tree_to_project(tree, rules="chinese", komi=7.5, meta=None):
    """把 MoveTree 序列化为项目 dict。"""
    meta = dict(meta or {})
    meta.setdefault("createdAt", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    return {
        "format": PROJECT_FORMAT,
        "version": PROJECT_VERSION,
        "meta": meta,
        "rules": rules,
        "komi": komi,
        "size": tree.size,
        "rootToMove": _color_letter_from_int(tree.root.board.to_move),
        "initialStones": tree.initial_stones_list(),
        "sgfResult": getattr(tree, "_sgf_re", None),
        "blackName": getattr(tree, "_sgf_pb", "黑方"),
        "whiteName": getattr(tree, "_sgf_pw", "白方"),
        "scoreResult": _score_to_json(getattr(tree, "score_result", None)),
        "deepComparisons": getattr(tree, "_deep_comparisons", {}),
        "reviewSummaryV2": getattr(tree, "_review_summary_v2", None),
        "profileSide": getattr(tree, "_profile_side", None),
        "currentPath": _current_path(tree),
        "root": _node_to_json(tree.root),
    }


def _apply_root_data(tree, data):
    root_data = data.get("root") or {}
    tree.root.comment = root_data.get("comment")
    tree.root.analysis = root_data.get("analysis")


def _build_child(parent, child_data, size):
    move = child_data.get("move")
    if not move or len(move) != 2:
        raise ValueError("项目节点缺少 move")
    cl, point = move[0], move[1]
    if str(point).lower() == "pass":
        nb = parent.board.pass_move()
        coord = None
    else:
        x, y = point_to_xy(point, size)
        nb = parent.board.try_play(x, y)
        coord = (x, y)
    node = MoveNode(nb, move=(cl, coord), parent=parent)
    node.comment = child_data.get("comment")
    node.analysis = child_data.get("analysis")
    parent.children.append(node)
    for grand in child_data.get("children") or []:
        _build_child(node, grand, size)
    return node


def project_to_tree(project):
    """从项目 dict 还原 MoveTree。"""
    if project.get("format") != PROJECT_FORMAT:
        raise ValueError("不是 KataGo 分析器项目文件")
    if int(project.get("version", 0)) > PROJECT_VERSION:
        raise ValueError("项目文件版本过新，当前程序无法打开")

    size = int(project.get("size", 19))
    tree = MoveTree(size)
    stones = []
    for cl, point in project.get("initialStones") or []:
        x, y = point_to_xy(point, size)
        stones.append((_color_int(cl), x, y))
    root_to_move = _color_int(project.get("rootToMove", "B"))
    if stones:
        tree.set_initial_stones(stones, to_move=root_to_move)
    else:
        tree.root.board.to_move = root_to_move

    _apply_root_data(tree, project)
    tree._sgf_re = project.get("sgfResult")
    tree._sgf_pb = project.get("blackName") or "黑方"
    tree._sgf_pw = project.get("whiteName") or "白方"
    tree.score_result = _score_from_json(project.get("scoreResult"))
    tree._deep_comparisons = dict(project.get("deepComparisons") or {})
    review_summary = project.get("reviewSummaryV2")
    # 摘要是可选加速数据：损坏或版本过新都不影响棋树本体打开。
    if isinstance(review_summary, dict):
        tree._review_summary_v2 = review_summary
        tree._analysis_signature = dict(
            review_summary.get("analysisSignature")
            or review_summary.get("analysis_signature")
            or {})
        tree._analysis_model = tree._analysis_signature.get("model")
        tree._review_summary_version_newer = int(
            review_summary.get("version", 1) or 1) > 1
    else:
        tree._review_summary_v2 = None
        tree._analysis_signature = {}
        tree._analysis_model = None
        tree._review_summary_version_newer = False
    profile_side = project.get("profileSide")
    tree._profile_side = (
        profile_side if profile_side in ("B", "W", "both", "unknown")
        else "unknown")

    tree.root.children = []
    for child in (project.get("root") or {}).get("children") or []:
        _build_child(tree.root, child, size)

    cur = tree.root
    for idx in project.get("currentPath") or []:
        if not (0 <= int(idx) < len(cur.children)):
            cur = tree.root
            break
        cur = cur.children[int(idx)]
    tree.current = cur
    return tree


def save_project(path, tree, rules="chinese", komi=7.5, meta=None):
    data = tree_to_project(tree, rules=rules, komi=komi, meta=meta)
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise
    return data


def load_project(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return project_to_tree(data), data
