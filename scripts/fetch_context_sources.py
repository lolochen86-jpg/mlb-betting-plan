#!/usr/bin/env python3
"""Best-effort fetch for weather and bullpen context caches."""

from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

from unified_expectation_model import BULLPEN_CACHE_JSON, DAILY_JSON, WEATHER_CACHE_JSON, bullpen_usage


ROOT = Path(__file__).resolve().parents[1]


VENUE_WEATHER_BY_TEAM_ZH = {
    "亞利桑那響尾蛇": {"venue": "Chase Field", "lat": 33.4455, "lon": -112.0667, "roof": True},
    "亞特蘭大勇士": {"venue": "Truist Park", "lat": 33.8908, "lon": -84.4678, "roof": False},
    "巴爾的摩金鶯": {"venue": "Oriole Park at Camden Yards", "lat": 39.2839, "lon": -76.6217, "roof": False},
    "波士頓紅襪": {"venue": "Fenway Park", "lat": 42.3467, "lon": -71.0972, "roof": False},
    "芝加哥白襪": {"venue": "Rate Field", "lat": 41.83, "lon": -87.6339, "roof": False},
    "芝加哥小熊": {"venue": "Wrigley Field", "lat": 41.9484, "lon": -87.6553, "roof": False},
    "辛辛那提紅人": {"venue": "Great American Ball Park", "lat": 39.0979, "lon": -84.5082, "roof": False},
    "克里夫蘭守護者": {"venue": "Progressive Field", "lat": 41.4962, "lon": -81.6852, "roof": False},
    "科羅拉多洛磯": {"venue": "Coors Field", "lat": 39.7561, "lon": -104.9942, "roof": False},
    "底特律老虎": {"venue": "Comerica Park", "lat": 42.339, "lon": -83.0485, "roof": False},
    "休士頓太空人": {"venue": "Daikin Park", "lat": 29.7573, "lon": -95.3555, "roof": True},
    "堪薩斯市皇家": {"venue": "Kauffman Stadium", "lat": 39.0517, "lon": -94.4803, "roof": False},
    "洛杉磯天使": {"venue": "Angel Stadium", "lat": 33.8003, "lon": -117.8827, "roof": False},
    "洛杉磯道奇": {"venue": "Dodger Stadium", "lat": 34.0739, "lon": -118.24, "roof": False},
    "邁阿密馬林魚": {"venue": "loanDepot park", "lat": 25.7781, "lon": -80.2197, "roof": True},
    "密爾瓦基釀酒人": {"venue": "American Family Field", "lat": 43.028, "lon": -87.9712, "roof": True},
    "明尼蘇達雙城": {"venue": "Target Field", "lat": 44.9817, "lon": -93.2776, "roof": False},
    "紐約大都會": {"venue": "Citi Field", "lat": 40.7571, "lon": -73.8458, "roof": False},
    "紐約洋基": {"venue": "Yankee Stadium", "lat": 40.8296, "lon": -73.9262, "roof": False},
    "運動家": {"venue": "Sutter Health Park", "lat": 38.5802, "lon": -121.513, "roof": False},
    "費城費城人": {"venue": "Citizens Bank Park", "lat": 39.9061, "lon": -75.1665, "roof": False},
    "匹茲堡海盜": {"venue": "PNC Park", "lat": 40.4469, "lon": -80.0057, "roof": False},
    "聖地牙哥教士": {"venue": "Petco Park", "lat": 32.7073, "lon": -117.1566, "roof": False},
    "舊金山巨人": {"venue": "Oracle Park", "lat": 37.7786, "lon": -122.3893, "roof": False},
    "西雅圖水手": {"venue": "T-Mobile Park", "lat": 47.5914, "lon": -122.3325, "roof": True},
    "聖路易紅雀": {"venue": "Busch Stadium", "lat": 38.6226, "lon": -90.1928, "roof": False},
    "坦帕灣光芒": {"venue": "Tropicana Field", "lat": 27.7682, "lon": -82.6534, "roof": True},
    "德州遊騎兵": {"venue": "Globe Life Field", "lat": 32.7473, "lon": -97.0842, "roof": True},
    "多倫多藍鳥": {"venue": "Rogers Centre", "lat": 43.6414, "lon": -79.3894, "roof": True},
    "華盛頓國民": {"venue": "Nationals Park", "lat": 38.873, "lon": -77.0074, "roof": False},
}


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {}


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def request_json(url: str, timeout: int = 12) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "mlb-betting-plan-weather/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def load_daily_rows(target_date: str) -> list[dict]:
    path = Path(str(DAILY_JSON).format(date=target_date))
    if not path.exists():
        raise SystemExit(f"Missing daily predictions: {path}")
    return read_json(path).get("all_predictions", [])


def time_left(started: float, max_seconds: int) -> bool:
    return time.monotonic() - started < max_seconds


def parse_game_time_utc(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def weather_factor(temp_c: float | None, humidity: float | None, wind_kmh: float | None, precip_prob: float | None, roof: bool) -> float:
    factor = 0.0
    if temp_c is not None:
        if temp_c >= 28:
            factor += 0.15
        elif temp_c <= 10:
            factor -= 0.10
    if humidity is not None and humidity >= 75 and temp_c is not None and temp_c >= 24:
        factor += 0.05
    if precip_prob is not None and precip_prob >= 50:
        factor -= 0.12
    if wind_kmh is not None and wind_kmh >= 22:
        factor += 0.04
    if roof:
        factor *= 0.35
    return round(max(-0.35, min(0.35, factor)), 3)


def nearest_hour_index(times: list[str], game_time: datetime) -> int | None:
    best = None
    best_delta = None
    for idx, value in enumerate(times):
        try:
            item = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        delta = abs((item - game_time).total_seconds())
        if best_delta is None or delta < best_delta:
            best = idx
            best_delta = delta
    return best


def open_meteo_weather(row: dict) -> dict:
    home = str(row.get("home_zh") or "")
    venue = VENUE_WEATHER_BY_TEAM_ZH.get(home)
    game_time = parse_game_time_utc(str(row.get("game_time_utc") or ""))
    if not venue:
        return {"source": "missing", "factor": 0.0, "note": f"missing venue coordinates for {home}"}
    if not game_time:
        return {"source": "missing", "factor": 0.0, "note": "missing game_time_utc"}
    params = {
        "latitude": str(venue["lat"]),
        "longitude": str(venue["lon"]),
        "hourly": "temperature_2m,relative_humidity_2m,precipitation_probability,wind_speed_10m,wind_direction_10m",
        "timezone": "UTC",
        "start_date": game_time.date().isoformat(),
        "end_date": game_time.date().isoformat(),
    }
    url = f"https://api.open-meteo.com/v1/forecast?{urllib.parse.urlencode(params)}"
    try:
        payload = request_json(url)
    except Exception as exc:
        return {"source": "missing", "factor": 0.0, "note": f"open-meteo unavailable: {exc}", "venue": venue["venue"]}
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    idx = nearest_hour_index(times, game_time)
    if idx is None:
        return {"source": "missing", "factor": 0.0, "note": "open-meteo missing hourly time", "venue": venue["venue"]}

    def at(key: str) -> float | None:
        values = hourly.get(key) or []
        try:
            return float(values[idx])
        except (IndexError, TypeError, ValueError):
            return None

    temp_c = at("temperature_2m")
    humidity = at("relative_humidity_2m")
    precip_prob = at("precipitation_probability")
    wind_kmh = at("wind_speed_10m")
    wind_dir = at("wind_direction_10m")
    return {
        "source": "open_meteo_forecast",
        "venue": venue["venue"],
        "home_team": home,
        "roof": bool(venue.get("roof")),
        "latitude": venue["lat"],
        "longitude": venue["lon"],
        "forecast_time_utc": times[idx],
        "temperature_c": temp_c,
        "humidity_pct": humidity,
        "precipitation_probability_pct": precip_prob,
        "wind_speed_kmh": wind_kmh,
        "wind_direction_deg": wind_dir,
        "factor": weather_factor(temp_c, humidity, wind_kmh, precip_prob, bool(venue.get("roof"))),
    }


def weather_is_usable(row: dict) -> bool:
    return str(row.get("source") or "") in {"open_meteo_forecast", "mlb_feed_weather"}


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
            if not game_pk or not time_left(started, max_seconds):
                continue
            if weather_is_usable(weather_cache.get(game_pk, {})):
                continue
            weather_cache[game_pk] = open_meteo_weather(row)
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
