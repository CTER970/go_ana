"""test_katago_integration —— 真实 KataGo Analysis Engine 协议集成测试。

纯 Python 单元测试测不出协议级错误（例如 allowMoves 发错格式时引擎只会
报解析错误）。本测试启动本地 katago-runtime 引擎，端到端验证：

  1. 普通查询的 moveInfos 含官方 prior 字段（而非 policy）；
  2. 榜外手 allowMoves 强制分析符合协议（dict + player/moves/untilDepth），
     引擎正常返回该手的 scoreLead（反馈 #2 的 P0 修复验证）；
  3. 若存在 Human SL 模型：humanSLProfile 查询返回 humanPolicy。

引擎/模型缺失时打印 SKIP 并以 0 退出（CI 与无运行时环境不失败）。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from config_manager import ConfigManager, find_runtime_dir


def check(name, cond, extra=""):
    print("[CHECK] %-46s %s %s" % (name, "OK" if cond else "FAIL", extra))
    if not cond:
        raise AssertionError(name)


def _drain_until(client, qid, timeout_s=60):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for rid, resp in client.poll():
            if rid == qid:
                return resp
        time.sleep(0.1)
    return None


def run():
    cfg = ConfigManager()
    engine = cfg.get("engine_path") or ""
    model = cfg.get("model_path") or ""
    cfg_path = cfg.cfg_abspath()
    if not (engine and os.path.exists(engine) and model and os.path.exists(model)
            and os.path.exists(cfg_path)):
        print("SKIP: 本机未找到 katago-runtime（engine=%s model=%s）" % (
            bool(engine and os.path.exists(engine)),
            bool(model and os.path.exists(model))))
        return

    from katago_client import KataGoAnalysisClient
    client = KataGoAnalysisClient(engine, cfg_path, model, cwd=HERE,
                                  human_model_path=cfg.get("human_model_path") or None)
    try:
        client.start()
        deadline = time.time() + 90
        while not client.ready and time.time() < deadline:
            time.sleep(0.1)
        check("引擎就绪（query_version 应答）", client.ready)

        base = {"moves": [], "initialStones": [], "rules": "chinese",
                "komi": 7.5, "boardXSize": 19, "boardYSize": 19,
                "maxVisits": 5, "includePolicy": True}

        # 1) 普通查询：moveInfos 必须带官方 prior 字段
        qid = client.analyze(dict(base))
        resp = _drain_until(client, qid)
        check("普通查询返回 moveInfos", bool(resp and resp.get("moveInfos")))
        if resp and resp.get("moveInfos"):
            check("moveInfo 含 prior 字段（协议验证 #1）",
                  "prior" in resp["moveInfos"][0],
                  str(sorted(k for k in resp["moveInfos"][0]
                             if k in ("prior", "policy"))))

        # 2) 榜外手 allowMoves：dict 协议，引擎返回该手 scoreLead
        #    注意 moves 必须是 [player, vertex] 对（app 产线同格式）
        from candidate_assessment import forced_move_query, forced_move_result
        forced = forced_move_query(
            dict(base, moves=[["B", "D4"]]), "R4", player="W")
        qid = client.analyze(forced)
        resp = _drain_until(client, qid)
        check("allowMoves 查询被引擎接受（协议验证 #2）", resp is not None
              and resp.get("moveInfos") is not None,
              str((resp or {}).get("turnNumber")))
        if resp and resp.get("moveInfos"):
            score, winrate, order = forced_move_result(resp, "R4")
            check("强制选点 R4 返回 scoreLead", score is not None,
                  "score=%s" % score)

        # 3) Human SL（可选）：humanPolicy 出现且不回退普通 prior
        human = cfg.get("human_model_path") or ""
        if human and os.path.exists(human) and client.human_model_active:
            from human_sl import human_query, parse_human_prior
            q = human_query(dict(base, moves=[["B", "D4"]], maxVisits=1), "rank_1d")
            qid = client.analyze(q)
            resp = _drain_until(client, qid)
            mis = (resp or {}).get("moveInfos") or []
            has_human = any("humanPolicy" in m or "humanPrior" in m
                            for m in mis)
            check("humanSLProfile 查询返回 humanPolicy", has_human,
                  str([sorted(k for k in m if "uman" in k) for m in mis[:1]]))
            if mis:
                check("humanPrior 与普通 prior 数值不同（fail closed 旁证）",
                      any(abs(float(m.get("humanPolicy", m.get("humanPrior", 0)))
                              - float(m.get("prior", 0))) > 1e-6 for m in mis))
        else:
            print("SKIP: 未配置 Human SL 模型（katago-runtime/models/ 放入 "
                  "b18 human 模型后本段自动启用）")

        print("test_katago_integration: 全部通过")
    finally:
        client.stop()


if __name__ == "__main__":
    run()
