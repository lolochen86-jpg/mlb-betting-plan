#!/usr/bin/env python3
"""Run the daily MLB prediction, settlement, odds, and ROI workflow."""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_step(label: str, args: list[str], allow_fail: bool = False) -> int:
    print(f"\n== {label} ==")
    print(" ".join(args))
    completed = subprocess.run(args, cwd=ROOT)
    if completed.returncode != 0 and not allow_fail:
        raise SystemExit(completed.returncode)
    if completed.returncode != 0:
        print(f"warning: {label} exited with {completed.returncode}")
    return completed.returncode


def odds_csv_path(target_date: str) -> Path:
    return ROOT / "data" / "odds" / f"mlb_moneyline_{target_date}.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run daily MLB workflow.")
    parser.add_argument("--date", default=date.today().isoformat(), help="Prediction and odds date in YYYY-MM-DD.")
    parser.add_argument(
        "--settle-date",
        default=None,
        help="Settlement date in YYYY-MM-DD. Defaults to --date.",
    )
    parser.add_argument("--unit", type=float, default=100)
    parser.add_argument("--min-edge", type=float, default=0.0)
    parser.add_argument(
        "--all-predictions",
        action="store_true",
        help="Track ROI for all predictions instead of high-confidence only.",
    )
    parser.add_argument(
        "--skip-odds-fetch",
        action="store_true",
        help="Do not fetch ESPN moneyline odds; use the existing local odds CSV.",
    )
    parser.add_argument(
        "--skip-backtest-refresh",
        action="store_true",
        help="Skip accuracy/backtest/dashboard refresh and only run daily operational files.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    target_date = args.date
    settle_date = args.settle_date or args.date
    py = sys.executable

    if not args.skip_backtest_refresh:
        run_step("真實預測準確率", [py, "scripts/run_real_mlb_prediction_accuracy.py"])
        run_step("固定賠率參考回測", [py, "scripts/run_real_mlb_backtest.py"])

    run_step("每日勝方預測", [py, "scripts/generate_daily_plan.py", "--date", target_date])
    run_step("賽果結算", [py, "scripts/settle_daily_predictions.py", "--date", settle_date])
    if odds_csv_path(target_date).exists():
        print(f"\n== 產生盤口填寫檔 ==\nexisting file kept: {odds_csv_path(target_date)}")
    else:
        run_step("產生盤口填寫檔", [py, "scripts/prepare_odds_template.py", "--date", target_date])

    if not args.skip_odds_fetch:
        run_step("ESPN 真實 moneyline 備援回填", [py, "scripts/fetch_espn_moneyline_odds.py", "--date", target_date])
        run_step("台灣運彩官方賠率回填", [py, "scripts/fetch_taiwan_sportslottery_odds.py", "--date", target_date])

    run_step(
        "盤口檢查",
        [py, "scripts/validate_odds_file.py", "--date", target_date, "--allow-partial"],
        allow_fail=True,
    )
    roi_args = [
        py,
        "scripts/settle_betting_roi.py",
        "--date",
        target_date,
        "--unit",
        str(args.unit),
        "--min-edge",
        str(args.min_edge),
        "--require-sportsbook",
        "台灣運彩",
    ]
    if args.all_predictions:
        roi_args.append("--all-predictions")
    run_step("投注 ROI 更新", roi_args)
    run_step("今日投注單", [py, "scripts/generate_betting_ticket.py", "--date", target_date])
    run_step("大小分 v1", [py, "scripts/run_totals_v1.py", "--date", target_date])
    run_step("進階因子勝方 v1", [py, "scripts/run_advanced_factors_model.py", "--date", target_date])
    run_step("首頁重建", [py, "scripts/generate_plan.py"])
    run_step("狀態報告", [py, "scripts/generate_status_report.py"])
    print("\nworkflow completed")


if __name__ == "__main__":
    main()
