#!/usr/bin/env python3
"""Evaluate pure MLB winner prediction accuracy with real final scores.

This report intentionally does not use odds, edge, staking, or ROI. It answers
one question first: how often did each model pick the correct winner?
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from run_real_mlb_backtest import (
    DEFAULT_GAMES_CSV,
    ModelA,
    ModelB,
    ModelC,
    ModelD,
    ModelE,
    TeamStats,
    load_games,
)


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"
RESULTS_JSON = DATA_DIR / "real_mlb_prediction_accuracy.json"
SUMMARY_CSV = DATA_DIR / "real_mlb_prediction_accuracy_summary.csv"
REPORT_HTML = DOCS_DIR / "prediction_accuracy.html"


def make_models() -> list:
    base_models = [ModelA(), ModelB(), ModelC(), ModelD()]
    return [*base_models, ModelE(base_models)]


def prediction_from_probability(game: dict, prob_home: float) -> dict:
    pick_home = prob_home >= 0.5
    actual_home = game["home_score"] > game["away_score"]
    return {
        "game_pk": game["game_pk"],
        "date": game["date"],
        "matchup_zh": f"{game['away_zh']} @ {game['home_zh']}",
        "away_zh": game["away_zh"],
        "home_zh": game["home_zh"],
        "away_score": game["away_score"],
        "home_score": game["home_score"],
        "pick_zh": game["home_zh"] if pick_home else game["away_zh"],
        "pick_side": "home" if pick_home else "away",
        "actual_winner_zh": game["home_zh"] if actual_home else game["away_zh"],
        "confidence": round(max(prob_home, 1 - prob_home), 4),
        "home_win_probability": round(prob_home, 4),
        "correct": pick_home == actual_home,
        "away_probable_pitcher_zh": game.get("away_probable_pitcher_zh", ""),
        "home_probable_pitcher_zh": game.get("home_probable_pitcher_zh", ""),
    }


def summarize_predictions(predictions: list[dict]) -> dict:
    total = len(predictions)
    correct = sum(1 for row in predictions if row["correct"])
    by_month: dict[str, list[dict]] = defaultdict(list)
    for row in predictions:
        by_month[row["date"][:7]].append(row)
    monthly = []
    for month in sorted(by_month):
        rows = by_month[month]
        month_correct = sum(1 for row in rows if row["correct"])
        monthly.append(
            {
                "month": month,
                "predictions": len(rows),
                "correct": month_correct,
                "accuracy_pct": round(month_correct / len(rows) * 100, 2) if rows else 0,
            }
        )
    return {
        "predictions": total,
        "correct": correct,
        "wrong": total - correct,
        "accuracy_pct": round(correct / total * 100, 2) if total else 0,
        "monthly": monthly,
        "prediction_details": predictions,
    }


def run_accuracy(games: list[dict]) -> dict:
    stats = TeamStats()
    models = make_models()
    ledger: dict[str, list[dict]] = {model.name: [] for model in models}
    by_date: dict[str, list[dict]] = defaultdict(list)
    for game in games:
        by_date[game["date"]].append(game)

    for day_index, day in enumerate(sorted(by_date)):
        if day_index % 7 == 0:
            for model in models:
                if hasattr(model, "recalibrate"):
                    model.recalibrate()
        games_today = by_date[day]
        for game in games_today:
            for model in models:
                prob_home = model.predict(game["home"], game["away"], stats)
                if prob_home is None:
                    continue
                prediction = prediction_from_probability(game, prob_home)
                ledger[model.name].append(prediction)
                model.history.append(prediction["correct"])
        for game in games_today:
            stats.update(game["home"], game["away"], game["home_score"], game["away_score"])

    first_date = games[0]["date"] if games else None
    last_date = games[-1]["date"] if games else None
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "backtest_period": {"start": first_date, "end": last_date},
        "data_source": {
            "type": "real_mlb_final_scores",
            "games_csv": str(DEFAULT_GAMES_CSV.relative_to(ROOT)),
            "games_evaluated": len(games),
            "note": "Pure winner-prediction accuracy only. Odds, edge, stake, and ROI are intentionally excluded.",
        },
        "models": {model.name: summarize_predictions(ledger[model.name]) for model in models},
    }


def write_outputs(results: dict) -> None:
    RESULTS_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = []
    for name, model in results["models"].items():
        rows.append(
            {
                "模型": name,
                "預測場次": model["predictions"],
                "正確": model["correct"],
                "錯誤": model["wrong"],
                "準確率%": model["accuracy_pct"],
            }
        )
    rows.sort(key=lambda row: (row["準確率%"], row["預測場次"]), reverse=True)
    with SUMMARY_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["模型", "預測場次", "正確", "錯誤", "準確率%"])
        writer.writeheader()
        writer.writerows(rows)
    REPORT_HTML.write_text(render_html(results, rows), encoding="utf-8")
    print(f"wrote {RESULTS_JSON}")
    print(f"wrote {SUMMARY_CSV}")
    print(f"wrote {REPORT_HTML}")
    for row in rows:
        print(f"{row['模型']}: predictions={row['預測場次']} correct={row['正確']} accuracy={row['準確率%']}%")


def render_html(results: dict, summary_rows: list[dict]) -> str:
    top = summary_rows[0] if summary_rows else None
    rows_html = "\n".join(
        f"""
        <tr>
          <td>{row['模型']}</td>
          <td>{row['預測場次']}</td>
          <td>{row['正確']}</td>
          <td>{row['錯誤']}</td>
          <td>{row['準確率%']:.2f}%</td>
        </tr>"""
        for row in summary_rows
    )
    best_model = top["模型"] if top else "無"
    best_accuracy = f"{top['準確率%']:.2f}%" if top else "0.00%"
    best_count = top["預測場次"] if top else 0
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>MLB 真實預測準確率</title>
  <style>
    body {{ margin: 0; background: #f7f8f6; color: #202421; font-family: "Microsoft JhengHei", "Noto Sans TC", system-ui, sans-serif; }}
    main {{ max-width: 1040px; margin: 0 auto; padding: 28px; }}
    h1 {{ margin: 0 0 6px; font-size: 30px; }}
    .meta {{ color: #68736d; line-height: 1.6; font-size: 14px; margin-bottom: 20px; }}
    .kpis {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin-bottom: 18px; }}
    .card {{ background: white; border: 1px solid #dfe5df; border-radius: 8px; padding: 18px; }}
    .label {{ color: #68736d; font-size: 13px; margin-bottom: 8px; }}
    .value {{ font-size: 28px; font-weight: 800; }}
    table {{ width: 100%; border-collapse: collapse; background: white; border: 1px solid #dfe5df; border-radius: 8px; overflow: hidden; }}
    th, td {{ text-align: left; border-bottom: 1px solid #dfe5df; padding: 12px 10px; white-space: nowrap; font-size: 14px; }}
    th {{ color: #68736d; font-size: 12px; }}
    .note {{ margin-top: 16px; padding: 12px 14px; border: 1px solid #dfe5df; border-radius: 8px; background: white; color: #68736d; line-height: 1.6; }}
    @media (max-width: 720px) {{ main {{ padding: 18px; }} .kpis {{ grid-template-columns: 1fr; }} table {{ display: block; overflow-x: auto; }} }}
  </style>
</head>
<body>
  <main>
    <h1>MLB 真實預測準確率</h1>
    <div class="meta">
      回測期間：{results['backtest_period']['start']} 至 {results['backtest_period']['end']}<br />
      真實比分場數：{results['data_source']['games_evaluated']} / 產生時間：{results['generated_at']}
    </div>
    <section class="kpis">
      <div class="card"><div class="label">準確率最佳模型</div><div class="value">{best_model}</div></div>
      <div class="card"><div class="label">最佳準確率</div><div class="value">{best_accuracy}</div></div>
      <div class="card"><div class="label">最佳模型預測場次</div><div class="value">{best_count}</div></div>
    </section>
    <table>
      <thead><tr><th>模型</th><th>預測場次</th><th>正確</th><th>錯誤</th><th>準確率</th></tr></thead>
      <tbody>{rows_html}</tbody>
    </table>
    <div class="note">此頁只統計真實比分下的勝方預測準確率，尚未使用真實盤口，因此不計算投注 ROI。盤口資料補齊後，ROI 應在獨立投注績效層計算。</div>
  </main>
</body>
</html>"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run pure prediction accuracy with saved real MLB final scores.")
    parser.add_argument("--games-csv", type=Path, default=DEFAULT_GAMES_CSV)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    games = load_games(args.games_csv)
    if not games:
        raise SystemExit(f"No completed games found in {args.games_csv}")
    write_outputs(run_accuracy(games))


if __name__ == "__main__":
    main()
