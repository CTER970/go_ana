"""棋风成本分类测试。"""
from style_cost import classify_style_cost
from style_profile import StyleDimension


def dim(loss, freq=10, samples=10, confidence="medium",
        blunder=0.0, inaccuracy=0.0, trend="stable"):
    return StyleDimension(
        key="fighting_preference", label="主动求战",
        sample_count=samples, evaluated_moves=100,
        frequency_per_100=freq, avg_score_loss=loss,
        blunder_rate=blunder, inaccuracy_rate=inaccuracy,
        confidence=confidence, recent_trend=trend)


def run():
    assert classify_style_cost(dim(1.0)).conclusion == "keep"
    high = classify_style_cost(dim(6.0, blunder=0.2))
    assert high.conclusion == "fix"
    medium = classify_style_cost(dim(3.0))
    assert medium.conclusion == "observe"
    assert classify_style_cost(dim(1.0, samples=3)).conclusion == "insufficient"
    assert classify_style_cost(dim(None)).conclusion == "insufficient"
    worsening = classify_style_cost(dim(6.0, trend="worsening"))
    stable = classify_style_cost(dim(6.0, trend="stable"))
    assert worsening.priority > stable.priority
    print("test_style_cost: PASS")


if __name__ == "__main__":
    run()
