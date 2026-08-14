"""SGF 导入/导出 —— MoveTree 与 SGF(Smart Game Format) 文本互转（v3：支持变化图 + 注释）。

SGF 坐标（与 GTP 不同，且【不】跳过字母 i）：
  列与行均用小写字母 a..(a+size-1)；首字母=列(左→右)，次字母=行(上→下)。
  本项目内部 grid[y][x]、y=0 为顶部，故 SGF 坐标 = chr(a+x)+chr(a+y)；pass 记作 []。

v3 支持：
  * 变化图（variations）导入/导出 —— 完整树，非仅主线
  * C[] 注释导入/导出；导出时把 AI 分析摘要追加进 C[]
  * 基本转义：\\、]、[
暂不要求：复杂标记(TR/MA/CR...)完整保留、多 game tree、非 19 路。
"""
from __future__ import annotations

import re

from board import color_letter, BLACK, WHITE
from movetree import MoveTree, MoveNode

GM, FF, CA = 1, 4, "UTF-8"


# ===================== 坐标 / 转义 =====================
def _sgf_coord(x: int, y: int) -> str:
    """(x,y) -> SGF 两字母，如 (15,3)->'pd'。"""
    return chr(ord("a") + x) + chr(ord("a") + y)


def _from_sgf_coord(s: str):
    """'pd' -> (15,3)；空串/非法/越界返回 None（即 pass）。"""
    if len(s) != 2:
        return None
    cx, cy = ord(s[0]) - ord("a"), ord(s[1]) - ord("a")
    if 0 <= cx < 36 and 0 <= cy < 36:
        return cx, cy
    return None


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("]", "\\]").replace("[", "\\[")


# ===================== AI 注释 =====================
def _ai_comment(analysis) -> str:
    """把 KataGo 分析响应转成多行中文摘要（写入 SGF C[]）。"""
    if not analysis:
        return ""
    root = analysis.get("rootInfo", {}) or {}
    mis = sorted(analysis.get("moveInfos", []) or [], key=lambda m: m.get("order", 99))
    lines = []
    wr = root.get("winrate")
    if wr is not None:
        lines.append("AI分析 黑胜率:%.1f%% 目差:%+.1f" % (wr * 100, root.get("scoreLead", 0) or 0))
    if mis:
        top = mis[0]
        lines.append("首选:%s PV:%s" % (top.get("move", "?"), " ".join(top.get("pv", [])[:6])))
        cands = []
        for i, m in enumerate(mis[:5]):
            letter = "ABCDEFGHJK"[i] if i < 9 else str(i)
            cands.append("%s %s %.1f%% %+.1f v%d" % (
                letter, m.get("move", "?"), (m.get("winrate", 0) or 0) * 100,
                m.get("scoreLead", 0) or 0, m.get("visits", 0) or 0))
        lines.append("候选: " + " | ".join(cands))
    return "\n".join(lines)


def _node_comment_text(node: MoveNode) -> str:
    """节点导出注释 = 用户注释 + AI 摘要（若有）。"""
    parts = []
    if getattr(node, "comment", None):
        parts.append(node.comment)
    ai = _ai_comment(getattr(node, "analysis", None))
    if ai:
        parts.append(ai)
    if not parts:
        return ""
    return "\n---\n".join(parts)


def _winner_text(result) -> str:
    """ScoreResult -> 人类可读胜负，如 "黑胜 3.5 目" / "白胜 7.5 目" / "和棋"。"""
    if result is None:
        return ""
    if result.winner == "Draw":
        return "和棋"
    side = "黑" if result.winner == "B" else "白"
    return "%s胜 %.1f 目" % (side, abs(result.margin))


def _scoring_comment(result) -> str:
    """把 ScoreResult 转成多行中文点目摘要（导出时追加进【根节点】C[]）。

    规则/黑方/白方明细/结果/死子清单。不修改 result。
    """
    if result is None:
        return ""
    lines = ["终局点目：", "规则：中国规则 / 面积数法"]
    lines.append("黑方：活子 %d + 地 %d = %g" % (
        result.black_stones, result.black_territory, result.black_area))
    lines.append("白方：活子 %d + 地 %d + 贴目 %g = %g" % (
        result.white_stones, result.white_territory, result.komi, result.white_area))
    lines.append("结果：%s（RE[]=%s）" % (_winner_text(result), result.result_text))
    if result.dead_black or result.dead_white:
        lines.append("死子：")
        if result.dead_black:
            lines.append("  黑：%s" % ", ".join(result.dead_black))
        if result.dead_white:
            lines.append("  白：%s" % ", ".join(result.dead_white))
    else:
        lines.append("死子：无")
    return "\n".join(lines)


# ===================== 导出（完整树 + 变化图 + 注释 + 点目结果）=====================
def export_sgf(tree: MoveTree, black_name: str = "黑方", white_name: str = "白方",
               komi: float = 7.5, rule: str = "Chinese", date: str = "",
               score_result=None) -> str:
    """导出【整棵树】为 SGF：主线 inline，其余子节点为变着 (...)；分析过的节点附 C[]。

    点目结果（score_result 为 ScoreResult 或 tree.score_result）：
      * RE[]：写入 SGF 根属性（如 RE[B+3.5] / RE[W+7.5] / RE[0]）；覆盖原 RE。
      * 点目摘要：追加到【根节点】C[]（不覆盖既有根注释）。
      * 未确认结果但导入时捕获过原 RE[]，则保留原 RE[]（tree._sgf_re）。
    """
    size = tree.size
    if score_result is None:
        score_result = getattr(tree, "score_result", None)
    header = ("(;GM[%d]FF[%d]CA[%s]AP[KataGo分析器:1.0]SZ[%d]KM[%s]PB[%s]PW[%s]RU[%s]DT[%s]"
              % (GM, FF, CA, size, komi, _escape(black_name), _escape(white_name),
                 _escape(rule), _escape(date)))
    # RE[]：确认结果优先；否则保留导入时的原 RE[]
    re_text = None
    if score_result is not None:
        re_text = score_result.result_text
    elif getattr(tree, "_sgf_re", None):
        re_text = tree._sgf_re
    if re_text:
        header += "RE[%s]" % _escape(re_text)
    # 让子 setup：HA / AB / AW
    black_xy = [xy for cl, xy in tree.initial_stones if cl == "B"]
    white_xy = [xy for cl, xy in tree.initial_stones if cl == "W"]
    setup = ""
    if black_xy:
        setup += "HA[%d]" % len(black_xy)
        setup += "AB" + "".join("[%s]" % _sgf_coord(x, y) for x, y in black_xy)
    if white_xy:
        setup += "AW" + "".join("[%s]" % _sgf_coord(x, y) for x, y in white_xy)
    # 根节点 C[]：用户根注释 + 点目摘要（确认后追加，不覆盖原注释）
    root_cmt_parts = []
    if getattr(tree.root, "comment", None):
        root_cmt_parts.append(tree.root.comment)
    if score_result is not None:
        sc = _scoring_comment(score_result)
        if sc:
            root_cmt_parts.append(sc)
    root_cmt = ""
    if root_cmt_parts:
        root_cmt = "C[%s]" % _escape("\n---\n".join(root_cmt_parts))
    return header + setup + root_cmt + _ser_children(tree.root) + ")"


def _ser_children(node: MoveNode) -> str:
    """序列化 node 的后代。

    单子 inline（继续主线序列）；多子则【全部】作为变着 ``(;...)``、主线居首——
    这样回导时所有子节点都挂到正确父节点、且 children 顺序与原树一致。
    """
    kids = node.children
    if not kids:
        return ""
    if len(kids) == 1:
        return _emit_node(kids[0]) + _ser_children(kids[0])
    s = ""
    for ch in kids:
        s += "(" + _emit_node(ch) + _ser_children(ch) + ")"
    return s


def _emit_node(node: MoveNode) -> str:
    cl, coord = node.move
    if coord is None:
        s = ";%s[]" % cl                                       # pass
    else:
        s = ";%s[%s]" % (cl, _sgf_coord(coord[0], coord[1]))
    cmt = _node_comment_text(node)
    if cmt:
        s += "C[%s]" % _escape(cmt)
    return s


# ===================== 导入：递归下降解析器 =====================
class _RawNode:
    __slots__ = ("props", "vars")

    def __init__(self):
        self.props = {}        # PropIdent(str) -> list[str] 值
        self.vars = []         # list[list[_RawNode]]：子变化图的节点链


def _parse_value(s: str, i: int):
    """i 指向 '[' 之后。读到未转义的 ']'。返回 (value, index_after_bracket)。"""
    out = []
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == "\\" and i + 1 < n:
            out.append(s[i + 1])      # 转义取下一字符（\] -> ]，\\ -> \）
            i += 2
        elif ch == "]":
            return "".join(out), i + 1
        else:
            out.append(ch)
            i += 1
    return "".join(out), i


def _parse_property(s: str, i: int):
    """从 s[i] 读一个属性：PropIdent + 一个或多个 [value]。返回 (ident, values, next_i)。"""
    n = len(s)
    start = i
    while i < n and "A" <= s[i] <= "Z":
        i += 1
    ident = s[start:i] or "X"
    vals = []
    while i < n:
        j = i
        while j < n and s[j].isspace():
            j += 1
        if j < n and s[j] == "[":
            val, j = _parse_value(s, j + 1)
            vals.append(val)
            i = j
        else:
            break
    return ident, vals, i


def _parse_variation(s: str, i: int):
    """i 指向 '(' 之后。解析到匹配的 ')'。返回 (nodes_list, index_after_paren)。"""
    nodes = []
    cur = None
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == ";":
            cur = _RawNode()
            nodes.append(cur)
            i += 1
        elif ch == "(":
            sub, i = _parse_variation(s, i + 1)
            if cur is None:
                cur = _RawNode()
                nodes.append(cur)
            cur.vars.append(sub)
        elif ch == ")":
            return nodes, i + 1
        elif ch.isspace():
            i += 1
        else:
            ident, vals, j = _parse_property(s, i)
            if j == i:
                i += 1            # 无法识别的字符，跳过避免死循环（容错畸形 SGF）
                continue
            i = j
            if cur is None:
                cur = _RawNode()
                nodes.append(cur)
            cur.props.setdefault(ident, []).extend(vals)
    return nodes, i


def parse_sgf_tree(text: str):
    """返回首个 game tree 的顶层节点链（list[_RawNode]）；无 '(' 返回 []。"""
    i = text.find("(")
    if i < 0:
        return []
    nodes, _ = _parse_variation(text, i + 1)
    return nodes


def _move_kind(val: str, size: int):
    """SGF 着手值 -> (kind, coord)。kind: 'pass'(空值) / 'play'(合法) / 'skip'(越界/非法)。"""
    if val == "":
        return ("pass", None)
    pos = _from_sgf_coord(val)
    if pos is None:
        return ("skip", None)
    x, y = pos
    if x >= size or y >= size:
        return ("skip", None)
    return ("play", (x, y))


def _raw_move(rn: _RawNode, size: int):
    """raw 节点 -> (color_letter, kind, coord)，非着子节点返回 None。"""
    for key, cl in (("B", "B"), ("W", "W")):
        if key in rn.props:
            kind, coord = _move_kind(rn.props[key][0], size)
            return (cl, kind, coord)
    return None


def _build(raw_nodes, tree: MoveTree, parent: MoveNode, skipped):
    """把一条变体的 raw 节点链挂到 parent 下；返回末尾 MoveNode。skipped 为 [int] 可变计数。"""
    cur = parent
    for rn in raw_nodes:
        comment = rn.props.get("C", [None])[0] if rn.props.get("C") else None
        mv = _raw_move(rn, tree.size)
        if mv is None:
            # 根/纯属性节点：不新建棋盘节点；注释挂到 cur（若非根）
            if comment and cur is not tree.root and not cur.comment:
                cur.comment = comment
            for sub in rn.vars:
                _build(sub, tree, cur, skipped)
            continue
        cl, kind, coord = mv
        if kind == "skip":
            skipped[0] += 1           # 越界/非法坐标，忽略此节点
            continue
        node_coord = coord if kind == "play" else None    # pass -> None
        child = None
        for ch in cur.children:
            if ch.move and ch.move[0] == cl and ch.move[1] == node_coord:
                child = ch
                break
        if child is None:
            expected = color_letter(cur.board.to_move)
            if kind == "pass":
                nb = cur.board.pass_move()
            elif cl != expected:
                skipped[0] += 1       # 颜色不符（让子 setup 等），跳过
                continue
            else:
                try:
                    nb = cur.board.try_play(coord[0], coord[1])
                except Exception:
                    skipped[0] += 1
                    continue
            child = MoveNode(nb, move=(cl, node_coord), parent=cur)
            cur.children.append(child)
        if comment and not child.comment:
            child.comment = comment
        for sub in rn.vars:
            _build(sub, tree, child, skipped)
        cur = child
    return cur


def _mainline_end(node: MoveNode) -> MoveNode:
    while node.children:
        node = node.children[0]
    return node


def _extract_setup(rn: _RawNode, size: int):
    """从根 raw 节点取 AB/AW setup 子 -> [(color_int, x, y), ...]。"""
    stones = []
    for key, color in (("AB", BLACK), ("AW", WHITE)):
        for val in rn.props.get(key, []):
            pos = _from_sgf_coord(val)
            if pos and pos[0] < size and pos[1] < size:
                stones.append((color, pos[0], pos[1]))
    return stones


def import_sgf(text: str, size: int = 19) -> MoveTree:
    """解析 SGF（含变化图 + 注释 + 让子 setup）为 MoveTree；current 停在主线末尾。

    跳过数挂在 ``tree._sgf_skipped``。变化图作为子节点挂载；注释写入 MoveNode.comment；
    HA/AB/AW 让子棋写入 tree.initial_stones 并置于根盘面。
    """
    m = re.search(r"SZ\s*\[\s*(\d+)\s*\]", text)
    if m:
        size = int(m.group(1))
    tree = MoveTree(size)
    skipped = [0]
    raw_nodes = parse_sgf_tree(text)
    if raw_nodes:
        # 捕获根属性 RE[]（终局结果），导出时若用户未重新确认则保留
        re_vals = raw_nodes[0].props.get("RE")
        tree._sgf_re = re_vals[0] if re_vals else None
        tree._sgf_pb = (raw_nodes[0].props.get("PB") or ["黑方"])[0] or "黑方"
        tree._sgf_pw = (raw_nodes[0].props.get("PW") or ["白方"])[0] or "白方"
        # 捕获根 C[] 注释（之前根注释不回导；点目摘要可能写在这里）
        c_vals = raw_nodes[0].props.get("C")
        if c_vals and c_vals[0]:
            tree.root.comment = c_vals[0]
        stones = _extract_setup(raw_nodes[0], size)
        if stones:
            tree.set_initial_stones(stones)
    else:
        tree._sgf_re = None
        tree._sgf_pb = "黑方"
        tree._sgf_pw = "白方"
    _build(raw_nodes, tree, tree.root, skipped)
    tree._sgf_skipped = skipped[0]
    tree.current = _mainline_end(tree.root)
    return tree
