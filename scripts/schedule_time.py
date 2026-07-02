#!/usr/bin/env python3
"""Shared schedule-time helpers for generated MLB reports."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DAILY_PREDICTIONS_JSON = DATA_DIR / "daily_predictions_{date}.json"
UNKNOWN_TIME = "未公布"


def load_time_index(target_date: str) -> dict[str, dict]:
    path = Path(str(DAILY_PREDICTIONS_JSON).format(date=target_date))
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    rows = data.get("all_predictions", [])
    return {
        str(row.get("game_pk", "")): {
            "game_time_tw": row.get("game_time_tw") or UNKNOWN_TIME,
            "game_time_utc": row.get("game_time_utc") or "",
        }
        for row in rows
        if str(row.get("game_pk", ""))
    }


def attach_game_time(row: dict, time_index: dict[str, dict]) -> dict:
    game_pk = str(row.get("game_pk", ""))
    time_row = time_index.get(game_pk, {})
    row["game_time_tw"] = row.get("game_time_tw") or time_row.get("game_time_tw", UNKNOWN_TIME)
    row["game_time_utc"] = row.get("game_time_utc") or time_row.get("game_time_utc", "")
    return row


def time_sort_key(row: dict) -> tuple[str, str]:
    return (str(row.get("game_time_utc", "")), str(row.get("game_pk", "")))
