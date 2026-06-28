#!/usr/bin/env python3
"""Fetch real MLB final scores from the public MLB Stats API."""

from __future__ import annotations

import argparse
import csv
import json
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

from name_localization import player_zh, team_zh


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DEFAULT_START = "2025-03-27"
DEFAULT_END = date.today().isoformat()
MLB_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"


def request_json(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "betting-plan-real-data/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_schedule(start_date: str, end_date: str) -> dict:
    params = {
        "sportId": "1",
        "startDate": start_date,
        "endDate": end_date,
        "hydrate": "team,linescore,probablePitcher",
    }
    url = f"{MLB_SCHEDULE_URL}?{urllib.parse.urlencode(params)}"
    return request_json(url)


def date_chunks(start_date: str, end_date: str, days: int) -> list[tuple[str, str]]:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    chunks = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=days - 1), end)
        chunks.append((cursor.isoformat(), chunk_end.isoformat()))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def fetch_schedule_chunked(start_date: str, end_date: str, chunk_days: int, sleep_seconds: float) -> tuple[dict, list[dict]]:
    merged = {"dates": []}
    chunk_meta = []
    seen_dates = set()
    for chunk_start, chunk_end in date_chunks(start_date, end_date, chunk_days):
        payload = fetch_schedule(chunk_start, chunk_end)
        dates = payload.get("dates", [])
        chunk_meta.append({"start": chunk_start, "end": chunk_end, "dates": len(dates)})
        print(f"fetched {chunk_start} -> {chunk_end}: dates={len(dates)}")
        for day in dates:
            day_key = day.get("date")
            if day_key in seen_dates:
                continue
            seen_dates.add(day_key)
            merged["dates"].append(day)
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
    merged["dates"].sort(key=lambda day: day.get("date", ""))
    return merged, chunk_meta


def normalize_games(payload: dict, allowed_game_types: set[str]) -> list[dict]:
    rows: list[dict] = []
    for day in payload.get("dates", []):
        game_date = day.get("date")
        for game in day.get("games", []):
            game_type = game.get("gameType", "")
            if allowed_game_types and game_type not in allowed_game_types:
                continue
            status = game.get("status", {})
            detailed_state = status.get("detailedState", "")
            coded_state = status.get("codedGameState", "")
            home = game.get("teams", {}).get("home", {})
            away = game.get("teams", {}).get("away", {})
            if "score" not in home or "score" not in away:
                continue
            home_score = int(home["score"])
            away_score = int(away["score"])
            home_team = home.get("team", {})
            away_team = away.get("team", {})
            home_pitcher = home.get("probablePitcher", {}) or {}
            away_pitcher = away.get("probablePitcher", {}) or {}
            rows.append(
                {
                    "date": game_date,
                    "game_pk": game.get("gamePk"),
                    "game_type": game_type,
                    "status": detailed_state,
                    "coded_state": coded_state,
                    "home_team_id": home_team.get("id"),
                    "home": home_team.get("name", ""),
                    "home_zh": team_zh(home_team.get("name", "")),
                    "away_team_id": away_team.get("id"),
                    "away": away_team.get("name", ""),
                    "away_zh": team_zh(away_team.get("name", "")),
                    "home_score": home_score,
                    "away_score": away_score,
                    "home_win": home_score > away_score,
                    "home_probable_pitcher_id": home_pitcher.get("id"),
                    "home_probable_pitcher": home_pitcher.get("fullName", ""),
                    "home_probable_pitcher_zh": player_zh(home_pitcher.get("fullName", "")),
                    "away_probable_pitcher_id": away_pitcher.get("id"),
                    "away_probable_pitcher": away_pitcher.get("fullName", ""),
                    "away_probable_pitcher_zh": player_zh(away_pitcher.get("fullName", "")),
                }
            )
    rows.sort(key=lambda r: (r["date"], int(r["game_pk"] or 0)))
    return rows


def write_outputs(
    rows: list[dict],
    payload: dict,
    start_date: str,
    end_date: str,
    game_types: list[str],
    chunk_meta: list[dict],
) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    csv_path = DATA_DIR / "real_mlb_games.csv"
    json_path = DATA_DIR / "real_mlb_games.json"
    provenance_path = DATA_DIR / "real_data_provenance.json"
    fields = [
        "date",
        "game_pk",
        "game_type",
        "status",
        "coded_state",
        "home_team_id",
        "home",
        "home_zh",
        "away_team_id",
        "away",
        "away_zh",
        "home_score",
        "away_score",
        "home_win",
        "home_probable_pitcher_id",
        "home_probable_pitcher",
        "home_probable_pitcher_zh",
        "away_probable_pitcher_id",
        "away_probable_pitcher",
        "away_probable_pitcher_zh",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    provenance = {
        "source": "MLB Stats API schedule endpoint",
        "url": MLB_SCHEDULE_URL,
        "sport_id": 1,
        "start_date": start_date,
        "end_date": end_date,
        "game_types": game_types,
        "actual_first_game_date": rows[0]["date"] if rows else None,
        "actual_last_game_date": rows[-1]["date"] if rows else None,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "raw_dates": len(payload.get("dates", [])),
        "fetch_chunks": chunk_meta,
        "games_written": len(rows),
        "coverage_warning": None
        if rows and rows[-1]["date"] == end_date
        else f"Requested through {end_date}, but MLB Stats API returned saved final-score rows only through {rows[-1]['date'] if rows else 'N/A'}.",
        "note": "Scores, teams, game ids, statuses, and probable pitcher names are fetched from MLB Stats API. Odds remain configured separately.",
    }
    provenance_path.write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {csv_path} ({len(rows)} games)")
    print(f"wrote {json_path}")
    print(f"wrote {provenance_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch real MLB final scores from MLB Stats API.")
    parser.add_argument("--start-date", default=DEFAULT_START, help="YYYY-MM-DD, default: 2025-03-27")
    parser.add_argument("--end-date", default=DEFAULT_END, help="YYYY-MM-DD, default: today")
    parser.add_argument("--chunk-days", type=int, default=31, help="Fetch schedule in date chunks to avoid long-range API truncation.")
    parser.add_argument(
        "--game-types",
        default="R",
        help="Comma-separated MLB gameType codes to keep. Default R = regular season.",
    )
    parser.add_argument("--sleep", type=float, default=0.0, help="Optional delay after fetch for polite scripted runs.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload, chunk_meta = fetch_schedule_chunked(args.start_date, args.end_date, args.chunk_days, args.sleep)
    game_types = [item.strip() for item in args.game_types.split(",") if item.strip()]
    rows = normalize_games(payload, set(game_types))
    write_outputs(rows, payload, args.start_date, args.end_date, game_types, chunk_meta)


if __name__ == "__main__":
    main()
