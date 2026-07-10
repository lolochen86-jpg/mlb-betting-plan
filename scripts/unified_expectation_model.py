#!/usr/bin/env python3
"""Unified expected-runs model shared by winner, score, and totals decisions."""

from __future__ import annotations

import argparse
import json
import math
import re
import urllib.request
from datetime import datetime
from pathlib import Path

from generate_game_simulator import team_profiles
from postgame_calibration import load_recent_calibration
from run_real_mlb_backtest import DEFAULT_GAMES_CSV, load_games


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DAILY_JSON = DATA_DIR / "daily_predictions_{date}.json"
UNIFIED_JSON = DATA_DIR / "unified_expectations_{date}.json"
MLB_API = "https://statsapi.mlb.com/api/v1"


def request_json(url: str, timeout: int = 8) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "unified-expectation-model/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def sigmoid(value: float) -> float:
    return 1 / (1 + math.exp(-value))


def lineup_factor(lineup: list[dict]) -> tuple[float, dict]:
    if not lineup:
        return 1.0, {"source_quality": "missing_lineup", "factor": 1.0}
    weighted = 0.0
    weight_sum = 0.0
    for player in lineup[:9]:
        slot = int(player.get("batting_order") or 9)
        slot_weight = max(0.65, 1.15 - (slot - 1) * 0.055)
        hitter = (
            0.42 * float(player.get("contact") or 1)
            + 0.38 * float(player.get("power") or 1)
            + 0.20 * float(player.get("patience") or 1)
            - 0.06 * (float(player.get("gidp") or 1) - 1)
        )
        weighted += hitter * slot_weight
        weight_sum += slot_weight
    factor = clamp(weighted / weight_sum if weight_sum else 1.0, 0.82, 1.22)
    return factor, {"source_quality": lineup[0].get("source", "lineup"), "factor": round(factor, 3)}


def weather_context(game_pk: str, fetch_live: bool = False) -> dict:
    if not fetch_live:
        return {"source": "not_connected", "factor": 0.0, "note": "real weather feed not connected in fast daily run"}
    try:
        payload = request_json(f"{MLB_API}/game/{game_pk}/feed/live")
    except Exception as exc:
        return {"source": "missing", "factor": 0.0, "note": f"weather unavailable: {exc}"}
    weather = ((payload.get("gameData") or {}).get("weather") or {})
    condition = str(weather.get("condition") or "")
    wind = str(weather.get("wind") or "")
    temp_text = str(weather.get("temp") or "")
    factor = 0.0
    temp_match = re.search(r"-?\d+", temp_text)
    if temp_match:
        temp = int(temp_match.group(0))
        if temp >= 82:
            factor += 0.15
        elif temp <= 50:
            factor -= 0.10
    wind_lower = wind.lower()
    if "out" in wind_lower:
        factor += 0.20
    elif "in" in wind_lower:
        factor -= 0.15
    if "dome" in condition.lower() or "roof closed" in condition.lower():
        factor *= 0.35
    return {
        "source": "mlb_feed_weather" if weather else "missing",
        "condition": condition,
        "wind": wind,
        "temp": temp_text,
        "factor": round(clamp(factor, -0.35, 0.35), 3),
    }


def bullpen_usage(team_id: int | str | None, target_date: str) -> dict:
    try:
        tid = int(team_id or 0)
    except (TypeError, ValueError):
        tid = 0
    if not tid:
        return {"source": "missing_team_id", "recent_ip": 0.0, "fatigue_runs": 0.0}
    try:
        from mlb_player_context import _team_recent_game_pks, _team_box_from_game

        game_pks = _team_recent_game_pks(tid, target_date, 4, 3)
        pitcher_ids = set()
        relief_pitchers = 0
        for game_pk in game_pks:
            box = _team_box_from_game(game_pk, tid)
            for pitcher_id in (box.get("pitchers") or [])[1:]:
                pitcher_ids.add(str(pitcher_id))
                relief_pitchers += 1
        fatigue_runs = clamp((relief_pitchers - 8) * 0.045, -0.10, 0.35)
        return {
            "source": "recent_boxscore_pitcher_usage",
            "recent_games": len(game_pks),
            "recent_relief_pitcher_appearances": relief_pitchers,
            "unique_recent_relievers": len(pitcher_ids),
            "fatigue_runs": round(fatigue_runs, 3),
        }
    except Exception as exc:
        return {"source": "missing", "recent_ip": 0.0, "fatigue_runs": 0.0, "note": str(exc)}


def load_daily(target_date: str) -> dict:
    path = Path(str(DAILY_JSON).format(date=target_date))
    if not path.exists():
        raise SystemExit(f"Missing daily predictions: {path}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def team_base_runs(profile: dict, opponent_profile: dict) -> float:
    return 0.58 * float(profile.get("offense", 4.45)) + 0.42 * float(opponent_profile.get("prevention", 4.45))


def pitcher_runs_modifier(opposing_pitcher: dict) -> float:
    return clamp((float(opposing_pitcher.get("run_prevention_factor", 1.0)) - 1.0) * 1.15, -0.55, 0.65)


def build_report(target_date: str, fetch_weather: bool = False, fetch_deep_context: bool = False) -> dict:
    daily = load_daily(target_date)
    history = [game for game in load_games(DEFAULT_GAMES_CSV) if game["date"] < target_date]
    profiles = team_profiles(history)
    calibration = load_recent_calibration(10)
    score_bias = float(calibration.get("score_total_bias_applied") or 0)
    season = target_date[:4]
    games = []

    for row in daily.get("all_predictions", []):
        away = row.get("away_zh", "")
        home = row.get("home_zh", "")
        game_pk = str(row.get("game_pk") or "")
        missing = []

        away_lineup = []
        home_lineup = []
        lineup_source = "not_connected_fast_run"
        if fetch_deep_context:
            from mlb_player_context import fetch_game_player_context, fetch_projected_lineup

            try:
                context = fetch_game_player_context(game_pk, season)
                away_lineup = context.get("away_lineup") or []
                home_lineup = context.get("home_lineup") or []
                lineup_source = context.get("lineup_source", "official_or_boxscore")
            except Exception:
                pass
            if not away_lineup or not home_lineup:
                try:
                    away_lineup = fetch_projected_lineup(row.get("away_team_id"), season, target_date)
                    home_lineup = fetch_projected_lineup(row.get("home_team_id"), season, target_date)
                    lineup_source = "projected_recent_lineup_order"
                except Exception as exc:
                    missing.append(f"lineup: {exc}")
        if not away_lineup or not home_lineup:
            missing.append("official_or_projected_lineup")

        away_lineup_factor, away_lineup_meta = lineup_factor(away_lineup)
        home_lineup_factor, home_lineup_meta = lineup_factor(home_lineup)

        away_pitcher_weight = float(row.get("away_pitcher_weight") or 0)
        home_pitcher_weight = float(row.get("home_pitcher_weight") or 0)
        if away_pitcher_weight <= 0 or home_pitcher_weight <= 0:
            missing.append("probable_pitcher_profile")

        weather = weather_context(game_pk, fetch_weather)
        if weather.get("source") in {"missing", "not_connected"}:
            missing.append("real_weather")
        if fetch_deep_context:
            away_bullpen = bullpen_usage(row.get("away_team_id"), target_date)
            home_bullpen = bullpen_usage(row.get("home_team_id"), target_date)
        else:
            away_bullpen = {"source": "not_connected_fast_run", "fatigue_runs": 0.0}
            home_bullpen = {"source": "not_connected_fast_run", "fatigue_runs": 0.0}
        if away_bullpen.get("source") in {"missing", "not_connected_fast_run"} or home_bullpen.get("source") in {"missing", "not_connected_fast_run"}:
            missing.append("bullpen_recent_usage")
        missing.append("injury_absence_feed")

        away_profile = profiles.get(away, {"offense": 4.45, "prevention": 4.45})
        home_profile = profiles.get(home, {"offense": 4.45, "prevention": 4.45})
        away_raw = (
            team_base_runs(away_profile, home_profile) * away_lineup_factor
            + 0.10 * (1 - home_pitcher_weight)
            + float(home_bullpen.get("fatigue_runs") or 0)
        )
        home_raw = (
            team_base_runs(home_profile, away_profile) * home_lineup_factor
            + 0.10 * (1 - away_pitcher_weight)
            + float(away_bullpen.get("fatigue_runs") or 0)
            + 0.10
        )
        weather_split = float(weather.get("factor") or 0) / 2
        bias_split = score_bias / 2
        away_runs = clamp(away_raw + weather_split + bias_split, 1.5, 8.5)
        home_runs = clamp(home_raw + weather_split + bias_split, 1.5, 8.5)
        total = away_runs + home_runs
        home_prob = clamp(sigmoid((home_runs - away_runs) / 1.85), 0.30, 0.70)
        pick_side = "home" if home_prob >= 0.5 else "away"
        games.append(
            {
                "date": target_date,
                "game_pk": game_pk,
                "game_time_tw": row.get("game_time_tw", ""),
                "game_time_utc": row.get("game_time_utc", ""),
                "matchup_zh": row.get("matchup_zh", f"{away} @ {home}"),
                "away_zh": away,
                "home_zh": home,
                "away_expected_runs": round(away_runs, 2),
                "home_expected_runs": round(home_runs, 2),
                "expected_total": round(total, 2),
                "pick_side": pick_side,
                "prediction_zh": home if pick_side == "home" else away,
                "home_win_prob": round(home_prob, 4),
                "away_win_prob": round(1 - home_prob, 4),
                "lineup_source": lineup_source,
                "away_lineup_factor": away_lineup_meta,
                "home_lineup_factor": home_lineup_meta,
                "away_pitcher_weight": away_pitcher_weight,
                "home_pitcher_weight": home_pitcher_weight,
                "weather": weather,
                "away_bullpen": away_bullpen,
                "home_bullpen": home_bullpen,
                "score_bias_applied": score_bias,
                "missing_data": sorted(set(missing)),
                "data_quality": "可用" if len(set(missing)) <= 2 else "需補資料",
                "model_version": "unified_expected_runs_v1",
            }
        )
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "target_date": target_date,
        "model": "統一得分期望 v1",
        "postgame_calibration": calibration,
        "summary": {
            "games": len(games),
            "missing_weather": sum(1 for game in games if "real_weather" in game["missing_data"]),
            "missing_lineup": sum(1 for game in games if "official_or_projected_lineup" in game["missing_data"]),
            "missing_bullpen": sum(1 for game in games if "bullpen_recent_usage" in game["missing_data"]),
        },
        "games": games,
    }


def write_outputs(report: dict) -> None:
    path = Path(str(UNIFIED_JSON).format(date=report["target_date"]))
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {path}")
    print(f"unified_games={report['summary']['games']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate unified expected-runs model.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--fetch-weather", action="store_true", help="Fetch per-game MLB live feed weather; slower and may be unavailable before games.")
    parser.add_argument("--fetch-deep-context", action="store_true", help="Fetch lineups and bullpen usage; slower.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    write_outputs(build_report(args.date, args.fetch_weather, args.fetch_deep_context))
