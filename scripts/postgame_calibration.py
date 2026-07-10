"""Postgame-review driven calibration for daily predictions."""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
POSTGAME_JSON = DATA_DIR / "postgame_review.json"


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def load_recent_calibration(days: int = 10) -> dict:
    default = {
        "source": "postgame_review",
        "days": 0,
        "games": 0,
        "winner_accuracy": None,
        "score_total_bias": 0.0,
        "score_total_bias_applied": 0.0,
        "winner_min_confidence": 0.55,
        "totals_min_edge": 0.025,
        "totals_min_prob": 0.58,
        "totals_min_line_gap": 1.0,
        "notes": ["尚無足夠賽後檢討樣本，使用保守預設門檻。"],
    }
    if not POSTGAME_JSON.exists():
        return default
    try:
        payload = json.loads(POSTGAME_JSON.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return default

    recent_days = payload.get("days", [])[-days:]
    games = [
        game
        for day in recent_days
        for game in day.get("games", [])
        if game.get("winner_correct") is not None
    ]
    if not games:
        return default

    winner_accuracy = mean(1.0 if game.get("winner_correct") else 0.0 for game in games)
    totals_games = [
        game
        for game in games
        if isinstance(game.get("predicted_total"), (int, float))
        and isinstance(game.get("actual_total"), (int, float))
    ]
    total_bias = mean(game["actual_total"] - game["predicted_total"] for game in totals_games) if totals_games else 0.0

    low_conf_games = [game for game in games if float(game.get("confidence") or 0) < 0.55]
    low_conf_accuracy = mean(1.0 if game.get("winner_correct") else 0.0 for game in low_conf_games) if low_conf_games else 0.5
    high_conf_misses = sum(
        1
        for game in games
        if float(game.get("confidence") or 0) >= 0.58 and not game.get("winner_correct")
    )

    notes = []
    winner_min_confidence = 0.55
    if low_conf_accuracy < 0.52:
        winner_min_confidence = 0.56
        notes.append("近十天低信心勝方表現偏弱，主推勝方門檻提高到 56%。")
    if high_conf_misses >= 5:
        winner_min_confidence = max(winner_min_confidence, 0.57)
        notes.append("近十天高信心失準偏多，勝方主推需更嚴格確認。")

    applied_bias = round(_clamp(total_bias, -0.75, 1.25), 2)
    if abs(applied_bias) >= 0.25:
        notes.append(f"近十天比分模型總分平均偏差 {total_bias:+.2f} 分，今日比分總分校正 {applied_bias:+.2f} 分。")

    mismatch_notes = sum(
        1
        for game in games
        for note in game.get("notes", [])
        if "勝方比分模型與大小分模型差距較大" in note
    )
    totals_min_line_gap = 1.0
    totals_min_edge = 0.025
    totals_min_prob = 0.58
    if mismatch_notes >= max(8, len(games) // 3):
        totals_min_line_gap = 1.25
        totals_min_edge = 0.03
        totals_min_prob = 0.59
        notes.append("近十天大小分與比分模型分歧頻繁，大小分需方向一致且離盤更遠才推薦。")

    return {
        "source": "postgame_review",
        "days": len(recent_days),
        "games": len(games),
        "winner_accuracy": round(winner_accuracy, 4),
        "low_confidence_accuracy": round(low_conf_accuracy, 4),
        "score_total_bias": round(total_bias, 3),
        "score_total_bias_applied": applied_bias,
        "winner_min_confidence": winner_min_confidence,
        "totals_min_edge": totals_min_edge,
        "totals_min_prob": totals_min_prob,
        "totals_min_line_gap": totals_min_line_gap,
        "mismatch_notes": mismatch_notes,
        "notes": notes or ["近十天檢討未觸發額外校正，維持原門檻。"],
    }
