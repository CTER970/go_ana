"""test_learning_store —— LearningStore JSON Repository 测试。"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from learning_event import (
    MASTERY_UNDERSTANDING, MASTERY_TRANSFERRED, RETRY_REPEATED,
    RETRY_CORRECTED, LearningEvent, event_id,
)
from learning_store import (
    get_due_reviews, get_event, get_events, get_events_by_category,
    get_events_by_game, remove_game, save_attempt, save_event, store_stats,
    sync_profile_summary,
)


def check(name, cond, extra=""):
    print("[CHECK] %-40s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        raise AssertionError(name)


def _problem(move_no, color="B", loss=6.0):
    return {"move_no": move_no, "color": color, "played_move": "R10",
            "best_move": "P9", "quality_key": "blunder", "score_loss": loss,
            "stage": "middle", "problem_tags": ["overplay"]}


def run():
    tmp = tempfile.mkdtemp(prefix="learning-store-")
    path = os.path.join(tmp, "learning_events.json")
    try:
        e1 = LearningEvent.from_problem("g1", _problem(82), game_name="第一局")
        e1.primary_category = "weak_groups"
        e1.learning_priority = 0.8
        e2 = LearningEvent.from_problem("g1", _problem(51, loss=4.0))
        e2.primary_category = "sente_tenuki"
        e2.learning_priority = 0.95
        e3 = LearningEvent.from_problem("g2", _problem(7, color="W"))
        e3.primary_category = "weak_groups"
        e3.learning_priority = 0.4

        save_event(e1, path)
        save_event(e2, path)
        save_event(e3, path)
        check("保存三条", len(get_events(path)) == 3)

        # 重新分析同手：客观字段更新、进度字段保留
        e1_new = LearningEvent.from_problem(
            "g1", _problem(82, loss=7.5), game_name="第一局")
        e1_new.primary_category = "attack_defense"
        e1_new.learning_priority = 0.7
        e1_new.user_retry_move = "Q11"      # 不应经此路径覆盖进度
        save_attempt(e1.id, "Q11", score_loss=0.7, assessment="acceptable",
                     ai_rank=2, retry_status=RETRY_CORRECTED, path=path)
        merged = save_event(e1_new, path)
        check("客观目损已更新", abs(merged.score_loss - 7.5) < 1e-9)
        check("作答历史保留", len(merged.attempts) == 1)
        check("重试结果保留", merged.retry_status == RETRY_CORRECTED
              and merged.retry_score_loss == 0.7)
        check("掌握状态保留", merged.mastery_state == MASTERY_UNDERSTANDING)

        # 查询
        g1 = get_events_by_game("g1", path)
        check("按局查询且按优先级排序",
              len(g1) == 2 and g1[0].move_no == 51)
        cats = get_events_by_category("weak_groups", path)
        check("按分类查询", len(cats) == 1 and cats[0].game_id == "g2")
        top = get_events(path, min_priority=0.7)
        check("优先级过滤", len(top) == 2)
        check("get_event 精确取回", get_event(e3.id, path).move_no == 7)
        check("未知 id 返回 None", get_event("nope", path) is None)

        # 复习到期
        save_attempt(e1.id, "R10", score_loss=6.0, assessment="bad",
                     retry_status=RETRY_REPEATED, path=path)
        # save_attempt 的到期日用真实今天（避免跨日跑测试时固定日期失配）
        due = get_due_reviews(path=path)
        check("重复错误当天到期", len(due) == 1 and due[0].id == e1.id)
        check("未来日期无到期", get_due_reviews(today="2020-01-01", path=path) == [])

        # 实战迁移后退出复习队列
        evt = get_event(e1.id, path)
        evt.mastery_state = MASTERY_TRANSFERRED
        save_event(evt, path)
        check("已迁移不再出现在到期队列",
              get_due_reviews(today="2026-08-15", path=path) == [])

        # 全量问题入库：problem_moves_all 优先于 top_problem_moves 切片
        # 用 sente_tenuki 类别，避免与 e1(attack_defense) 产生实战复发联动
        def _tp(m, loss=5.0):
            return {"move_no": m, "color": "B", "played_move": "R10",
                    "best_move": "P9", "quality_key": "blunder",
                    "score_loss": loss, "stage": "middle",
                    "problem_tags": ["tenuki_timing"]}
        all_summary = {"problem_moves_all": [
            _tp(m, loss=2.5 + i * 0.5) for i, m in enumerate(
                [120, 121, 122, 123, 124, 125, 126, 127])],
            "top_problem_moves": [_tp(120), _tp(121), _tp(122)]}
        save_event(LearningEvent.from_problem("g9", _tp(1)), path=path)
        n_all = sync_profile_summary(
            {"id": "g9", "profileSide": "B"}, all_summary, path)
        check("全量问题入库（8 条而非 Top3）", n_all == 8, str(n_all))
        g9 = get_events_by_game("g9", path)
        check("小目损问题也进入学习库",
              any(e.move_no == 127 for e in g9), str([e.move_no for e in g9]))
        remove_game("g9", path)

        # 旧记录无 problem_moves_all → 回退 top_problem_moves
        legacy = sync_profile_summary(
            {"id": "g8", "profileSide": "B"},
            {"top_problem_moves": [_tp(10), _tp(11)]}, path)
        check("旧记录回退 Top 切片", legacy == 2, str(legacy))
        remove_game("g8", path)

        # 统计与删除
        stats = store_stats(path)
        check("统计字段齐全",
              stats["total"] == 3 and stats["games"] == 2
              and stats["by_mastery"].get(MASTERY_TRANSFERRED) == 1,
              "%s / %s" % (stats["total"], stats["by_mastery"]))
        check("按局删除联动", remove_game("g1", path) == 2
              and len(get_events(path)) == 1)
        check("save_attempt 不凭空造事件",
              save_attempt("ghost", "A1", path=path) is None)

        print("test_learning_store: 全部通过")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def run_real_game_transitions():
    """实战维度掌握迁移：复发→unstable、观察窗内消失→transferred（反馈修复5）。"""
    from learning_event import (
        MASTERY_RETAINED, MASTERY_TRANSFERRED, MASTERY_UNSTABLE, event_id,
    )

    def _cat_problem(move_no, tags, loss=5.0):
        return {"move_no": move_no, "color": "B", "played_move": "R10",
                "best_move": "P9", "quality_key": "blunder", "score_loss": loss,
                "stage": "middle", "problem_tags": tags}

    tmp = tempfile.mkdtemp(prefix="learning-transition-")
    path = os.path.join(tmp, "learning_events.json")
    try:
        # gA：attack_defense（将设为 retained）+ endgame（保持 new）
        sync_profile_summary({"id": "gA", "profileSide": "B"}, {
            "problem_moves_all": [
                _cat_problem(10, ["overplay"]),
                _cat_problem(90, ["endgame_value"])]}, path)
        evt_a = get_event(event_id("gA", 10, "B"), path)
        evt_a.mastery_state = MASTERY_RETAINED
        save_event(evt_a, path)

        # 5 盘不含 attack_defense 的新实战 → gA 挤出观察窗 → transferred
        for i in range(5):
            sync_profile_summary({"id": "gB%d" % i, "profileSide": "B"}, {
                "problem_moves_all": [_cat_problem(5, ["tenuki_timing"])]},
                path)
        evt_a = get_event(event_id("gA", 10, "B"), path)
        check("观察窗内消失 → transferred",
              evt_a.mastery_state == MASTERY_TRANSFERRED, evt_a.mastery_state)
        evt_e = get_event(event_id("gA", 90, "B"), path)
        check("new 状态不被迁移逻辑碰", evt_e.mastery_state == "new")

        # 已 transferred 的类别在实战复发 → 重新打开为 unstable
        sync_profile_summary({"id": "gZ", "profileSide": "B"}, {
            "problem_moves_all": [_cat_problem(7, ["overplay"])]}, path)
        evt_a = get_event(event_id("gA", 10, "B"), path)
        check("迁移后复发 → 重新 unstable",
              evt_a.mastery_state == MASTERY_UNSTABLE, evt_a.mastery_state)

        # 时间方向守卫单元测试：复发只向后看（created_at = 入库先后）
        # 手工构造 gOld(1月) 与 gNew(6月) 各一个 retained 事件，
        # 以 gOld 为"本盘"触发迁移时，入库更晚的 gNew 不得被误标
        from learning_store import _apply_real_game_transitions
        tmp2 = tempfile.mkdtemp(prefix="learning-order-")
        path2 = os.path.join(tmp2, "learning_events.json")
        try:
            store = {"version": 1, "events": [
                {"id": event_id("gOld", 5, "B"), "game_id": "gOld",
                 "move_no": 5, "player_color": "B",
                 "primary_category": "attack_defense",
                 "mastery_state": "retained",
                 "created_at": "2026-01-01 10:00:00.000"},
                {"id": event_id("gNew", 7, "B"), "game_id": "gNew",
                 "move_no": 7, "player_color": "B",
                 "primary_category": "attack_defense",
                 "mastery_state": "retained",
                 "created_at": "2026-06-01 10:00:00.000"},
            ]}
            from learning_store import save_store, load_store
            save_store(store, path2)
            _apply_real_game_transitions(path2, "gOld", {"attack_defense"})
            after = {e["game_id"]: e["mastery_state"]
                     for e in load_store(path2)["events"]}
            check("旧盘触发不误标入库更晚的盘（方向守卫）",
                  after == {"gOld": "retained", "gNew": "retained"}, str(after))
            _apply_real_game_transitions(path2, "gNew", {"attack_defense"})
            after = {e["game_id"]: e["mastery_state"]
                     for e in load_store(path2)["events"]}
            check("新盘触发正常重开旧盘 unstable",
                  after == {"gOld": "unstable", "gNew": "retained"}, str(after))
        finally:
            import shutil as _sh
            _sh.rmtree(tmp2, ignore_errors=True)

        # 训练错题（review recurrence）不触发 unstable：mistake_book 路径
        from mistake_book import apply_training_outcomes, sync_profile_summary as mb_sync
        book_path = os.path.join(tmp, "mistake_book.json")
        mb_sync({"id": "gA", "profileSide": "B"}, {
            "top_problem_moves": [_cat_problem(10, ["overplay"])]}, book_path)
        # 先推到 retained：三次 good
        iid = event_id("gA", 10, "B")
        for _ in range(3):
            from mistake_book import record_review
            record_review(iid, "good", book_path)
        apply_training_outcomes("gA", [(10, "B", "again")], book_path)
        from mistake_book import get_item
        it = get_item(iid, book_path)
        check("训练复发降档 retained→understanding（不冒充实战 unstable）",
              it.get("masteryState") == "understanding", str(it.get("masteryState")))
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def run_identity_filtered_recurrence():
    """对手的同类坏棋不能触发本人的实战复发。"""
    from learning_event import MASTERY_RETAINED, event_id

    def _problem(move_no, color, tags):
        return {
            "move_no": move_no,
            "color": color,
            "played_move": "R10",
            "best_move": "P9",
            "quality_key": "blunder",
            "score_loss": 5.0,
            "stage": "middle",
            "problem_tags": tags,
        }

    tmp = tempfile.mkdtemp(prefix="learning-identity-")
    path = os.path.join(tmp, "learning_events.json")
    try:
        # 本人首盘出现攻击/防守问题，并已通过复习进入 retained。
        sync_profile_summary({"id": "gA", "profileSide": "B"}, {
            "problem_moves_all": [_problem(10, "B", ["overplay"])]}, path)
        first = get_event(event_id("gA", 10, "B"), path)
        first.mastery_state = MASTERY_RETAINED
        save_event(first, path)

        # 第二盘只有白方（对手）重复该类别；本人执黑的题属于别类。
        sync_profile_summary({"id": "gB", "profileSide": "B"}, {
            "problem_moves_all": [
                _problem(22, "W", ["overplay"]),
                _problem(31, "B", ["tenuki_timing"]),
            ]}, path)
        retained = get_event(event_id("gA", 10, "B"), path)
        check("对手同类失误不触发本人实战复发",
              retained.mastery_state == MASTERY_RETAINED,
              retained.mastery_state)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def run_delete_game_recurrence_drops():
    """删除一盘历史棋后 recurrence 必须下降（反馈 #24-5，幽灵数据回归）。"""
    from learning_priority import build_recurrence_index

    def _tp(m, loss=5.0):
        return {"move_no": m, "color": "B", "played_move": "R10",
                "best_move": "P9", "quality_key": "blunder", "score_loss": loss,
                "stage": "middle", "problem_tags": ["overplay"]}
    tmp = tempfile.mkdtemp(prefix="learning-del-")
    path = os.path.join(tmp, "learning_events.json")
    try:
        for gid in ("g1", "g2", "g3"):
            sync_profile_summary({"id": gid, "profileSide": "B"},
                                 {"problem_moves_all": [_tp(10), _tp(20)]}, path)
        before = build_recurrence_index(get_events(path))
        check("删除前 attack_defense 出现 3 盘",
              before.get("attack_defense") == 3, str(before))
        remove_game("g2", path)
        after = build_recurrence_index(get_events(path))
        check("删除一盘后 recurrence 降为 2 盘",
              after.get("attack_defense") == 2, str(after))
        # 派生字段不残留：重同步 g1 后 recurrence_count 按新库重算
        sync_profile_summary({"id": "g4", "profileSide": "B"},
                             {"problem_moves_all": [_tp(5)]}, path)
        evt4 = [e for e in get_events_by_game("g4", path)][0]
        check("重算 recurrence_count 不残留旧值（无幽灵复发）",
              evt4.recurrence_count == 2, str(evt4.recurrence_count))
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def run_review_outcome_and_final():
    """P0-4/P1-2：调度唯一写入者 + 优先级 final 唯一值。"""
    from learning_store import apply_review_outcome, finalize_priority

    def _tp(m, loss=5.0):
        return {"move_no": m, "color": "B", "played_move": "R10",
                "best_move": "P9", "quality_key": "blunder", "score_loss": loss,
                "stage": "middle", "problem_tags": ["tenuki_timing"]}
    tmp = tempfile.mkdtemp(prefix="p04-")
    path = os.path.join(tmp, "learning_events.json")
    book = os.path.join(tmp, "mistake_book.json")
    try:
        sync_profile_summary({"id": "gQ", "profileSide": "B"},
                             {"problem_moves_all": [_tp(9)]}, path)
        eid = event_id("gQ", 9, "B")
        evt = apply_review_outcome(eid, "good", today="2026-08-16", path=path)
        check("调度写入事件（good→3天/understanding）",
              evt.review_interval_days == 3
              and evt.mastery_state == "understanding"
              and evt.review_due_date == "2026-08-19", str(evt.review_due_date))
        evt = apply_review_outcome(eid, "good", today="2026-08-19", path=path)
        check("间隔复利（3→7天）", evt.review_interval_days == 7)
        evt = apply_review_outcome(eid, "good", today="2026-08-26", path=path)
        check("间隔≥7天 → retained", evt.mastery_state == "retained")
        evt = apply_review_outcome(eid, "again", today="2026-08-27", path=path,
                                   attempt={"played_move": "R10",
                                            "score_loss": 4.9,
                                            "assessment": "bad"})
        check("again 重置+作答同账",
              evt.review_interval_days == 1 and evt.review_lapses == 1
              and len(evt.attempts) == 1
              and evt.mastery_state == "new")
        # 书侧投影与事件一致
        import mistake_book as mb
        mb.sync_profile_summary(
            {"id": "gQ", "profileSide": "B"},
            {"top_problem_moves": [
                dict(_tp(9), problem_tags=["tenuki_timing"])]}, book)
        it = mb.get_item(eid, book)
        check("书侧调度为事件投影（P0-4）",
              it["intervalDays"] == 1 and it["dueDate"] == "2026-08-28"
              and it["masteryState"] == "new", str(it.get("dueDate")))
        # final 优先级：落库后同源
        pri = {"final_score": 0.77, "components": {"severity": 0.5},
               "version": 1}
        check("finalize_priority 落库", finalize_priority(eid, pri, path=path))
        evt = get_event(eid, path=path)
        check("final 值与状态持久化",
              abs(evt.learning_priority - 0.77) < 1e-9
              and evt.priority_status == "final")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def run_recurrence_cluster_wiring():
    """阶段7 error_chain 接线：写入路径自动填 recurrence_cluster。

    (a) 同簇事件簇 id 一致、远离链另起一簇；(b) 无本人问题手不报错；
    (d) 旧数据（无该键 / 空串）读取与合并兼容。
    """
    from learning_store import save_store

    def _tp(m, loss=5.0, tags=("overplay",)):
        return {"move_no": m, "color": "B", "played_move": "R10",
                "best_move": "P9", "quality_key": "blunder",
                "score_loss": loss, "stage": "middle",
                "problem_tags": list(tags)}

    tmp = tempfile.mkdtemp(prefix="learning-chain-")
    path = os.path.join(tmp, "learning_events.json")
    try:
        # (a) 大纲 §48 原型：63 留弱棋 → 一串后果 → 151 爆发；201 远离另成簇
        n = sync_profile_summary({"id": "gC", "profileSide": "B"}, {
            "problem_moves_all": [
                _tp(63, 1.8), _tp(84, 2.5), _tp(107, 3.0),
                _tp(129, 2.2), _tp(151, 9.5),
                _tp(201, 5.0, tags=("endgame_value",)),
            ]}, path)
        check("链局全部入库", n == 6, str(n))
        events = {e.move_no: e for e in get_events_by_game("gC", path)}
        ids = {m: e.recurrence_cluster for m, e in events.items()}
        check("同簇事件共享簇 id（chain-63）",
              all(ids[m] == "chain-63" for m in (63, 84, 107, 129, 151)),
              str(ids))
        check("远离链另起一簇（chain-201）", ids[201] == "chain-201", str(ids))
        check("簇 id 持久化后可读回",
              get_event(event_id("gC", 151, "B"), path).recurrence_cluster
              == "chain-63")

        # 重新分析改判（151 与原链切断、自成单簇）：合并时簇 id 按新聚类
        # 覆盖（recurrence_cluster 是派生统计，绝不继承旧簇）；未被重写的
        # 历史事件保持原簇 id——与其他派生字段同一 upsert 语义
        sync_profile_summary({"id": "gC", "profileSide": "B"}, {
            "problem_moves_all": [_tp(151, 8.0)]}, path)
        after = {e.move_no: e for e in get_events_by_game("gC", path)}
        check("重新分析后簇 id 覆盖不继承旧簇",
              after[151].recurrence_cluster == "chain-151"
              and after[63].recurrence_cluster == "chain-63",
              str({m: e.recurrence_cluster for m, e in after.items()}))

        # (b) 无本人问题手（只有对手的问题）：返回 0 不写库不报错
        empty = sync_profile_summary({"id": "gD", "profileSide": "B"}, {
            "problem_moves_all": [dict(_tp(30, 4.0), color="W")]}, path)
        check("无本人问题手返回 0 且不报错",
              empty == 0 and get_events_by_game("gD", path) == [])
        direct = LearningEvent.from_problem("gD", _tp(30, 4.0))
        check("单条 save_event 路径簇 id 保持空串",
              save_event(direct, path).recurrence_cluster == "")

        # (d) 旧数据兼容：无 recurrence_cluster 键 / 显式空串
        tmp2 = tempfile.mkdtemp(prefix="learning-chain-legacy-")
        path2 = os.path.join(tmp2, "learning_events.json")
        try:
            old_missing = {"id": event_id("gL", 40, "B"), "game_id": "gL",
                           "move_no": 40, "player_color": "B",
                           "primary_category": "attack_defense"}
            old_empty = dict(old_missing, id=event_id("gL", 60, "B"),
                             move_no=60, recurrence_cluster="")
            save_store({"version": 1, "events": [old_missing, old_empty]},
                       path2)
            e1 = get_event(old_missing["id"], path2)
            e2 = get_event(old_empty["id"], path2)
            check("旧数据读取簇 id 默认空串",
                  e1 is not None and e1.recurrence_cluster == ""
                  and e2 is not None and e2.recurrence_cluster == "")
            # 旧事件随重新分析入库 → 簇 id 升级为新聚类结果；
            # 未被重写的旧事件保持空串（读取端按"未聚类"处理）
            sync_profile_summary({"id": "gL", "profileSide": "B"}, {
                "problem_moves_all": [_tp(40, 3.0), _tp(52, 6.0)]}, path2)
            check("重新入库后旧事件簇 id 升级",
                  get_event(old_missing["id"], path2).recurrence_cluster
                  == "chain-40")
            check("未重写的旧事件保持空串",
                  get_event(old_empty["id"], path2).recurrence_cluster == "")
        finally:
            import shutil as _sh
            _sh.rmtree(tmp2, ignore_errors=True)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    run()
    run_real_game_transitions()
    run_delete_game_recurrence_drops()
    run_review_outcome_and_final()
    run_recurrence_cluster_wiring()
    run_identity_filtered_recurrence()
