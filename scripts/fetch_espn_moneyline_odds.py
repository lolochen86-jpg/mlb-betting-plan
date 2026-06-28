#!/usr/bin/env python3
"""Fill local MLB moneyline CSV from ESPN scoreboard odds.

ESPN currently exposes DraftKings moneyline odds in the scoreboard JSON for
some upcoming games. This script writes only odds that are present in that
public payload and leaves missing games blank.
"""

from __future__ import annotations

import argparse
import csv
import json
import urllib.request
from datetime import date, datetime
from pathlib import Path

from name_localization import team_zh
from prepare_odds_template import FIELDS, ODDS_CSV, write_template


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
ODDS_DIR = DATA_DIR / "odds"
SOURCE_JSON = ODDS_DIR / "espn_moneyline_source_{date}.json"
ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"


def request_json(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "betting-plan-espn-odds/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def espn_date(target_date: str) -> str:
    return target_date.replace("-", "")


def close_odds(odds: dict, side: str) -> str:
    value = odds.get("moneyline", {}).get(side, {}).get("close", {}).get("odds", "")
    return str(value).strip()


def extract_moneylines(payload: dict) -> dict[tuple[str, str], dict]:
    records = {}
    for event in payload.get("events", []):
        competition = (event.get("competitions") or [{}])[0]
        competitors = competition.get("competitors", [])
        team_by_home_away = {}
        for competitor in competitors:
            side = competitor.get("homeAway", "")
            team_name = competitor.get("team", {}).get("displayName", "")
            team_by_home_away[side] = team_name
        away_name = team_by_home_away.get("away", "")
        home_name = team_by_home_away.get("home", "")
        away_zh = team_zh(away_name)
        home_zh = team_zh(home_name)
        odds_list = competition.get("odds") or []
        if not odds_list or not away_zh or not home_zh:
            continue
        odds = odds_list[0]
        away_moneyline = close_odds(odds, "away")
        home_moneyline = close_odds(odds, "home")
        if not away_moneyline or not home_moneyline:
            continue
        provider = odds.get("provider", {}).get("displayName") or odds.get("provider", {}).get("name") or "ESPN"
        records[(away_zh, home_zh)] = {
            "sportsbook": provider,
            "away_moneyline": away_moneyline,
            "home_moneyline": home_moneyline,
            "espn_event_id": event.get("id", ""),
            "espn_short_name": event.get("shortName", ""),
            "details": odds.get("details", ""),
        }
    return records


def read_rows(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_rows(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def fill_odds(target_date: str, overwrite_template: bool) -> dict:
    odds_path = Path(str(ODDS_CSV).format(date=target_date))
    if not odds_path.exists():
        write_template(target_date, overwrite=False)
    elif overwrite_template:
        write_template(target_date, overwrite=True)
    url = f"{ESPN_SCOREBOARD_URL}?dates={espn_date(target_date)}"
    payload = request_json(url)
    source_path = Path(str(SOURCE_JSON).format(date=target_date))
    source_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    moneylines = extract_moneylines(payload)
    rows = read_rows(odds_path)
    filled = 0
    missing = []
    captured_at_tw = datetime.now().isoformat(timespec="seconds")
    for row in rows:
        key = (row.get("away_zh", ""), row.get("home_zh", ""))
        odds = moneylines.get(key)
        if not odds:
            missing.append({"game_pk": row.get("game_pk", ""), "matchup_zh": f"{key[0]} @ {key[1]}"})
            continue
        row["sportsbook"] = odds["sportsbook"]
        row["captured_at_tw"] = captured_at_tw
        row["away_moneyline"] = odds["away_moneyline"]
        row["home_moneyline"] = odds["home_moneyline"]
        filled += 1
    write_rows(odds_path, rows)
    return {
        "odds_csv": odds_path,
        "source_json": source_path,
        "filled": filled,
        "rows": len(rows),
        "missing": missing,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fill MLB moneyline CSV from ESPN scoreboard odds.")
    parser.add_argument("--date", default=date.today().isoformat(), help="Target date in YYYY-MM-DD.")
    parser.add_argument("--overwrite-template", action="store_true", help="Regenerate the local date template before filling.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = fill_odds(args.date, args.overwrite_template)
    print(f"wrote {result['odds_csv']}")
    print(f"wrote {result['source_json']}")
    print(f"filled={result['filled']} rows={result['rows']} missing={len(result['missing'])}")
    for row in result["missing"]:
        print(f"missing odds: {row['game_pk']} {row['matchup_zh']}")


if __name__ == "__main__":
    main()
