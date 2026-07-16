#!/usr/bin/env python3
"""Run the imported MLB Quant runtime bundle against the local daily slate."""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"
MODEL_DIR = ROOT / "models" / "quant_bundle_2026_07_16"
OUTPUT_JSON = DATA_DIR / "quant_model_predictions_{date}.json"
OUTPUT_CSV = DATA_DIR / "quant_model_predictions_{date}.csv"
OUTPUT_HTML = DOCS_DIR / "quant_model.html"
DAILY_JSON = DATA_DIR / "daily_predictions_{date}.json"
SIM_JSON = DATA_DIR / "game_simulator_{date}.json"
UNIFIED_JSON = DATA_DIR / "unified_expectations_{date}.json"
TOTALS_JSON = DATA_DIR / "totals_predictions_{date}.json"

sys.path.insert(0, str(MODEL_DIR))
from ml_engine_runtime import (  # noqa: E402
    calibrate_expected_runs_to_win_probability,
    load_model_bundle,
    predict_poisson_ou,
    predict_xgboost_ml,
)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return default


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return default
        return number
    except (TypeError, ValueError):
        return default


def starter_runs(era_or_xfip: float) -> float:
    return float(era_or_xfip) / 9.0 * 6.0


def base_feature_row(feature_names: set[str]) -> dict[str, float]:
    row = {name: 0.0 for name in feature_names}
    defaults = {
        "home_lineup_woba_ema_sum": 9.0 * 0.315,
        "away_lineup_woba_ema_sum": 9.0 * 0.315,
        "home_lineup_iso_ema_sum": 9.0 * 0.160,
        "away_lineup_iso_ema_sum": 9.0 * 0.160,
        "home_lineup_size": 9.0,
        "away_lineup_size": 9.0,
        "home_sp_xfip_ema": 4.20,
        "away_sp_xfip_ema": 4.20,
        "home_sp_k_bb_pct_ema": 0.14,
        "away_sp_k_bb_pct_ema": 0.14,
        "home_sp_gb_pct": 0.43,
        "away_sp_gb_pct": 0.43,
        "home_sp_expected_runs_base": starter_runs(4.20),
        "away_sp_expected_runs_base": starter_runs(4.20),
        "home_sp_expected_runs_adj": starter_runs(4.20),
        "away_sp_expected_runs_adj": starter_runs(4.20),
        "home_def_synergy_total": 0.0,
        "away_def_synergy_total": 0.0,
        "home_def_gb_weight": 1.0,
        "away_def_gb_weight": 1.0,
        "adi": 100.0,
        "hr_factor": 1.0,
        "temperature_f": 72.0,
        "elevation_ft": 500.0,
        "wind_speed_mph": 5.0,
    }
    for key, value in defaults.items():
        if key in row:
            row[key] = float(value)
    return row


def lineup_metrics(lineup: list[dict]) -> tuple[float, float, float, list[str]]:
    if not lineup:
        return 9.0 * 0.315, 9.0 * 0.160, 0.0, ["lineup_missing"]
    woba_sum = 0.0
    iso_sum = 0.0
    count = 0
    missing = []
    for player in lineup[:9]:
        avg = safe_float(player.get("avg"), 0.245)
        obp = safe_float(player.get("obp"), 0.315)
        slg = safe_float(player.get("slg"), 0.405)
        contact = safe_float(player.get("contact"), 1.0)
        power = safe_float(player.get("power"), 1.0)
        patience = safe_float(player.get("patience"), 1.0)
        woba_proxy = clamp(0.48 * obp + 0.34 * slg + 0.18 * avg, 0.240, 0.430)
        woba_proxy *= clamp(0.34 * contact + 0.33 * power + 0.33 * patience, 0.82, 1.22)
        woba_sum += clamp(woba_proxy, 0.230, 0.460)
        iso_sum += clamp(slg - avg, 0.040, 0.340)
        count += 1
    if count < 9:
        missing.append("lineup_under_9")
        woba_sum += (9 - count) * 0.315
        iso_sum += (9 - count) * 0.160
    return woba_sum, iso_sum, float(max(count, 9)), missing


def pitcher_metrics(profile: dict, weight: float) -> tuple[float, float, float, float, float, list[str]]:
    missing = []
    era = safe_float(profile.get("era"), 4.20)
    if not profile:
        missing.append("pitcher_profile_missing")
    k_factor = safe_float(profile.get("k_factor"), 1.0)
    bb_factor = safe_float(profile.get("bb_factor"), 1.0)
    gb_factor = safe_float(profile.get("gb_factor"), 1.0)
    run_prevention = safe_float(profile.get("run_prevention_factor"), 1.0)
    xfip = clamp(era, 2.10, 6.20)
    k_bb = clamp(0.14 + (k_factor - 1.0) * 0.085 - (bb_factor - 1.0) * 0.075, 0.035, 0.260)
    gb_pct = clamp(0.43 + (gb_factor - 1.0) * 0.11, 0.28, 0.62)
    base = starter_runs(xfip)
    adj = starter_runs(clamp(xfip * clamp(run_prevention, 0.72, 1.32), 2.00, 6.70))
    weight = clamp(weight, 0.0, 1.0)
    if weight < 0.85:
        missing.append("probable_pitcher_not_fully_confirmed")
        adj = adj * weight + starter_runs(4.35) * (1.0 - weight)
    return xfip, k_bb, gb_pct, base, adj, missing


def weather_metrics(unified_row: dict) -> tuple[float, float, float, float, list[str]]:
    weather = unified_row.get("weather") or {}
    missing = []
    temp_c = safe_float(weather.get("temperature_c"), None) if weather.get("temperature_c") is not None else None
    wind_kmh = safe_float(weather.get("wind_speed_kmh"), None) if weather.get("wind_speed_kmh") is not None else None
    factor = safe_float(weather.get("factor"), 0.0)
    if temp_c is None:
        missing.append("weather_temperature_missing")
        temperature_f = 72.0
    else:
        temperature_f = temp_c * 9.0 / 5.0 + 32.0
    wind_mph = 5.0 if wind_kmh is None else wind_kmh * 0.621371
    if wind_kmh is None:
        missing.append("weather_wind_missing")
    hr_factor = clamp(1.0 + factor * 0.40, 0.84, 1.18)
    adi = clamp(100.0 - (temperature_f - 72.0) * 0.35, 88.0, 108.0)
    return adi, hr_factor, clamp(temperature_f, 35.0, 105.0), clamp(wind_mph, 0.0, 28.0), missing


def total_line_for_game(target_date: str, game_pk: str) -> float:
    totals = load_json(Path(str(TOTALS_JSON).format(date=target_date)), {})
    rows = [*totals.get("all_predictions", []), *totals.get("candidates", []), *totals.get("skipped", [])]
    for row in rows:
        if str(row.get("game_pk")) == str(game_pk):
            line = row.get("official_totals_line") or row.get("total_line")
            if line:
                return safe_float(line, 8.5)
    return 8.5


def build_feature_row(bundle: Any, daily_row: dict, sim_row: dict, unified_row: dict) -> tuple[dict[str, float], list[str]]:
    feature_names = set(bundle.moneyline_model.feature_names) | set(bundle.totals_model.feature_columns)
    row = base_feature_row(feature_names)
    missing: list[str] = []

    for side in ("home", "away"):
        lineup = sim_row.get(f"{side}_lineup") or []
        woba_sum, iso_sum, size, lineup_missing = lineup_metrics(lineup)
        row[f"{side}_lineup_woba_ema_sum"] = woba_sum
        row[f"{side}_lineup_iso_ema_sum"] = iso_sum
        row[f"{side}_lineup_size"] = size
        missing.extend(f"{side}_{item}" for item in lineup_missing)

        profile = sim_row.get(f"{side}_pitcher_profile") or {}
        weight = safe_float(daily_row.get(f"{side}_pitcher_weight") or sim_row.get(f"{side}_pitcher_weight"), 0.0)
        xfip, k_bb, gb_pct, base, adj, pitcher_missing = pitcher_metrics(profile, weight)
        row[f"{side}_sp_xfip_ema"] = xfip
        row[f"{side}_sp_k_bb_pct_ema"] = k_bb
        row[f"{side}_sp_gb_pct"] = gb_pct
        row[f"{side}_sp_expected_runs_base"] = base
        row[f"{side}_sp_expected_runs_adj"] = adj
        row[f"{side}_def_gb_weight"] = gb_pct / 0.43
        missing.extend(f"{side}_{item}" for item in pitcher_missing)

    adi, hr_factor, temperature_f, wind_speed_mph, weather_missing = weather_metrics(unified_row)
    row["adi"] = adi
    row["hr_factor"] = hr_factor
    row["temperature_f"] = temperature_f
    row["wind_speed_mph"] = wind_speed_mph
    missing.extend(weather_missing)
    return {key: row[key] for key in feature_names}, sorted(set(missing))


def prediction_quality(missing: list[str]) -> str:
    if not missing:
        return "完整"
    if len(missing) <= 2 and not any("lineup_missing" in item for item in missing):
        return "可用但需留意"
    return "資料不足先觀察"


def build_report(target_date: str) -> dict:
    daily = load_json(Path(str(DAILY_JSON).format(date=target_date)), {})
    sim = load_json(Path(str(SIM_JSON).format(date=target_date)), {})
    unified = load_json(Path(str(UNIFIED_JSON).format(date=target_date)), {})
    metadata = load_json(MODEL_DIR / "metadata.json", {})
    bundle = load_model_bundle(MODEL_DIR)

    sim_by_game = {str(row.get("game_pk")): row for row in sim.get("games", [])}
    unified_by_game = {str(row.get("game_pk")): row for row in unified.get("games", [])}
    rows = []
    for daily_row in daily.get("all_predictions", []):
        game_pk = str(daily_row.get("game_pk") or "")
        sim_row = sim_by_game.get(game_pk, {})
        unified_row = unified_by_game.get(game_pk, {})
        features, missing = build_feature_row(bundle, daily_row, sim_row, unified_row)
        frame = pd.DataFrame([{**features, "game_id": game_pk, "mlb_game_pk": game_pk, "game_date": target_date}])
        ml = predict_xgboost_ml(bundle.moneyline_model, frame)[0]
        line = total_line_for_game(target_date, game_pk)
        total_pred = predict_poisson_ou(bundle.totals_model, frame, totals_lines={game_pk: line})[0]
        home_prob = safe_float(ml["true_win_probability"]["home"], 0.5)
        away_prob = 1.0 - home_prob
        expected_runs = total_pred["expected_runs"]
        calibrated = calibrate_expected_runs_to_win_probability(expected_runs, home_prob)
        home_runs = round(safe_float(calibrated.get("home"), expected_runs.get("home", 4.3)), 2)
        away_runs = round(safe_float(calibrated.get("away"), expected_runs.get("away", 4.3)), 2)
        over_prob = safe_float(total_pred.get("probability", {}).get("over"), 0.0)
        under_prob = safe_float(total_pred.get("probability", {}).get("under"), 0.0)
        pick_side = "home" if home_prob >= 0.5 else "away"
        pick_zh = daily_row.get("home_zh") if pick_side == "home" else daily_row.get("away_zh")
        totals_pick = "大分" if over_prob > under_prob else "小分"
        totals_prob = max(over_prob, under_prob)
        confidence = max(home_prob, away_prob)
        official_pick = daily_row.get("prediction_zh", "")
        unified_pick = unified_row.get("prediction_zh", "")
        rows.append(
            {
                "date": target_date,
                "game_pk": game_pk,
                "game_time_tw": daily_row.get("game_time_tw", ""),
                "status": daily_row.get("status", ""),
                "matchup_zh": daily_row.get("matchup_zh", ""),
                "away_zh": daily_row.get("away_zh", ""),
                "home_zh": daily_row.get("home_zh", ""),
                "quant_prediction_zh": pick_zh,
                "quant_pick_side": pick_side,
                "quant_confidence": round(confidence, 4),
                "home_win_probability": round(home_prob, 4),
                "away_win_probability": round(away_prob, 4),
                "predicted_away_score": away_runs,
                "predicted_home_score": home_runs,
                "predicted_total": round(away_runs + home_runs, 2),
                "total_line": line,
                "totals_pick_zh": totals_pick,
                "totals_probability": round(totals_prob, 4),
                "over_probability": round(over_prob, 4),
                "under_probability": round(under_prob, 4),
                "official_model_pick_zh": official_pick,
                "unified_model_pick_zh": unified_pick,
                "model_alignment": "一致" if pick_zh and pick_zh in {official_pick, unified_pick} else "分歧",
                "data_quality": prediction_quality(missing),
                "missing_features": missing,
            }
        )
    rows.sort(key=lambda row: (row.get("game_time_tw") or "", int(row.get("game_pk") or 0)))
    better_note = (
        "目前不能判定更完美。此 bundle holdout 獨贏準確率約 "
        f"{metadata.get('metadata', {}).get('metrics', {}).get('moneyline', {}).get('accuracy', 0) * 100:.2f}%；"
        "先列為 Q-量化候選，累積實戰結算後再調整權重。"
    )
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "target_date": target_date,
        "model": "Q-量化模型 XGBoost/Poisson Bundle 2026-07-16",
        "model_dir": str(MODEL_DIR.relative_to(ROOT)),
        "metadata": metadata,
        "summary": {
            "games": len(rows),
            "aligned_with_existing": sum(1 for row in rows if row["model_alignment"] == "一致"),
            "complete_data": sum(1 for row in rows if row["data_quality"] == "完整"),
            "holdout_moneyline_accuracy": metadata.get("metadata", {}).get("metrics", {}).get("moneyline", {}).get("accuracy"),
            "holdout_total_runs_mae": metadata.get("metadata", {}).get("metrics", {}).get("totals", {}).get("total_runs_mae"),
        },
        "review": {
            "is_more_perfect": False,
            "conclusion": better_note,
            "recommended_use": "先作為候選確認模型；只有在同場實戰準確率連續優於 A-畢氏勝率與統一得分模型後，才提高為主模型權重。",
            "next_improvements": [
                "用本專案 2026 已結算資料建立 Q 模型專屬結算紀錄。",
                "補足官方先發打線與缺陣，減少 lineup_missing。",
                "把台灣運彩盤口齊全後加入 edge/ROI 實測，而不是只看勝率。",
            ],
        },
        "games": rows,
    }


def write_csv(report: dict, path: Path) -> None:
    rows = report.get("games", [])
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = [
        "date",
        "game_time_tw",
        "game_pk",
        "status",
        "matchup_zh",
        "quant_prediction_zh",
        "quant_confidence",
        "predicted_away_score",
        "predicted_home_score",
        "predicted_total",
        "total_line",
        "totals_pick_zh",
        "totals_probability",
        "model_alignment",
        "data_quality",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def pct(value: Any) -> str:
    return f"{safe_float(value) * 100:.1f}%"


def render_html(report: dict) -> str:
    summary = report.get("summary", {})
    metadata = report.get("metadata", {}).get("metadata", {})
    rows = []
    for row in report.get("games", []):
        missing = ", ".join(row.get("missing_features", [])) or "-"
        rows.append(
            f"""
            <tr>
              <td>{html.escape(str(row.get('game_time_tw', '')))}</td>
              <td>{html.escape(str(row.get('status', '')))}</td>
              <td>{html.escape(str(row.get('matchup_zh', '')))}<span>GamePk {html.escape(str(row.get('game_pk', '')))}</span></td>
              <td>{html.escape(str(row.get('quant_prediction_zh', '')))}<span>{pct(row.get('quant_confidence'))}</span></td>
              <td>{html.escape(str(row.get('away_zh', '')))} {row.get('predicted_away_score')} : {html.escape(str(row.get('home_zh', '')))} {row.get('predicted_home_score')}</td>
              <td>{row.get('predicted_total')}<span>盤口 {row.get('total_line')} / {html.escape(str(row.get('totals_pick_zh', '')))} {pct(row.get('totals_probability'))}</span></td>
              <td><b class="pill {'ok' if row.get('model_alignment') == '一致' else 'warn'}">{html.escape(str(row.get('model_alignment', '')))}</b></td>
              <td>{html.escape(str(row.get('data_quality', '')))}<span>{html.escape(missing)}</span></td>
            </tr>
            """
        )
    rows_html = "\n".join(rows) or '<tr><td colspan="8">今天沒有可預測賽事。</td></tr>'
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MLB Q-量化模型</title>
  <style>
    body {{ margin: 0; background: #f5f7f4; color: #14211d; font-family: "Noto Sans TC", "Microsoft JhengHei", Arial, sans-serif; }}
    main {{ width: min(1180px, calc(100% - 48px)); margin: 0 auto; padding: 28px 0 48px; }}
    header {{ display: flex; justify-content: space-between; gap: 18px; align-items: end; border-bottom: 1px solid #dfe6df; padding-bottom: 18px; margin-bottom: 22px; }}
    h1 {{ margin: 0 0 8px; font-size: 30px; letter-spacing: 0; }}
    p {{ margin: 0; color: #65736d; line-height: 1.65; }}
    .cards {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin: 18px 0; }}
    .card {{ background: #fff; border: 1px solid #dfe6df; border-radius: 8px; padding: 15px; }}
    .card span, td span {{ display: block; color: #66756f; font-size: 12px; margin-top: 5px; line-height: 1.45; }}
    .card strong {{ display: block; font-size: 26px; margin-top: 6px; }}
    section {{ background: #fff; border: 1px solid #dfe6df; border-radius: 8px; padding: 16px; margin-top: 14px; }}
    h2 {{ margin: 0 0 10px; font-size: 20px; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; margin-top: 12px; }}
    th, td {{ text-align: left; border-bottom: 1px solid #e4e9e4; padding: 10px 9px; vertical-align: top; font-size: 14px; }}
    th {{ background: #eef5f1; color: #40514b; font-size: 12px; }}
    .pill {{ display: inline-block; border-radius: 999px; padding: 4px 8px; font-size: 12px; }}
    .ok {{ background: #dff3ea; color: #12634f; }}
    .warn {{ background: #fde7e7; color: #993c3c; }}
    @media (max-width: 900px) {{ .cards {{ grid-template-columns: 1fr 1fr; }} header {{ display: block; }} table {{ min-width: 980px; }} section.table-wrap {{ overflow-x: auto; }} }}
  </style>
</head>
<body>
<main>
  <header>
    <div>
      <h1>MLB Q-量化模型</h1>
      <p>新增桌面模型包：XGBoost 獨贏 + Poisson 大小分。先作為候選模型，和現有正式模型同場對比。</p>
    </div>
    <p>目標日期：{html.escape(str(report.get('target_date', '')))}<br>產生時間：{html.escape(str(report.get('generated_at', '')))}</p>
  </header>
  <div class="cards">
    <div class="card"><span>今日賽事</span><strong>{summary.get('games', 0)}</strong></div>
    <div class="card"><span>與現有模型同向</span><strong>{summary.get('aligned_with_existing', 0)}</strong></div>
    <div class="card"><span>Bundle holdout 勝率</span><strong>{pct(summary.get('holdout_moneyline_accuracy'))}</strong></div>
    <div class="card"><span>Bundle 總分 MAE</span><strong>{safe_float(summary.get('holdout_total_runs_mae')):.2f}</strong></div>
  </div>
  <section>
    <h2>是否更完美</h2>
    <p>{html.escape(str(report.get('review', {}).get('conclusion', '')))}</p>
    <p>{html.escape(str(report.get('review', {}).get('recommended_use', '')))}</p>
    <p>訓練列數：{metadata.get('train_rows', '-')} / holdout：{metadata.get('holdout_rows', '-')} / 訓練時間：{html.escape(str(report.get('metadata', {}).get('trained_at', '-')))}</p>
  </section>
  <section class="table-wrap">
    <h2>今日 Q 模型預測</h2>
    <table>
      <thead><tr><th>台灣時間</th><th>狀態</th><th>對戰</th><th>Q 勝方</th><th>Q 比分</th><th>Q 大小分</th><th>搭配</th><th>資料品質</th></tr></thead>
      <tbody>{rows_html}</tbody>
    </table>
  </section>
</main>
</body>
</html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Run imported MLB Quant model bundle.")
    parser.add_argument("--date", required=True, help="Target date in YYYY-MM-DD.")
    args = parser.parse_args()
    report = build_report(args.date)
    json_path = Path(str(OUTPUT_JSON).format(date=args.date))
    csv_path = Path(str(OUTPUT_CSV).format(date=args.date))
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(report, csv_path)
    OUTPUT_HTML.write_text(render_html(report), encoding="utf-8")
    print(f"quant_predictions={len(report.get('games', []))} output={json_path}")


if __name__ == "__main__":
    main()
