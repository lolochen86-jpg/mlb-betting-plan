#!/usr/bin/env python3
"""Best-effort fetch for weather and bullpen context caches."""

from __future__ import annotations

import argparse
import json
import time
from datetime import date
from pathlib import Path

from unified_expectation_model import BULLPEN_CACHE_JSON, DAILY_JSON, WEATHER_CACHE_JSON, bullpen_usage, weather_context


ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {}


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_daily_rows(target_date: str) -> list[dict]:
    path = Path(str(DAILY_JSON).format(date=target_date))
    if not path.exists():
        raise SystemExit(f"Missing daily predictions: {path}")
    return read_json(path).get("all_predictions", [])


def time_left(started: float, max_seconds: int) -> bool:
    return time.monotonic() - started < max_seconds


def build_context(target_date: str, max_seconds: int, fetch_weather: bool, fetch_bullpen: bool) -> dict:
    started = time.monotonic()
    rows = load_daily_rows(target_date)
    weather_path = Path(str(WEATHER_CACHE_JSON).format(date=target_date))
    bullpen_path = Path(str(BULLPEN_CACHE_JSON).format(date=target_date))
    weather_cache = read_json(weather_path)
    bullpen_cache = read_json(bullpen_path)

    if fetch_weather:
        for row in rows:
            game_pk = str(row.get("game_pk", ""))
            if not game_pk or game_pk in weather_cache or not time_left(started, max_seconds):
                continue
            weather_cache[game_pk] = weather_context(game_pk, fetch_live=True)
            write_json(weather_path, weather_cache)

    if fetch_bullpen:
        team_ids = []
        for row in rows:
            for key in ("away_team_id", "home_team_id"):
                team_id = str(row.get(key) or "")
                if team_id and team_id not in team_ids:
                    team_ids.append(team_id)
        for team_id in team_ids:
            if team_id in bullpen_cache or not time_left(started, max_seconds):
                continue
            bullpen_cache[team_id] = bullpen_usage(team_id, target_date)
            write_json(bullpen_path, bullpen_cache)

    return {
        "target_date": target_date,
        "weather_cached": len(weather_cache),
        "bullpen_cached": len(bullpen_cache),
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "weather_path": str(weather_path),
        "bullpen_path": str(bullpen_path),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch MLB context caches with a time budget.")
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--max-seconds", type=int, default=75)
    parser.add_argument("--skip-weather", action="store_true")
    parser.add_argument("--skip-bullpen", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    report = build_context(
        args.date,
        max(5, args.max_seconds),
        fetch_weather=not args.skip_weather,
        fetch_bullpen=not args.skip_bullpen,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
