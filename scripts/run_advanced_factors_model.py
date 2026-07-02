#!/usr/bin/env python3
"""Generate an advanced MLB winner model using batting, pitching, bullpen, streak, and venue factors."""

from __future__ import annotations

import argparse
import csv
import json
import math
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from fetch_taiwan_sportslottery_odds import normalize_team_name
from fetch_real_mlb_data import MLB_SCHEDULE_URL
from name_localization import team_zh
from run_real_mlb_backtest import DEFAULT_GAMES_CSV, load_games
from schedule_time import attach_game_time, load_time_index, time_sort_key
from settle_betting_roi import implied_probability, parse_moneyline


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"
ODDS_DIR = DATA_DIR / "odds"

ODDS_CSV = ODDS_DIR / "mlb_moneyline_{date}.csv"
ADV_JSON = DATA_DIR / "advanced_factors_predictions_{date}.json"
ADV_CSV = DATA_DIR / "advanced_factors_predictions_{date}.csv"
ADV_HTML = DOCS_DIR / "advanced_factors.html"
SOURCE_JSON = DATA_DIR / "sources" / "advanced_factors_sources_{date}.json"


TEAM_ID_BY_ZH = {
    "亞利桑那響尾蛇": 109,
    "亞特蘭大勇士": 144,
    "巴爾的摩金鶯": 110,
    "波士頓紅襪": 111,
    "芝加哥白襪": 145,
    "芝加哥小熊": 112,
    "辛辛那提紅人": 113,
    "克里夫蘭守護者": 114,
    "科羅拉多洛磯": 115,
    "底特律老虎": 116,
    "休士頓太空人": 117,
    "堪薩斯市皇家": 118,
    "洛杉磯天使": 108,
    "洛杉磯道奇": 119,
    "邁阿密馬林魚": 146,
    "密爾瓦基釀酒人": 158,
    "明尼蘇達雙城": 142,
    "紐約大都會": 121,
    "紐約洋基": 147,
    "運動家": 133,
    "費城費城人": 143,
    "匹茲堡海盜": 134,
    "聖地牙哥教士": 135,
    "舊金山巨人": 137,
    "西雅圖水手": 136,
    "聖路易紅雀": 138,
    "坦帕灣光芒": 139,
    "德州遊騎兵": 140,
    "多倫多藍鳥": 141,
    "華盛頓國民": 120,
}


def request_json(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "advanced-factors-model/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def safe_float(value: object, default: float = 0.0) -> float:
    if value in (None, "", "-.--"):
        return default
    try:
        return float(str(value))
    except ValueError:
        return default


def per(value: float, denom: float) -> float:
    return value / denom if denom else 0.0


def sigmoid(score: float) -> float:
    return 1 / (1 + math.exp(-score))


def fetch_schedule(target_date: str) -> list[dict]:
    params = {
        "sportId": "1",
        "date": target_date,
        "hydrate": "team,probablePitcher,venue",
    }
    payload = request_json(f"{MLB_SCHEDULE_URL}?{urllib.parse.urlencode(params)}")
    games = []
    for day in payload.get("dates", []):
        for game in day.get("games", []):
            away = game.get("teams", {}).get("away", {})
            home = game.get("teams", {}).get("home", {})
            games.append(
                {
                    "game_pk": str(game.get("gamePk", "")),
                    "away_zh": normalize_team_name(team_zh(away.get("team", {}).get("name", ""))),
                    "home_zh": normalize_team_name(team_zh(home.get("team", {}).get("name", ""))),
                    "away_team_id": away.get("team", {}).get("id"),
                    "home_team_id": home.get("team", {}).get("id"),
                    "away_pitcher_id": (away.get("probablePitcher") or {}).get("id"),
                    "home_pitcher_id": (home.get("probablePitcher") or {}).get("id"),
                    "away_pitcher": (away.get("probablePitcher") or {}).get("fullName", ""),
                    "home_pitcher": (home.get("probablePitcher") or {}).get("fullName", ""),
                    "venue": (game.get("venue") or {}).get("name", ""),
                    "status": game.get("status", {}).get("detailedState", ""),
                }
            )
    return games


def stats_url(entity: str, entity_id: int, group: str, season: str) -> str:
    return f"https://statsapi.mlb.com/api/v1/{entity}/{entity_id}/stats?stats=season&group={group}&season={season}"


def stat_split(payload: dict) -> dict:
    stats = payload.get("stats") or []
    splits = stats[0].get("splits") if stats else []
    return (splits[0].get("stat") if splits else {}) or {}


def fetch_team_stats(team_ids: set[int], season: str) -> dict[int, dict]:
    out = {}
    for team_id in sorted(team_ids):
        hitting = stat_split(request_json(stats_url("teams", team_id, "hitting", season)))
        pitching = stat_split(request_json(stats_url("teams", team_id, "pitching", season)))
        out[team_id] = {"hitting": hitting, "pitching": pitching}
    return out


def fetch_pitcher_stats(pitcher_ids: set[int], season: str) -> dict[int, dict]:
    out = {}
    for pitcher_id in sorted(pid for pid in pitcher_ids if pid):
        out[pitcher_id] = stat_split(request_json(stats_url("people", pitcher_id, "pitching", season)))
    return out


def recent_context(history: list[dict], target_date: str, recent_n: int = 12) -> dict:
    prior = [game for game in history if game["date"] < target_date]
    by_team = defaultdict(list)
    h2h = defaultdict(list)
    home_total = defaultdict(list)
    for game in prior:
        away = game["away_zh"]
        home = game["home_zh"]
        away_runs = game["away_score"]
        home_runs = game["home_score"]
        by_team[away].append({"rs": away_runs, "ra": home_runs, "win": away_runs > home_runs})
        by_team[home].append({"rs": home_runs, "ra": away_runs, "win": home_runs > away_runs})
        key = tuple(sorted([away, home]))
        h2h[key].append({"winner": away if away_runs > home_runs else home, "away": away, "home": home})
        home_total[home].append(away_runs + home_runs)
    return {"by_team": by_team, "h2h": h2h, "home_total": home_total, "recent_n": recent_n}


def streak_score(team: str, ctx: dict) -> float:
    games = ctx["by_team"].get(team, [])[-8:]
    if not games:
        return 0.0
    streak = 0
    last = games[-1]["win"]
    for game in reversed(games):
        if game["win"] == last:
            streak += 1
        else:
            break
    return (streak / 8) if last else -(streak / 8)


def recent_run_diff(team: str, ctx: dict) -> float:
    games = ctx["by_team"].get(team, [])[-ctx["recent_n"] :]
    if not games:
        return 0.0
    return sum(game["rs"] - game["ra"] for game in games) / len(games)


def h2h_score(team: str, opponent: str, ctx: dict) -> float:
    key = tuple(sorted([team, opponent]))
    games = ctx["h2h"].get(key, [])[-10:]
    if not games:
        return 0.0
    wins = sum(1 for game in games if game["winner"] == team)
    return (wins / len(games)) - 0.5


def venue_factor(home_team: str, ctx: dict) -> float:
    totals = ctx["home_total"].get(home_team, [])[-40:]
    if not totals:
        return 0.0
    league_totals = [total for values in ctx["home_total"].values() for total in values[-40:]]
    league_avg = sum(league_totals) / len(league_totals) if league_totals else 8.7
    return (sum(totals) / len(totals) - league_avg) / 4


def offense_score(stat: dict) -> float:
    ab = safe_float(stat.get("atBats"))
    pa = safe_float(stat.get("plateAppearances"), ab + safe_float(stat.get("baseOnBalls")))
    hits = safe_float(stat.get("hits"))
    doubles = safe_float(stat.get("doubles"))
    triples = safe_float(stat.get("triples"))
    hr = safe_float(stat.get("homeRuns"))
    total_bases = hits + doubles + 2 * triples + 3 * hr
    avg = safe_float(stat.get("avg"))
    obp = safe_float(stat.get("obp"))
    slg = safe_float(stat.get("slg"))
    k_rate = per(safe_float(stat.get("strikeOuts")), pa)
    bb_rate = per(safe_float(stat.get("baseOnBalls")), pa)
    gidp_rate = per(safe_float(stat.get("groundIntoDoublePlay")), pa)
    tb_rate = per(total_bases, ab)
    return 4.0 * (avg - 0.245) + 2.5 * (obp - 0.315) + 2.0 * (slg - 0.400) + 1.2 * (tb_rate - 0.40) + 1.8 * bb_rate - 1.6 * k_rate - 5.0 * gidp_rate


def pitcher_score(stat: dict) -> float:
    innings = safe_float(stat.get("inningsPitched"))
    if innings <= 0:
        return 0.0
    era = safe_float(stat.get("era"), 4.5)
    whip = safe_float(stat.get("whip"), 1.35)
    k9 = per(safe_float(stat.get("strikeOuts")) * 9, innings)
    bb9 = per(safe_float(stat.get("baseOnBalls")) * 9, innings)
    hr9 = per(safe_float(stat.get("homeRuns")) * 9, innings)
    gb = safe_float(stat.get("groundOuts"))
    fb = safe_float(stat.get("airOuts"))
    gb_ratio = per(gb, gb + fb)
    return 0.22 * (4.3 - era) + 0.45 * (1.30 - whip) + 0.035 * (k9 - 8.3) - 0.05 * (bb9 - 3.2) - 0.08 * (hr9 - 1.1) + 0.25 * (gb_ratio - 0.48)


def bullpen_score(stat: dict, ctx_score: float) -> float:
    innings = safe_float(stat.get("inningsPitched"))
    era = safe_float(stat.get("era"), 4.5)
    whip = safe_float(stat.get("whip"), 1.35)
    k9 = per(safe_float(stat.get("strikeOuts")) * 9, innings)
    bb9 = per(safe_float(stat.get("baseOnBalls")) * 9, innings)
    return 0.16 * (4.2 - era) + 0.30 * (1.32 - whip) + 0.025 * (k9 - 8.5) - 0.035 * (bb9 - 3.4) + 0.06 * ctx_score


def load_taiwan_odds(target_date: str) -> dict[str, dict]:
    path = Path(str(ODDS_CSV).format(date=target_date))
    if not path.exists():
        return {}
    rows = {}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("sportsbook") != "台灣運彩":
                continue
            rows[str(row.get("game_pk", ""))] = row
    return rows


def build_report(target_date: str, min_edge: float) -> dict:
    season = target_date[:4]
    schedule = fetch_schedule(target_date)
    team_ids = {int(g["away_team_id"]) for g in schedule if g.get("away_team_id")} | {int(g["home_team_id"]) for g in schedule if g.get("home_team_id")}
    pitcher_ids = {int(g["away_pitcher_id"]) for g in schedule if g.get("away_pitcher_id")} | {int(g["home_pitcher_id"]) for g in schedule if g.get("home_pitcher_id")}
    team_stats = fetch_team_stats(team_ids, season)
    pitcher_stats = fetch_pitcher_stats(pitcher_ids, season)
    history = load_games(DEFAULT_GAMES_CSV)
    ctx = recent_context(history, target_date)
    odds_by_pk = load_taiwan_odds(target_date)
    time_index = load_time_index(target_date)

    rows = []
    for game in schedule:
        away = normalize_team_name(game["away_zh"])
        home = normalize_team_name(game["home_zh"])
        away_id = int(game["away_team_id"])
        home_id = int(game["home_team_id"])
        away_team = team_stats.get(away_id, {})
        home_team = team_stats.get(home_id, {})
        away_pitcher = pitcher_stats.get(int(game["away_pitcher_id"] or 0), {})
        home_pitcher = pitcher_stats.get(int(game["home_pitcher_id"] or 0), {})

        away_components = {
            "打擊率壘包": offense_score(away_team.get("hitting", {})),
            "先發投手": pitcher_score(away_pitcher),
            "牛棚深度": bullpen_score(away_team.get("pitching", {}), -recent_run_diff(home, ctx)),
            "近期戰績": 0.055 * recent_run_diff(away, ctx),
            "隊史連勝連敗": 0.28 * streak_score(away, ctx),
            "對戰組合": 0.18 * h2h_score(away, home, ctx),
            "場地天氣代理": 0.02 * venue_factor(home, ctx),
        }
        home_components = {
            "打擊率壘包": offense_score(home_team.get("hitting", {})),
            "先發投手": pitcher_score(home_pitcher),
            "牛棚深度": bullpen_score(home_team.get("pitching", {}), -recent_run_diff(away, ctx)),
            "近期戰績": 0.055 * recent_run_diff(home, ctx),
            "隊史連勝連敗": 0.28 * streak_score(home, ctx),
            "對戰組合": 0.18 * h2h_score(home, away, ctx),
            "場地天氣代理": 0.02 * venue_factor(home, ctx),
            "主場": 0.09,
        }
        away_score = sum(away_components.values())
        home_score = sum(home_components.values())
        prob_home = max(0.30, min(0.70, sigmoid(home_score - away_score)))
        pick_side = "home" if prob_home >= 0.5 else "away"
        pick_zh = home if pick_side == "home" else away
        confidence = prob_home if pick_side == "home" else 1 - prob_home
        odds_row = odds_by_pk.get(game["game_pk"])
        market_prob = None
        edge = None
        odds_value = ""
        decision = "缺台灣運彩盤口"
        if odds_row:
            odds_value = odds_row["home_moneyline"] if pick_side == "home" else odds_row["away_moneyline"]
            parsed_odds = parse_moneyline(odds_value)
            market_prob = implied_probability(parsed_odds)
            edge = confidence - market_prob
            decision = "進階模型候選" if edge >= min_edge else "不推薦"
        rows.append(
            attach_game_time(
                {
                "date": target_date,
                "game_pk": game["game_pk"],
                "sportsbook": "台灣運彩" if odds_row else "",
                "matchup_zh": f"{away} @ {home}",
                "prediction_zh": pick_zh,
                "pick_side": pick_side,
                "confidence": round(confidence, 4),
                "odds": odds_value,
                "market_implied_prob": round(market_prob, 4) if market_prob is not None else "",
                "edge": round(edge, 4) if edge is not None else "",
                "advanced_score_home": round(home_score, 4),
                "advanced_score_away": round(away_score, 4),
                "home_components": home_components,
                "away_components": away_components,
                "decision": decision,
                "status": game.get("status", ""),
                },
                time_index,
            )
        )
    rows.sort(
        key=lambda row: (
            row["decision"] != "進階模型候選",
            time_sort_key(row),
            -(row.get("edge") if isinstance(row.get("edge"), float) else -99),
        )
    )
    candidates = [row for row in rows if row["decision"] == "進階模型候選"]
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "target_date": target_date,
        "model": "進階因子勝方 v1",
        "settings": {"min_edge": min_edge, "require_sportsbook": "台灣運彩"},
        "factor_notes": [
            "打擊率壘包：AVG/OBP/SLG、推估壘包率、三振率、四壞率、雙殺打率。",
            "投手型態：ERA/WHIP、K/9、BB/9、HR/9、滾地/飛球出局比例。",
            "牛棚深度：目前用全隊投球與近期失分代理，球員層牛棚可在 v2 加入。",
            "代打厚度與教練調度：目前以全隊打擊深度與近期一分差/連勝連敗代理，v2 需接每日名單與用人紀錄。",
            "場地天氣：目前用主場歷史總分代理，v2 可接球場座標與即時天氣。",
        ],
        "summary": {"games": len(rows), "candidates": len(candidates), "with_taiwan_odds": sum(1 for r in rows if r["sportsbook"] == "台灣運彩")},
        "candidates": candidates,
        "all_predictions": rows,
    }


def render_rows(rows: list[dict]) -> str:
    if not rows:
        return '<tr><td colspan="10">目前沒有進階模型候選。</td></tr>'
    return "\n".join(
        f"""
        <tr>
          <td>{row['game_pk']}</td>
          <td>{row.get('game_time_tw', '')}</td>
          <td>{row['matchup_zh']}</td>
          <td>{row['prediction_zh']}</td>
          <td>{float(row['confidence']) * 100:.1f}%</td>
          <td>{row.get('odds', '')}</td>
          <td>{float(row['market_implied_prob'] or 0) * 100:.1f}%</td>
          <td>{float(row['edge'] or 0) * 100:.1f}%</td>
          <td>{row['advanced_score_home']} / {row['advanced_score_away']}</td>
          <td>{row['decision']}</td>
        </tr>"""
        for row in rows
    )


def render_html(report: dict) -> str:
    candidate_rows = render_rows(report["candidates"])
    all_rows = render_rows(report["all_predictions"])
    notes = "".join(f"<li>{note}</li>" for note in report["factor_notes"])
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>MLB 進階因子勝方模型</title>
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
    <h1>MLB 進階因子勝方模型</h1>
    <div class="meta">
      日期：{report['target_date']}<br />
      模型：{report['model']}<br />
      台灣運彩盤口覆蓋：{report['summary']['with_taiwan_odds']} / {report['summary']['games']}<br />
      候選：{report['summary']['candidates']}<br />
      產生時間：{report['generated_at']}
    </div>
    <h2>進階模型候選</h2>
    <table>
      <thead><tr><th>GamePk</th><th>台灣時間</th><th>對戰</th><th>預測勝方</th><th>信心</th><th>台灣運彩賠率</th><th>市場隱含</th><th>Edge</th><th>主/客分數</th><th>決策</th></tr></thead>
      <tbody>{candidate_rows}</tbody>
    </table>
    <h2>全部進階預測</h2>
    <table>
      <thead><tr><th>GamePk</th><th>台灣時間</th><th>對戰</th><th>預測勝方</th><th>信心</th><th>台灣運彩賠率</th><th>市場隱含</th><th>Edge</th><th>主/客分數</th><th>決策</th></tr></thead>
      <tbody>{all_rows}</tbody>
    </table>
    <div class="note"><strong>因子說明</strong><ul>{notes}</ul></div>
  </main>
</body>
</html>"""


def write_outputs(report: dict) -> None:
    target_date = report["target_date"]
    json_path = Path(str(ADV_JSON).format(date=target_date))
    csv_path = Path(str(ADV_CSV).format(date=target_date))
    source_path = Path(str(SOURCE_JSON).format(date=target_date))
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(json.dumps({"generated_at": report["generated_at"], "source": "MLB Stats API team/person season stats"}, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = [
        "date",
        "game_pk",
        "game_time_tw",
        "game_time_utc",
        "sportsbook",
        "matchup_zh",
        "prediction_zh",
        "confidence",
        "odds",
        "market_implied_prob",
        "edge",
        "advanced_score_home",
        "advanced_score_away",
        "decision",
        "status",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(report["all_predictions"])
    ADV_HTML.write_text(render_html(report), encoding="utf-8")
    print(f"wrote {json_path}")
    print(f"wrote {csv_path}")
    print(f"wrote {ADV_HTML}")
    print(f"advanced_candidates={report['summary']['candidates']} games={report['summary']['games']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run advanced factors MLB winner model.")
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--min-edge", type=float, default=0.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = build_report(args.date, args.min_edge)
    write_outputs(report)


if __name__ == "__main__":
    main()
