"""online_import —— 在线棋谱导入（URL 直链下载 + OGS 适配器）。

设计原则：
- 纯标准库（urllib），不引入 pip 依赖，保持项目可裸 ``python app.py`` 启动。
- 所有网络访问收敛到 ``_http_get`` 单一出口：限速、超时、大小上限、统一报错，
  测试用 monkeypatch 替换它即可全离线跑通（见 test_online_import.py）。
- 下载只做"拿 SGF 文本"，入库一律交给 game_library.import_sgf_text：
  按内容 sha1 去重、不覆盖已有分析缓存。
- 定位是个人复盘工具：只按需拉取公开棋谱，请求间隔不低于
  MIN_REQUEST_INTERVAL，不做并发批量抓取。
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from game_library import import_sgf_text

OGS_BASE = "https://online-go.com"
OGS_API = OGS_BASE + "/api/v1"

DEFAULT_TIMEOUT = 20.0        # 单请求超时（秒）
MIN_REQUEST_INTERVAL = 0.6    # 相邻请求最小间隔（秒），对公共 API 保持礼貌
MAX_RESPONSE_BYTES = 8 * 1024 * 1024   # 8MB 上限，异常响应直接拒绝
USER_AGENT = "katago-local-analyzer/1.0 (personal SGF import)"

# OGS 对局页 https://online-go.com/game/12345（可带 / 或锚点后缀）
_OGS_GAME_PAGE_RE = re.compile(
    r"^https?://(?:www\.)?online-go\.com/game/(\d+)", re.IGNORECASE)
# 直接给的 OGS API SGF 地址也接受
_OGS_API_SGF_RE = re.compile(
    r"^https?://(?:www\.)?online-go\.com/api/v1/games/(\d+)/sgf/?$",
    re.IGNORECASE)

_last_request_at = [0.0]   # 模块级可变状态，测试里可重置


class OnlineImportError(Exception):
    """在线导入失败，message 面向用户可直接展示。"""


# ===================== 网络层（唯一出口） =====================

def _throttle():
    """相邻请求间隔限速；被测试 monkeypatch 掉 _sleep 可加速。"""
    now = time.monotonic()
    wait = MIN_REQUEST_INTERVAL - (now - _last_request_at[0])
    if wait > 0:
        time.sleep(wait)
    _last_request_at[0] = time.monotonic()


def _http_get(url, timeout=DEFAULT_TIMEOUT):
    """GET 并返回响应文本；网络错误统一翻译成 OnlineImportError。"""
    _throttle()
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json, application/x-go-sgf, text/*;q=0.8",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise OnlineImportError("资源不存在（404）：%s" % url) from None
        raise OnlineImportError(
            "服务器返回错误 HTTP %d：%s" % (e.code, url)) from None
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        raise OnlineImportError(
            "无法连接 %s（%s）。请检查网络后重试。" % (url, reason)) from None
    except TimeoutError:
        raise OnlineImportError("请求超时（%.0f 秒）：%s" % (timeout, url)) from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise OnlineImportError("响应超过 8MB，已中止下载：%s" % url)
    return raw.decode("utf-8", "replace")


def _http_get_json(url, timeout=DEFAULT_TIMEOUT):
    text = _http_get(url, timeout=timeout)
    try:
        return json.loads(text)
    except ValueError:
        raise OnlineImportError("接口返回的不是有效 JSON：%s" % url) from None


# ===================== 通用 URL 导入 =====================

def resolve_sgf_url(url):
    """把用户输入的链接解析成可直接下载的 SGF 地址。

    支持：.sgf 直链、OGS 对局页链接、OGS API SGF 地址。
    其他页面（需要登录/动态渲染的棋谱站）明确拒绝并给出指引。
    返回 (sgf_url, source_label)。
    """
    url = (url or "").strip()
    if not re.match(r"^https?://", url, re.IGNORECASE):
        raise OnlineImportError("请输入以 http(s):// 开头的链接")
    m = _OGS_GAME_PAGE_RE.match(url)
    if m:
        game_id = m.group(1)
        return "%s/games/%s/sgf" % (OGS_API, game_id), "OGS 对局 #%s" % game_id
    if _OGS_API_SGF_RE.match(url):
        m2 = _OGS_API_SGF_RE.match(url)
        return url, "OGS 对局 #%s" % m2.group(1)
    path = urllib.parse.urlparse(url).path.lower()
    if path.endswith(".sgf"):
        return url, os.path.basename(path) or "在线棋谱"
    raise OnlineImportError(
        "暂不支持该页面（只有 .sgf 直链和 OGS 对局页可直接下载）。"
        "星阵/涨棋网请在官网导出 SGF 后用「粘贴 SGF」或收件箱导入。")


def _ensure_sgf(text, label):
    if "(;" not in text:
        raise OnlineImportError("下载的内容不是 SGF 棋谱：%s" % label)
    return text


def guess_name_from_url(url):
    """从 URL 推断入库显示名；推不出就按来源生成。"""
    path = urllib.parse.urlparse((url or "").strip()).path
    base = os.path.basename(path)
    if base.lower().endswith(".sgf") and base != ".sgf":
        return base
    return "在线导入-%s.sgf" % time.strftime("%Y%m%d-%H%M%S")


def download_from_url(url):
    """下载 URL 指向的棋谱，返回 (sgf_text, name)；不落库（入库在 UI 线程做）。"""
    sgf_url, _label = resolve_sgf_url(url)
    return _ensure_sgf(_http_get(sgf_url), url), guess_name_from_url(url)


def import_from_url(url, rules="chinese", komi=7.5):
    """下载 URL 指向的棋谱并入库（一步到位版，供测试/脚本用）。"""
    text, name = download_from_url(url)
    return import_sgf_text(
        text, rules=rules, komi=komi, name=name, source_kind="online-url")


# ===================== OGS 适配器 =====================

def _rank_label(ranking):
    """OGS numerical ranking → 段位文案（约 30-x kyu，x-29 dan）。"""
    try:
        r = float(ranking)
    except (TypeError, ValueError):
        return ""
    if r >= 30:
        return "%dd" % min(9, int(round(r - 29)))
    return "%dk" % max(1, int(round(30 - r)))


def ogs_find_player(username):
    """按用户名查 OGS 玩家，返回 {'id', 'username', 'rank'}；找不到报错。"""
    name = (username or "").strip()
    if not name:
        raise OnlineImportError("请输入 OGS 用户名")
    data = _http_get_json("%s/players/?username=%s" % (
        OGS_API, urllib.parse.quote(name)))
    results = data.get("results") or []
    if not results:
        raise OnlineImportError("OGS 上未找到玩家「%s」，请检查用户名" % name)
    exact = next(
        (p for p in results
         if str(p.get("username", "")).lower() == name.lower()), results[0])
    return {
        "id": exact.get("id"),
        "username": exact.get("username", name),
        "rank": _rank_label(exact.get("ranking")),
    }


def _ogs_result_text(g):
    if g.get("annulled"):
        return "已作废"
    if g.get("white_lost"):
        return "黑胜"
    if g.get("black_lost"):
        return "白胜"
    return g.get("outcome") or "—"


def ogs_list_games(username, limit=20):
    """列出玩家最近已结束的对局（新→旧），供界面勾选下载。"""
    limit = max(1, min(int(limit or 20), 50))
    player = ogs_find_player(username)
    data = _http_get_json(
        "%s/players/%s/games/?ended__isnull=false&ordering=-ended&page_size=%d"
        % (OGS_API, player["id"], limit))
    games = []
    for g in data.get("results") or []:
        players = g.get("players") or {}
        black = (players.get("black") or {}).get("username", "?")
        white = (players.get("white") or {}).get("username", "?")
        width = g.get("width") or 19
        height = g.get("height") or 19
        games.append({
            "id": g.get("id"),
            "black": black,
            "white": white,
            "result": _ogs_result_text(g),
            "size": "%dx%d" % (width, height) if width != height else "%d路" % width,
            "ended": (g.get("ended") or "")[:10],
            "name": "OGS %s vs %s (%s)" % (black, white, (g.get("ended") or "")[:10]),
        })
    if not games:
        raise OnlineImportError("玩家 %s 没有已结束的对局" % player["username"])
    return player, games


def ogs_game_sgf(game_id):
    """下载单盘 OGS 对局的 SGF 文本。"""
    url = "%s/games/%s/sgf" % (OGS_API, game_id)
    return _ensure_sgf(_http_get(url), "OGS 对局 #%s" % game_id)


def download_ogs_games(games, progress=None):
    """批量下载 OGS 对局 SGF 文本，不落库。

    games 为 ogs_list_games 返回的条目（含 id/name）。progress(done, total,
    name) 在每盘完成后回调（后台线程内执行，只做线程安全的事）。
    返回 {"items": [{"name", "text"}], "failed": [{"game", "error"}]}。
    """
    items, failed = [], []
    total = len(games)
    for idx, g in enumerate(games, 1):
        name = g.get("name") or ("OGS-%s" % g.get("id"))
        try:
            items.append({"name": name + ".sgf", "text": ogs_game_sgf(g.get("id"))})
        except Exception as e:
            failed.append({"game": name, "error": str(e)})
        if progress:
            try:
                progress(idx, total, name)
            except Exception:
                pass
    return {"items": items, "failed": failed}


def import_ogs_games(games, rules="chinese", komi=7.5, progress=None):
    """批量下载并入库（一步到位版，供测试/脚本用）。"""
    result = download_ogs_games(games, progress=progress)
    imported, duplicates = [], []
    for item in result["items"]:
        rec, created = import_sgf_text(
            item["text"], rules=rules, komi=komi,
            name=item["name"], source_kind="online-ogs")
        (imported if created else duplicates).append(rec)
    return {"imported": imported, "duplicates": duplicates,
            "failed": result["failed"]}
