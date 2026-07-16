#!/usr/bin/env python3
"""Publish versioned JSON files that other websites can consume."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
API_DIR = ROOT / "docs" / "api" / "v1"
SITE_BASE = "https://raw.githubusercontent.com/lolochen86-jpg/mlb-betting-plan/main/docs"


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return {}


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def game_date_tw(row: dict) -> str:
    value = str(row.get("game_time_tw") or "")
    return value[:10] if len(value) >= 10 else ""


def build_monte_carlo(target_date: str) -> dict:
    previous_date = (date.fromisoformat(target_date) - timedelta(days=1)).isoformat()
    previous = load_json(DATA_DIR / f"monte_carlo_{previous_date}.json")
    current = load_json(DATA_DIR / f"monte_carlo_{target_date}.json")
    today_games = [
        {**row, "mlb_date": previous_date, "taiwan_date": target_date}
        for row in previous.get("games", [])
        if game_date_tw(row) == target_date
    ]
    future_games = [
        {**row, "mlb_date": target_date, "taiwan_date": game_date_tw(row)}
        for row in current.get("games", [])
    ]
    return {
        "api_version": "v1",
        "taiwan_display_date": target_date,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "simulations_per_game": current.get("simulations_per_game", 10000),
        "taiwan_today": today_games,
        "taiwan_future": future_games,
        "games": today_games + future_games,
    }


def build_daily(target_date: str) -> dict:
    previous_date = (date.fromisoformat(target_date) - timedelta(days=1)).isoformat()
    previous = load_json(DATA_DIR / f"daily_predictions_{previous_date}.json")
    payload = load_json(DATA_DIR / f"daily_predictions_{target_date}.json")
    pending = payload.get("pending_unsettled_predictions", [])
    today_predictions = [
        {**row, "mlb_date": previous_date, "taiwan_date": target_date}
        for row in previous.get("all_predictions", [])
        if game_date_tw(row) == target_date
    ]
    future_predictions = [
        {**row, "mlb_date": target_date, "taiwan_date": game_date_tw(row)}
        for row in payload.get("all_predictions", [])
    ]
    return {
        "api_version": "v1",
        "taiwan_display_date": target_date,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "models": payload.get("models", {}),
        "data_source": payload.get("data_source", {}),
        "postgame_calibration": payload.get("postgame_calibration", {}),
        "taiwan_today": today_predictions,
        "taiwan_future": future_predictions,
        "taiwan_today_pending": [row for row in pending if game_date_tw(row) == target_date],
        "taiwan_future_pending": [row for row in pending if game_date_tw(row) > target_date],
        "all_predictions": today_predictions + future_predictions,
        "high_confidence_predictions": payload.get("high_confidence_predictions", []),
    }


def copy_json(source: Path, destination: Path) -> bool:
    if not source.exists():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return True


def render_docs(target_date: str, endpoints: dict[str, str]) -> str:
    rows = "".join(
        f'<tr><td><code>{name}</code></td><td><a href="{url}">{url}</a></td></tr>'
        for name, url in endpoints.items()
    )
    example_url = endpoints["latest_monte_carlo"]
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>MLB 預測資料 API</title>
  <style>
    body {{ margin:0; background:#f5f7f5; color:#1e2924; font-family:"Microsoft JhengHei","Noto Sans TC",system-ui,sans-serif; }}
    main {{ max-width:980px; margin:0 auto; padding:32px 20px 48px; }}
    h1 {{ margin:0 0 8px; }} h2 {{ margin-top:28px; }}
    p {{ color:#61706a; line-height:1.7; }}
    table {{ width:100%; border-collapse:collapse; background:#fff; border:1px solid #dce4df; }}
    th,td {{ padding:12px; border-bottom:1px solid #dce4df; text-align:left; }}
    code,pre {{ font-family:Consolas,monospace; }}
    pre {{ overflow:auto; padding:16px; background:#17231f; color:#e8f3ee; border-radius:8px; }}
    a {{ color:#126458; }}
  </style>
</head>
<body>
<main>
  <h1>MLB 預測資料 API</h1>
  <p>公開唯讀 JSON API，更新日期：{target_date}。GitHub Pages 靜態端點不需要 API Key。</p>
  <h2>端點</h2>
  <table><thead><tr><th>資料</th><th>網址</th></tr></thead><tbody>{rows}</tbody></table>
  <h2>JavaScript 範例</h2>
  <pre><code>const response = await fetch("{example_url}");
const data = await response.json();
console.log(data.taiwan_today);</code></pre>
  <p>投注資料僅供模型研究，不保證獲利。其他網站應顯示資料產生時間與來源。</p>
</main>
</body>
</html>"""


def generate(target_date: str) -> None:
    latest = API_DIR / "latest"
    archive = API_DIR / "dates" / target_date
    daily = build_daily(target_date)
    monte_carlo = build_monte_carlo(target_date)
    write_json(latest / "daily-predictions.json", daily)
    write_json(latest / "monte-carlo.json", monte_carlo)
    write_json(archive / "daily-predictions.json", daily)
    write_json(archive / "monte-carlo.json", monte_carlo)

    sources = {
        "unified-expectations.json": DATA_DIR / f"unified_expectations_{target_date}.json",
        "totals.json": DATA_DIR / f"totals_predictions_{target_date}.json",
        "markets.json": DATA_DIR / "odds" / f"taiwan_sportslottery_markets_{target_date}.json",
        "betting-roi.json": DATA_DIR / f"betting_roi_{target_date}.json",
        "settlement.json": DATA_DIR / f"prediction_settlement_{target_date}.json",
        "postgame-review.json": DATA_DIR / "postgame_review.json",
        "quant-model.json": DATA_DIR / f"quant_model_predictions_{target_date}.json",
    }
    for filename, source in sources.items():
        copy_json(source, latest / filename)
        copy_json(source, archive / filename)

    endpoint_names = ["daily-predictions.json", "monte-carlo.json", *sources.keys()]
    endpoints = {
        f"latest_{name.removesuffix('.json').replace('-', '_')}": f"{SITE_BASE}/api/v1/latest/{name}"
        for name in endpoint_names
    }
    manifest = {
        "api_version": "v1",
        "target_date": target_date,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "base_url": f"{SITE_BASE}/api/v1",
        "endpoints": endpoints,
        "archive_pattern": f"{SITE_BASE}/api/v1/dates/YYYY-MM-DD/{{resource}}.json",
    }
    write_json(API_DIR / "manifest.json", manifest)
    docs_path = ROOT / "docs" / "api" / "index.html"
    docs_path.parent.mkdir(parents=True, exist_ok=True)
    docs_path.write_text(render_docs(target_date, endpoints), encoding="utf-8")
    print(f"api_target_date={target_date}")
    print(f"api_today_games={len(monte_carlo['taiwan_today'])}")
    print(f"api_future_games={len(monte_carlo['taiwan_future'])}")
    print(f"wrote {API_DIR}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate public static JSON API files.")
    parser.add_argument("--date", default=date.today().isoformat())
    return parser.parse_args()


if __name__ == "__main__":
    generate(parse_args().date)
