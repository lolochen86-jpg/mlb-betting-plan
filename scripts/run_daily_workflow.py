#!/usr/bin/env python3
"""Run the daily MLB prediction, settlement, odds, and ROI workflow."""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TAIWAN_SPORTSBOOK = "台灣運彩"


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


def prediction_json_path(target_date: str) -> Path:
    return ROOT / "data" / f"daily_predictions_{target_date}.json"


def previous_date(target_date: str) -> str:
    return (date.fromisoformat(target_date) - timedelta(days=1)).isoformat()


def date_range(start_date: str, end_date: str) -> list[str]:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    days = []
    cursor = start
    while cursor <= end:
        days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return days


def roi_args(py: str, target_date: str, unit: float, min_edge: float, all_predictions: bool) -> list[str]:
    args = [
        py,
        "scripts/settle_betting_roi.py",
        "--date",
        target_date,
        "--unit",
        str(unit),
        "--min-edge",
        str(min_edge),
        "--require-sportsbook",
        TAIWAN_SPORTSBOOK,
    ]
    if all_predictions:
        args.append("--all-predictions")
    return args


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run daily MLB workflow.")
    parser.add_argument("--date", default=date.today().isoformat(), help="Prediction and odds date in YYYY-MM-DD.")
    parser.add_argument(
        "--settle-date",
        default=None,
        help="Last settlement date in YYYY-MM-DD. Defaults to the day before --date.",
    )
    parser.add_argument(
        "--settlement-lookback-days",
        type=int,
        default=7,
        help="Backfill prediction settlement for recent dates with local prediction files.",
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
        help="Do not fetch ESPN/Taiwan odds; use existing local odds CSV.",
    )
    parser.add_argument(
        "--skip-backtest-refresh",
        action="store_true",
        help="Skip accuracy/backtest/dashboard refresh and only run daily operational files.",
    )
    parser.add_argument(
        "--skip-history-refresh",
        action="store_true",
        help="Do not refresh real final-score history before settlement backfill.",
    )
    return parser.parse_args()


def settlement_dates(target_date: str, settle_date: str, lookback_days: int) -> list[str]:
    end = date.fromisoformat(settle_date)
    start = end - timedelta(days=max(0, lookback_days - 1))
    dates = [item for item in date_range(start.isoformat(), end.isoformat()) if prediction_json_path(item).exists()]
    if target_date not in dates and prediction_json_path(target_date).exists():
        dates.append(target_date)
    return sorted(set(dates))


def settle_recent_predictions(py: str, dates: list[str], args: argparse.Namespace) -> None:
    if not dates:
        print("\n== 近期預測結算 ==\nno local prediction files found")
        return
    for item in dates:
        run_step(f"結算預測 {item}", [py, "scripts/settle_daily_predictions.py", "--date", item])
        if odds_csv_path(item).exists():
            run_step(
                f"結算 ROI {item}",
                roi_args(py, item, args.unit, args.min_edge, args.all_predictions),
                allow_fail=True,
            )
        else:
            print(f"\n== 結算 ROI {item} ==\nmissing odds file, skipped: {odds_csv_path(item)}")


def main() -> None:
    args = parse_args()
    target_date = args.date
    settle_date = args.settle_date or previous_date(target_date)
    py = sys.executable

    if not args.skip_history_refresh:
        run_step("更新歷史比分", [py, "scripts/fetch_real_mlb_data.py", "--end-date", settle_date])

    if not args.skip_backtest_refresh:
        run_step("刷新真實預測準確率", [py, "scripts/run_real_mlb_prediction_accuracy.py"])
        run_step("刷新真實回測", [py, "scripts/run_real_mlb_backtest.py"])
        run_step("重建總覽", [py, "scripts/generate_plan.py"])

    dates_to_settle = settlement_dates(target_date, settle_date, args.settlement_lookback_days)
    settle_recent_predictions(py, dates_to_settle, args)

    run_step("產生每日勝方預測", [py, "scripts/generate_daily_plan.py", "--date", target_date])
    run_step(
        "抓取先發打線、天氣、牛棚資料",
        [py, "scripts/fetch_context_sources.py", "--date", target_date, "--max-seconds", "75"],
        allow_fail=True,
    )
    run_step("產生逐打席模擬資料", [py, "scripts/generate_game_simulator.py", "--date", target_date])
    run_step("產生統一得分期望", [py, "scripts/unified_expectation_model.py", "--date", target_date])
    run_step("初步結算今日預測", [py, "scripts/settle_daily_predictions.py", "--date", target_date])

    if odds_csv_path(target_date).exists():
        print(f"\n== 保留既有盤口範本 ==\nexisting file kept: {odds_csv_path(target_date)}")
    else:
        run_step("建立盤口範本", [py, "scripts/prepare_odds_template.py", "--date", target_date])

    if not args.skip_odds_fetch:
        run_step("抓 ESPN moneyline 參考盤", [py, "scripts/fetch_espn_moneyline_odds.py", "--date", target_date])
        run_step("抓台灣運彩獨贏盤", [py, "scripts/fetch_taiwan_sportslottery_odds.py", "--date", target_date])
        run_step("抓台灣運彩完整玩法盤口", [py, "scripts/fetch_taiwan_sportslottery_markets.py", "--date", target_date])

    run_step(
        "檢查盤口檔",
        [py, "scripts/validate_odds_file.py", "--date", target_date, "--allow-partial"],
        allow_fail=True,
    )
    run_step("產生大小分模型", [py, "scripts/run_totals_v1.py", "--date", target_date])
    run_step("產生進階因子模型", [py, "scripts/run_advanced_factors_model.py", "--date", target_date])
    run_step("產生蒙地卡羅模擬", [py, "scripts/generate_monte_carlo.py", "--date", target_date, "--simulations", "10000"])
    run_step("產生 Q-量化候選模型", [py, "scripts/run_quant_bundle_model.py", "--date", target_date])
    run_step("用蒙地卡羅更新大小分模型", [py, "scripts/run_totals_v1.py", "--date", target_date])
    run_step("產生盤口研究", [py, "scripts/generate_market_research.py", "--date", target_date])
    run_step("重建每日預測頁", [py, "scripts/generate_daily_plan.py", "--date", target_date])
    run_step("更新 Q-量化候選模型", [py, "scripts/run_quant_bundle_model.py", "--date", target_date])
    run_step("最終結算每日預測", [py, "scripts/settle_daily_predictions.py", "--date", target_date])
    run_step("結算投注 ROI", roi_args(py, target_date, args.unit, args.min_edge, args.all_predictions))
    run_step("產生投注單", [py, "scripts/generate_betting_ticket.py", "--date", target_date])
    run_step("產生賽後檢討", [py, "scripts/generate_postgame_review.py"])
    run_step("重建總覽", [py, "scripts/generate_plan.py"])
    run_step("更新狀態頁", [py, "scripts/generate_status_report.py"])
    run_step("套用側邊選單", [py, "scripts/apply_site_navigation.py"])
    run_step("產生公開 JSON API", [py, "scripts/generate_public_api.py", "--date", target_date])
    print("\nworkflow completed")


if __name__ == "__main__":
    main()
