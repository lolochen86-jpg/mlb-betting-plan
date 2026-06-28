#!/usr/bin/env python3
"""Backtest MLB models with 2024-2025 training data and 2026 test games."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from fetch_real_mlb_data import fetch_schedule_chunked, normalize_games
from run_real_mlb_backtest import ModelA, ModelB, ModelC, ModelD, ModelE, TeamStats
from run_totals_v1 import predict_total, team_total_stats


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"

GAMES_CSV = DATA_DIR / "backtest_2024_2026_games.csv"
GAMES_JSON = DATA_DIR / "backtest_2024_2026_games.json"
RESULTS_JSON = DATA_DIR / "backtest_train_2024_2025_test_2026.json"
WINNER_CSV = DATA_DIR / "backtest_2026_winner_accuracy.csv"
TOTALS_CSV = DATA_DIR / "backtest_2026_totals_quality.csv"
REPORT_HTML = DOCS_DIR / "backtest_2026_report.html"


def make_models() -> list:
    base_models = [ModelA(), ModelB(), ModelC(), ModelD()]
    return [*base_models, ModelE(base_models)]


def by_date(games: list[dict]) -> dict[str, list[dict]]:
    rows: dict[str, list[dict]] = defaultdict(list)
    for game in games:
        rows[game["date"]].append(game)
    return rows


def load_or_fetch_games(start_date: str, end_date: str, refresh: bool) -> list[dict]:
    if GAMES_JSON.exists() and not refresh:
        return json.loads(GAMES_JSON.read_text(encoding="utf-8"))
    payload, chunk_meta = fetch_schedule_chunked(start_date, end_date, chunk_days=31, sleep_seconds=0)
    rows = normalize_games(payload, {"R"})
    DATA_DIR.mkdir(exist_ok=True)
    GAMES_JSON.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = list(rows[0].keys()) if rows else []
    with GAMES_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    (DATA_DIR / "backtest_2024_2026_fetch_meta.json").write_text(
        json.dumps(
            {
                "source": "MLB Stats API schedule endpoint",
                "start_date": start_date,
                "end_date": end_date,
                "game_types": ["R"],
                "games_written": len(rows),
                "chunk_meta": chunk_meta,
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return rows


def prediction_row(game: dict, model_name: str, prob_home: float) -> dict:
    pick_home = prob_home >= 0.5
    actual_home = bool(game["home_win"])
    return {
        "date": game["date"],
        "game_pk": game["game_pk"],
        "model": model_name,
        "matchup_zh": f"{game['away_zh']} @ {game['home_zh']}",
        "pick_zh": game["home_zh"] if pick_home else game["away_zh"],
        "actual_winner_zh": game["home_zh"] if actual_home else game["away_zh"],
        "confidence": round(max(prob_home, 1 - prob_home), 4),
        "home_win_probability": round(prob_home, 4),
        "correct": pick_home == actual_home,
    }


def train_models(train_games: list[dict]) -> tuple[TeamStats, list]:
    stats = TeamStats()
    models = make_models()
    train_by_date = by_date(train_games)
    for day_index, day in enumerate(sorted(train_by_date)):
        if day_index % 7 == 0:
            for model in models:
                if hasattr(model, "recalibrate"):
                    model.recalibrate()
        for game in train_by_date[day]:
            for model in models:
                prob = model.predict(game["home"], game["away"], stats)
                if prob is not None:
                    model.history.append((prob >= 0.5) == bool(game["home_win"]))
        for game in train_by_date[day]:
            stats.update(game["home"], game["away"], game["home_score"], game["away_score"])
    return stats, models


def advanced_proxy_context(history: list[dict]) -> dict:
    by_team = defaultdict(list)
    h2h = defaultdict(list)
    venue_totals = defaultdict(list)
    for game in history:
        away = game["away_zh"]
        home = game["home_zh"]
        away_runs = int(game["away_score"])
        home_runs = int(game["home_score"])
        by_team[away].append({"rs": away_runs, "ra": home_runs, "win": away_runs > home_runs})
        by_team[home].append({"rs": home_runs, "ra": away_runs, "win": home_runs > away_runs})
        h2h[tuple(sorted([away, home]))].append({"winner": away if away_runs > home_runs else home})
        venue_totals[home].append(away_runs + home_runs)
    return {"by_team": by_team, "h2h": h2h, "venue_totals": venue_totals}


def mean(values: list[float], fallback: float = 0.0) -> float:
    return sum(values) / len(values) if values else fallback


def streak(team: str, ctx: dict) -> float:
    games = ctx["by_team"].get(team, [])[-8:]
    if not games:
        return 0.0
    last = games[-1]["win"]
    count = 0
    for game in reversed(games):
        if game["win"] == last:
            count += 1
        else:
            break
    return count / 8 if last else -count / 8


def team_factor_score(team: str, opponent: str, home_team: str, ctx: dict) -> float:
    games = ctx["by_team"].get(team, [])
    opp_games = ctx["by_team"].get(opponent, [])
    recent = games[-20:]
    opp_recent = opp_games[-20:]
    if not games:
        return 0.0
    season_rs = mean([g["rs"] for g in games], 4.4)
    season_ra = mean([g["ra"] for g in games], 4.4)
    recent_rs = mean([g["rs"] for g in recent], season_rs)
    recent_ra = mean([g["ra"] for g in recent], season_ra)
    opp_recent_ra = mean([g["ra"] for g in opp_recent], 4.4)
    run_diff = mean([g["rs"] - g["ra"] for g in recent], 0.0)
    win_rate = mean([1.0 if g["win"] else 0.0 for g in recent], 0.5)
    key = tuple(sorted([team, opponent]))
    h2h_rows = ctx["h2h"].get(key, [])[-10:]
    h2h_edge = mean([1.0 if row["winner"] == team else 0.0 for row in h2h_rows], 0.5) - 0.5
    home_totals = ctx["venue_totals"].get(home_team, [])[-40:]
    all_totals = [total for values in ctx["venue_totals"].values() for total in values[-40:]]
    venue_edge = (mean(home_totals, 8.8) - mean(all_totals, 8.8)) / 8
    return (
        0.18 * (recent_rs - 4.4)
        - 0.16 * (recent_ra - 4.4)
        + 0.10 * (season_rs - season_ra)
        + 0.10 * (opp_recent_ra - 4.4)
        + 0.09 * run_diff
        + 0.35 * (win_rate - 0.5)
        + 0.22 * streak(team, ctx)
        + 0.20 * h2h_edge
        + 0.03 * venue_edge
    )


def advanced_proxy_prediction(game: dict, history: list[dict]) -> float:
    ctx = advanced_proxy_context(history)
    away = game["away_zh"]
    home = game["home_zh"]
    away_score = team_factor_score(away, home, home, ctx)
    home_score = team_factor_score(home, away, home, ctx) + 0.08
    prob_home = 1 / (1 + math.exp(-(home_score - away_score)))
    return max(0.30, min(0.70, prob_home))


def backtest_winners(train_games: list[dict], test_games: list[dict]) -> dict:
    stats, models = train_models(train_games)
    ledger = {model.name: [] for model in models}
    advanced_rows = []
    advanced_history = list(train_games)
    test_by_date = by_date(test_games)
    for day_index, day in enumerate(sorted(test_by_date)):
        if day_index % 7 == 0:
            for model in models:
                if hasattr(model, "recalibrate"):
                    model.recalibrate()
        for game in test_by_date[day]:
            for model in models:
                prob = model.predict(game["home"], game["away"], stats)
                if prob is None:
                    continue
                row = prediction_row(game, model.name, prob)
                ledger[model.name].append(row)
                model.history.append(row["correct"])
            advanced_prob = advanced_proxy_prediction(game, advanced_history)
            advanced_rows.append(prediction_row(game, "F-進階因子代理", advanced_prob))
        for game in test_by_date[day]:
            stats.update(game["home"], game["away"], game["home_score"], game["away_score"])
            advanced_history.append(game)

    summary = []
    for model_name, rows in ledger.items():
        total = len(rows)
        correct = sum(1 for row in rows if row["correct"])
        high_conf = [row for row in rows if float(row["confidence"]) >= 0.55]
        high_correct = sum(1 for row in high_conf if row["correct"])
        summary.append(
            {
                "model": model_name,
                "predictions": total,
                "correct": correct,
                "wrong": total - correct,
                "accuracy_pct": round(correct / total * 100, 2) if total else 0,
                "high_confidence_predictions": len(high_conf),
                "high_confidence_accuracy_pct": round(high_correct / len(high_conf) * 100, 2) if high_conf else 0,
            }
        )
    total = len(advanced_rows)
    correct = sum(1 for row in advanced_rows if row["correct"])
    high_conf = [row for row in advanced_rows if float(row["confidence"]) >= 0.55]
    high_correct = sum(1 for row in high_conf if row["correct"])
    summary.append(
        {
            "model": "F-進階因子代理",
            "predictions": total,
            "correct": correct,
            "wrong": total - correct,
            "accuracy_pct": round(correct / total * 100, 2) if total else 0,
            "high_confidence_predictions": len(high_conf),
            "high_confidence_accuracy_pct": round(high_correct / len(high_conf) * 100, 2) if high_conf else 0,
        }
    )
    ledger["F-進階因子代理"] = advanced_rows
    summary.sort(key=lambda row: (row["accuracy_pct"], row["predictions"]), reverse=True)
    return {"summary": summary, "details": ledger}


def backtest_totals(train_games: list[dict], test_games: list[dict], recent_games: int) -> dict:
    history = list(train_games)
    rows = []
    for game in test_games:
        teams, league_total, sigma = team_total_stats(history, recent_games)
        predicted = predict_total(game["away_zh"], game["home_zh"], teams, league_total)
        actual = float(game["away_score"] + game["home_score"])
        error = predicted - actual
        rows.append(
            {
                "date": game["date"],
                "game_pk": game["game_pk"],
                "matchup_zh": f"{game['away_zh']} @ {game['home_zh']}",
                "predicted_total": round(predicted, 2),
                "actual_total": actual,
                "absolute_error": round(abs(error), 2),
                "signed_error": round(error, 2),
            }
        )
        history.append(game)
    mae = sum(row["absolute_error"] for row in rows) / len(rows) if rows else 0
    rmse = math.sqrt(sum(float(row["signed_error"]) ** 2 for row in rows) / len(rows)) if rows else 0
    within_1 = sum(1 for row in rows if float(row["absolute_error"]) <= 1.0)
    within_2 = sum(1 for row in rows if float(row["absolute_error"]) <= 2.0)
    within_3 = sum(1 for row in rows if float(row["absolute_error"]) <= 3.0)
    return {
        "summary": {
            "model": "大小分 v1 - 近期/整季得失分混合",
            "games": len(rows),
            "mae_runs": round(mae, 2),
            "rmse_runs": round(rmse, 2),
            "within_1_run_pct": round(within_1 / len(rows) * 100, 2) if rows else 0,
            "within_2_runs_pct": round(within_2 / len(rows) * 100, 2) if rows else 0,
            "within_3_runs_pct": round(within_3 / len(rows) * 100, 2) if rows else 0,
            "official_over_under_accuracy_pct": None,
            "official_line_note": "沒有保存 2026 每場台灣運彩賽前總分線，因此不能誠實計算官方大小分命中率；本段先回測總分預測誤差。",
        },
        "details": rows,
    }


def write_outputs(report: dict) -> None:
    RESULTS_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with WINNER_CSV.open("w", encoding="utf-8", newline="") as f:
        fields = ["model", "predictions", "correct", "wrong", "accuracy_pct", "high_confidence_predictions", "high_confidence_accuracy_pct"]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(report["winner"]["summary"])
    with TOTALS_CSV.open("w", encoding="utf-8", newline="") as f:
        fields = ["date", "game_pk", "matchup_zh", "predicted_total", "actual_total", "absolute_error", "signed_error"]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(report["totals"]["details"])
    REPORT_HTML.write_text(render_html(report), encoding="utf-8")
    print(f"wrote {RESULTS_JSON}")
    print(f"wrote {WINNER_CSV}")
    print(f"wrote {TOTALS_CSV}")
    print(f"wrote {REPORT_HTML}")


def render_html(report: dict) -> str:
    winner_rows = "\n".join(
        f"""
        <tr>
          <td>{row['model']}</td>
          <td>{row['predictions']}</td>
          <td>{row['correct']}</td>
          <td>{row['wrong']}</td>
          <td>{row['accuracy_pct']:.2f}%</td>
          <td>{row['high_confidence_predictions']}</td>
          <td>{row['high_confidence_accuracy_pct']:.2f}%</td>
        </tr>"""
        for row in report["winner"]["summary"]
    )
    totals = report["totals"]["summary"]
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>MLB 2026 測試回測</title>
  <style>
    body {{ margin: 0; background: #f7f8f6; color: #202421; font-family: "Microsoft JhengHei", "Noto Sans TC", system-ui, sans-serif; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 28px; }}
    h1 {{ margin: 0 0 6px; font-size: 30px; }}
    h2 {{ margin: 24px 0 12px; font-size: 18px; }}
    .meta, .note {{ color: #68736d; line-height: 1.6; font-size: 14px; }}
    .grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 18px 0; }}
    .card {{ background: white; border: 1px solid #dfe5df; border-radius: 8px; padding: 16px; }}
    .label {{ color: #68736d; font-size: 13px; margin-bottom: 8px; }}
    .value {{ font-size: 26px; font-weight: 800; }}
    table {{ width: 100%; border-collapse: collapse; background: white; border: 1px solid #dfe5df; border-radius: 8px; overflow: hidden; }}
    th, td {{ text-align: left; border-bottom: 1px solid #dfe5df; padding: 12px 10px; white-space: nowrap; font-size: 14px; }}
    th {{ color: #68736d; font-size: 12px; }}
    .note {{ margin-top: 16px; padding: 12px 14px; border: 1px solid #dfe5df; border-radius: 8px; background: white; }}
    @media (max-width: 800px) {{ main {{ padding: 18px; }} .grid {{ grid-template-columns: 1fr; }} table {{ display: block; overflow-x: auto; }} }}
  </style>
</head>
<body>
  <main>
    <h1>MLB 2026 測試回測</h1>
    <div class="meta">
      訓練資料：{report['periods']['train_start']} 至 {report['periods']['train_end']}<br />
      測試資料：{report['periods']['test_start']} 至 {report['periods']['test_end']}<br />
      訓練場數：{report['periods']['train_games']} / 測試場數：{report['periods']['test_games']}<br />
      產生時間：{report['generated_at']}
    </div>
    <section class="grid">
      <div class="card"><div class="label">獨贏最佳模型</div><div class="value">{report['winner']['summary'][0]['model']}</div></div>
      <div class="card"><div class="label">獨贏最佳準確率</div><div class="value">{report['winner']['summary'][0]['accuracy_pct']:.2f}%</div></div>
      <div class="card"><div class="label">大小分 MAE</div><div class="value">{totals['mae_runs']:.2f}</div></div>
      <div class="card"><div class="label">大小分 2 分內</div><div class="value">{totals['within_2_runs_pct']:.2f}%</div></div>
    </section>
    <h2>獨贏準確率</h2>
    <table>
      <thead><tr><th>模型</th><th>預測場次</th><th>正確</th><th>錯誤</th><th>準確率</th><th>高信心場次</th><th>高信心準確率</th></tr></thead>
      <tbody>{winner_rows}</tbody>
    </table>
    <h2>大小分總分模型品質</h2>
    <table>
      <thead><tr><th>模型</th><th>場次</th><th>MAE</th><th>RMSE</th><th>1 分內</th><th>2 分內</th><th>3 分內</th><th>官方大小分命中率</th></tr></thead>
      <tbody><tr><td>{totals['model']}</td><td>{totals['games']}</td><td>{totals['mae_runs']:.2f}</td><td>{totals['rmse_runs']:.2f}</td><td>{totals['within_1_run_pct']:.2f}%</td><td>{totals['within_2_runs_pct']:.2f}%</td><td>{totals['within_3_runs_pct']:.2f}%</td><td>N/A</td></tr></tbody>
    </table>
    <div class="note">{totals['official_line_note']}</div>
  </main>
</body>
</html>"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest 2026 using 2024-2025 training data.")
    parser.add_argument("--start-date", default="2024-03-20")
    parser.add_argument("--train-end", default="2025-12-31")
    parser.add_argument("--test-start", default="2026-01-01")
    parser.add_argument("--test-end", default=date.today().isoformat())
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--recent-games", type=int, default=20)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    games = load_or_fetch_games(args.start_date, args.test_end, args.refresh)
    train_games = [game for game in games if args.start_date <= game["date"] <= args.train_end]
    test_games = [game for game in games if args.test_start <= game["date"] <= args.test_end]
    if not train_games or not test_games:
        raise SystemExit("Missing train or test games after filtering.")
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "periods": {
            "train_start": train_games[0]["date"],
            "train_end": train_games[-1]["date"],
            "test_start": test_games[0]["date"],
            "test_end": test_games[-1]["date"],
            "train_games": len(train_games),
            "test_games": len(test_games),
        },
        "winner": backtest_winners(train_games, test_games),
        "totals": backtest_totals(train_games, test_games, args.recent_games),
    }
    write_outputs(report)
    print("winner summary:")
    for row in report["winner"]["summary"]:
        print(f"{row['model']}: {row['accuracy_pct']}% ({row['correct']}/{row['predictions']})")
    totals = report["totals"]["summary"]
    print(f"totals: MAE={totals['mae_runs']} RMSE={totals['rmse_runs']} within2={totals['within_2_runs_pct']}% official_ou=N/A")


if __name__ == "__main__":
    main()
