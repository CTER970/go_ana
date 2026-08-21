"""self_review —— 项目自审查 Agent。

每次修改代码后运行：python self_review.py

基于本项目历史上 5 轮审查发现的 bug 类型，自动化检查以下维度：
  1. 协议层：KataGo API 字段名 / allowMoves 格式 / HumanSL fail-closed
  2. 判分层：CandidateAssessment 是否只算一次 / 单盘表现是否泄漏进容差
  3. 数据层：同一概念是否有多套实现 / 复发是否按唯一盘数
  4. 状态机：掌握状态写入路径是否唯一 / 训练 vs 实战语义是否分离
  5. UI 一致性：文案是否与行为匹配 / 死代码
  6. 测试完整性：新增逻辑是否有对应测试
  7. 全量回归：48 项测试是否全绿
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ===================== 工具 =====================

class Finding:
    def __init__(self, severity, rule, message, file="", line=0):
        self.severity = severity  # P0 / P1 / P2 / OK
        self.rule = rule
        self.message = message
        self.file = file
        self.line = line

    def __str__(self):
        loc = "%s:%s" % (self.file, self.line) if self.file else ""
        return "[%s] %s: %s %s" % (self.severity, self.rule, self.message, loc)


def read(path):
    full = os.path.join(HERE, path)
    if not os.path.exists(full):
        return ""
    with open(full, "r", encoding="utf-8") as f:
        return f.read()


def grep(pattern, path, flags=0):
    text = read(path)
    return list(re.finditer(pattern, text, flags))


def count(pattern, path):
    return len(grep(pattern, path))


# ===================== 检查规则 =====================

def check_protocol(finds):
    """1. KataGo 协议层"""
    # 1a. prior 字段（不是 policy）
    hits = grep(r'_num\(\w+,\s*"policy"\)', "review.py")
    if hits:
        finds.append(Finding("P0", "PROTO-prior",
            "review.py 仍在读 'policy' 字段（KataGo 是 'prior'）",
            "review.py", hits[0].start()))
    else:
        finds.append(Finding("OK", "PROTO-prior", "prior 字段正确"))

    # 1b. allowMoves 格式（必须是 dict 列表）
    hits = grep(r'allowMoves.*\[\s*"', "candidate_assessment.py")
    if hits:
        finds.append(Finding("P0", "PROTO-allowMoves",
            "allowMoves 仍在用字符串数组（需要 dict: player/moves/untilDepth）",
            "candidate_assessment.py"))
    else:
        finds.append(Finding("OK", "PROTO-allowMoves", "allowMoves dict 格式正确"))

    # 1c. HumanSL 不回退普通 prior
    hits = grep(r'_PRIOR_KEYS.*"prior"', "human_sl.py")
    if hits:
        finds.append(Finding("P0", "PROTO-humanSL-fallback",
            "_PRIOR_KEYS 包含 'prior'——普通 AI policy 冒充 humanPrior",
            "human_sl.py"))
    else:
        finds.append(Finding("OK", "PROTO-humanSL-fallback", "HumanSL fail-closed 正确"))


def check_assessment(finds):
    """2. 判分层"""
    # 2a. 单盘表现不参与判题容差
    hits = grep(r'player_performance.*performance_label', "app.py")
    if hits:
        # 检查是否在 _assessment_context 之外
        for h in hits:
            ctx = read("app.py")[max(0, h.start()-500):h.start()]
            if "_assessment_context" not in ctx:
                finds.append(Finding("P0", "ASSESS-per-game-fallback",
                    "单盘表现(player_performance)泄漏进判题容差",
                    "app.py"))
                break
        else:
            finds.append(Finding("OK", "ASSESS-per-game-fallback", "容差无单盘回退"))
    else:
        finds.append(Finding("OK", "ASSESS-per-game-fallback", "容差无单盘回退"))

    # 2b. complexity 是否仍为 0（伪接线已删）
    hits = grep(r'complexity\s*=\s*1\.0\s*-\s*learnability', "app.py")
    if hits:
        finds.append(Finding("P1", "ASSESS-fake-complexity",
            "complexity = 1-learnability 伪接线回归",
            "app.py"))
    else:
        finds.append(Finding("OK", "ASSESS-fake-complexity", "complexity=0 正确"))

    # 2c. 普通 prior 不冒充人类难度
    hits = grep(r'low_ai_prior', "learning_priority.py")
    if hits:
        finds.append(Finding("P1", "ASSESS-prior-as-human",
            "learnability 仍在用普通 prior 推断人类难度",
            "learning_priority.py"))
    else:
        finds.append(Finding("OK", "ASSESS-prior-as-human", "prior 不冒充人类难度"))

    # 2d. grade_attempt 旧排名判分是否复活
    hits = grep(r'def grade_attempt\(', "mistake_book.py")
    if hits:
        finds.append(Finding("P0", "ASSESS-rank-grading",
            "grade_attempt（AI前N=正确）排名判分仍存在",
            "mistake_book.py"))
    else:
        finds.append(Finding("OK", "ASSESS-rank-grading", "排名判分已删除"))

    # 2e. 持久化是否传 assessment（不算两次）
    hits = grep(r'record_graded_attempt\((?!.*assessment=)', "app.py")
    # 简化检查：_drill_persist_free_answer 里是否传了 assessment=
    drill_persist = read("app.py")
    if "_drill_persist_free_answer" in drill_persist:
        seg_start = drill_persist.index("def _drill_persist_free_answer")
        seg_end = drill_persist.index("\n    def ", seg_start + 10)
        seg = drill_persist[seg_start:seg_end]
        if "assessment=assessment" not in seg:
            finds.append(Finding("P0", "ASSESS-double-compute",
                "_drill_persist_free_answer 未传 assessment（UI 与持久化结果可能漂移）",
                "app.py"))
        else:
            finds.append(Finding("OK", "ASSESS-double-compute", "判分只算一次"))


def check_data_source(finds):
    """3. 数据层"""
    # 3a. 复发按唯一盘数
    code = read("learning_priority.py")
    if "build_recurrence_index" in code:
        seg = code[code.index("def build_recurrence_index"):
                   code.index("\ndef ", code.index("def build_recurrence_index") + 10)]
        if "set()" in seg or "games_by_category" in seg:
            finds.append(Finding("OK", "DATA-recurrence-games", "复发按唯一盘数"))
        else:
            finds.append(Finding("P0", "DATA-recurrence-games",
                "build_recurrence_index 可能未按唯一 game_id 去重"))
    # 3b. 全量问题入库
    store = read("learning_store.py")
    if "problem_moves_all" in store:
        finds.append(Finding("OK", "DATA-full-ingest", "全量问题入库"))
    else:
        finds.append(Finding("P0", "DATA-full-ingest",
            "learning_store 未使用 problem_moves_all"))

    # 3c. 派生字段不走进度合并（无幽灵复发）
    if "recurrence_count" in store:
        seg = store[store.index("_PROGRESS_DEFAULTS"):
                    store.index("}", store.index("_PROGRESS_DEFAULTS"))]
        if "recurrence_count" in seg:
            finds.append(Finding("P1", "DATA-ghost-recurrence",
                "recurrence_count 在进度合并名单中（删除棋后幽灵复发）"))
        else:
            finds.append(Finding("OK", "DATA-ghost-recurrence", "派生字段不继承旧值"))

    # 3d. 入库阈值 ≥1.0
    pp = read("player_profile.py")
    if ">= 1.0" in pp and "EVENT_ELIGIBILITY" in pp:
        finds.append(Finding("OK", "DATA-threshold", "入库阈值 1.0 目"))
    elif ">= 2.0" in pp and "problem_moves_all" not in pp:
        finds.append(Finding("P1", "DATA-threshold", "入库阈值可能仍为 2.0"))


def check_state_machine(finds):
    """4. 状态机"""
    mb = read("mistake_book.py")

    # 4a. set_mastered 不制造 retained
    if "set_mastered" in mb:
        seg = mb[mb.index("def set_mastered"):
                 mb.index("\ndef ", mb.index("def set_mastered") + 10)]
        if '"retained"' in seg:
            finds.append(Finding("P0", "STATE-manual-retained",
                "set_mastered 仍直接设 retained"))
        else:
            finds.append(Finding("OK", "STATE-manual-retained", "手动标记不制造 retained"))

    # 4b. 训练 good 封顶 understanding
    if "apply_training_outcomes" in mb:
        seg = mb[mb.index("def apply_training_outcomes"):
                 mb.index("\ndef ", mb.index("def apply_training_outcomes") + 10)]
        if '"retained"' in seg and "understanding" not in seg:
            finds.append(Finding("P0", "STATE-training-retained",
                "训练 good 直接设 retained"))
        else:
            finds.append(Finding("OK", "STATE-training-retained", "训练封顶 understanding"))

    # 4c. 反向同步是否退役
    if "_mirror_mastery_to_events" in mb:
        # 检查是否只在 _apply_review_result 中（委托路径允许）
        finds.append(Finding("P2", "STATE-mirror",
            "_mirror_mastery_to_events 仍存在（确认是委托缓存而非反向同步）"))

    # 4d. unstable 只由实战触发
    ls = read("learning_store.py")
    if "_apply_real_game_transitions" in ls:
        finds.append(Finding("OK", "STATE-unstable-source", "实战触发 unstable 路径存在"))


def check_ui_text(finds):
    """5. UI 一致性 + 死代码"""
    app = read("app.py")

    # 5a. 候选区按钮格式
    if '"%d  %s" % (idx + 1, mv)' in app or '"%d  %s" % (idx + 1,' in app:
        finds.append(Finding("OK", "UI-candidate-format", "候选按钮=序号+坐标"))
    if '"%s  %s" % (letter, mv)' in app:
        finds.append(Finding("P1", "UI-candidate-format", "候选按钮仍在用字母"))

    # 5b. 棋盘标记用红色数字
    if 'fill=COLORS["red"]' in app and 'label = str(index + 1)' in app:
        finds.append(Finding("OK", "UI-board-marker", "棋盘红色数字"))

    # 5c. 硬编码色值
    ui_files = []
    for root, _dirs, files in os.walk(os.path.join(HERE, "ui")):
        for f in files:
            if f.endswith(".py") and f != "tokens.py":
                ui_files.append(os.path.join(root, f))
    hex_in_ui = []
    for path in ui_files:
        with open(path, "r", encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                code = line.split("#")[0]
                if re.search(r"#[0-9a-fA-F]{6}\b", code):
                    hex_in_ui.append("%s:%d" % (os.path.basename(path), i))
    if hex_in_ui:
        finds.append(Finding("P1", "UI-hardcoded-colors",
            "ui/ 目录有 %d 处硬编码色值" % len(hex_in_ui)))
    else:
        finds.append(Finding("OK", "UI-hardcoded-colors", "ui/ 无散落色值"))

    # 5d. pyflakes 非测试文件
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pyflakes"] +
            [f for f in os.listdir(HERE) if f.endswith(".py") and not f.startswith("test_")],
            capture_output=True, text=True, timeout=30, cwd=HERE)
        lines = [l for l in (result.stdout or "").strip().split("\n") if l.strip()]
        if lines:
            finds.append(Finding("P2", "UI-pyflakes",
                "pyflakes %d 条（非测试文件）" % len(lines)))
        else:
            finds.append(Finding("OK", "UI-pyflakes", "pyflakes 干净"))
    except Exception:
        pass


def check_tests(finds):
    """6. 测试完整性"""
    # 核心测试文件存在
    required = [
        "test_candidate_assessment.py",
        "test_learning_store.py",
        "test_learning_priority.py",
        "test_taxonomy.py",
        "test_human_sl.py",
        "test_coach.py",
        "test_error_chain_profile.py",
        "test_katago_integration.py",
        "test_ui_v6.py",
        "test_training.py",
    ]
    missing = [f for f in required if not os.path.exists(os.path.join(HERE, f))]
    if missing:
        finds.append(Finding("P1", "TEST-missing",
            "缺少核心测试：%s" % ", ".join(missing)))
    else:
        finds.append(Finding("OK", "TEST-missing", "核心测试文件齐全（10 个）"))


def check_domain(finds):
    """8. 学习领域不变式（D1-D10）"""
    try:
        from domain_invariants import check_all_domain
        violations = check_all_domain()
        if violations:
            for vid, msg in violations:
                finds.append(Finding("P0", "DOMAIN-%s" % vid, msg))
        else:
            finds.append(Finding("OK", "DOMAIN",
                "学习领域不变式全部通过（7 条）"))
    except ImportError:
        finds.append(Finding("P1", "DOMAIN",
            "domain_invariants.py 不存在"))
    except Exception as e:
        finds.append(Finding("P0", "DOMAIN",
            "领域不变式检查异常: %r" % e))


def check_regression(finds):
    """7. 全量回归"""
    tests = sorted(f for f in os.listdir(HERE)
                   if f.startswith("test_") and f.endswith(".py"))
    passed, failed = 0, []
    for test in tests:
        try:
            result = subprocess.run(
                [sys.executable, "-X", "utf8", test],
                timeout=180, cwd=HERE,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if result.returncode == 0:
                passed += 1
            else:
                failed.append(test)
        except subprocess.TimeoutExpired:
            failed.append(test + "(timeout)")
        except Exception:
            failed.append(test + "(error)")

    total = len(tests)
    if failed:
        finds.append(Finding("P0", "REGRESSION",
            "回归 %d/%d 通过，失败：%s" % (passed, total, ", ".join(failed))))
    else:
        finds.append(Finding("OK", "REGRESSION", "全量回归 %d/%d 全绿" % (passed, total)))


# ===================== 主入口 =====================

def run():
    print("=" * 64)
    print(" 自审查 Agent —— go_ana 学习系统")
    print("=" * 64)
    start = time.time()

    finds = []
    check_protocol(finds)
    check_assessment(finds)
    check_data_source(finds)
    check_state_machine(finds)
    check_ui_text(finds)
    check_tests(finds)
    check_domain(finds)

    print("\n--- 静态检查 ---")
    p0 = [f for f in finds if f.severity == "P0"]
    p1 = [f for f in finds if f.severity == "P1"]
    p2 = [f for f in finds if f.severity == "P2"]
    ok = [f for f in finds if f.severity == "OK"]

    for f in finds:
        print("  %s" % f)

    print("\n--- 全量回归 ---")
    reg_finds = []
    check_regression(reg_finds)
    for f in reg_finds:
        print("  %s" % f)
    finds.extend(reg_finds)

    elapsed = time.time() - start
    print("\n" + "=" * 64)
    print(" 结论：%d OK ｜ %d P0 ｜ %d P1 ｜ %d P2  ｜ 耗时 %.1fs" % (
        len(ok), len(p0), len(p1), len(p2), elapsed))

    if p0:
        print(" ⚠ 存在 P0 问题，必须修复后再提交！")
        return 1
    if p1:
        print(" ⚠ 存在 P1 问题，建议尽快修复")
        return 0
    print(" ✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(run())
