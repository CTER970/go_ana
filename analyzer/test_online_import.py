"""test_online_import —— 在线导入测试（全离线：mock _http_get，不访问网络）。

覆盖：URL 解析、OGS 适配器解析、HTTP 错误翻译、下载入库去重、批量下载部分失败。
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import game_library as gl
import online_import as oi
from online_import import OnlineImportError, resolve_sgf_url


def check(name, cond, extra=""):
    print(("[CHECK] %-42s %s %s" % (name, "OK" if cond else "FAIL", extra)))
    if not cond:
        raise AssertionError(name)


SGF_A = "(;GM[1]FF[4]SZ[19]PB[alice]PW[tester];B[pd];W[dp];B[pp])"
SGF_B = "(;GM[1]FF[4]SZ[19]PB[tester]PW[bob];B[dd];W[qp])"

PLAYER_JSON = ('{"count":1,"results":[{"id":568679,"username":"tester",'
               '"ranking":22.03}]}')
GAMES_JSON = ('{"count":2,"results":['
              '{"id":111,"players":{"black":{"username":"alice"},"white":{"username":"tester"}},'
              '"width":19,"height":19,"ended":"2026-08-01T10:00:00Z","outcome":"Resignation",'
              '"black_lost":true,"white_lost":false,"annulled":false},'
              '{"id":222,"players":{"black":{"username":"tester"},"white":{"username":"bob"}},'
              '"width":9,"height":9,"ended":"2026-08-02T10:00:00Z","outcome":"3.5 points",'
              '"black_lost":false,"white_lost":true,"annulled":false}]}')

API = "https://online-go.com/api/v1"

ROUTES = {
    API + "/players/?username=tester": PLAYER_JSON,
    API + "/players/568679/games/": GAMES_JSON,
    API + "/games/111/sgf": SGF_A,
    API + "/games/222/sgf": SGF_B,
    "https://example.com/kifu/game.sgf": SGF_A,
}


def mock_http(routes=None, fail_ids=()):
    """按完整 URL 前缀路由的 _http_get 替身；fail_ids 命中即抛错。"""
    routes = routes if routes is not None else ROUTES

    def fake(url, timeout=oi.DEFAULT_TIMEOUT):
        for gid in fail_ids:
            if "/games/%s/sgf" % gid in url:
                raise OnlineImportError("资源不存在（404）：%s" % url)
        for prefix, body in routes.items():
            if url == prefix or url.startswith(prefix):
                return body
        raise OnlineImportError("mock: 无路由 %s" % url)
    return fake


class LibrarySandbox:
    """把 game_library 重定向到临时目录，退出时还原。"""

    def __enter__(self):
        self.tmp = tempfile.mkdtemp()
        self.orig = (gl.LIBRARY_DIR, gl.INBOX_DIR, gl.SGF_DIR,
                     gl.PROJECT_DIR, gl.INDEX_PATH, gl.PROFILE_CACHE_PATH)
        gl.LIBRARY_DIR = self.tmp
        gl.INBOX_DIR = os.path.join(self.tmp, "inbox")
        gl.SGF_DIR = os.path.join(self.tmp, "sgf")
        gl.PROJECT_DIR = os.path.join(self.tmp, "projects")
        gl.INDEX_PATH = os.path.join(self.tmp, "index.json")
        gl.PROFILE_CACHE_PATH = os.path.join(self.tmp, "profile_cache.json")
        return self

    def __exit__(self, *exc):
        (gl.LIBRARY_DIR, gl.INBOX_DIR, gl.SGF_DIR, gl.PROJECT_DIR,
         gl.INDEX_PATH, gl.PROFILE_CACHE_PATH) = self.orig
        shutil.rmtree(self.tmp, ignore_errors=True)


def test_resolve_sgf_url():
    url, label = resolve_sgf_url("https://online-go.com/game/89887270")
    check("OGS 对局页转 API 地址", url == API + "/games/89887270/sgf", url)
    url2, _ = resolve_sgf_url("https://online-go.com/game/12345/all-moves")
    check("OGS 对局页带后缀仍可解析", url2 == API + "/games/12345/sgf", url2)
    url3, _ = resolve_sgf_url("https://online-go.com/api/v1/games/77/sgf")
    check("OGS API 直链原样接受", url3 == url3)
    url4, label4 = resolve_sgf_url("http://weiqi.example.com/2024/final.sgf")
    check(".sgf 直链原样接受", url4.endswith("final.sgf") and label4 == "final.sgf")

    for bad in ("", "not-a-url", "ftp://x/y.sgf"):
        try:
            resolve_sgf_url(bad)
            check("非法输入应报错：%r" % bad, False)
        except OnlineImportError:
            check("非法输入应报错：%r" % bad, True)
    try:
        resolve_sgf_url("https://www.19x19.com/mygames")
        check("无接口平台给出指引", False)
    except OnlineImportError as e:
        check("无接口平台给出指引", "粘贴" in str(e) or "导出" in str(e))


def test_rank_label():
    check("ranking 22 → 8k", oi._rank_label(22.03) == "8k", oi._rank_label(22.03))
    check("ranking 37 → 8d", oi._rank_label(37.2) == "8d", oi._rank_label(37.2))
    check("ranking 100 → 封顶 9d", oi._rank_label(100) == "9d")
    check("ranking 缺失 → 空串", oi._rank_label(None) == "")


def test_ogs_list_games():
    with LibrarySandbox():
        orig = oi._http_get
        oi._http_get = mock_http()
        try:
            player, games = oi.ogs_list_games("tester", limit=10)
            check("玩家解析", player["id"] == 568679 and player["rank"] == "8k",
                  str(player))
            check("对局数", len(games) == 2, str(len(games)))
            g1, g2 = games
            check("对局字段-黑白名", g1["black"] == "alice" and g1["white"] == "tester")
            check("对局字段-结果", g1["result"] == "白胜" and g2["result"] == "黑胜")
            check("对局字段-棋盘", g1["size"] == "19路" and g2["size"] == "9路",
                  "%s / %s" % (g1["size"], g2["size"]))
            check("对局字段-日期截断", g1["ended"] == "2026-08-01", g1["ended"])
        finally:
            oi._http_get = orig


def test_ogs_empty_player_and_games():
    with LibrarySandbox():
        orig = oi._http_get
        oi._http_get = mock_http(routes={
            API + "/players/?username=ghost": '{"count":0,"results":[]}'})
        try:
            try:
                oi.ogs_list_games("ghost")
                check("查无此玩家应报错", False)
            except OnlineImportError as e:
                check("查无此玩家应报错", "未找到玩家" in str(e))
        finally:
            oi._http_get = orig
        oi._http_get = mock_http(routes={
            API + "/players/?username=x": PLAYER_JSON,
            API + "/players/568679/games/": '{"count":0,"results":[]}'})
        try:
            try:
                oi.ogs_list_games("x")
                check("没有已结束对局应报错", False)
            except OnlineImportError as e:
                check("没有已结束对局应报错", "已结束" in str(e))
        finally:
            oi._http_get = orig


def test_import_from_url_and_dedup():
    with LibrarySandbox():
        orig = oi._http_get
        oi._http_get = mock_http()
        try:
            rec, created = oi.import_from_url("https://example.com/kifu/game.sgf")
            check("URL 导入成功", created and rec.get("name") == "game.sgf", str(rec))
            check("来源标记 online-url", rec.get("sourceKind") == "online-url")
            rec2, created2 = oi.import_from_url("https://example.com/kifu/game.sgf")
            check("重复下载不重复入库", not created2 and rec2["id"] == rec["id"])
        finally:
            oi._http_get = orig


def test_download_rejects_non_sgf():
    with LibrarySandbox():
        orig = oi._http_get
        oi._http_get = mock_http(routes={
            "https://example.com/fake.sgf": "<html>not sgf</html>"})
        try:
            try:
                oi.download_from_url("https://example.com/fake.sgf")
                check("非 SGF 内容应报错", False)
            except OnlineImportError as e:
                check("非 SGF 内容应报错", "不是 SGF" in str(e))
        finally:
            oi._http_get = orig


def test_import_ogs_games_partial_failure():
    with LibrarySandbox():
        orig = oi._http_get
        oi._http_get = mock_http(fail_ids=("222",))
        progress_log = []
        try:
            player, games = oi.ogs_list_games("tester")
            result = oi.import_ogs_games(
                games, progress=lambda d, t, n: progress_log.append((d, t)))
            check("成功 1 盘", len(result["imported"]) == 1,
                  str(result["imported"]))
            check("失败 1 盘（404）", len(result["failed"]) == 1
                  and "404" in result["failed"][0]["error"])
            rec = result["imported"][0]
            check("OGS 名称带 vs 与日期", "vs" in rec.get("name", ""), rec["name"])
            check("来源标记 online-ogs", rec.get("sourceKind") == "online-ogs")
            check("进度回调逐盘触发", progress_log == [(1, 2), (2, 2)],
                  str(progress_log))
        finally:
            oi._http_get = orig


def test_http_error_translation():
    """真 _http_get 的错误翻译：HTTPError/URLError → 友好文案（不发真实请求）。"""
    import urllib.error
    import urllib.request

    orig_urlopen = urllib.request.urlopen
    orig_interval = oi.MIN_REQUEST_INTERVAL
    oi.MIN_REQUEST_INTERVAL = 0
    oi._last_request_at[0] = 0.0
    try:
        urllib.request.urlopen = lambda *a, **k: (_ for _ in ()).throw(
            urllib.error.HTTPError("u", 404, "Not Found", {}, None))
        try:
            oi._http_get("https://online-go.com/x")
            check("HTTP 404 翻译", False)
        except OnlineImportError as e:
            check("HTTP 404 翻译", "404" in str(e))

        urllib.request.urlopen = lambda *a, **k: (_ for _ in ()).throw(
            urllib.error.URLError("conn refused"))
        try:
            oi._http_get("https://online-go.com/x")
            check("连接失败翻译", False)
        except OnlineImportError as e:
            check("连接失败翻译", "无法连接" in str(e))
    finally:
        urllib.request.urlopen = orig_urlopen
        oi.MIN_REQUEST_INTERVAL = orig_interval


if __name__ == "__main__":
    print("=" * 60)
    print(" 在线导入测试（离线 mock，不访问网络）")
    print("=" * 60)
    test_resolve_sgf_url(); print()
    test_rank_label(); print()
    test_ogs_list_games(); print()
    test_ogs_empty_player_and_games(); print()
    test_import_from_url_and_dedup(); print()
    test_download_rejects_non_sgf(); print()
    test_import_ogs_games_partial_failure(); print()
    test_http_error_translation(); print()
    print("test_online_import 全部通过 ✅")
