"""产品化 UI 纯逻辑测试。"""
from ui_product import (
    build_game_context, compact_text, fit_window_size, semantic_message_kind)


def main():
    title, meta = build_game_context(
        "对局样本.sgf", "cter", "围棋精灵", "chinese", 7.5, 80, 200, "B+R")
    assert title == "对局样本"
    assert "cter" in meta and "围棋精灵" in meta
    assert "中国规则" in meta and "80/200 手" in meta and "B+R" in meta

    title2, meta2 = build_game_context(
        "新棋局", "黑方", "白方", "japanese", 6.5, 0, 0)
    assert title2 == "新棋局"
    assert "日本规则" in meta2 and "贴 6.5" in meta2

    title3, _meta3 = build_game_context(
        "新棋局", "甲", "乙", "aga", 7.5, 0, 0)
    assert title3 == "甲 vs 乙"

    assert compact_text("123456", 5) == "1234…"
    assert compact_text("123456", 1) == "…"
    assert compact_text("123456", 0) == ""
    assert compact_text("123456", "bad") == "123456"
    _bad_title, bad_meta = build_game_context(
        "坏设置.sgf", "黑", "白", "chinese", "bad", "x", None)
    assert "贴 7.5" in bad_meta and "0/0 手" in bad_meta
    assert semantic_message_kind("已保存项目") == "success"
    assert semantic_message_kind("正在分析整盘") == "progress"
    assert semantic_message_kind("请先启动引擎") == "warning"
    assert semantic_message_kind("导入失败：格式错误") == "error"
    assert semantic_message_kind("第 10 手") == "neutral"

    assert fit_window_size("1241x774", 1280, 768) == (1241, 688)
    assert fit_window_size("", 1920, 1080) == (1240, 760)
    assert fit_window_size("bad", 1100, 700) == (1068, 620)
    print("test_ui_product: PASS")


def test_ui_product():
    main()


if __name__ == "__main__":
    main()
