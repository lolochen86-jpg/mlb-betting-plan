#!/usr/bin/env python3
"""Shared schedule-time helpers for generated MLB reports."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DAILY_PREDICTIONS_JSON = DATA_DIR / "daily_predictions_{date}.json"
UNKNOWN_TIME = "未公布"


def normalize_game_time_tw(value: str, mlb_date: str = "") -> str:
    text = str(value or "").strip()
    if not text or text == UNKNOWN_TIME:
        return UNKNOWN_TIME
    if len(text) >= 16 and text[4] == "-" and text[7] == "-":
        return text[:16]
    if len(text) >= 11 and "/" in text and ":" in text:
        try:
            month_day, clock = text.split(" ", 1)
            month, day = [int(part) for part in month_day.split("/", 1)]
            year = int((mlb_date or str(date.today()))[:4])
            return f"{year:04d}-{month:02d}-{day:02d} {clock[:5]}"
        except Exception:
            return text
    return text


def load_time_index(target_date: str) -> dict[str, dict]:
    path = Path(str(DAILY_PREDICTIONS_JSON).format(date=target_date))
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    rows = data.get("all_predictions", [])
    return {
        str(row.get("game_pk", "")): {
            "game_time_tw": normalize_game_time_tw(row.get("game_time_tw") or UNKNOWN_TIME, target_date),
            "game_time_utc": row.get("game_time_utc") or "",
        }
        for row in rows
        if str(row.get("game_pk", ""))
    }


def attach_game_time(row: dict, time_index: dict[str, dict]) -> dict:
    game_pk = str(row.get("game_pk", ""))
    time_row = time_index.get(game_pk, {})
    row["game_time_tw"] = normalize_game_time_tw(row.get("game_time_tw") or time_row.get("game_time_tw", UNKNOWN_TIME), row.get("date", ""))
    row["game_time_utc"] = row.get("game_time_utc") or time_row.get("game_time_utc", "")
    return row


def time_sort_key(row: dict) -> tuple[str, str]:
    return (str(row.get("game_time_utc", "")), str(row.get("game_pk", "")))
