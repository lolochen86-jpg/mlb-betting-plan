#!/usr/bin/env python3
"""Compare pre-fix and post-fix projected lineup Monte Carlo quality.

This intentionally forces projected lineups even when historical official
boxscores are now available, so it measures the pre-game projection logic.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import urllib.parse
from collections import Counter
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from generate_game_simulator import default_pitcher_profile, team_profiles
from generate_monte_carlo import load_moneyline, load_totals, summarize_game
from mlb_player_context import (
    MLB_API,
    _person_hitting_stat,
    _roster_player_row,
    fetch_pitcher_profile,
    fetch_projected_lineup,
    request_json,
    safe_float,
)
from run_real_mlb_backtest import DEFAULT_GAMES_CSV, load_games


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"
DAILY_JSON = DATA_DIR / "daily_predictions_{date}.json"
OUT_JSON = DATA_DIR / "lineup_fix_comparison_{start}_{end}.json"
OUT_CSV = DATA_DIR / "lineup_fix_comparison_{start}_{end}.csv"
OUT_HTML = DOCS_DIR / "lineup_fix_comparison.html"
REGULAR_POSITIONS = {"C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "DH"}


def pct(correct: int, total: int) -> float:
    return round(correct / total * 100, 2) if total else 0.0


def daily_dates() -> list[str]:
    dates = []
    for path in DATA_DIR.glob("daily_predictions_*.json"):
        dates.append(path.stem.replace("daily_predictions_", ""))
    return sorted(dates)


@lru_cache(maxsize=256)
def legacy_old_roster_lineup(team_id: int | str | None, season: str) -> list[dict]:
    try:
        tid = int(team_id or 0)
    except (TypeError, ValueError):
        tid = 0
    if not tid:
        return []
    url = f"{MLB_API}/teams/{tid}/roster?rosterType=active&hydrate=person(stats(type=season,group=hitting,season={season}))"
    payload = request_json(url)
    candidates = []
    for entry in payload.get("roster", []):
        position = entry.get("position") or {}
        if position.get("type") == "Pitcher" or position.get("abbreviation") == "P":
            continue
        stat = _person_hitting_stat(entry.get("person") or {})
        plate_appearances = safe_float(stat.get("plateAppearances"))
        ops = safe_float(stat.get("ops"), safe_float(stat.get("obp"), 0.315) + safe_float(stat.get("slg"), 0.400))
        candidates.append((plate_appearances, ops, entry))
    candidates.sort(key=lambda item: (item[0] >= 40, item[0], item[1]), reverse=True)
    return [_roster_player_row(entry, idx, "before_old_roster") for idx, (_, _, entry) in enumerate(candidates[:9], start=1)]


def fetch_actuals(start_date: str, end_date: str) -> dict[str, dict]:
    params = {
        "sportId": "1",
        "startDate": start_date,
        "endDate": end_date,
        "hydrate": "team,linescore",
        "gameTypes": "R",
    }
    payload = request_json(f"{MLB_API}/schedule?{urllib.parse.urlencode(params)}")
    actuals = {}
    for day in payload.get("dates", []):
        for game in day.get("games", []):
            status = game.get("status", {})
            if status.get("abstractGameState") != "Final":
                continue
            teams = game.get("teams") or {}
            away = teams.get("away") or {}
            home = teams.get("home") or {}
            if "score" not in away or "score" not in home:
                continue
            game_pk = str(game.get("gamePk") or "")
            away_score = int(away["score"])
            home_score = int(home["score"])
            actuals[game_pk] = {
                "game_pk": game_pk,
                "date": day.get("date", ""),
                "away_score": away_score,
                "home_score": home_score,
                "total": away_score + home_score,
                "winner": "away" if away_score > home_score else "home",
            }
    return actuals


def lineup_quality(lineup: list[dict]) -> dict:
    orders = [int(player.get("batting_order") or idx + 1) for idx, player in enumerate(lineup)]
    positions = [player.get("pos") for player in lineup]
    duplicate_positions = sum(count - 1 for pos, count in Counter(positions).items() if pos in REGULAR_POSITIONS and count > 1)
    return {
        "bad_order": 0 if orders == list(range(1, 10)) else 1,
        "duplicate_positions": duplicate_positions,
    }


def build_projected_game(row: dict, target_date: str, profiles: dict, variant: str) -> dict | None:
    season = target_date[:4]
    if variant == "before_old_roster":
        away_lineup = legacy_old_roster_lineup(row.get("away_team_id"), season)
        home_lineup = legacy_old_roster_lineup(row.get("home_team_id"), season)
    elif variant == "after_fixed_recent":
        away_lineup = fetch_projected_lineup(row.get("away_team_id"), season, target_date)
        home_lineup = fetch_projected_lineup(row.get("home_team_id"), season, target_date)
    else:
        raise ValueError(f"unknown variant: {variant}")
    if len(away_lineup) < 9 or len(home_lineup) < 9:
        return None
    away_pitcher_profile = default_pitcher_profile()
    home_pitcher_profile = default_pitcher_profile()
    try:
        away_pitcher_profile = {**away_pitcher_profile, **fetch_pitcher_profile(row.get("away_probable_pitcher_id"), season)}
        home_pitcher_profile = {**home_pitcher_profile, **fetch_pitcher_profile(row.get("home_probable_pitcher_id"), season)}
    except Exception:
        pass
    away = row.get("away_zh", "")
    home = row.get("home_zh", "")
    return {
        "date": target_date,
        "game_pk": str(row.get("game_pk", "")),
        "status": row.get("status", ""),
        "away": away,
        "home": home,
        "away_pitcher_profile": away_pitcher_profile,
        "home_pitcher_profile": home_pitcher_profile,
        "lineup_source": variant,
        "prediction": row.get("prediction_zh", ""),
        "confidence": row.get("confidence", 0),
        "away_profile": profiles.get(away, {"offense": 4.4, "prevention": 4.4, "power": 1, "contact": 1}),
        "home_profile": profiles.get(home, {"offense": 4.4, "prevention": 4.4, "power": 1, "contact": 1}),
        "away_lineup": away_lineup,
        "home_lineup": home_lineup,
    }


def evaluate_variant(dates: list[str], actuals: dict[str, dict], simulations: int, variant: str) -> dict:
    history_all = load_games(DEFAULT_GAMES_CSV)
    rows = []
    summary = {
        "variant": variant,
        "games": 0,
        "winner_correct": 0,
        "totals_games": 0,
        "totals_correct": 0,
        "bad_order_games": 0,
        "duplicate_positions": 0,
    }
    for target_date in dates:
        daily_path = Path(str(DAILY_JSON).format(date=target_date))
        if not daily_path.exists():
            continue
        daily = json.loads(daily_path.read_text(encoding="utf-8"))
        history = [game for game in history_all if game["date"] < target_date]
        profiles = team_profiles(history)
        totals = load_totals(target_date)
        moneyline = load_moneyline(target_date)
        for row in daily.get("all_predictions", []):
            game_pk = str(row.get("game_pk", ""))
            actual = actuals.get(game_pk)
            if not actual:
                continue
            game = build_projected_game(row, target_date, profiles, variant)
            if not game:
                continue
            result = summarize_game(game, simulations, totals, moneyline)
            pick_side = "away" if result["away_win_prob"] >= result["home_win_prob"] else "home"
            winner_correct = pick_side == actual["winner"]
            q_away = lineup_quality(game["away_lineup"])
            q_home = lineup_quality(game["home_lineup"])
            duplicate_positions = q_away["duplicate_positions"] + q_home["duplicate_positions"]
            bad_order = q_away["bad_order"] + q_home["bad_order"]
            totals_pick = ""
            totals_correct = None
            if result.get("total_line") is not None and game_pk in totals:
                if actual["total"] > float(result["total_line"]):
                    actual_total_side = "over"
                elif actual["total"] < float(result["total_line"]):
                    actual_total_side = "under"
                else:
                    actual_total_side = "push"
                totals_pick = "over" if (result.get("over_prob") or 0) >= (result.get("under_prob") or 0) else "under"
                totals_correct = totals_pick == actual_total_side
                if actual_total_side != "push":
                    summary["totals_games"] += 1
                    summary["totals_correct"] += 1 if totals_correct else 0
            summary["games"] += 1
            summary["winner_correct"] += 1 if winner_correct else 0
            summary["bad_order_games"] += 1 if bad_order else 0
            summary["duplicate_positions"] += duplicate_positions
            rows.append(
                {
                    "variant": variant,
                    "date": target_date,
                    "game_pk": game_pk,
                    "matchup": f"{game['away']} @ {game['home']}",
                    "actual_score": f"{actual['away_score']}-{actual['home_score']}",
                    "actual_winner": actual["winner"],
                    "winner_pick": pick_side,
                    "winner_correct": winner_correct,
                    "avg_away_score": result["avg_away_score"],
                    "avg_home_score": result["avg_home_score"],
                    "actual_total": actual["total"],
                    "total_line": result.get("total_line"),
                    "totals_pick": totals_pick,
                    "totals_correct": totals_correct,
                    "duplicate_positions": duplicate_positions,
                    "bad_order": bad_order,
                }
            )
    summary["winner_accuracy_pct"] = pct(summary["winner_correct"], summary["games"])
    summary["totals_accuracy_pct"] = pct(summary["totals_correct"], summary["totals_games"])
    return {"summary": summary, "rows": rows}


def render_html(report: dict) -> str:
    summary_rows = "\n".join(
        f"""
        <tr>
          <td>{html.escape(row['variant'])}</td>
          <td>{row['games']}</td>
          <td>{row['winner_correct']}</td>
          <td>{row['winner_accuracy_pct']:.2f}%</td>
          <td>{row['totals_games']}</td>
          <td>{row['totals_accuracy_pct']:.2f}%</td>
          <td>{row['bad_order_games']}</td>
          <td>{row['duplicate_positions']}</td>
        </tr>"""
        for row in report["summary"]
    )
    detail_rows = "\n".join(
        f"""
        <tr>
          <td>{row['date']}</td>
          <td>{html.escape(row['variant'])}</td>
          <td>{row['game_pk']}</td>
          <td>{html.escape(row['matchup'])}</td>
          <td>{row['actual_score']}</td>
          <td>{row['winner_pick']}</td>
          <td>{'Y' if row['winner_correct'] else 'N'}</td>
          <td>{row['total_line']}</td>
          <td>{row['totals_pick']}</td>
          <td>{'' if row['totals_correct'] is None else ('Y' if row['totals_correct'] else 'N')}</td>
          <td>{row['duplicate_positions']}</td>
        </tr>"""
        for row in report["rows"][:300]
    )
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>修正前 / 修正後打線比較</title>
  <style>
    body {{ margin: 0; background: #f4f6f5; color: #17201c; font-family: "Microsoft JhengHei", system-ui, sans-serif; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 28px 18px; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; }}
    .meta {{ color: #66736c; margin-bottom: 18px; }}
    table {{ width: 100%; border-collapse: collapse; background: white; border: 1px solid #dce3df; margin: 14px 0 28px; }}
    th, td {{ padding: 10px 12px; border-bottom: 1px solid #e5ebe7; text-align: left; font-size: 14px; }}
    th {{ background: #eef3f0; font-weight: 800; }}
    .note {{ background: #fff8e8; border: 1px solid #ead49d; padding: 12px 14px; border-radius: 8px; }}
  </style>
</head>
<body>
<main>
  <h1>修正前 / 修正後打線比較</h1>
  <div class="meta">日期：{report['start_date']} 至 {report['end_date']}，每場模擬 {report['simulations']} 次，產生時間 {report['generated_at']}</div>
  <div class="note">此報告強制使用賽前 projected lineup，不使用賽後官方 boxscore 打線，目的在衡量打線推估修正本身。</div>
  <h2>總結</h2>
  <table>
    <thead><tr><th>版本</th><th>場次</th><th>勝方正確</th><th>勝方準確率</th><th>大小分場次</th><th>大小分準確率</th><th>打序異常場</th><th>重複守位數</th></tr></thead>
    <tbody>{summary_rows}</tbody>
  </table>
  <h2>明細</h2>
  <table>
    <thead><tr><th>日期</th><th>版本</th><th>GamePk</th><th>對戰</th><th>實際比分</th><th>勝方預測</th><th>勝方</th><th>大小分線</th><th>大小分預測</th><th>大小分</th><th>重複守位</th></tr></thead>
    <tbody>{detail_rows}</tbody>
  </table>
</main>
</body>
</html>"""


def render_html_v2(report: dict) -> str:
    def variant_label(value: str) -> str:
        return {"before_old_roster": "修正前", "after_fixed_recent": "修正後"}.get(value, value)

    def variant_desc(value: str) -> str:
        return {
            "before_old_roster": "active roster + OPS/PA 排打線",
            "after_fixed_recent": "近期真實棒次 + 守位去重",
        }.get(value, value)

    def result_badge(value: bool | None) -> str:
        if value is None:
            return ""
        cls = "ok" if value else "bad"
        text = "正確" if value else "錯誤"
        return f'<span class="result {cls}">{text}</span>'

    def side_label(value: str) -> str:
        return "客隊" if value == "away" else "主隊" if value == "home" else "-"

    def total_label(value: str) -> str:
        return "大分" if value == "over" else "小分" if value == "under" else "-"

    before = next((row for row in report["summary"] if row["variant"] == "before_old_roster"), report["summary"][0])
    after = next((row for row in report["summary"] if row["variant"] == "after_fixed_recent"), report["summary"][-1])
    winner_delta = after["winner_accuracy_pct"] - before["winner_accuracy_pct"]
    totals_delta = after["totals_accuracy_pct"] - before["totals_accuracy_pct"]
    duplicate_delta = before["duplicate_positions"] - after["duplicate_positions"]
    compared_games = max(row["games"] for row in report["summary"]) if report["summary"] else 0

    summary_rows = "\n".join(
        f"""
        <tr>
          <td><strong>{variant_label(row['variant'])}</strong><span>{variant_desc(row['variant'])}</span></td>
          <td>{row['games']}</td>
          <td>{row['winner_correct']}</td>
          <td>{row['winner_accuracy_pct']:.2f}%</td>
          <td>{row['totals_games']}</td>
          <td>{row['totals_accuracy_pct']:.2f}%</td>
          <td>{row['bad_order_games']}</td>
          <td>{row['duplicate_positions']}</td>
        </tr>"""
        for row in report["summary"]
    )
    detail_rows = "\n".join(
        f"""
        <tr>
          <td>{row['date']}</td>
          <td><span class="tag {'tag-after' if row['variant'] == 'after_fixed_recent' else 'tag-before'}">{variant_label(row['variant'])}</span></td>
          <td>{row['game_pk']}</td>
          <td>{html.escape(row['matchup'])}</td>
          <td>{row['actual_score']}</td>
          <td>{side_label(row['winner_pick'])}</td>
          <td>{result_badge(row['winner_correct'])}</td>
          <td>{row['total_line']}</td>
          <td>{total_label(row['totals_pick'])}</td>
          <td>{result_badge(row['totals_correct'])}</td>
          <td>{row['duplicate_positions']}</td>
        </tr>"""
        for row in report["rows"][:300]
    )
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>修正前 / 修正後打線比較</title>
  <style>
    :root {{
      --bg: #f5f7f6;
      --surface: #ffffff;
      --ink: #17201c;
      --muted: #68746e;
      --line: #dce4df;
      --soft: #edf3f0;
      --green: #176454;
      --amber: #9a6719;
      --blue: #234a6b;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--bg); color: var(--ink); font-family: "Microsoft JhengHei", "Noto Sans TC", system-ui, sans-serif; letter-spacing: 0; }}
    main {{ max-width: 1220px; margin: 0 auto; padding: 28px 18px 40px; }}
    h1 {{ margin: 0 0 8px; font-size: 30px; line-height: 1.2; }}
    h2 {{ margin: 28px 0 12px; font-size: 20px; }}
    p {{ line-height: 1.7; }}
    .hero {{ display: grid; grid-template-columns: minmax(0, 1fr) 360px; gap: 18px; align-items: stretch; }}
    .intro, .callout, .panel {{ background: var(--surface); border: 1px solid var(--line); border-radius: 8px; }}
    .intro {{ padding: 22px; }}
    .meta {{ color: var(--muted); font-size: 14px; line-height: 1.7; }}
    .callout {{ padding: 18px; background: #f1f7f4; border-color: #cfe0d8; }}
    .callout strong {{ display: block; font-size: 18px; margin-bottom: 8px; color: var(--green); }}
    .kpis {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 16px 0 8px; }}
    .kpi {{ background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 16px; min-height: 118px; }}
    .kpi .label {{ color: var(--muted); font-size: 13px; font-weight: 700; }}
    .kpi .value {{ font-size: 28px; font-weight: 900; margin: 8px 0 4px; }}
    .kpi .hint {{ color: var(--muted); font-size: 13px; line-height: 1.5; }}
    .value.good {{ color: var(--green); }}
    .value.warn {{ color: var(--amber); }}
    .value.blue {{ color: var(--blue); }}
    .panel {{ padding: 16px; margin-top: 14px; }}
    .notes {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
    .note-title {{ font-weight: 900; margin-bottom: 8px; }}
    ul {{ margin: 0; padding-left: 20px; color: var(--muted); line-height: 1.8; }}
    table {{ width: 100%; border-collapse: separate; border-spacing: 0; background: white; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; margin: 12px 0 28px; }}
    th, td {{ padding: 11px 12px; border-bottom: 1px solid #e7ede9; text-align: left; font-size: 14px; vertical-align: middle; white-space: nowrap; }}
    th {{ background: var(--soft); color: #42504a; font-size: 12px; font-weight: 900; }}
    td span {{ display: block; color: var(--muted); font-size: 12px; margin-top: 2px; }}
    tr:last-child td {{ border-bottom: 0; }}
    .tag {{ display: inline-flex; align-items: center; border-radius: 999px; padding: 4px 9px; font-size: 12px; font-weight: 900; margin: 0; }}
    .tag-before {{ color: #7a4b12; background: #fff2d6; }}
    .tag-after {{ color: #135f50; background: #dff2ea; }}
    .result {{ display: inline-flex; border-radius: 999px; padding: 4px 9px; font-size: 12px; font-weight: 900; margin: 0; }}
    .result.ok {{ color: #126048; background: #dff2ea; }}
    .result.bad {{ color: #9b2f29; background: #f9dddd; }}
    .table-wrap {{ overflow-x: auto; }}
    .footnote {{ color: var(--muted); font-size: 13px; line-height: 1.7; margin-top: -12px; }}
    @media (max-width: 900px) {{
      .hero, .notes {{ grid-template-columns: 1fr; }}
      .kpis {{ grid-template-columns: repeat(2, 1fr); }}
    }}
    @media (max-width: 560px) {{
      main {{ padding: 18px 12px 30px; }}
      h1 {{ font-size: 25px; }}
      .kpis {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
<main>
  <section class="hero">
    <div class="intro">
      <h1>修正前 / 修正後打線比較</h1>
      <div class="meta">比較日期：{report['start_date']} 至 {report['end_date']}<br />每場蒙地卡羅模擬：{report['simulations']} 次 / 可比對樣本：{compared_games} 場 / 產生時間：{report['generated_at']}</div>
      <p>這份報告只測「賽前預估打線」本身，不使用賽後已公布的官方 boxscore 打線。目的，是確認大谷棒次、重複 CF、替補球員混入先發這類問題修正後，是否讓模型輸出更合理。</p>
    </div>
    <div class="callout">
      <strong>目前小樣本結論</strong>
      <p>修正後勝方多對 1 場，大小分也多對 1 場，重複守位從 {before['duplicate_positions']} 降到 {after['duplicate_positions']}。方向是正確的，但樣本仍偏小，後續每天會繼續累積。</p>
    </div>
  </section>

  <section class="kpis">
    <div class="kpi"><div class="label">勝方準確率變化</div><div class="value good">+{winner_delta:.2f}%</div><div class="hint">{before['winner_accuracy_pct']:.2f}% → {after['winner_accuracy_pct']:.2f}%</div></div>
    <div class="kpi"><div class="label">大小分準確率變化</div><div class="value good">+{totals_delta:.2f}%</div><div class="hint">{before['totals_accuracy_pct']:.2f}% → {after['totals_accuracy_pct']:.2f}%</div></div>
    <div class="kpi"><div class="label">重複守位改善</div><div class="value blue">-{duplicate_delta}</div><div class="hint">{before['duplicate_positions']} → {after['duplicate_positions']}</div></div>
    <div class="kpi"><div class="label">可比對場次</div><div class="value warn">{compared_games}</div><div class="hint">需同時有預測檔、盤口與真實完賽比分</div></div>
  </section>

  <section class="notes">
    <div class="panel">
      <div class="note-title">修正前怎麼排</div>
      <ul>
        <li>用 active roster 球員名單。</li>
        <li>依 OPS / PA 大致排序。</li>
        <li>容易把固定第一棒排錯，或產生兩個 CF。</li>
      </ul>
    </div>
    <div class="panel">
      <div class="note-title">修正後怎麼排</div>
      <ul>
        <li>只採近期 boxscore 的先發打序碼 100/200/.../900。</li>
        <li>每個棒次只挑一位最常先發的人。</li>
        <li>守位用近期實際守備位置，並避免同隊重複。</li>
      </ul>
    </div>
  </section>

  <h2>總結</h2>
  <div class="table-wrap">
    <table>
      <thead><tr><th>版本</th><th>場次</th><th>勝方正確</th><th>勝方準確率</th><th>大小分場次</th><th>大小分準確率</th><th>打序異常場</th><th>重複守位數</th></tr></thead>
      <tbody>{summary_rows}</tbody>
    </table>
  </div>
  <p class="footnote">注意：這不是全季回測，只是針對「打線修正」可比對樣本做 A/B 驗證。樣本越累積，結論才會越穩。</p>

  <h2>逐場明細</h2>
  <div class="table-wrap">
    <table>
      <thead><tr><th>日期</th><th>版本</th><th>GamePk</th><th>對戰</th><th>實際比分</th><th>勝方預測</th><th>勝方</th><th>大小分線</th><th>大小分預測</th><th>大小分</th><th>重複守位</th></tr></thead>
      <tbody>{detail_rows}</tbody>
    </table>
  </div>
</main>
</body>
</html>"""


def write_outputs(report: dict) -> None:
    start = report["start_date"]
    end = report["end_date"]
    json_path = Path(str(OUT_JSON).format(start=start, end=end))
    csv_path = Path(str(OUT_CSV).format(start=start, end=end))
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = [
        "variant",
        "date",
        "game_pk",
        "matchup",
        "actual_score",
        "actual_winner",
        "winner_pick",
        "winner_correct",
        "avg_away_score",
        "avg_home_score",
        "actual_total",
        "total_line",
        "totals_pick",
        "totals_correct",
        "duplicate_positions",
        "bad_order",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(report["rows"])
    OUT_HTML.write_text(render_html_v2(report), encoding="utf-8")
    print(f"wrote {json_path}")
    print(f"wrote {csv_path}")
    print(f"wrote {OUT_HTML}")


def parse_args() -> argparse.Namespace:
    dates = daily_dates()
    parser = argparse.ArgumentParser(description="Compare before/after projected lineup fix with Monte Carlo outcomes.")
    parser.add_argument("--start-date", default=dates[0] if dates else datetime.now().date().isoformat())
    parser.add_argument("--end-date", default=dates[-1] if dates else datetime.now().date().isoformat())
    parser.add_argument("--simulations", type=int, default=1000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dates = [d for d in daily_dates() if args.start_date <= d <= args.end_date]
    actuals = fetch_actuals(args.start_date, args.end_date)
    variants = [
        evaluate_variant(dates, actuals, args.simulations, "before_old_roster"),
        evaluate_variant(dates, actuals, args.simulations, "after_fixed_recent"),
    ]
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "start_date": args.start_date,
        "end_date": args.end_date,
        "simulations": args.simulations,
        "dates": dates,
        "actual_games": len(actuals),
        "summary": [item["summary"] for item in variants],
        "rows": [row for item in variants for row in item["rows"]],
        "note": "Projected-lineup-only comparison. Historical official lineups are intentionally bypassed.",
    }
    write_outputs(report)
    for row in report["summary"]:
        print(
            f"{row['variant']}: winner={row['winner_accuracy_pct']}% "
            f"({row['winner_correct']}/{row['games']}), totals={row['totals_accuracy_pct']}% "
            f"({row['totals_correct']}/{row['totals_games']}), duplicate_positions={row['duplicate_positions']}"
        )


if __name__ == "__main__":
    main()
