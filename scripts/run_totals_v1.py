#!/usr/bin/env python3
"""Generate MLB totals over/under v1 predictions from real scores and Taiwan Sports Lottery odds."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from fetch_taiwan_sportslottery_odds import BASEBALL_GAMES_URL, decimal_odds, normalize_team_name, request_json
from run_real_mlb_backtest import DEFAULT_GAMES_CSV, load_games


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"
ODDS_DIR = DATA_DIR / "odds"

DAILY_PREDICTIONS_JSON = DATA_DIR / "daily_predictions_{date}.json"
TAIWAN_SOURCE_JSON = ODDS_DIR / "taiwan_sportslottery_baseball_source_{date}.json"
TOTALS_JSON = DATA_DIR / "totals_predictions_{date}.json"
TOTALS_CSV = DATA_DIR / "totals_predictions_{date}.csv"
TOTALS_HTML = DOCS_DIR / "totals_predictions.html"


def normal_cdf(x: float, mean: float, sigma: float) -> float:
    return 0.5 * (1 + math.erf((x - mean) / (sigma * math.sqrt(2))))


def read_daily_predictions(target_date: str) -> list[dict]:
    path = Path(str(DAILY_PREDICTIONS_JSON).format(date=target_date))
    if not path.exists():
        raise SystemExit(f"Missing daily predictions: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("all_predictions", [])


def load_or_fetch_taiwan_source(target_date: str) -> list[dict]:
    path = Path(str(TAIWAN_SOURCE_JSON).format(date=target_date))
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    games = request_json(BASEBALL_GAMES_URL)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(games, ensure_ascii=False, indent=2), encoding="utf-8")
    return games


def implied(decimal_value: float) -> float:
    return 1 / decimal_value


def extract_totals_markets(games: list[dict]) -> dict[tuple[str, str], dict]:
    records = {}
    for game in games:
        away_zh = normalize_team_name(game.get("an", ""))
        home_zh = normalize_team_name(game.get("hn", ""))
        markets = []
        for market in game.get("ms", []):
            name = str(market.get("name", ""))
            if market.get("ti") != "360" or not name.startswith("[總分]大小"):
                continue
            over = under = None
            for selection in market.get("cs", []):
                if not selection.get("pd") or not selection.get("pu") or not selection.get("hv"):
                    continue
                decimal_value = float(decimal_odds(selection))
                row = {
                    "selection": selection.get("name", ""),
                    "line": float(selection.get("hv")),
                    "odds": decimal_value,
                    "implied": implied(decimal_value),
                }
                if str(selection.get("name", "")).startswith("大"):
                    over = row
                elif str(selection.get("name", "")).startswith("小"):
                    under = row
            if over and under and over["line"] == under["line"]:
                markets.append(
                    {
                        "line": over["line"],
                        "over_odds": over["odds"],
                        "under_odds": under["odds"],
                        "over_implied": over["implied"],
                        "under_implied": under["implied"],
                        "balance": abs(over["implied"] - under["implied"]),
                        "market_name": name,
                    }
                )
        if markets:
            markets.sort(key=lambda row: (row["balance"], abs(row["line"] - 8.5)))
            records[(away_zh, home_zh)] = markets[0]
    return records


def avg(values: list[float], fallback: float) -> float:
    return sum(values) / len(values) if values else fallback


def team_total_stats(history: list[dict], recent_games: int) -> tuple[dict, float, float]:
    totals = [float(game["away_score"]) + float(game["home_score"]) for game in history]
    league_total = avg(totals, 8.7)
    sigma = max(2.8, math.sqrt(avg([(total - league_total) ** 2 for total in totals], 10.0)))
    teams = defaultdict(lambda: {"for": [], "against": [], "recent_for": [], "recent_against": []})
    for game in history:
        away = game["away_zh"]
        home = game["home_zh"]
        away_runs = float(game["away_score"])
        home_runs = float(game["home_score"])
        teams[away]["for"].append(away_runs)
        teams[away]["against"].append(home_runs)
        teams[home]["for"].append(home_runs)
        teams[home]["against"].append(away_runs)
    for values in teams.values():
        values["recent_for"] = values["for"][-recent_games:]
        values["recent_against"] = values["against"][-recent_games:]
    return teams, league_total, sigma


def predict_total(away_zh: str, home_zh: str, teams: dict, league_total: float) -> float:
    league_team_runs = league_total / 2

    def team_runs_for(team: str) -> float:
        row = teams.get(team, {})
        return 0.60 * avg(row.get("recent_for", []), league_team_runs) + 0.40 * avg(row.get("for", []), league_team_runs)

    def team_runs_allowed(team: str) -> float:
        row = teams.get(team, {})
        return 0.60 * avg(row.get("recent_against", []), league_team_runs) + 0.40 * avg(row.get("against", []), league_team_runs)

    away_expected = 0.55 * team_runs_for(away_zh) + 0.45 * team_runs_allowed(home_zh)
    home_expected = 0.55 * team_runs_for(home_zh) + 0.45 * team_runs_allowed(away_zh)
    return away_expected + home_expected


def build_report(target_date: str, recent_games: int, min_edge: float) -> dict:
    history = [game for game in load_games(DEFAULT_GAMES_CSV) if game["date"] < target_date]
    daily_rows = read_daily_predictions(target_date)
    taiwan_games = load_or_fetch_taiwan_source(target_date)
    totals_markets = extract_totals_markets(taiwan_games)
    teams, league_total, sigma = team_total_stats(history, recent_games)
    predictions = []
    skipped = []
    for row in daily_rows:
        key = (normalize_team_name(row.get("away_zh", "")), normalize_team_name(row.get("home_zh", "")))
        market = totals_markets.get(key)
        if not market:
            skipped.append({**row, "skip_reason": "沒有台灣運彩全場大小分盤口"})
            continue
        predicted_total = predict_total(key[0], key[1], teams, league_total)
        line = float(market["line"])
        over_prob = 1 - normal_cdf(line, predicted_total, sigma)
        under_prob = 1 - over_prob
        pick = "大分" if predicted_total > line else "小分"
        model_prob = over_prob if pick == "大分" else under_prob
        odds = market["over_odds"] if pick == "大分" else market["under_odds"]
        market_prob = implied(float(odds))
        edge = model_prob - market_prob
        decision = "大小分候選" if edge >= min_edge else "不推薦"
        predictions.append(
            {
                "date": target_date,
                "game_pk": row.get("game_pk", ""),
                "sportsbook": "台灣運彩",
                "matchup_zh": row.get("matchup_zh", ""),
                "line": line,
                "predicted_total": round(predicted_total, 2),
                "pick": pick,
                "odds": odds,
                "model_prob": round(model_prob, 4),
                "market_implied_prob": round(market_prob, 4),
                "edge": round(edge, 4),
                "decision": decision,
                "status": row.get("status", ""),
            }
        )
    predictions.sort(key=lambda row: (row["decision"] == "大小分候選", row["edge"]), reverse=True)
    candidates = [row for row in predictions if row["decision"] == "大小分候選"]
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "target_date": target_date,
        "model": "大小分 v1 - 近期/整季得失分混合",
        "settings": {"recent_games": recent_games, "min_edge": min_edge, "require_sportsbook": "台灣運彩"},
        "data_source": {
            "training_games": len(history),
            "last_training_date": history[-1]["date"] if history else "",
            "odds_source": "台灣運彩官方全場總分大小 ti=360",
            "league_avg_total": round(league_total, 2),
            "total_sigma": round(sigma, 2),
        },
        "summary": {
            "games": len(daily_rows),
            "with_totals_market": len(predictions),
            "candidates": len(candidates),
            "skipped": len(skipped),
        },
        "candidates": candidates,
        "all_predictions": predictions,
        "skipped": skipped,
    }


def write_outputs(report: dict) -> None:
    target_date = report["target_date"]
    json_path = Path(str(TOTALS_JSON).format(date=target_date))
    csv_path = Path(str(TOTALS_CSV).format(date=target_date))
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = [
        "date",
        "game_pk",
        "sportsbook",
        "matchup_zh",
        "line",
        "predicted_total",
        "pick",
        "odds",
        "model_prob",
        "market_implied_prob",
        "edge",
        "decision",
        "status",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(report["all_predictions"])
    TOTALS_HTML.write_text(render_html(report), encoding="utf-8")
    print(f"wrote {json_path}")
    print(f"wrote {csv_path}")
    print(f"wrote {TOTALS_HTML}")
    print(
        f"totals_markets={report['summary']['with_totals_market']} "
        f"candidates={report['summary']['candidates']} skipped={report['summary']['skipped']}"
    )


def render_rows(rows: list[dict]) -> str:
    if not rows:
        return '<tr><td colspan="10">目前沒有大小分候選。</td></tr>'
    return "\n".join(
        f"""
        <tr>
          <td>{row.get('game_pk', '')}</td>
          <td>{row.get('matchup_zh', '')}</td>
          <td>{row.get('line', '')}</td>
          <td>{row.get('predicted_total', '')}</td>
          <td>{row.get('pick', '')}</td>
          <td>{row.get('odds', '')}</td>
          <td>{float(row.get('model_prob') or 0) * 100:.1f}%</td>
          <td>{float(row.get('market_implied_prob') or 0) * 100:.1f}%</td>
          <td>{float(row.get('edge') or 0) * 100:.1f}%</td>
          <td>{row.get('decision', '')}</td>
        </tr>"""
        for row in rows
    )


def render_html(report: dict) -> str:
    candidate_rows = render_rows(report["candidates"])
    all_rows = render_rows(report["all_predictions"])
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>MLB 大小分 v1</title>
  <style>
    body {{ margin: 0; background: #f7f8f6; color: #202421; font-family: "Microsoft JhengHei", "Noto Sans TC", system-ui, sans-serif; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 28px; }}
    h1 {{ margin: 0 0 6px; font-size: 30px; }}
    h2 {{ margin: 24px 0 12px; font-size: 18px; }}
    .meta, .note {{ color: #68736d; line-height: 1.6; font-size: 14px; }}
    table {{ width: 100%; border-collapse: collapse; background: white; border: 1px solid #dfe5df; border-radius: 8px; overflow: hidden; }}
    th, td {{ text-align: left; border-bottom: 1px solid #dfe5df; padding: 12px 10px; white-space: nowrap; font-size: 14px; }}
    th {{ color: #68736d; font-size: 12px; }}
    .note {{ margin-top: 16px; padding: 12px 14px; border: 1px solid #dfe5df; border-radius: 8px; background: white; }}
    @media (max-width: 720px) {{ main {{ padding: 18px; }} table {{ display: block; overflow-x: auto; }} }}
  </style>
</head>
<body>
  <main>
    <h1>MLB 大小分 v1</h1>
    <div class="meta">
      日期：{report['target_date']}<br />
      模型：{report['model']}<br />
      訓練場數：{report['data_source']['training_games']} / 訓練截止：{report['data_source']['last_training_date']}<br />
      台灣運彩全場大小分盤：{report['summary']['with_totals_market']} / 候選：{report['summary']['candidates']}<br />
      聯盟平均總分：{report['data_source']['league_avg_total']} / 波動參數：{report['data_source']['total_sigma']}<br />
      產生時間：{report['generated_at']}
    </div>
    <h2>大小分候選</h2>
    <table>
      <thead><tr><th>GamePk</th><th>對戰</th><th>台灣運彩線</th><th>模型總分</th><th>方向</th><th>賠率</th><th>模型機率</th><th>市場隱含</th><th>Edge</th><th>決策</th></tr></thead>
      <tbody>{candidate_rows}</tbody>
    </table>
    <h2>全部大小分預測</h2>
    <table>
      <thead><tr><th>GamePk</th><th>對戰</th><th>台灣運彩線</th><th>模型總分</th><th>方向</th><th>賠率</th><th>模型機率</th><th>市場隱含</th><th>Edge</th><th>決策</th></tr></thead>
      <tbody>{all_rows}</tbody>
    </table>
    <div class="note">大小分 v1 只使用台灣運彩全場總分大小盤，不使用 ESPN 備援；目前是模型驗證層，尚未併入主投注單。</div>
  </main>
</body>
</html>"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate MLB totals over/under v1 predictions.")
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--recent-games", type=int, default=20)
    parser.add_argument("--min-edge", type=float, default=0.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = build_report(args.date, args.recent_games, args.min_edge)
    write_outputs(report)


if __name__ == "__main__":
    main()
