"""棋风画像纯逻辑测试。"""
from style_profile import build_style_profile, region_of_move


def move(no, color="B", played="Q16", best="Q16", stage="opening",
         loss=0.5, quality="good", tags=None, rank=1, meaningful=True):
    return {
        "move_no": no, "color": color, "played_move": played,
        "best_move": best, "stage": stage, "score_loss": loss,
        "winrate_drop": loss * 2, "quality_key": quality,
        "ai_rank": rank, "top3_match": rank <= 3,
        "problem_tags": list(tags or []),
        "analysis_available": True,
        "is_meaningful_position": meaningful,
    }


def record(game_id, moves, side="B", visits=200):
    signature = {
        "model": "m", "rules": "chinese", "komi": 7.5,
        "visits": visits, "quality_version": 1}
    return {
        "id": game_id, "name": game_id + ".sgf", "profileSide": side,
        "projectPath": game_id + ".json",
        "profileSummary": {
            "version": 2, "user_side": side,
            "evaluated_moves": len(moves),
            "analysis_signature": signature},
        "reviewSummaryV2": {
            "version": 1, "analysisSignature": signature,
            "moveQuality": moves},
    }


def run():
    empty = build_style_profile([])
    assert len(empty.dimensions) == 8
    assert all(item.confidence == "low" for item in empty.dimensions)

    records = []
    for game in range(4):
        moves = []
        for i in range(12):
            moves.append(move(i * 2 + 1, played="Q16", loss=0.5))
            moves.append(move(
                i * 2 + 2, color="W", played="D4", loss=9.0,
                quality="blunder", tags=["overplay"]))
        moves.extend([
            move(30 + game, played="K10", best="Q16", loss=8.0,
                 quality="blunder", tags=["overplay"], rank=8),
            move(40 + game, played="D4", best="Q16", loss=4.0,
                 quality="inaccuracy", tags=["opening_direction"], rank=9),
        ])
        records.append(record("g%d" % game, moves, side="B"))
    profile = build_style_profile(records)
    assert profile.games_count == 4
    assert profile.evaluated_moves_count == 56  # 白方样本不混入
    assert len(profile.dimensions) == 8
    by_key = {item.key: item for item in profile.dimensions}
    assert by_key["territory_preference"].sample_count >= 48
    assert by_key["fighting_preference"].sample_count == 4
    assert by_key["tenuki_tendency"].sample_count >= 4
    assert by_key["territory_preference"].representative_moves
    assert region_of_move("K10") == "center"
    assert region_of_move("Q16") == "top_right"

    settled = record("settled", [
        move(1, meaningful=False, loss=20, quality="blunder",
             tags=["overplay"])])
    profile2 = build_style_profile([settled])
    assert profile2.evaluated_moves_count == 0
    print("test_style_profile: PASS")


if __name__ == "__main__":
    run()
