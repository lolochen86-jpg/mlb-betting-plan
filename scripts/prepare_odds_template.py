#!/usr/bin/env python3
"""Create a date-specific real moneyline odds template from daily predictions."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
ODDS_DIR = DATA_DIR / "odds"
PREDICTIONS_JSON = DATA_DIR / "daily_predictions_{date}.json"
ODDS_CSV = ODDS_DIR / "mlb_moneyline_{date}.csv"

FIELDS = [
    "date",
    "game_pk",
    "sportsbook",
    "captured_at_tw",
    "away_zh",
    "home_zh",
    "away_moneyline",
    "home_moneyline",
]


def build_rows(target_date: str) -> list[dict]:
    prediction_path = Path(str(PREDICTIONS_JSON).format(date=target_date))
    if not prediction_path.exists():
        raise SystemExit(f"Missing prediction file: {prediction_path}")
    data = json.loads(prediction_path.read_text(encoding="utf-8"))
    rows = []
    for prediction in data.get("all_predictions", []):
        rows.append(
            {
                "date": target_date,
                "game_pk": prediction.get("game_pk", ""),
                "sportsbook": "",
                "captured_at_tw": "",
                "away_zh": prediction.get("away_zh", ""),
                "home_zh": prediction.get("home_zh", ""),
                "away_moneyline": "",
                "home_moneyline": "",
            }
        )
    rows.sort(key=lambda row: int(row["game_pk"] or 0))
    return rows


def write_template(target_date: str, overwrite: bool) -> Path:
    ODDS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = Path(str(ODDS_CSV).format(date=target_date))
    if output_path.exists() and not overwrite:
        raise SystemExit(f"Odds template already exists: {output_path}\nUse --overwrite to regenerate it.")
    rows = build_rows(target_date)
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare a date-specific MLB moneyline odds template.")
    parser.add_argument("--date", default=date.today().isoformat(), help="Target date in YYYY-MM-DD.")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    path = write_template(args.date, args.overwrite)
    print(f"wrote {path}")
    print("Fill sportsbook, captured_at_tw, away_moneyline, and home_moneyline with real odds before ROI settlement.")


if __name__ == "__main__":
    main()
