#!/usr/bin/env python3
"""Fill MLB odds from Taiwan Sports Lottery official sportsbook JSON.

The official pre-match feed exposes fractional prices as pd/pu. Taiwan Sports
Lottery displays these as decimal odds, where decimal = 1 + pu / pd.
"""

from __future__ import annotations

import argparse
import csv
import json
import urllib.request
from datetime import date, datetime
from pathlib import Path

from prepare_odds_template import FIELDS, ODDS_CSV, write_template


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
ODDS_DIR = DATA_DIR / "odds"
SOURCE_JSON = ODDS_DIR / "taiwan_sportslottery_baseball_source_{date}.json"
BASEBALL_GAMES_URL = "https://blob3rd.sportslottery.com.tw/apidata/Pre/34731.1-Games.zh.json"
TEAM_ALIASES = {
    "辛辛那堤紅人": "辛辛那提紅人",
    "堪薩斯皇家": "堪薩斯市皇家",
    "亞歷桑那響尾蛇": "亞利桑那響尾蛇",
    "科羅拉多落磯": "科羅拉多洛磯",
}


def normalize_team_name(name: str) -> str:
    text = str(name or "").strip()
    return TEAM_ALIASES.get(text, text)


def request_json(url: str, timeout: int = 30) -> list[dict]:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "betting-plan-taiwan-sportslottery/1.0",
            "Referer": "https://www.sportslottery.com.tw/",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def decimal_odds(selection: dict) -> str:
    pd = float(selection["pd"])
    pu = float(selection["pu"])
    value = 1 + pu / pd
    return f"{value:.2f}"


def winner_market(game: dict) -> dict | None:
    for market in game.get("ms", []):
        if market.get("name") == "不讓分" or market.get("ti") == "354":
            return market
    return None


def extract_official_odds(games: list[dict]) -> dict[tuple[str, str], dict]:
    records = {}
    for game in games:
        market = winner_market(game)
        if not market:
            continue
        away_zh = normalize_team_name(game.get("an", ""))
        home_zh = normalize_team_name(game.get("hn", ""))
        if not away_zh or not home_zh:
            continue
        away_odds = ""
        home_odds = ""
        for selection in market.get("cs", []):
            side = selection.get("v")
            if not selection.get("pd") or not selection.get("pu"):
                continue
            if side == "A":
                away_odds = decimal_odds(selection)
            elif side == "H":
                home_odds = decimal_odds(selection)
        if away_odds and home_odds:
            records[(away_zh, home_zh)] = {
                "sportsbook": "台灣運彩",
                "away_moneyline": away_odds,
                "home_moneyline": home_odds,
                "official_event_id": game.get("id", ""),
                "event_no": game.get("no", ""),
                "kickoff_tw": game.get("kt", ""),
                "market_id": market.get("id", ""),
                "market_updated_at_tw": market.get("ss", ""),
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


def fill_odds(target_date: str, overwrite_template: bool, overwrite_existing: bool) -> dict:
    odds_path = Path(str(ODDS_CSV).format(date=target_date))
    if not odds_path.exists():
        write_template(target_date, overwrite=False)
    elif overwrite_template:
        write_template(target_date, overwrite=True)

    games = request_json(BASEBALL_GAMES_URL)
    source_path = Path(str(SOURCE_JSON).format(date=target_date))
    source_path.write_text(json.dumps(games, ensure_ascii=False, indent=2), encoding="utf-8")
    official_odds = extract_official_odds(games)

    rows = read_rows(odds_path)
    filled = 0
    preserved = 0
    missing = []
    captured_at_tw = datetime.now().isoformat(timespec="seconds")
    for row in rows:
        key = (normalize_team_name(row.get("away_zh", "")), normalize_team_name(row.get("home_zh", "")))
        odds = official_odds.get(key)
        if not odds:
            missing.append({"game_pk": row.get("game_pk", ""), "matchup_zh": f"{key[0]} @ {key[1]}"})
            continue
        if not overwrite_existing and row.get("away_moneyline") and row.get("home_moneyline"):
            preserved += 1
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
        "preserved": preserved,
        "rows": len(rows),
        "missing": missing,
        "official_games": len(games),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fill MLB odds CSV from Taiwan Sports Lottery official odds.")
    parser.add_argument("--date", default=date.today().isoformat(), help="Target MLB date in YYYY-MM-DD.")
    parser.add_argument("--overwrite-template", action="store_true")
    parser.add_argument("--keep-existing", action="store_true", help="Do not replace already-filled odds rows.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = fill_odds(
        target_date=args.date,
        overwrite_template=args.overwrite_template,
        overwrite_existing=not args.keep_existing,
    )
    print(f"wrote {result['odds_csv']}")
    print(f"wrote {result['source_json']}")
    print(
        f"filled={result['filled']} preserved={result['preserved']} rows={result['rows']} "
        f"official_games={result['official_games']} missing={len(result['missing'])}"
    )
    for row in result["missing"]:
        print(f"missing Taiwan Sports Lottery odds: {row['game_pk']} {row['matchup_zh']}")


if __name__ == "__main__":
    main()
