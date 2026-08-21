"""个人棋风画像：从现有轻量复盘摘要提取可追溯的统计倾向。

第一版不猜测棋手心理，也不依赖 ownership。无法由现有摘要可靠识别的
维度会保留为空并标记样本不足。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional

from player_profile import analysis_signatures_compatible

VERSION = 1

DIMENSIONS = (
    ("territory_preference", "实地确认"),
    ("influence_preference", "外势发展"),
    ("fighting_preference", "主动求战"),
    ("stability_preference", "稳健选择"),
    ("tenuki_tendency", "脱先倾向"),
    ("advantage_pressure", "优势局继续施压"),
    ("comeback_complexity", "劣势局复杂化"),
    ("endgame_safety", "官子收束"),
)


@dataclass
class StyleDimension:
    key: str
    label: str
    sample_count: int = 0
    evaluated_moves: int = 0
    frequency_per_100: float = 0.0
    avg_score_loss: Optional[float] = None
    avg_winrate_drop: Optional[float] = None
    blunder_rate: float = 0.0
    inaccuracy_rate: float = 0.0
    top3_match_rate: Optional[float] = None
    recent_trend: str = "insufficient"
    confidence: str = "low"
    representative_moves: list = field(default_factory=list)
    evidence_notes: list = field(default_factory=list)
    version: int = VERSION

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, raw):
        data = dict(raw or {})
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: data[key] for key in allowed if key in data})


@dataclass
class StyleProfile:
    profile_id: str = "default"
    generated_at: str = ""
    games_count: int = 0
    evaluated_moves_count: int = 0
    analysis_signature_summary: dict = field(default_factory=dict)
    dimensions: list = field(default_factory=list)
    cost_results: list = field(default_factory=list)
    style_summary: str = ""
    strengths: list = field(default_factory=list)
    high_cost_habits: list = field(default_factory=list)
    uncertain_findings: list = field(default_factory=list)
    representative_moves: list = field(default_factory=list)
    confidence: str = "low"
    warnings: list = field(default_factory=list)
    source_game_ids: list = field(default_factory=list)
    version: int = VERSION

    def to_dict(self):
        data = asdict(self)
        data["dimensions"] = [
            item.to_dict() if hasattr(item, "to_dict") else dict(item)
            for item in self.dimensions
        ]
        data["cost_results"] = [
            item.to_dict() if hasattr(item, "to_dict") else dict(item)
            for item in self.cost_results
        ]
        return data


def region_of_move(move, board_size=19):
    """把 GTP 坐标粗分为角、边、中央区域；仅用于统计，不作棋理断言。"""
    text = str(move or "").upper()
    if not text or text == "PASS":
        return "unknown"
    columns = "ABCDEFGHJKLMNOPQRST"
    try:
        x = columns.index(text[0])
        y_from_bottom = int(text[1:]) - 1
        y = board_size - 1 - y_from_bottom
    except (ValueError, IndexError):
        return "unknown"
    near_left, near_right = x <= 5, x >= board_size - 6
    near_top, near_bottom = y <= 5, y >= board_size - 6
    if near_left and near_top:
        return "top_left"
    if near_right and near_top:
        return "top_right"
    if near_left and near_bottom:
        return "bottom_left"
    if near_right and near_bottom:
        return "bottom_right"
    edge = min(x, y, board_size - 1 - x, board_size - 1 - y)
    if edge <= 4:
        if y <= 4:
            return "top_side"
        if y >= board_size - 5:
            return "bottom_side"
        if x <= 4:
            return "left_side"
        return "right_side"
    return "center"


def _normalized_records(records):
    candidates = []
    for record in records or []:
        profile_summary = record.get("profileSummary") or {}
        review_summary = record.get("reviewSummaryV2") or {}
        side = record.get("profileSide") or profile_summary.get("user_side") or "unknown"
        if side not in ("B", "W", "both"):
            continue
        signature = (
            profile_summary.get("analysis_signature")
            or review_summary.get("analysisSignature") or {})
        moves = review_summary.get("moveQuality") or []
        candidates.append((record, profile_summary, moves, side, signature))
    if not candidates:
        return [], 0
    latest_signature = candidates[-1][4]
    latest_version = int(candidates[-1][1].get("version", 1) or 1)
    compatible = [
        item for item in candidates
        if int(item[1].get("version", 1) or 1) == latest_version
        and analysis_signatures_compatible(item[4], latest_signature)
    ]
    return compatible, len(candidates) - len(compatible)


def _matches_dimension(key, move):
    tags = set(move.get("problem_tags") or move.get("problemTags") or [])
    stage = move.get("stage") or "middle"
    played = move.get("played_move") or move.get("playedMove")
    best = move.get("best_move") or move.get("bestMove")
    region = region_of_move(played)
    best_region = region_of_move(best)
    loss = float(move.get("score_loss") or move.get("scoreLoss") or 0.0)
    ai_rank = move.get("ai_rank", move.get("aiRank"))
    corner = region in ("top_left", "top_right", "bottom_left", "bottom_right")
    side = region.endswith("_side")

    if key == "territory_preference":
        return stage == "endgame" or (
            stage in ("opening", "middle") and (corner or side))
    if key == "influence_preference":
        return stage in ("opening", "middle") and region == "center"
    if key == "fighting_preference":
        # 现有轻量摘要没有邻近棋子/接触战结构；只使用明确的过分标签，
        # 不把所有高目损手臆测成“主动求战”。
        return "overplay" in tags
    if key == "stability_preference":
        return loss <= 1.5 and ai_rank is not None and int(ai_rank) <= 3
    if key == "tenuki_tendency":
        return "opening_direction" in tags or (
            stage != "endgame" and ai_rank is not None and int(ai_rank) > 5
            and region != "unknown" and best_region != "unknown"
            and region != best_region)
    if key == "advantage_pressure":
        return "advantage_management" in tags
    if key == "comeback_complexity":
        return "comeback_attempt" in tags
    if key == "endgame_safety":
        return stage == "endgame"
    return False


def _dimension_trend(game_samples):
    if len(game_samples) < 4:
        return "insufficient"
    split = len(game_samples) // 2
    previous = game_samples[:split]
    recent = game_samples[-split:]
    previous_rate = sum(previous) / max(1, len(previous))
    recent_rate = sum(recent) / max(1, len(recent))
    threshold = max(0.5, previous_rate * 0.15)
    if recent_rate >= previous_rate + threshold:
        return "worsening"
    if recent_rate <= previous_rate - threshold:
        return "improving"
    return "stable"


def _build_dimension(key, label, moves, total_moves, game_ids):
    selected = [move for move in moves if _matches_dimension(key, move)]
    sample_count = len(selected)
    losses = [
        float(move.get("score_loss", move.get("scoreLoss")))
        for move in selected
        if move.get("score_loss", move.get("scoreLoss")) is not None
    ]
    drops = [
        float(move.get("winrate_drop", move.get("winrateDrop")))
        for move in selected
        if move.get("winrate_drop", move.get("winrateDrop")) is not None
    ]
    qualities = [
        move.get("quality_key", move.get("qualityKey", "unknown"))
        for move in selected
    ]
    top3 = [
        bool(move.get("top3_match", move.get("top3Match")))
        for move in selected
        if move.get("ai_rank", move.get("aiRank")) is not None
    ]
    per_game = []
    for game_id in game_ids:
        game_total = sum(1 for move in moves if move["_game_id"] == game_id)
        game_count = sum(
            1 for move in selected if move["_game_id"] == game_id)
        per_game.append(game_count / max(1, game_total) * 100.0)
    confidence = (
        "high" if sample_count >= 20 and len(game_ids) >= 5
        else "medium" if sample_count >= 8 and len(game_ids) >= 3
        else "low")
    representatives = sorted(
        selected,
        key=lambda move: float(
            move.get("score_loss", move.get("scoreLoss", 0.0)) or 0.0),
        reverse=True)[:5]
    representatives = [
        {key: value for key, value in move.items() if not key.startswith("_")}
        for move in representatives
    ]
    avg_loss = sum(losses) / len(losses) if losses else None
    notes = [
        "命中 %d 手，占 %d 手有效样本的 %.1f%%。" % (
            sample_count, total_moves,
            sample_count / max(1, total_moves) * 100.0)
    ]
    if avg_loss is not None:
        notes.append("该类样本平均目损 %.2f。" % avg_loss)
    if sample_count < 5:
        notes.append("样本少于 5 手，暂不输出强棋风结论。")
    return StyleDimension(
        key=key,
        label=label,
        sample_count=sample_count,
        evaluated_moves=total_moves,
        frequency_per_100=sample_count / max(1, total_moves) * 100.0,
        avg_score_loss=avg_loss,
        avg_winrate_drop=(sum(drops) / len(drops) if drops else None),
        blunder_rate=(
            qualities.count("blunder") / sample_count if sample_count else 0.0),
        inaccuracy_rate=(
            qualities.count("inaccuracy") / sample_count if sample_count else 0.0),
        top3_match_rate=(
            sum(top3) / len(top3) * 100.0 if top3 else None),
        recent_trend=_dimension_trend(per_game),
        confidence=confidence,
        representative_moves=representatives,
        evidence_notes=notes,
    )


def build_style_profile(game_records, player_profile=None, profile_id="default"):
    """从棋局库记录构建 8 维棋风画像。"""
    normalized, excluded = _normalized_records(game_records)
    moves = []
    game_ids = []
    for record, _summary, move_items, side, _signature in normalized:
        game_id = str(record.get("id") or "")
        game_ids.append(game_id)
        for raw in move_items:
            move = dict(raw)
            if side in ("B", "W") and move.get("color") != side:
                continue
            if not move.get("analysis_available", move.get("analysisAvailable", True)):
                continue
            if not move.get(
                    "is_meaningful_position",
                    move.get("isMeaningfulPosition", True)):
                continue
            move["_game_id"] = game_id
            move["_game_name"] = record.get("name") or game_id
            move["game_id"] = game_id
            move["game_name"] = record.get("name") or game_id
            move["project_path"] = record.get("projectPath")
            moves.append(move)
    dimensions = [
        _build_dimension(key, label, moves, len(moves), game_ids)
        for key, label in DIMENSIONS
    ]
    confidence = (
        "high" if len(game_ids) >= 10 and len(moves) >= 300
        else "medium" if len(game_ids) >= 3 and len(moves) >= 80
        else "low")
    warnings = []
    if excluded:
        warnings.append("%d 盘因分析口径不兼容未纳入。" % excluded)
    if len(game_ids) < 3:
        warnings.append("当前少于 3 盘，棋风结论仅作低置信参考。")
    profile = StyleProfile(
        profile_id=profile_id,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        games_count=len(game_ids),
        evaluated_moves_count=len(moves),
        analysis_signature_summary=(
            dict(normalized[-1][4]) if normalized else {}),
        dimensions=dimensions,
        representative_moves=sorted(
            [move for dimension in dimensions
             for move in dimension.representative_moves],
            key=lambda move: float(
                move.get("score_loss", move.get("scoreLoss", 0.0)) or 0.0),
            reverse=True)[:12],
        confidence=confidence,
        warnings=warnings,
        source_game_ids=game_ids,
    )
    return profile
