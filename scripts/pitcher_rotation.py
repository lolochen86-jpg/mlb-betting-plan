"""Infer probable starters from recent team rotation when MLB has not announced one."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import date


def _pitcher_fields(game: dict, side: str) -> tuple[object, str, str]:
    return (
        game.get(f"{side}_probable_pitcher_id") or game.get(f"{side}_pitcher_id"),
        str(game.get(f"{side}_probable_pitcher") or game.get(f"{side}_pitcher") or ""),
        str(game.get(f"{side}_probable_pitcher_zh") or game.get(f"{side}_pitcher_zh") or ""),
    )


def rotation_candidates(history: list[dict], team: str, target_date: str) -> list[dict]:
    appearances = []
    for game in history:
        game_date = str(game.get("date") or "")
        if not game_date or game_date >= target_date:
            continue
        for side in ("away", "home"):
            if str(game.get(f"{side}_zh") or "") != team:
                continue
            pitcher_id, name, name_zh = _pitcher_fields(game, side)
            if pitcher_id:
                appearances.append(
                    {
                        "date": game_date,
                        "pitcher_id": int(pitcher_id),
                        "name": name,
                        "name_zh": name_zh or name,
                    }
                )
    appearances.sort(key=lambda row: row["date"])
    recent = appearances[-15:]
    frequency = Counter(row["pitcher_id"] for row in recent)
    latest_by_pitcher = {}
    for row in recent:
        latest_by_pitcher[row["pitcher_id"]] = row

    target = date.fromisoformat(target_date)
    candidates = []
    for row in latest_by_pitcher.values():
        rest_days = (target - date.fromisoformat(row["date"])).days
        if rest_days < 4:
            continue
        rest_score = math.exp(-abs(rest_days - 5) / 2.0)
        stability = min(1.0, frequency[row["pitcher_id"]] / 3.0)
        confidence = max(0.35, min(0.75, 0.30 + 0.30 * rest_score + 0.15 * stability))
        candidates.append({**row, "rest_days": rest_days, "confidence": round(confidence, 3)})
    candidates.sort(
        key=lambda row: (
            abs(row["rest_days"] - 5),
            -frequency[row["pitcher_id"]],
            row["date"],
        )
    )
    return candidates


def enrich_probable_pitchers(schedule: list[dict], history: list[dict], target_date: str) -> list[dict]:
    team_cache: dict[str, list[dict]] = {}
    used_by_team: dict[str, set[int]] = defaultdict(set)
    for game in schedule:
        for side in ("away", "home"):
            team = str(game.get(f"{side}_zh") or "")
            id_key = f"{side}_probable_pitcher_id" if f"{side}_probable_pitcher_id" in game else f"{side}_pitcher_id"
            name_key = f"{side}_probable_pitcher" if f"{side}_probable_pitcher" in game else f"{side}_pitcher"
            zh_key = f"{side}_probable_pitcher_zh"
            pitcher_id = game.get(id_key)
            if pitcher_id:
                game[f"{side}_pitcher_source"] = "official_probable"
                game[f"{side}_pitcher_confidence"] = 1.0
                game[f"{side}_pitcher_weight"] = 0.90
                continue

            team_cache.setdefault(team, rotation_candidates(history, team, target_date))
            candidate = next(
                (row for row in team_cache[team] if row["pitcher_id"] not in used_by_team[team]),
                None,
            )
            if not candidate:
                game[f"{side}_pitcher_source"] = "league_average"
                game[f"{side}_pitcher_confidence"] = 0.0
                game[f"{side}_pitcher_weight"] = 0.0
                continue

            used_by_team[team].add(candidate["pitcher_id"])
            game[id_key] = candidate["pitcher_id"]
            game[name_key] = candidate["name"]
            if zh_key in game:
                game[zh_key] = candidate["name_zh"]
            game[f"{side}_pitcher_source"] = "inferred_rotation"
            game[f"{side}_pitcher_confidence"] = candidate["confidence"]
            game[f"{side}_pitcher_weight"] = candidate["confidence"]
            game[f"{side}_pitcher_rest_days"] = candidate["rest_days"]
    return schedule


def pitcher_display(name: str, source: str, confidence: float) -> str:
    if source == "inferred_rotation":
        return f"{name or '未知投手'}（輪值推估 {confidence * 100:.0f}%）"
    if source == "official_probable":
        return f"{name or '未公布'}（官方預告）"
    return "未公布（聯盟平均）"


def blend_pitcher_profile(profile: dict, weight: float) -> dict:
    weight = max(0.0, min(1.0, float(weight or 0)))
    defaults = {
        "era": 4.50,
        "whip": 1.350,
        "k_factor": 1.0,
        "bb_factor": 1.0,
        "hr_factor": 1.0,
        "gb_factor": 1.0,
        "run_prevention_factor": 1.0,
    }
    blended = dict(profile)
    for key, neutral in defaults.items():
        value = float(profile.get(key, neutral) or neutral)
        blended[key] = neutral + (value - neutral) * weight
    blended["pitcher_weight"] = weight
    return blended
