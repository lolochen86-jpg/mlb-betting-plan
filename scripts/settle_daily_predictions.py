#!/usr/bin/env python3
"""Settle daily MLB winner predictions against real final scores.

This is still accuracy-only. It does not read odds and does not calculate ROI.
"""

from __future__ import annotations

import argparse
import csv
import json
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

from fetch_real_mlb_data import MLB_SCHEDULE_URL
from name_localization import team_zh


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"
PREDICTIONS_JSON = DATA_DIR / "daily_predictions_{date}.json"
SETTLEMENT_JSON = DATA_DIR / "prediction_settlement_{date}.json"
SETTLEMENT_CSV = DATA_DIR / "prediction_settlement_{date}.csv"
PREDICTION_LOG_CSV = DATA_DIR / "prediction_log.csv"
PREDICTION_LOG_HTML = DOCS_DIR / "prediction_log.html"


def request_json(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "betting-plan-settlement/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_results(target_date: str) -> dict[str, dict]:
    params = {
        "sportId": "1",
        "date": target_date,
        "hydrate": "team,linescore,probablePitcher",
    }
    payload = request_json(f"{MLB_SCHEDULE_URL}?{urllib.parse.urlencode(params)}")
    results = {}
    for day in payload.get("dates", []):
        for game in day.get("games", []):
            if game.get("gameType") != "R":
                continue
            home = game.get("teams", {}).get("home", {})
            away = game.get("teams", {}).get("away", {})
            home_team = home.get("team", {})
            away_team = away.get("team", {})
            game_pk = str(game.get("gamePk") or "")
            has_score = "score" in home and "score" in away and home.get("score") is not None and away.get("score") is not None
            status = game.get("status", {}).get("detailedState", "")
            row = {
                "game_pk": game_pk,
                "status": status,
                "home": home_team.get("name", ""),
                "home_zh": team_zh(home_team.get("name", "")),
                "away": away_team.get("name", ""),
                "away_zh": team_zh(away_team.get("name", "")),
                "home_score": int(home["score"]) if has_score else None,
                "away_score": int(away["score"]) if has_score else None,
                "is_final": status in {"Final", "Completed Early"} and has_score,
            }
            if row["is_final"]:
                row["actual_winner_zh"] = row["home_zh"] if row["home_score"] > row["away_score"] else row["away_zh"]
                row["score"] = f"{row['away_score']}-{row['home_score']}"
            else:
                row["actual_winner_zh"] = ""
                row["score"] = ""
            results[game_pk] = row
    return results


def settle_predictions(target_date: str) -> dict:
    prediction_path = Path(str(PREDICTIONS_JSON).format(date=target_date))
    if not prediction_path.exists():
        raise SystemExit(f"Missing prediction file: {prediction_path}")
    prediction_data = json.loads(prediction_path.read_text(encoding="utf-8"))
    results_by_pk = fetch_results(target_date)
    rows = []
    for pred in prediction_data.get("all_predictions", []):
        result = results_by_pk.get(str(pred.get("game_pk", "")), {})
        is_final = bool(result.get("is_final"))
        correct = None
        if is_final:
            correct = pred["prediction_zh"] == result["actual_winner_zh"]
        rows.append(
            {
                "date": target_date,
                "game_pk": pred.get("game_pk", ""),
                "decision": pred.get("decision", ""),
                "matchup_zh": pred.get("matchup_zh", ""),
                "prediction_zh": pred.get("prediction_zh", ""),
                "confidence": pred.get("confidence", 0),
                "confirmation_pick_zh": pred.get("confirmation_pick_zh", ""),
                "confirmation_same_direction": pred.get("confirmation_same_direction", False),
                "status": result.get("status", pred.get("status", "")),
                "score": result.get("score", ""),
                "actual_winner_zh": result.get("actual_winner_zh", ""),
                "settlement": "correct" if correct is True else "wrong" if correct is False else "pending",
                "is_final": is_final,
            }
        )

    final_rows = [row for row in rows if row["is_final"]]
    correct_rows = [row for row in final_rows if row["settlement"] == "correct"]
    high_rows = [row for row in final_rows if row["decision"] == "高信心預測"]
    high_correct = [row for row in high_rows if row["settlement"] == "correct"]
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "target_date": target_date,
        "source_prediction_file": str(prediction_path.relative_to(ROOT)),
        "summary": {
            "predictions": len(rows),
            "final_games": len(final_rows),
            "pending_games": len(rows) - len(final_rows),
            "correct": len(correct_rows),
            "wrong": len(final_rows) - len(correct_rows),
            "accuracy_pct": round(len(correct_rows) / len(final_rows) * 100, 2) if final_rows else 0,
            "high_confidence_final": len(high_rows),
            "high_confidence_correct": len(high_correct),
            "high_confidence_accuracy_pct": round(len(high_correct) / len(high_rows) * 100, 2) if high_rows else 0,
        },
        "settlements": rows,
        "note": "Accuracy settlement only. Odds and betting ROI are not calculated here.",
    }


def write_settlement(settlement: dict) -> None:
    target_date = settlement["target_date"]
    json_path = Path(str(SETTLEMENT_JSON).format(date=target_date))
    csv_path = Path(str(SETTLEMENT_CSV).format(date=target_date))
    json_path.write_text(json.dumps(settlement, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = [
        "date",
        "decision",
        "matchup_zh",
        "prediction_zh",
        "confidence",
        "confirmation_pick_zh",
        "status",
        "score",
        "actual_winner_zh",
        "settlement",
        "is_final",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(settlement["settlements"])
    rebuild_prediction_log()
    print(f"wrote {json_path}")
    print(f"wrote {csv_path}")
    print(f"wrote {PREDICTION_LOG_CSV}")
    print(f"wrote {PREDICTION_LOG_HTML}")
    summary = settlement["summary"]
    print(
        f"final={summary['final_games']} pending={summary['pending_games']} "
        f"correct={summary['correct']} accuracy={summary['accuracy_pct']}%"
    )


def rebuild_prediction_log() -> None:
    rows = []
    for path in sorted(DATA_DIR.glob("prediction_settlement_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for row in data.get("settlements", []):
            rows.append(row)
    rows.sort(key=lambda row: (row["date"], row.get("game_time_utc", ""), row.get("game_pk", "")), reverse=True)
    fields = [
        "date",
        "decision",
        "matchup_zh",
        "prediction_zh",
        "confidence",
        "confirmation_pick_zh",
        "status",
        "score",
        "actual_winner_zh",
        "settlement",
        "is_final",
    ]
    with PREDICTION_LOG_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    PREDICTION_LOG_HTML.write_text(render_log_html(rows), encoding="utf-8")


def render_log_html(rows: list[dict]) -> str:
    final_rows = [row for row in rows if str(row.get("is_final")).lower() == "true" or row.get("is_final") is True]
    correct = [row for row in final_rows if row.get("settlement") == "correct"]
    accuracy = round(len(correct) / len(final_rows) * 100, 2) if final_rows else 0
    body = "\n".join(
        f"""
        <tr>
          <td>{row.get('date', '')}</td>
          <td>{row.get('decision', '')}</td>
          <td>{row.get('matchup_zh', '')}</td>
          <td>{row.get('prediction_zh', '')}</td>
          <td>{float(row.get('confidence') or 0) * 100:.1f}%</td>
          <td>{row.get('score', '')}</td>
          <td>{row.get('actual_winner_zh', '')}</td>
          <td>{'正確' if row.get('settlement') == 'correct' else '錯誤' if row.get('settlement') == 'wrong' else '待結算'}</td>
        </tr>"""
        for row in rows
    )
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>MLB 實戰預測結算紀錄</title>
  <style>
    body {{ margin: 0; background: #f7f8f6; color: #202421; font-family: "Microsoft JhengHei", "Noto Sans TC", system-ui, sans-serif; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 28px; }}
    h1 {{ margin: 0 0 6px; font-size: 30px; }}
    .meta {{ color: #68736d; line-height: 1.6; font-size: 14px; margin-bottom: 18px; }}
    table {{ width: 100%; border-collapse: collapse; background: white; border: 1px solid #dfe5df; border-radius: 8px; overflow: hidden; }}
    th, td {{ text-align: left; border-bottom: 1px solid #dfe5df; padding: 12px 10px; white-space: nowrap; font-size: 14px; }}
    th {{ color: #68736d; font-size: 12px; }}
    .note {{ margin-top: 16px; padding: 12px 14px; border: 1px solid #dfe5df; border-radius: 8px; background: white; color: #68736d; }}
    @media (max-width: 720px) {{ main {{ padding: 18px; }} table {{ display: block; overflow-x: auto; }} }}
  </style>
</head>
<body>
  <main>
    <h1>MLB 實戰預測結算紀錄</h1>
    <div class="meta">已完賽：{len(final_rows)} / 正確：{len(correct)} / 準確率：{accuracy:.2f}%</div>
    <table>
      <thead><tr><th>日期</th><th>類型</th><th>對戰</th><th>預測勝方</th><th>信心</th><th>比分</th><th>實際勝方</th><th>結果</th></tr></thead>
      <tbody>{body}</tbody>
    </table>
    <div class="note">此頁只追蹤勝方預測準確率；盤口與投注 ROI 將在真實盤口資料補上後另行計算。</div>
  </main>
</body>
</html>"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Settle daily MLB predictions against final scores.")
    parser.add_argument("--date", default=date.today().isoformat(), help="Target date in YYYY-MM-DD.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    write_settlement(settle_predictions(args.date))


if __name__ == "__main__":
    main()
