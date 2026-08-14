"""test_game_library —— 本地棋谱库测试。"""
import os
import sys
import tempfile
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import game_library as gl
from movetree import MoveTree
from project_store import load_project


def check(name, cond, extra=""):
    print(("[CHECK] %-34s %s %s" % (name, "OK" if cond else "FAIL", extra)))
    if not cond:
        raise AssertionError(name)


def sample_tree():
    t = MoveTree(19)
    t.play(3, 3)
    t.play(15, 15)
    t.root.analysis = {"rootInfo": {"scoreLead": 0.0}, "moveInfos": [{"move": "D16", "order": 0}]}
    return t


def test_library_add_and_update():
    tmp = tempfile.mkdtemp()
    orig = (gl.LIBRARY_DIR, gl.INBOX_DIR, gl.SGF_DIR, gl.PROJECT_DIR,
            gl.INDEX_PATH, gl.PROFILE_CACHE_PATH)
    try:
        gl.LIBRARY_DIR = tmp
        gl.INBOX_DIR = os.path.join(tmp, "inbox")
        gl.SGF_DIR = os.path.join(tmp, "sgf")
        gl.PROJECT_DIR = os.path.join(tmp, "projects")
        gl.INDEX_PATH = os.path.join(tmp, "index.json")
        gl.PROFILE_CACHE_PATH = os.path.join(tmp, "profile_cache.json")

        sgf = "(;GM[1]FF[4]SZ[19];B[dd];W[pp])"
        src = os.path.join(tmp, "game.sgf")
        with open(src, "w", encoding="utf-8") as f:
            f.write(sgf)
        t = sample_tree()
        rec = gl.add_sgf_to_library(src, sgf, t, rules="chinese", komi=7.5)
        check("记录 id", bool(rec["id"]), str(rec))
        check("SGF 文件存在", os.path.exists(rec["sgfPath"]), rec["sgfPath"])
        check("项目文件存在", os.path.exists(rec["projectPath"]), rec["projectPath"])
        check("分析进度字段", rec["analyzed"] == 1 and rec["totalNodes"] == 3, str(rec))
        check("索引 1 条", len(gl.list_records()) == 1, str(gl.list_records()))
        check("搜索命中名称", len(gl.search_records("game")) == 1)
        check("搜索不命中", len(gl.search_records("missing")) == 0)

        t2, _data = load_project(rec["projectPath"])
        check("项目可打开", t2.current.moves_list() == [["B", "D16"], ["W", "Q4"]], str(t2.current.moves_list()))
        check("analysis 缓存保存", t2.root.analysis["moveInfos"][0]["move"] == "D16")

        rec2 = gl.add_sgf_to_library(src, sgf, t, rules="chinese", komi=7.5)
        check("重复导入复用 id", rec2["id"] == rec["id"])
        check("重复导入不增记录", len(gl.list_records()) == 1, str(gl.list_records()))

        t.play(16, 15)
        updated = gl.update_project_snapshot(rec["id"], t, rules="chinese", komi=7.5)
        check("更新项目记录", updated is not None)
        check("更新后分析进度仍有字段", "analyzed" in updated and "totalNodes" in updated, str(updated))
        t3, _data = load_project(rec["projectPath"])
        check("更新后手数保存", t3.current.depth == 3, str(t3.current.depth))
        touched = gl.touch_record(rec["id"])
        check("最近打开时间写入", bool(touched and touched.get("lastOpenedAt")), str(touched))
        gl.update_game_profile_summary(rec["id"], {
            "version": 1, "game_id": rec["id"], "evaluated_moves": 2,
            "score_loss_sum": 3.0, "avg_score_loss": 1.5,
            "stage_stats": {}, "color_stats": {},
            "top_problem_moves": [{
                "move_no": 1, "color": "B", "played_move": "D16",
                "best_move": "Q16", "quality_key": "blunder",
                "score_loss": 6.0, "problem_tags": ["opening_direction"],
            }],
        })
        gl.update_profile_side(rec["id"], "B")
        profiled = gl.get_recent_profile_summaries(10)
        check("画像摘要写入索引", len(profiled) == 1)
        check("画像执棋方独立保存", profiled[0]["profileSide"] == "B")
        from mistake_book import list_items as list_mistakes
        mistake_path = os.path.join(tmp, "mistake_book.json")
        check("画像问题手同步错题本",
              len(list_mistakes(mistake_path, today="2026-06-30")) == 1)
        check("索引原子写无残留 tmp", not os.path.exists(gl.INDEX_PATH + ".tmp"))
        package = {
            "version": 1,
            "recordId": rec["id"],
            "taskId": "stage-1-20",
            "status": "ready",
            "preparedRounds": 10,
            "entries": {"position": {"rootInfo": {}, "moveInfos": []}},
            "updatedAt": "2026-06-28 12:00:00",
            "signature": {"model": "test.bin.gz", "visits": 80},
        }
        cached_rec = gl.save_training_cache(rec["id"], package)
        check("训练应手缓存写入", gl.load_training_cache(rec["id"])["taskId"] == "stage-1-20")
        check("训练缓存状态进入索引", cached_rec["trainingCache"]["entries"] == 1, str(cached_rec))
        cache_path = gl.training_cache_path(rec["id"])
        check("训练缓存文件存在", os.path.exists(cache_path), cache_path)
        check("删除记录成功", gl.delete_record(rec["id"]) is True)
        check("删除后索引为空", len(gl.list_records()) == 0, str(gl.list_records()))
        check("删除后项目文件移除", not os.path.exists(rec["projectPath"]))
        check("删除后训练缓存移除", not os.path.exists(cache_path))
        check("删除后错题一并移除",
              len(list_mistakes(mistake_path, today="2026-06-30")) == 0)

        pasted = "(;GM[1]FF[4]SZ[19];B[pd];W[dp])"
        paste_rec, created = gl.import_sgf_text(
            pasted, rules="chinese", komi=7.5, name="我的粘贴棋谱.sgf")
        paste_dup, created_again = gl.import_sgf_text(
            pasted, rules="chinese", komi=7.5, name="重复名称.sgf")
        check("粘贴 SGF 入库", created and paste_rec.get("sourceKind") == "paste")
        check("粘贴棋谱保留可读名称", paste_rec.get("name") == "我的粘贴棋谱.sgf")
        check("粘贴 SGF 内容去重", not created_again and paste_dup.get("id") == paste_rec.get("id"))
        gl.delete_record(paste_rec.get("id"))
    finally:
        (gl.LIBRARY_DIR, gl.INBOX_DIR, gl.SGF_DIR, gl.PROJECT_DIR,
         gl.INDEX_PATH, gl.PROFILE_CACHE_PATH) = orig
        shutil.rmtree(tmp)


def test_scan_inbox_dedup_and_preserve_cache():
    tmp = tempfile.mkdtemp()
    orig = (gl.LIBRARY_DIR, gl.INBOX_DIR, gl.SGF_DIR, gl.PROJECT_DIR,
            gl.INDEX_PATH, gl.PROFILE_CACHE_PATH)
    try:
        gl.LIBRARY_DIR = tmp
        gl.INBOX_DIR = os.path.join(tmp, "inbox")
        gl.SGF_DIR = os.path.join(tmp, "sgf")
        gl.PROJECT_DIR = os.path.join(tmp, "projects")
        gl.INDEX_PATH = os.path.join(tmp, "index.json")
        gl.PROFILE_CACHE_PATH = os.path.join(tmp, "profile_cache.json")
        os.makedirs(gl.INBOX_DIR)

        sgf = "(;GM[1]FF[4]SZ[19];B[dd];W[pp])"
        path = os.path.join(gl.INBOX_DIR, "auto.sgf")
        with open(path, "w", encoding="utf-8") as f:
            f.write(sgf)

        result = gl.scan_inbox(rules="chinese", komi=7.5)
        check("自动扫描入库 1 盘", len(result["imported"]) == 1, str(result))
        rec = result["imported"][0]
        check("来源标记 inbox", rec.get("sourceKind") == "inbox", str(rec))
        t1, _data = load_project(rec["projectPath"])
        check("自动项目可打开", t1.current.moves_list() == [["B", "D16"], ["W", "Q4"]])

        # 模拟这盘棋已经分析过并写回缓存；重复扫描不能覆盖项目快照。
        t1.root.analysis = {"rootInfo": {"scoreLead": 9.0}, "moveInfos": [{"move": "Q16", "order": 0}]}
        gl.update_project_snapshot(rec["id"], t1, rules="chinese", komi=7.5)
        result2 = gl.scan_inbox(rules="chinese", komi=7.5)
        check("重复扫描不新增", len(result2["imported"]) == 0 and len(result2["duplicates"]) == 1, str(result2))
        t2, _data = load_project(rec["projectPath"])
        check("重复扫描保留分析缓存", t2.root.analysis["rootInfo"]["scoreLead"] == 9.0)

        bad = os.path.join(gl.INBOX_DIR, "bad.sgf")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("not an sgf")
        result3 = gl.scan_inbox(rules="chinese", komi=7.5)
        check("坏文件进入失败列表", len(result3["failed"]) == 1, str(result3))
    finally:
        (gl.LIBRARY_DIR, gl.INBOX_DIR, gl.SGF_DIR, gl.PROJECT_DIR,
         gl.INDEX_PATH, gl.PROFILE_CACHE_PATH) = orig
        shutil.rmtree(tmp)


if __name__ == "__main__":
    print("=" * 60)
    print(" 本地棋谱库测试")
    print("=" * 60)
    test_library_add_and_update(); print()
    test_scan_inbox_dedup_and_preserve_cache(); print()
    print("test_game_library 全部通过 ✅")
