"""test_learning_store —— LearningStore JSON Repository 测试。"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from learning_event import (
    KIND_ENDGAME_DRILL, KIND_PROBLEM, MASTERY_UNDERSTANDING,
    MASTERY_TRANSFERRED, RETRY_REPEATED, RETRY_CORRECTED, LearningEvent,
    endgame_event_id, event_id,
)
from learning_store import (
    get_due_reviews, get_event, get_events, get_events_by_category,
    get_events_by_game, record_endgame_drill_attempt, remove_game,
    save_attempt, save_event, store_stats, sync_profile_summary,
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


def run_retry_zero_not_swallowed():
    """显式 retry 事件字段必须原样落库，不得被默认值合并吞掉。

    第一波审查修复的回归项：save_event 合并进度字段时曾用
    "值 == 默认值" 判未设置，导致显式记录的 retry_score_loss=0.0
    （用户重选 AI 最佳点，目损恰为 0）被误判未设置而继承旧值。
    """
    tmp = tempfile.mkdtemp(prefix="learning-retry0-")
    path = os.path.join(tmp, "learning_events.json")
    try:
        e = LearningEvent.from_problem("gR", _problem(30, loss=6.0))
        e.primary_category = "attack_defense"
        save_event(e, path)
        # 第一次重试：目损 4.2（有旧值可供误继承）
        save_attempt(e.id, "Q11", score_loss=4.2, assessment="bad",
                     ai_rank=3, retry_status=RETRY_REPEATED, path=path)
        evt = get_event(e.id, path)
        check("首次重试落库", abs(evt.retry_score_loss - 4.2) < 1e-9
              and evt.retry_status == RETRY_REPEATED)
        # 第二次重试：重选 AI 最佳，目损恰为 0.0（合法显式值）
        save_attempt(e.id, "P9", score_loss=0.0, assessment="good",
                     ai_rank=0, retry_status=RETRY_CORRECTED, path=path)
        evt = get_event(e.id, path)
        check("retry_score_loss=0.0 不被默认值合并吞掉",
              evt.retry_score_loss == 0.0
              and evt.user_retry_move == "P9"
              and evt.retry_status == RETRY_CORRECTED,
              "got loss=%r move=%r" % (evt.retry_score_loss,
                                       evt.user_retry_move))
        # 再走 save_event upsert（重新分析路径）也不得回吐旧重试值
        e_new = LearningEvent.from_problem("gR", _problem(30, loss=6.5))
        e_new.primary_category = "attack_defense"
        merged = save_event(e_new, path)
        check("upsert 后重试结果仍为显式新值",
              merged.retry_score_loss == 0.0
              and merged.user_retry_move == "P9"
              and merged.retry_status == RETRY_CORRECTED,
              "got loss=%r move=%r" % (merged.retry_score_loss,
                                       merged.user_retry_move))
        # 对照：未显式记录 retry 的新事件仍继承旧进度（默认合并语义不变）
        e_new2 = LearningEvent.from_problem("gR2", _problem(8, loss=3.0))
        save_event(e_new2, path)
        save_attempt(e_new2.id, "K10", score_loss=5.0, assessment="bad",
                     retry_status=RETRY_REPEATED, path=path)
        # gR2 事件本身不带 retry_status → 重新分析 upsert 应继承旧重试
        e_new2b = LearningEvent.from_problem("gR2", _problem(8, loss=3.3))
        merged2 = save_event(e_new2b, path)
        check("无新 retry 时默认合并仍继承旧值",
              abs(merged2.retry_score_loss - 5.0) < 1e-9
              and merged2.user_retry_move == "K10")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)




def run_default_path_redirection():
    """W29 审查回归：默认路径必须调用时解析（set_path/gl 派生生效）。

    历史缺陷：def f(path=DEFAULT_PATH) 在导入期固化路径，重定向对
    "走默认值"的调用无效，测试可能写穿生产 learning_events.json。
    """
    import hashlib
    import learning_store as ls

    def _digest(path):
        if not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    real = ls.default_path()
    before = _digest(real)
    tmp = tempfile.mkdtemp(prefix="ls-redirect-")
    try:
        check("save_event 默认参数未固化", ls.save_event.__defaults__[0] is None)
        check("get_events 默认参数未固化", ls.get_events.__defaults__[0] is None)

        redirected = os.path.join(tmp, "learning_events.json")
        ls.set_path(redirected)
        try:
            evt = LearningEvent(
                game_id="redirect-g1", move_no=5, player_color="B",
                played_move="R10", best_move="P9", score_loss=4.0)
            save_event(evt)
            check("set_path 重定向写入生效", os.path.exists(redirected))
            check("默认读取命中重定向位置",
                  len(get_events()) == 1)
            check("生产 learning_events 未被触碰", _digest(real) == before)
        finally:
            ls.set_path(None)
        check("set_path(None) 恢复默认", ls.get_path() == ls.default_path())

        import game_library as gl
        gl_orig = gl.LIBRARY_DIR
        gl.LIBRARY_DIR = tmp
        try:
            check("默认路径跟随 game_library.LIBRARY_DIR",
                  ls.get_path() == redirected)
        finally:
            gl.LIBRARY_DIR = gl_orig
        print("[PASS] run_default_path_redirection")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
        ls.set_path(None)


def run_endgame_drill_events():
    """接力板#12 回归：官子作答事件入库、与问题手事件隔离、不进 SRS。"""
    from types import SimpleNamespace

    drill = SimpleNamespace(
        move_number=55, color="B", played_move="B9", best_move="C2",
        loss=5.0, drill_kind="loss", value=5.0)
    tmp = tempfile.mkdtemp(prefix="ls-endgame-")
    path = os.path.join(tmp, "learning_events.json")
    try:
        # 空 game_id 不凭空造事件（与 save_attempt 同原则）
        check("空 game_id 返回 None",
              record_endgame_drill_attempt("", drill, "C2", path=path) is None)

        evt = record_endgame_drill_attempt(
            "ge1", drill, "C2", game_name="官子局",
            score_loss=0.0, assessment="best", ai_rank=1, path=path)
        check("首次作答创建官子事件",
              evt is not None and evt.kind == KIND_ENDGAME_DRILL
              and evt.game_id == "ge1" and len(evt.attempts) == 1)
        check("作答历史落在 attempts",
              evt.attempts[0]["played_move"] == "C2"
              and evt.attempts[0]["assessment"] == "best"
              and evt.attempts[0]["score_loss"] == 0.0)

        # 第二次作答同一题：get-or-create，不产生第二条事件
        evt2 = record_endgame_drill_attempt(
            "ge1", drill, "Q16", score_loss=1.5, assessment="acceptable",
            ai_rank=2, path=path)
        check("重复作答只追加 attempts 不新增事件",
              evt2.id == evt.id and len(evt2.attempts) == 2
              and len(get_events(path)) == 1)

        # 同局同手同色的问题手事件并存：id 命名空间隔离，互不覆盖
        pe = LearningEvent.from_problem("ge1", _problem(55), game_name="官子局")
        pe.primary_category = "endgame_timing"
        pe.learning_priority = 0.6
        merged = save_event(pe, path)
        check("问题手事件与官子事件 id 不冲突",
              merged.id == event_id("ge1", 55, "B")
              and merged.id != endgame_event_id("ge1", 55, "B")
              and merged.kind == KIND_PROBLEM
              and len(get_events(path)) == 2)
        # 问题手事件 upsert 后官子事件的作答历史不受影响
        check("问题手 upsert 不冲掉官子作答",
              len(get_event(endgame_event_id("ge1", 55, "B"), path).attempts) == 2)

        # kind 过滤：问题手口径的查询看不到官子事件
        check("kind=problem 过滤官子事件",
              [e.kind for e in get_events(path, kind=KIND_PROBLEM)]
              == [KIND_PROBLEM])
        check("按局查询 kind 过滤",
              len(get_events_by_game("ge1", path, kind=KIND_PROBLEM)) == 1
              and len(get_events_by_game("ge1", path,
                                         kind=KIND_ENDGAME_DRILL)) == 1
              and len(get_events_by_game("ge1", path)) == 2)

        # 官子事件不进 SRS：无到期日、无掌握迁移，永不进复习队列
        check("官子事件不进到期复习队列",
              get_due_reviews(today="2030-01-01", path=path) == [])
        check("官子事件掌握状态恒 new",
              get_event(evt.id, path).mastery_state == "new")

        # 复发统计不受官子事件污染（primary_category 空 → build_recurrence_index 跳过）
        from learning_priority import build_recurrence_index
        check("复发索引不含官子事件",
              build_recurrence_index(get_events(path)) == {"endgame_timing": 1})

        # 画像汇总：问题手口径隔离 + 官子一行数据
        from learning_profile import format_learning_summary, summarize_learning
        summary = summarize_learning(get_events(path))
        check("画像问题手口径不含官子事件",
              summary["events_total"] == 1
              and summary["category_distribution"].get("unclassified") is None)
        eg = summary["endgame_drill"]
        check("官子汇总 answered/correct/目损",
              eg["answered"] == 2 and eg["correct"] == 2
              and eg["accuracy"] == 100.0
              and abs(eg["avg_answer_loss"] - 0.75) < 1e-9, str(eg))
        text = format_learning_summary(summary)
        check("画像一行呈现官子表现",
              "官子训练" in text and "2 题" in text and "100%" in text, text)

        # 统计与删除联动
        stats = store_stats(path)
        check("store_stats 官子事件单列",
              stats["total"] == 2 and stats["endgame_drills"] == 1
              and "unclassified" not in stats["by_category"], str(stats))
        check("按局删除联动（问题手+官子一并清）",
              remove_game("ge1", path) == 2 and get_events(path) == [])
        print("[PASS] run_endgame_drill_events")
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
    run_retry_zero_not_swallowed()
    run_default_path_redirection()
    run_endgame_drill_events()
