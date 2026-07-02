#!/usr/bin/env python3
"""Export all available MLB Taiwan Sports Lottery pre-match markets.

The older odds fetcher keeps the existing moneyline CSV contract. This script
preserves every official market/selection in a normalized file so research
pages can compare more bet types without losing raw sportsbook fields.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from fetch_taiwan_sportslottery_odds import (
    BASEBALL_GAMES_URL,
    SOURCE_JSON,
    decimal_odds,
    normalize_team_name,
    request_json,
)


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
ODDS_DIR = DATA_DIR / "odds"
DAILY_PREDICTIONS_JSON = DATA_DIR / "daily_predictions_{date}.json"
MARKETS_JSON = ODDS_DIR / "taiwan_sportslottery_markets_{date}.json"
MARKETS_CSV = ODDS_DIR / "taiwan_sportslottery_markets_{date}.csv"
SPORTSBOOK = "台灣運彩"

FIELDS = [
    "date",
    "captured_at_tw",
    "sportsbook",
    "game_pk",
    "official_event_id",
    "event_no",
    "kickoff_tw",
    "away_zh",
    "home_zh",
    "matchup_zh",
    "market_id",
    "market_ti",
    "market_name",
    "selection_id",
    "selection_name",
    "selection_side",
    "selection_line",
    "odds_decimal",
    "pd",
    "pu",
    "raw_v",
    "raw_hv",
    "raw_cn",
]


def load_daily_match_index(target_date: str) -> dict[tuple[str, str], str]:
    path = Path(str(DAILY_PREDICTIONS_JSON).format(date=target_date))
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("all_predictions", [])
    return {
        (
            normalize_team_name(row.get("away_zh", "")),
            normalize_team_name(row.get("home_zh", "")),
        ): str(row.get("game_pk", ""))
        for row in rows
        if row.get("away_zh") and row.get("home_zh") and row.get("game_pk")
    }


def load_or_fetch_source(target_date: str, refresh: bool) -> list[dict]:
    source_path = Path(str(SOURCE_JSON).format(date=target_date))
    if source_path.exists() and not refresh:
        return json.loads(source_path.read_text(encoding="utf-8"))
    source_path.parent.mkdir(parents=True, exist_ok=True)
    games = request_json(BASEBALL_GAMES_URL)
    source_path.write_text(json.dumps(games, ensure_ascii=False, indent=2), encoding="utf-8")
    return games


def selection_line(selection: dict) -> str:
    for key in ("hv", "h", "p"):
        value = selection.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def selection_odds(selection: dict) -> str:
    if not selection.get("pd") or not selection.get("pu"):
        return ""
    try:
        return decimal_odds(selection)
    except Exception:
        return ""


def extract_rows(target_date: str, games: list[dict]) -> tuple[list[dict], dict]:
    captured_at_tw = datetime.now().isoformat(timespec="seconds")
    match_index = load_daily_match_index(target_date)
    rows: list[dict] = []
    matched_games = 0
    market_counter: Counter[str] = Counter()

    for game in games:
        away_zh = normalize_team_name(game.get("an", ""))
        home_zh = normalize_team_name(game.get("hn", ""))
        game_pk = match_index.get((away_zh, home_zh), "")
        if game_pk:
            matched_games += 1
        matchup = f"{away_zh} @ {home_zh}".strip()
        for market in game.get("ms", []):
            market_ti = str(market.get("ti", "") or "")
            market_name = str(market.get("name", "") or "")
            market_counter[f"{market_ti} {market_name}".strip()] += 1
            for selection in market.get("cs", []):
                rows.append(
                    {
                        "date": target_date,
                        "captured_at_tw": captured_at_tw,
                        "sportsbook": SPORTSBOOK,
                        "game_pk": game_pk,
                        "official_event_id": game.get("id", ""),
                        "event_no": game.get("no", ""),
                        "kickoff_tw": game.get("kt", ""),
                        "away_zh": away_zh,
                        "home_zh": home_zh,
                        "matchup_zh": matchup,
                        "market_id": market.get("id", ""),
                        "market_ti": market_ti,
                        "market_name": market_name,
                        "selection_id": selection.get("id", ""),
                        "selection_name": selection.get("name", ""),
                        "selection_side": selection.get("v", ""),
                        "selection_line": selection_line(selection),
                        "odds_decimal": selection_odds(selection),
                        "pd": selection.get("pd", ""),
                        "pu": selection.get("pu", ""),
                        "raw_v": selection.get("v", ""),
                        "raw_hv": selection.get("hv", ""),
                        "raw_cn": selection.get("cn", ""),
                    }
                )

    rows.sort(
        key=lambda row: (
            row["kickoff_tw"],
            str(row["event_no"]),
            str(row["market_ti"]),
            str(row["market_id"]),
            str(row["raw_cn"]),
            row["selection_name"],
        )
    )
    summary = {
        "official_games": len(games),
        "matched_games": matched_games,
        "markets": sum(market_counter.values()),
        "unique_market_types": len(market_counter),
        "selections": len(rows),
        "market_counts": dict(sorted(market_counter.items(), key=lambda item: (-item[1], item[0]))),
    }
    return rows, summary


def write_outputs(target_date: str, rows: list[dict], summary: dict) -> dict:
    csv_path = Path(str(MARKETS_CSV).format(date=target_date))
    json_path = Path(str(MARKETS_JSON).format(date=target_date))
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "target_date": target_date,
        "sportsbook": SPORTSBOOK,
        "source": "Taiwan Sports Lottery official pre-match baseball JSON",
        "summary": summary,
        "markets": rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"csv": csv_path, "json": json_path}


def build_report(target_date: str, refresh: bool) -> dict:
    games = load_or_fetch_source(target_date, refresh=refresh)
    rows, summary = extract_rows(target_date, games)
    paths = write_outputs(target_date, rows, summary)
    return {"paths": paths, "summary": summary}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export every MLB Taiwan Sports Lottery market.")
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--refresh", action="store_true", help="Fetch the official source again instead of using local source JSON.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = build_report(args.date, refresh=args.refresh)
    print(f"wrote {report['paths']['csv']}")
    print(f"wrote {report['paths']['json']}")
    print(
        "official_games={official_games} matched_games={matched_games} markets={markets} "
        "unique_market_types={unique_market_types} selections={selections}".format(**report["summary"])
    )


if __name__ == "__main__":
    main()
