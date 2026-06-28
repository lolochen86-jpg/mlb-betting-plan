#!/usr/bin/env python3
"""Search for a better MLB winner model without tuning on the 2026 test set."""

from __future__ import annotations

import csv
import itertools
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from backtest_train_2024_2025_test_2026 import (
    GAMES_JSON,
    advanced_proxy_prediction,
    prediction_row,
    train_models,
)
from run_real_mlb_backtest import TeamStats


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"

RESULTS_JSON = DATA_DIR / "winner_model_search_results.json"
RESULTS_CSV = DATA_DIR / "winner_model_search_results.csv"
REPORT_HTML = DOCS_DIR / "winner_model_search.html"


def by_date(games: list[dict]) -> dict[str, list[dict]]:
    rows: dict[str, list[dict]] = defaultdict(list)
    for game in games:
        rows[game["date"]].append(game)
    return rows


def load_games() -> list[dict]:
    if not GAMES_JSON.exists():
        raise SystemExit(f"Missing cached games: {GAMES_JSON}. Run backtest_train_2024_2025_test_2026.py first.")
    games = json.loads(GAMES_JSON.read_text(encoding="utf-8"))
    games.sort(key=lambda row: (row["date"], int(row["game_pk"] or 0)))
    return games


def collect_predictions(seed_games: list[dict], eval_games: list[dict]) -> dict[str, list[dict]]:
    stats, models = train_models(seed_games)
    model_by_name = {model.name: model for model in models}
    model_names = [model.name for model in models] + ["F-進階因子代理"]
    ledger = {name: [] for name in model_names}
    history = list(seed_games)
    eval_by_day = by_date(eval_games)
    for day_index, day in enumerate(sorted(eval_by_day)):
        if day_index % 7 == 0:
            for model in models:
                if hasattr(model, "recalibrate"):
                    model.recalibrate()
        for game in eval_by_day[day]:
            for name, model in model_by_name.items():
                prob = model.predict(game["home"], game["away"], stats)
                if prob is None:
                    continue
                row = prediction_row(game, name, prob)
                ledger[name].append(row)
                model.history.append(row["correct"])
            advanced_prob = advanced_proxy_prediction(game, history)
            ledger["F-進階因子代理"].append(prediction_row(game, "F-進階因子代理", advanced_prob))
        for game in eval_by_day[day]:
            stats.update(game["home"], game["away"], game["home_score"], game["away_score"])
            history.append(game)
    return ledger


def index_by_game(rows: list[dict]) -> dict[str, dict]:
    return {str(row["game_pk"]): row for row in rows}


def aligned_rows(ledger: dict[str, list[dict]], model_names: list[str]) -> list[dict]:
    indexes = {name: index_by_game(ledger[name]) for name in model_names}
    common = set(indexes[model_names[0]].keys())
    for name in model_names[1:]:
        common &= set(indexes[name].keys())
    rows = []
    for game_pk in sorted(common, key=lambda value: int(value)):
        first = indexes[model_names[0]][game_pk]
        rows.append(
            {
                "game_pk": game_pk,
                "date": first["date"],
                "matchup_zh": first["matchup_zh"],
                "actual_home_win": first["actual_winner_zh"] == first["matchup_zh"].split(" @ ")[1],
                "actual_winner_zh": first["actual_winner_zh"],
                "home_zh": first["matchup_zh"].split(" @ ")[1],
                "away_zh": first["matchup_zh"].split(" @ ")[0],
                "probs": {name: indexes[name][game_pk]["home_win_probability"] for name in model_names},
            }
        )
    return rows


def evaluate_blend(rows: list[dict], weights: dict[str, float]) -> dict:
    predictions = []
    for row in rows:
        prob_home = sum(float(row["probs"][name]) * weight for name, weight in weights.items())
        pick_home = prob_home >= 0.5
        confidence = max(prob_home, 1 - prob_home)
        correct = pick_home == row["actual_home_win"]
        predictions.append(
            {
                "game_pk": row["game_pk"],
                "date": row["date"],
                "matchup_zh": row["matchup_zh"],
                "pick_zh": row["home_zh"] if pick_home else row["away_zh"],
                "actual_winner_zh": row["actual_winner_zh"],
                "home_win_probability": round(prob_home, 4),
                "confidence": round(confidence, 4),
                "correct": correct,
            }
        )
    total = len(predictions)
    correct = sum(1 for row in predictions if row["correct"])
    high = [row for row in predictions if float(row["confidence"]) >= 0.55]
    high_correct = sum(1 for row in high if row["correct"])
    return {
        "predictions": total,
        "correct": correct,
        "wrong": total - correct,
        "accuracy_pct": round(correct / total * 100, 2) if total else 0,
        "high_confidence_predictions": len(high),
        "high_confidence_accuracy_pct": round(high_correct / len(high) * 100, 2) if high else 0,
        "details": predictions,
    }


def candidate_weights(model_names: list[str]) -> list[dict[str, float]]:
    candidates = [{name: 1.0 if name == model else 0.0 for name in model_names} for model in model_names]
    grid_values = [0, 1, 2, 3, 4]
    for raw in itertools.product(grid_values, repeat=len(model_names)):
        total = sum(raw)
        if total == 0:
            continue
        weights = {name: value / total for name, value in zip(model_names, raw)}
        if len([value for value in weights.values() if value > 0]) < 2:
            continue
        candidates.append(weights)
    unique = {}
    for weights in candidates:
        key = tuple(round(weights[name], 3) for name in model_names)
        unique[key] = weights
    return list(unique.values())


def main() -> None:
    games = load_games()
    train_2024 = [game for game in games if "2024-01-01" <= game["date"] <= "2024-12-31"]
    valid_2025 = [game for game in games if "2025-01-01" <= game["date"] <= "2025-12-31"]
    train_2024_2025 = [game for game in games if "2024-01-01" <= game["date"] <= "2025-12-31"]
    test_2026 = [game for game in games if "2026-01-01" <= game["date"] <= "2026-12-31"]
    if not train_2024 or not valid_2025 or not test_2026:
        raise SystemExit("Missing train, validation, or test games.")

    model_names = ["A-畢氏勝率", "B-近期狀態", "C-ELO評分", "D-近期失分代理", "E-對照組(Ensemble)", "F-進階因子代理"]
    valid_ledger = collect_predictions(train_2024, valid_2025)
    valid_rows = aligned_rows(valid_ledger, model_names)
    ranked = []
    for weights in candidate_weights(model_names):
        result = evaluate_blend(valid_rows, weights)
        ranked.append({"weights": weights, **{k: v for k, v in result.items() if k != "details"}})
    ranked.sort(key=lambda row: (row["accuracy_pct"], row["high_confidence_accuracy_pct"], row["high_confidence_predictions"]), reverse=True)
    best_weights = ranked[0]["weights"]

    test_ledger = collect_predictions(train_2024_2025, test_2026)
    test_rows = aligned_rows(test_ledger, model_names)
    test_result = evaluate_blend(test_rows, best_weights)
    single_model_results = []
    for name in model_names:
        weights = {model: 1.0 if model == name else 0.0 for model in model_names}
        result = evaluate_blend(test_rows, weights)
        single_model_results.append({"model": name, **{k: v for k, v in result.items() if k != "details"}})
    single_model_results.sort(key=lambda row: row["accuracy_pct"], reverse=True)

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "method": "2024 train -> 2025 validation weight search -> 2026 untouched test",
        "periods": {
            "train_for_search": [train_2024[0]["date"], train_2024[-1]["date"], len(train_2024)],
            "validation": [valid_2025[0]["date"], valid_2025[-1]["date"], len(valid_2025)],
            "final_train": [train_2024_2025[0]["date"], train_2024_2025[-1]["date"], len(train_2024_2025)],
            "test": [test_2026[0]["date"], test_2026[-1]["date"], len(test_2026)],
        },
        "best_validation": {k: v for k, v in ranked[0].items() if k != "weights"},
        "best_weights": best_weights,
        "test_blend": {k: v for k, v in test_result.items() if k != "details"},
        "test_single_models": single_model_results,
        "test_details": test_result["details"],
    }
    RESULTS_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with RESULTS_CSV.open("w", encoding="utf-8", newline="") as f:
        fields = ["model", "predictions", "correct", "wrong", "accuracy_pct", "high_confidence_predictions", "high_confidence_accuracy_pct"]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerow({"model": "G-驗證集最佳混合", **report["test_blend"]})
        writer.writerows(single_model_results)
    REPORT_HTML.write_text(render_html(report), encoding="utf-8")
    print(f"wrote {RESULTS_JSON}")
    print(f"wrote {RESULTS_CSV}")
    print(f"wrote {REPORT_HTML}")
    print(f"best_weights={best_weights}")
    print(f"test_blend={report['test_blend']}")


def render_html(report: dict) -> str:
    weights = report["best_weights"]
    weight_rows = "".join(f"<tr><td>{name}</td><td>{value:.2f}</td></tr>" for name, value in weights.items() if value > 0)
    model_rows = "\n".join(
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
        for row in [{"model": "G-驗證集最佳混合", **report["test_blend"]}, *report["test_single_models"]]
    )
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>MLB 更佳模型搜尋</title>
  <style>
    body {{ margin: 0; background: #f7f8f6; color: #202421; font-family: "Microsoft JhengHei", "Noto Sans TC", system-ui, sans-serif; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 28px; }}
    h1 {{ margin: 0 0 6px; font-size: 30px; }}
    h2 {{ margin: 24px 0 12px; font-size: 18px; }}
    .meta, .note {{ color: #68736d; line-height: 1.6; font-size: 14px; }}
    table {{ width: 100%; border-collapse: collapse; background: white; border: 1px solid #dfe5df; border-radius: 8px; overflow: hidden; margin-bottom: 16px; }}
    th, td {{ text-align: left; border-bottom: 1px solid #dfe5df; padding: 12px 10px; white-space: nowrap; font-size: 14px; }}
    th {{ color: #68736d; font-size: 12px; }}
    .note {{ padding: 12px 14px; border: 1px solid #dfe5df; border-radius: 8px; background: white; }}
    @media (max-width: 720px) {{ main {{ padding: 18px; }} table {{ display: block; overflow-x: auto; }} }}
  </style>
</head>
<body>
  <main>
    <h1>MLB 更佳模型搜尋</h1>
    <div class="meta">
      方法：{report['method']}<br />
      2024 訓練：{report['periods']['train_for_search'][0]} 至 {report['periods']['train_for_search'][1]}，{report['periods']['train_for_search'][2]} 場<br />
      2025 驗證：{report['periods']['validation'][0]} 至 {report['periods']['validation'][1]}，{report['periods']['validation'][2]} 場<br />
      2026 測試：{report['periods']['test'][0]} 至 {report['periods']['test'][1]}，{report['periods']['test'][2]} 場<br />
      產生時間：{report['generated_at']}
    </div>
    <h2>驗證集選出的混合權重</h2>
    <table><thead><tr><th>模型</th><th>權重</th></tr></thead><tbody>{weight_rows}</tbody></table>
    <h2>2026 測試比較</h2>
    <table>
      <thead><tr><th>模型</th><th>預測場次</th><th>正確</th><th>錯誤</th><th>準確率</th><th>高信心場次</th><th>高信心準確率</th></tr></thead>
      <tbody>{model_rows}</tbody>
    </table>
    <div class="note">這不是在 2026 上調參；權重只用 2025 驗證集選出，2026 作保留測試。</div>
  </main>
</body>
</html>"""


if __name__ == "__main__":
    main()
