#!/usr/bin/env python3
"""Generate a Chinese daily MLB winner prediction plan from real saved scores."""

from __future__ import annotations

import argparse
import csv
import json
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fetch_real_mlb_data import MLB_SCHEDULE_URL
from name_localization import player_zh, team_zh
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
DAILY_PLAN_JSON = DATA_DIR / "daily_predictions_{date}.json"
DAILY_PLAN_CSV = DATA_DIR / "daily_predictions_{date}.csv"
DAILY_PLAN_HTML = DOCS_DIR / "daily_predictions.html"
MONTE_CARLO_JSON = DATA_DIR / "monte_carlo_{date}.json"
TW_TZ = ZoneInfo("Asia/Taipei")


def request_json(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "betting-plan-daily/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_schedule(target_date: str) -> list[dict]:
    params = {
        "sportId": "1",
        "date": target_date,
        "hydrate": "team,probablePitcher,linescore",
    }
    payload = request_json(f"{MLB_SCHEDULE_URL}?{urllib.parse.urlencode(params)}")
    games = []
    for day in payload.get("dates", []):
        for game in day.get("games", []):
            if game.get("gameType") != "R":
                continue
            home = game.get("teams", {}).get("home", {})
            away = game.get("teams", {}).get("away", {})
            home_team = home.get("team", {})
            away_team = away.get("team", {})
            home_pitcher = home.get("probablePitcher", {}) or {}
            away_pitcher = away.get("probablePitcher", {}) or {}
            games.append(
                {
                    "date": target_date,
                    "game_pk": str(game.get("gamePk") or ""),
                    "game_time_utc": game.get("gameDate", ""),
                    "game_time_tw": game_time_tw(game.get("gameDate", "")),
                    "status": game.get("status", {}).get("detailedState", ""),
                    "home": home_team.get("name", ""),
                    "home_team_id": home_team.get("id"),
                    "home_zh": team_zh(home_team.get("name", "")),
                    "away": away_team.get("name", ""),
                    "away_team_id": away_team.get("id"),
                    "away_zh": team_zh(away_team.get("name", "")),
                    "home_probable_pitcher": home_pitcher.get("fullName", ""),
                    "home_probable_pitcher_id": home_pitcher.get("id"),
                    "home_probable_pitcher_zh": player_zh(home_pitcher.get("fullName", "")),
                    "away_probable_pitcher": away_pitcher.get("fullName", ""),
                    "away_probable_pitcher_id": away_pitcher.get("id"),
                    "away_probable_pitcher_zh": player_zh(away_pitcher.get("fullName", "")),
                }
            )
    games.sort(key=lambda row: (row.get("game_time_utc") or "", int(row["game_pk"] or 0)))
    return games


def game_time_tw(game_date: str) -> str:
    if not game_date:
        return "未公布"
    try:
        parsed = datetime.fromisoformat(game_date.replace("Z", "+00:00"))
    except ValueError:
        return "未公布"
    return parsed.astimezone(TW_TZ).strftime("%Y-%m-%d %H:%M")


def train_models(games: list[dict]) -> tuple[TeamStats, dict[str, object]]:
    stats = TeamStats()
    base_models = [ModelA(), ModelB(), ModelC(), ModelD()]
    ensemble = ModelE(base_models)
    models = [*base_models, ensemble]
    by_date: dict[str, list[dict]] = defaultdict(list)
    for game in games:
        by_date[game["date"]].append(game)

    for index, day in enumerate(sorted(by_date)):
        games_today = by_date[day]
        if index % 7 == 0:
            ensemble.recalibrate()
        for model in models:
            for game in games_today:
                prob_home = model.predict(game["home"], game["away"], stats)
                if prob_home is None:
                    continue
                picked_home = prob_home >= 0.5
                home_win = game["home_score"] > game["away_score"]
                model.history.append(picked_home == home_win)
        for game in games_today:
            stats.update(game["home"], game["away"], game["home_score"], game["away_score"])
    ensemble.recalibrate()
    return stats, {model.name: model for model in models}


def pick_from_probability(home_zh: str, away_zh: str, prob_home: float) -> dict:
    if prob_home >= 0.5:
        return {"side": "home", "team_zh": home_zh, "confidence": prob_home}
    return {"side": "away", "team_zh": away_zh, "confidence": 1 - prob_home}


def load_score_predictions(target_date: str) -> dict[str, dict]:
    path = Path(str(MONTE_CARLO_JSON).format(date=target_date))
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {}
    rows = payload.get("games", [])
    return {str(row.get("game_pk", "")): row for row in rows if str(row.get("game_pk", ""))}


def merge_score_predictions(rows: list[dict], target_date: str) -> None:
    scores = load_score_predictions(target_date)
    for row in rows:
        score = scores.get(str(row.get("game_pk", "")))
        if not score:
            row.update(
                {
                    "predicted_away_score": None,
                    "predicted_home_score": None,
                    "predicted_total": None,
                    "score_prediction_zh": "-",
                    "total_prediction_zh": "-",
                    "monte_carlo_pick_zh": "-",
                }
            )
            continue
        away_score = score.get("avg_away_score")
        home_score = score.get("avg_home_score")
        total = score.get("avg_total")
        row.update(
            {
                "predicted_away_score": away_score,
                "predicted_home_score": home_score,
                "predicted_total": total,
                "score_prediction_zh": f"{row['away_zh']} {away_score:.2f} : {row['home_zh']} {home_score:.2f}",
                "total_prediction_zh": f"{total:.2f}",
                "monte_carlo_pick_zh": score.get("moneyline_pick", "-"),
            }
        )


def build_daily_plan(target_date: str, games_csv: Path, min_confidence: float) -> dict:
    history = [game for game in load_games(games_csv) if game["date"] < target_date]
    schedule = fetch_schedule(target_date)
    stats, models = train_models(history)
    production_name = "A-畢氏勝率"
    confirmation_name = "E-對照組(Ensemble)"
    production_model = models[production_name]
    confirmation_model = models[confirmation_name]
    candidates = []

    for game in schedule:
        prod_prob = production_model.predict(game["home"], game["away"], stats)
        conf_prob = confirmation_model.predict(game["home"], game["away"], stats)
        if prod_prob is None:
            continue
        prod_pick = pick_from_probability(game["home_zh"], game["away_zh"], prod_prob)
        conf_pick = pick_from_probability(game["home_zh"], game["away_zh"], conf_prob) if conf_prob is not None else None
        same_direction = bool(conf_pick and conf_pick["side"] == prod_pick["side"])
        confidence_pass = prod_pick["confidence"] >= min_confidence
        candidates.append(
            {
                "date": target_date,
                "game_pk": game["game_pk"],
                "game_time_utc": game.get("game_time_utc", ""),
                "game_time_tw": game.get("game_time_tw", "未公布"),
                "status": game["status"],
                "matchup_zh": f"{game['away_zh']} @ {game['home_zh']}",
                "away_zh": game["away_zh"],
                "away_team_id": game.get("away_team_id"),
                "home_zh": game["home_zh"],
                "home_team_id": game.get("home_team_id"),
                "away_probable_pitcher_zh": game["away_probable_pitcher_zh"] or "未公布",
                "away_probable_pitcher_id": game.get("away_probable_pitcher_id"),
                "home_probable_pitcher_zh": game["home_probable_pitcher_zh"] or "未公布",
                "home_probable_pitcher_id": game.get("home_probable_pitcher_id"),
                "prediction_zh": prod_pick["team_zh"],
                "pick_side": prod_pick["side"],
                "confidence": round(prod_pick["confidence"], 4),
                "confirmation_pick_zh": conf_pick["team_zh"] if conf_pick else "未通過",
                "confirmation_same_direction": same_direction,
                "confidence_pass": confidence_pass,
                "decision": "高信心預測" if same_direction and confidence_pass else "一般預測",
            }
        )

    merge_score_predictions(candidates, target_date)
    candidates.sort(key=lambda row: (row["decision"] == "高信心預測", row["confidence"]), reverse=True)
    recommendations = [row for row in candidates if row["decision"] == "高信心預測"]
    watchlist = [row for row in candidates if row not in recommendations]
    last_training_date = history[-1]["date"] if history else None
    expected_training_cutoff = (date.fromisoformat(target_date) - timedelta(days=1)).isoformat()
    stale_training = bool(last_training_date and last_training_date < expected_training_cutoff)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "target_date": target_date,
        "models": {
            "production": production_name,
            "confirmation": confirmation_name,
        },
        "settings": {
            "min_confidence": min_confidence,
            "odds_status": "not_used_for_accuracy_first",
        },
        "data_source": {
            "history_csv": str(games_csv.relative_to(ROOT)) if games_csv.is_relative_to(ROOT) else str(games_csv),
            "training_games": len(history),
            "last_training_date": last_training_date,
            "expected_training_cutoff": expected_training_cutoff,
            "schedule_source": "MLB Stats API schedule endpoint",
            "schedule_games": len(schedule),
            "warning": (
                f"模型訓練資料只到 {last_training_date}，低於目標日前一天 {expected_training_cutoff}；請先重抓歷史比分。"
                if stale_training
                else None
            ),
            "freshness_note": (
                f"訓練資料已更新到目標日前一天 {expected_training_cutoff}。"
                if last_training_date == expected_training_cutoff
                else ""
            ),
        },
        "high_confidence_predictions": recommendations,
        "all_predictions": candidates,
        "watchlist": watchlist,
    }


def write_outputs(plan: dict) -> None:
    target_date = plan["target_date"]
    json_path = Path(str(DAILY_PLAN_JSON).format(date=target_date))
    csv_path = Path(str(DAILY_PLAN_CSV).format(date=target_date))
    json_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = plan["all_predictions"]
    fields = [
        "date",
        "game_pk",
        "game_time_tw",
        "game_time_utc",
        "decision",
        "matchup_zh",
        "away_probable_pitcher_zh",
        "home_probable_pitcher_zh",
        "prediction_zh",
        "confidence",
        "confirmation_pick_zh",
        "confirmation_same_direction",
        "score_prediction_zh",
        "predicted_away_score",
        "predicted_home_score",
        "predicted_total",
        "monte_carlo_pick_zh",
        "status",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    DAILY_PLAN_HTML.write_text(render_html(plan), encoding="utf-8")
    print(f"wrote {json_path}")
    print(f"wrote {csv_path}")
    print(f"wrote {DAILY_PLAN_HTML}")
    print(f"high_confidence_predictions={len(plan['high_confidence_predictions'])} all_predictions={len(plan['all_predictions'])}")


def render_rows(rows: list[dict]) -> str:
    if not rows:
        return '<tr><td colspan="9">沒有符合條件的場次</td></tr>'
    parts = []
    for row in rows:
        parts.append(
            f"""
            <tr>
              <td>{row['decision']}</td>
              <td>{row.get('game_time_tw', '未公布')}</td>
              <td>{row['matchup_zh']}</td>
              <td>{row['away_probable_pitcher_zh']} / {row['home_probable_pitcher_zh']}</td>
              <td>{row['prediction_zh']}</td>
              <td>{row.get('score_prediction_zh', '-')}</td>
              <td>{row.get('total_prediction_zh', '-')}</td>
              <td>{row['confidence'] * 100:.1f}%</td>
              <td>{row['confirmation_pick_zh']}</td>
            </tr>"""
        )
    return "\n".join(parts)


def render_schedule_rows(rows: list[dict]) -> str:
    if not rows:
        return '<tr><td colspan="11">沒有賽程</td></tr>'
    parts = []
    for row in rows:
        parts.append(
            f"""
            <tr>
              <td>{row['game_pk']}</td>
              <td>{row.get('game_time_tw', '未公布')}</td>
              <td>{row['status']}</td>
              <td>{row['matchup_zh']}</td>
              <td>{row['away_probable_pitcher_zh']} / {row['home_probable_pitcher_zh']}</td>
              <td>{row['prediction_zh']}</td>
              <td>{row.get('score_prediction_zh', '-')}</td>
              <td>{row.get('total_prediction_zh', '-')}</td>
              <td>{row['confidence'] * 100:.1f}%</td>
              <td>{row['confirmation_pick_zh']}</td>
              <td>{row['decision']}</td>
            </tr>"""
        )
    return "\n".join(parts)


def render_html(plan: dict) -> str:
    rec_rows = render_rows(plan["high_confidence_predictions"])
    watch_rows = render_rows(plan["watchlist"])
    schedule_rows_recommendation = render_schedule_rows(plan["all_predictions"])
    schedule_rows_time = render_schedule_rows(
        sorted(plan["all_predictions"], key=lambda row: (row.get("game_time_utc") or "", int(row.get("game_pk") or 0)))
    )
    schedule_payload = json.dumps(
        {"recommendation": schedule_rows_recommendation, "time": schedule_rows_time},
        ensure_ascii=False,
    )
    warning = plan["data_source"].get("warning") or ""
    freshness_note = plan["data_source"].get("freshness_note") or ""
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>每日 MLB 勝方預測</title>
  <style>
    body {{ margin: 0; background: #f7f8f6; color: #202421; font-family: "Microsoft JhengHei", "Noto Sans TC", system-ui, sans-serif; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 28px; }}
    h1 {{ margin: 0 0 6px; font-size: 30px; }}
    h2 {{ margin: 24px 0 12px; font-size: 18px; }}
    .meta {{ color: #68736d; line-height: 1.6; font-size: 14px; }}
    .warning {{ margin-top: 16px; padding: 12px 14px; border: 1px solid #e2c47a; background: #fff8e6; border-radius: 8px; color: #765315; }}
    .toolbar {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin: 14px 0 10px; }}
    .toolbar span {{ color: #68736d; font-size: 13px; font-weight: 700; }}
    .sort-btn {{ border: 1px solid #dfe5df; border-radius: 8px; background: white; color: #24433b; padding: 8px 10px; font: inherit; font-weight: 800; cursor: pointer; }}
    .sort-btn.active {{ background: #165f56; border-color: #165f56; color: white; }}
    table {{ width: 100%; border-collapse: collapse; background: white; border: 1px solid #dfe5df; border-radius: 8px; overflow: hidden; }}
    th, td {{ text-align: left; border-bottom: 1px solid #dfe5df; padding: 12px 10px; white-space: nowrap; font-size: 14px; }}
    th {{ color: #68736d; font-size: 12px; }}
    @media (max-width: 720px) {{ main {{ padding: 18px; }} table {{ display: block; overflow-x: auto; }} h1 {{ font-size: 25px; }} }}
  </style>
</head>
<body>
  <main>
    <h1>每日 MLB 勝方預測</h1>
    <div class="meta">
      MLB日期：{plan['target_date']}<br />
      主模型：{plan['models']['production']} / 確認模型：{plan['models']['confirmation']}<br />
      訓練場數：{plan['data_source']['training_games']} / 訓練截止：{plan['data_source']['last_training_date']} / 賽程場數：{plan['data_source']['schedule_games']}<br />
      產生時間：{plan['generated_at']}<br />
      {freshness_note}
    </div>
    {f'<div class="warning">{warning}</div>' if warning else ''}
    <h2>完整賽程表</h2>
    <div class="toolbar">
      <span>排序方式</span>
      <button class="sort-btn active" id="sortRecommendation" type="button">推薦高低</button>
      <button class="sort-btn" id="sortTime" type="button">比賽時間</button>
    </div>
    <table>
      <thead><tr><th>GamePk</th><th>台灣開賽時間</th><th>狀態</th><th>對戰</th><th>先發投手</th><th>模型預測</th><th>預測比分</th><th>預測總分</th><th>信心</th><th>確認模型</th><th>分類</th></tr></thead>
      <tbody id="scheduleRows">{schedule_rows_recommendation}</tbody>
    </table>
    <div class="warning">投注單請看 <a href="betting_ticket.html">今日投注單</a>。該頁只列入真實盤口與 edge 條件通過的場次。</div>
    <h2>高信心預測</h2>
    <table>
      <thead><tr><th>決策</th><th>台灣開賽時間</th><th>對戰</th><th>先發投手</th><th>預測勝方</th><th>預測比分</th><th>預測總分</th><th>信心</th><th>確認模型</th></tr></thead>
      <tbody>{rec_rows}</tbody>
    </table>
    <h2>一般預測</h2>
    <table>
      <thead><tr><th>決策</th><th>台灣開賽時間</th><th>對戰</th><th>先發投手</th><th>預測勝方</th><th>預測比分</th><th>預測總分</th><th>信心</th><th>確認模型</th></tr></thead>
      <tbody>{watch_rows}</tbody>
    </table>
    <div class="warning">比分預測取自蒙地卡羅 10,000 次單場模擬平均值；完整模擬分布請看 <a href="monte_carlo.html">蒙地卡羅模擬</a>。投注單請看 <a href="betting_ticket.html">今日投注單</a>。該頁只列入真實盤口與 edge 條件通過的場次。</div>
    <div class="warning">之前做的預測驗證：<a href="prediction_log.html">結算紀錄</a> 看每場命中/錯誤；<a href="postgame_review.html">賽後檢討</a> 看每日總結；<a href="winner_model_search.html">模型搜尋</a> 看歷史模型驗證。</div>
  </main>
  <script>
    const SCHEDULE_ROWS = {schedule_payload};
    const buttons = {{
      recommendation: document.getElementById('sortRecommendation'),
      time: document.getElementById('sortTime')
    }};
    function setScheduleSort(mode) {{
      document.getElementById('scheduleRows').innerHTML = SCHEDULE_ROWS[mode];
      buttons.recommendation.classList.toggle('active', mode === 'recommendation');
      buttons.time.classList.toggle('active', mode === 'time');
    }}
    buttons.recommendation.addEventListener('click', () => setScheduleSort('recommendation'));
    buttons.time.addEventListener('click', () => setScheduleSort('time'));
  </script>
</body>
</html>"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a Chinese daily MLB winner prediction plan.")
    parser.add_argument("--date", default=date.today().isoformat(), help="Target date in YYYY-MM-DD.")
    parser.add_argument("--games-csv", type=Path, default=DEFAULT_GAMES_CSV)
    parser.add_argument("--min-confidence", type=float, default=0.55)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    plan = build_daily_plan(
        args.date,
        args.games_csv,
        args.min_confidence,
    )
    write_outputs(plan)


if __name__ == "__main__":
    main()
