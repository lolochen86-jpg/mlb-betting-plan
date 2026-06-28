#!/usr/bin/env python3
"""Validate an MLB odds CSV before ROI settlement."""

from __future__ import annotations

import argparse
import csv
from datetime import date
from pathlib import Path

from settle_betting_roi import ODDS_CSV, REQUIRED_ODDS_FIELDS, parse_moneyline


def validate_file(path: Path, allow_partial: bool) -> tuple[list[str], list[str], int, int]:
    errors = []
    warnings = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing_fields = [field for field in REQUIRED_ODDS_FIELDS if field not in (reader.fieldnames or [])]
        if missing_fields:
            return [f"缺少必要欄位: {', '.join(missing_fields)}"], [], 0, 0
        row_count = 0
        filled_count = 0
        for line_no, row in enumerate(reader, start=2):
            if not any(row.values()):
                continue
            row_count += 1
            matchup = f"{row.get('away_zh', '')} @ {row.get('home_zh', '')}"
            odds_blank = not str(row.get("away_moneyline", "")).strip() and not str(row.get("home_moneyline", "")).strip()
            if odds_blank and allow_partial:
                warnings.append(f"{path}:{line_no} {matchup} 尚未填入盤口，ROI 結算會跳過此場")
                continue
            for field in ["date", "away_zh", "home_zh", "sportsbook", "captured_at_tw"]:
                if not str(row.get(field, "")).strip():
                    errors.append(f"{path}:{line_no} 欄位 {field} 空白")
            for field in ["away_moneyline", "home_moneyline"]:
                try:
                    parse_moneyline(row.get(field, ""))
                except ValueError as exc:
                    errors.append(f"{path}:{line_no} 欄位 {field} 無效: {exc}")
            if not any(error.startswith(f"{path}:{line_no} ") for error in errors):
                filled_count += 1
    return errors, warnings, row_count, filled_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a real MLB odds CSV.")
    parser.add_argument("--date", default=date.today().isoformat(), help="Target date in YYYY-MM-DD.")
    parser.add_argument("--file", type=Path, help="Optional odds CSV path.")
    parser.add_argument("--allow-partial", action="store_true", help="Allow completely blank odds rows and report them as warnings.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    path = args.file or Path(str(ODDS_CSV).format(date=args.date))
    if not path.exists():
        raise SystemExit(f"找不到盤口檔: {path}")
    errors, warnings, row_count, filled_count = validate_file(path, args.allow_partial)
    if errors:
        print(f"盤口檔檢查未通過：{path}")
        print(f"資料列數：{row_count}")
        for error in errors[:80]:
            print(f"- {error}")
        if len(errors) > 80:
            print(f"- 還有 {len(errors) - 80} 個錯誤未列出")
        raise SystemExit(1)
    print(f"盤口檔檢查通過：{path}")
    print(f"資料列數：{row_count}")
    print(f"已填盤口列數：{filled_count}")
    for warning in warnings[:40]:
        print(f"警告：{warning}")
    if len(warnings) > 40:
        print(f"警告：還有 {len(warnings) - 40} 個未列出")


if __name__ == "__main__":
    main()
