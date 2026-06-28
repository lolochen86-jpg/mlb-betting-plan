#!/usr/bin/env python3
"""Generate daily MLB Monte Carlo simulation report."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import random
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path

from generate_game_simulator import build_sim_data
from settle_betting_roi import implied_probability, parse_moneyline


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"
ODDS_DIR = DATA_DIR / "odds"

MONEYLINE_CSV = ODDS_DIR / "mlb_moneyline_{date}.csv"
TOTALS_CSV = DATA_DIR / "totals_predictions_{date}.csv"
MC_JSON = DATA_DIR / "monte_carlo_{date}.json"
MC_CSV = DATA_DIR / "monte_carlo_{date}.csv"
MC_HTML = DOCS_DIR / "monte_carlo.html"

OUTCOMES = {
    "single": 15.2,
    "double": 4.7,
    "triple": 0.5,
    "homer": 3.3,
    "walk": 8.2,
    "strikeout": 22.0,
    "groundout": 20.0,
    "flyout": 17.0,
    "lineout": 7.0,
}


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def fair_decimal(probability: float) -> str:
    if probability <= 0:
        return "-"
    return f"{1 / probability:.2f}"


def stable_seed(*parts: str) -> int:
    text = "|".join(str(part) for part in parts)
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:16], 16)


def load_moneyline(target_date: str) -> dict[str, dict]:
    path = Path(str(MONEYLINE_CSV).format(date=target_date))
    if not path.exists():
        return {}
    rows = {}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            game_pk = str(row.get("game_pk", "")).strip()
            if not game_pk:
                continue
            try:
                row["away_moneyline_value"] = parse_moneyline(row.get("away_moneyline", ""))
                row["home_moneyline_value"] = parse_moneyline(row.get("home_moneyline", ""))
                row["away_implied"] = implied_probability(row["away_moneyline_value"])
                row["home_implied"] = implied_probability(row["home_moneyline_value"])
            except Exception:
                continue
            rows[game_pk] = row
    return rows


def load_totals(target_date: str) -> dict[str, dict]:
    path = Path(str(TOTALS_CSV).format(date=target_date))
    if not path.exists():
        return {}
    rows = {}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            game_pk = str(row.get("game_pk", "")).strip()
            if not game_pk:
                continue
            try:
                row["line_value"] = float(row.get("line", ""))
            except ValueError:
                continue
            rows[game_pk] = row
    return rows


def fresh_box(game: dict) -> dict[str, list[dict]]:
    return {
        "away": [deepcopy(player) | {"AB": 0, "R": 0, "H": 0, "RBI": 0, "BB": 0, "K": 0, "TB": 0, "HR": 0} for player in game["away_lineup"]],
        "home": [deepcopy(player) | {"AB": 0, "R": 0, "H": 0, "RBI": 0, "BB": 0, "K": 0, "TB": 0, "HR": 0} for player in game["home_lineup"]],
    }


def weighted_outcome(
    rng: random.Random,
    batter: dict,
    batting_profile: dict,
    pitching_profile: dict,
    pitcher_profile: dict,
    bases: list[dict | None],
    outs: int,
) -> str:
    offense = batting_profile["offense"] / 4.45
    prevention = (pitching_profile["prevention"] / 4.45) * pitcher_profile.get("run_prevention_factor", 1)
    k_factor = pitcher_profile.get("k_factor", 1)
    bb_factor = pitcher_profile.get("bb_factor", 1)
    hr_factor = pitcher_profile.get("hr_factor", 1)
    gb_factor = pitcher_profile.get("gb_factor", 1)
    weights = {
        "single": OUTCOMES["single"] * offense * batter["contact"],
        "double": OUTCOMES["double"] * offense * batter["power"],
        "triple": OUTCOMES["triple"] * offense * batter["power"],
        "homer": OUTCOMES["homer"] * offense * batter["power"] * batting_profile["power"] * hr_factor,
        "walk": OUTCOMES["walk"] * batter["patience"] * bb_factor,
        "strikeout": OUTCOMES["strikeout"] * (1.02 / batter["contact"]) * prevention * k_factor,
        "groundout": OUTCOMES["groundout"] * prevention * gb_factor,
        "flyout": OUTCOMES["flyout"],
        "lineout": OUTCOMES["lineout"],
    }
    if bases[0] and outs < 2:
        weights["gidp"] = 5.8 * batter["gidp"] * prevention * gb_factor
    roll = rng.random() * sum(weights.values())
    for outcome, weight in weights.items():
        roll -= weight
        if roll <= 0:
            return outcome
    return "groundout"


def force_walk(bases: list[dict | None], runner: dict) -> int:
    if not bases[0]:
        bases[0] = runner
        return 0
    if not bases[1]:
        bases[1] = bases[0]
        bases[0] = runner
        return 0
    if not bases[2]:
        bases[2] = bases[1]
        bases[1] = bases[0]
        bases[0] = runner
        return 0
    bases[2]["R"] += 1
    bases[2] = bases[1]
    bases[1] = bases[0]
    bases[0] = runner
    return 1


def advance_bases(bases: list[dict | None], batter: dict, base_count: int) -> tuple[int, int]:
    runs = 0
    rbi = 0
    for idx in range(2, -1, -1):
        runner = bases[idx]
        if not runner:
            continue
        bases[idx] = None
        target = idx + base_count
        if target >= 3:
            runs += 1
            rbi += 1
            runner["R"] += 1
        else:
            bases[target] = runner
    if base_count >= 4:
        runs += 1
        rbi += 1
        batter["R"] += 1
    else:
        bases[base_count - 1] = batter
    return runs, rbi


def simulate_game(game: dict, rng: random.Random) -> dict:
    inning = 1
    half = "top"
    outs = 0
    bases: list[dict | None] = [None, None, None]
    score = {"away": 0, "home": 0}
    hits = {"away": 0, "home": 0}
    batting_index = {"away": 0, "home": 0}
    box = fresh_box(game)
    plate_appearances = 0

    while inning <= 12 and plate_appearances < 130:
        side = "away" if half == "top" else "home"
        fielding = "home" if side == "away" else "away"
        batter = box[side][batting_index[side] % 9]
        batting_profile = game[f"{side}_profile"]
        pitching_profile = game[f"{fielding}_profile"]
        pitcher_profile = game.get(f"{fielding}_pitcher_profile", {})
        outcome = weighted_outcome(rng, batter, batting_profile, pitching_profile, pitcher_profile, bases, outs)
        runs = 0

        if outcome in {"single", "double", "triple", "homer"}:
            base_count = {"single": 1, "double": 2, "triple": 3, "homer": 4}[outcome]
            batter["AB"] += 1
            batter["H"] += 1
            batter["TB"] += base_count
            batter["HR"] += 1 if outcome == "homer" else 0
            hits[side] += 1
            runs, rbi = advance_bases(bases, batter, base_count)
            batter["RBI"] += rbi
        elif outcome == "walk":
            batter["BB"] += 1
            runs = force_walk(bases, batter)
            batter["RBI"] += runs
        elif outcome == "strikeout":
            batter["AB"] += 1
            batter["K"] += 1
            outs += 1
        elif outcome == "gidp":
            batter["AB"] += 1
            outs += min(2, 3 - outs)
            bases[0] = None
        else:
            batter["AB"] += 1
            outs += 1

        score[side] += runs
        plate_appearances += 1
        batting_index[side] = (batting_index[side] + 1) % 9

        if half == "bottom" and inning >= 9 and score["home"] > score["away"]:
            break
        if outs >= 3:
            outs = 0
            bases = [None, None, None]
            if half == "top":
                half = "bottom"
            else:
                if inning >= 9 and score["home"] != score["away"]:
                    break
                inning += 1
                half = "top"

    return {
        "away_score": score["away"],
        "home_score": score["home"],
        "total": score["away"] + score["home"],
        "winner": "away" if score["away"] > score["home"] else "home",
        "hits": hits,
        "box": box,
    }


def summarize_game(game: dict, simulations: int, totals: dict, moneyline: dict) -> dict:
    rng = random.Random(stable_seed(game["date"], game["game_pk"], str(simulations)))
    wins = {"away": 0, "home": 0}
    scores = {"away": 0, "home": 0}
    totals_list = []
    hit_totals = {
        "away": {player["name"]: 0 for player in game["away_lineup"]},
        "home": {player["name"]: 0 for player in game["home_lineup"]},
    }

    line = totals.get(str(game["game_pk"]), {}).get("line_value")
    over_count = 0
    under_count = 0
    push_count = 0

    for _ in range(simulations):
        result = simulate_game(game, rng)
        wins[result["winner"]] += 1
        scores["away"] += result["away_score"]
        scores["home"] += result["home_score"]
        totals_list.append(result["total"])
        if line is not None:
            if result["total"] > line:
                over_count += 1
            elif result["total"] < line:
                under_count += 1
            else:
                push_count += 1
        for side in ("away", "home"):
            for player in result["box"][side]:
                hit_totals[side][player["name"]] += player["H"]

    away_win_prob = wins["away"] / simulations
    home_win_prob = wins["home"] / simulations
    avg_away = scores["away"] / simulations
    avg_home = scores["home"] / simulations
    avg_total = sum(totals_list) / simulations
    total_line = line if line is not None else round(avg_total * 2) / 2
    over_prob = over_count / simulations if line is not None else None
    under_prob = under_count / simulations if line is not None else None

    odds_row = moneyline.get(str(game["game_pk"]))
    away_market = odds_row.get("away_implied") if odds_row else None
    home_market = odds_row.get("home_implied") if odds_row else None
    away_edge = away_win_prob - away_market if away_market is not None else None
    home_edge = home_win_prob - home_market if home_market is not None else None

    moneyline_pick = "不推薦"
    if odds_row and max(away_edge or -1, home_edge or -1) > 0:
        moneyline_pick = game["away"] if (away_edge or -1) >= (home_edge or -1) else game["home"]

    totals_pick = "無大小分盤口"
    if line is not None and over_prob is not None and under_prob is not None:
        totals_pick = "大分" if over_prob >= under_prob else "小分"

    players = []
    for side, team in (("away", game["away"]), ("home", game["home"])):
        for player in game[f"{side}_lineup"]:
            players.append(
                {
                    "team": team,
                    "side": side,
                    "name": player["name"],
                    "position": player["pos"],
                    "expected_hits": round(hit_totals[side][player["name"]] / simulations, 3),
                }
            )
    players.sort(key=lambda row: row["expected_hits"], reverse=True)

    return {
        "game_pk": game["game_pk"],
        "matchup_zh": f"{game['away']} @ {game['home']}",
        "away_zh": game["away"],
        "home_zh": game["home"],
        "lineup_source": game.get("lineup_source", ""),
        "simulations": simulations,
        "avg_away_score": round(avg_away, 2),
        "avg_home_score": round(avg_home, 2),
        "avg_total": round(avg_total, 2),
        "away_win_prob": round(away_win_prob, 4),
        "home_win_prob": round(home_win_prob, 4),
        "away_fair_odds": fair_decimal(away_win_prob),
        "home_fair_odds": fair_decimal(home_win_prob),
        "sportsbook": odds_row.get("sportsbook") if odds_row else "",
        "away_market_odds": odds_row.get("away_moneyline") if odds_row else "",
        "home_market_odds": odds_row.get("home_moneyline") if odds_row else "",
        "away_market_implied": round(away_market, 4) if away_market is not None else None,
        "home_market_implied": round(home_market, 4) if home_market is not None else None,
        "away_edge": round(away_edge, 4) if away_edge is not None else None,
        "home_edge": round(home_edge, 4) if home_edge is not None else None,
        "moneyline_pick": moneyline_pick,
        "total_line": total_line,
        "over_prob": round(over_prob, 4) if over_prob is not None else None,
        "under_prob": round(under_prob, 4) if under_prob is not None else None,
        "push_prob": round(push_count / simulations, 4) if line is not None else None,
        "totals_pick": totals_pick,
        "top_hitters": players[:8],
    }


def build_report(target_date: str, simulations: int) -> dict:
    sim_data = build_sim_data(target_date)
    moneyline = load_moneyline(target_date)
    totals = load_totals(target_date)
    games = [summarize_game(game, simulations, totals, moneyline) for game in sim_data["games"]]
    games.sort(key=lambda row: max(row["away_win_prob"], row["home_win_prob"]), reverse=True)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "target_date": target_date,
        "simulations_per_game": simulations,
        "games": games,
        "note": "Monte Carlo uses the same role-based plate-appearance engine as the game simulator. It is a probability distribution, not official MLB play-by-play or guaranteed betting profit.",
    }


def write_csv(report: dict) -> None:
    path = Path(str(MC_CSV).format(date=report["target_date"]))
    fields = [
        "game_pk",
        "matchup_zh",
        "simulations",
        "avg_away_score",
        "avg_home_score",
        "avg_total",
        "away_win_prob",
        "home_win_prob",
        "away_fair_odds",
        "home_fair_odds",
        "sportsbook",
        "away_market_odds",
        "home_market_odds",
        "away_edge",
        "home_edge",
        "moneyline_pick",
        "lineup_source",
        "total_line",
        "over_prob",
        "under_prob",
        "totals_pick",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in report["games"]:
            writer.writerow({field: row.get(field, "") for field in fields})


def render_html(report: dict) -> str:
    cards = []
    for game in report["games"]:
        away_prob = pct(game["away_win_prob"])
        home_prob = pct(game["home_win_prob"])
        over_text = pct(game["over_prob"]) if game["over_prob"] is not None else "-"
        under_text = pct(game["under_prob"]) if game["under_prob"] is not None else "-"
        away_edge = pct(game["away_edge"]) if game["away_edge"] is not None else "-"
        home_edge = pct(game["home_edge"]) if game["home_edge"] is not None else "-"
        hitters = "".join(
            f"<tr><td>{html.escape(player['name'])}</td><td>{html.escape(player['team'])}</td><td>{player['position']}</td><td>{player['expected_hits']:.3f}</td></tr>"
            for player in game["top_hitters"]
        )
        source_label = {
            "official_mlb_boxscore": "官方先發打線",
            "projected_roster_stats_lineup": "先發未公布，使用 active roster 與本季打擊資料預估",
            "fallback_role_lineup": "先發未公布，使用角色打線",
        }.get(game.get("lineup_source", ""), game.get("lineup_source", "") or "未標記")
        cards.append(
            f"""
      <section class="game-card">
        <div class="game-head">
          <div>
            <h2>{html.escape(game['matchup_zh'])}</h2>
            <p>平均比分 {game['away_zh']} {game['avg_away_score']:.2f}：{game['home_zh']} {game['avg_home_score']:.2f}，平均總分 {game['avg_total']:.2f}</p>
            <p>打線來源：{html.escape(source_label)}</p>
          </div>
          <div class="pick">{html.escape(game['moneyline_pick'])}</div>
        </div>
        <div class="metrics">
          <div><span>{html.escape(game['away_zh'])} 勝率</span><strong>{away_prob}</strong><small>公平賠率 {game['away_fair_odds']} / edge {away_edge}</small></div>
          <div><span>{html.escape(game['home_zh'])} 勝率</span><strong>{home_prob}</strong><small>公平賠率 {game['home_fair_odds']} / edge {home_edge}</small></div>
          <div><span>大小分 {game['total_line']}</span><strong>大 {over_text}</strong><small>小 {under_text} / {html.escape(game['totals_pick'])}</small></div>
          <div><span>台灣運彩盤口</span><strong>{html.escape(str(game['sportsbook'] or '無'))}</strong><small>客 {html.escape(str(game['away_market_odds'] or '-'))} / 主 {html.escape(str(game['home_market_odds'] or '-'))}</small></div>
        </div>
        <details>
          <summary>球員安打期望值 Top 8</summary>
          <table><thead><tr><th>球員</th><th>球隊</th><th>守位</th><th>期望安打</th></tr></thead><tbody>{hitters}</tbody></table>
        </details>
      </section>"""
        )
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>MLB 蒙地卡羅模擬</title>
  <style>
    :root {{ --bg:#f4f6f3; --surface:#fff; --ink:#17201b; --muted:#68736e; --line:#dbe2dc; --accent:#155f56; --warn:#a15b17; }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--ink); font-family:"Microsoft JhengHei","Noto Sans TC",system-ui,sans-serif; letter-spacing:0; }}
    header {{ padding:28px 34px 18px; border-bottom:1px solid var(--line); background:#fff; }}
    nav {{ display:flex; gap:10px; flex-wrap:wrap; margin-bottom:18px; }}
    nav a {{ color:var(--accent); text-decoration:none; font-weight:800; border:1px solid var(--line); padding:8px 10px; border-radius:8px; background:#fff; }}
    h1 {{ margin:0 0 8px; font-size:32px; }}
    p {{ margin:0; color:var(--muted); line-height:1.6; }}
    main {{ max-width:1280px; margin:0 auto; padding:22px; }}
    .summary {{ display:grid; grid-template-columns:repeat(3,1fr); gap:14px; margin-bottom:18px; }}
    .summary div, .game-card {{ background:var(--surface); border:1px solid var(--line); border-radius:8px; }}
    .summary div {{ padding:16px; }}
    .summary span, .metrics span {{ display:block; color:var(--muted); font-size:13px; font-weight:800; }}
    .summary strong {{ display:block; font-size:28px; margin-top:4px; }}
    .game-card {{ padding:18px; margin-bottom:16px; }}
    .game-head {{ display:flex; align-items:start; justify-content:space-between; gap:18px; border-bottom:1px solid var(--line); padding-bottom:14px; margin-bottom:14px; }}
    h2 {{ margin:0 0 6px; font-size:22px; }}
    .pick {{ min-width:120px; text-align:center; border:1px solid var(--line); border-radius:8px; padding:10px; font-weight:900; color:var(--warn); background:#fff8ed; }}
    .metrics {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; }}
    .metrics div {{ border:1px solid var(--line); border-radius:8px; padding:12px; min-height:96px; }}
    .metrics strong {{ display:block; font-size:24px; margin:6px 0; }}
    .metrics small {{ color:var(--muted); line-height:1.5; }}
    details {{ margin-top:14px; }}
    summary {{ cursor:pointer; font-weight:900; color:var(--accent); }}
    table {{ width:100%; border-collapse:collapse; margin-top:10px; font-size:14px; }}
    th,td {{ border-bottom:1px solid var(--line); padding:9px; text-align:left; }}
    th:last-child,td:last-child {{ text-align:right; }}
    footer {{ color:var(--muted); font-size:12px; line-height:1.6; padding:4px 0 24px; }}
    @media (max-width:900px) {{ .summary,.metrics {{ grid-template-columns:1fr; }} header {{ padding:22px; }} main {{ padding:14px; }} .game-head {{ display:block; }} .pick {{ margin-top:12px; }} }}
  </style>
</head>
<body>
  <header>
    <nav>
      <a href="index.html">總覽</a>
      <a href="daily_predictions.html">今日預測</a>
      <a href="totals_predictions.html">大小分</a>
      <a href="game_simulator.html">逐打席模擬</a>
      <a href="betting_ticket.html">投注單</a>
    </nav>
    <h1>蒙地卡羅模擬</h1>
    <p>目標日期：{report['target_date']} / 每場模擬 {report['simulations_per_game']:,} 次 / 產生時間：{report['generated_at']}</p>
  </header>
  <main>
    <section class="summary">
      <div><span>賽程場數</span><strong>{len(report['games'])}</strong></div>
      <div><span>總模擬場次</span><strong>{len(report['games']) * report['simulations_per_game']:,}</strong></div>
      <div><span>用途</span><strong>勝率 / 大小分 / 安打期望</strong></div>
    </section>
    {''.join(cards)}
    <footer>{html.escape(report['note'])}</footer>
  </main>
</body>
</html>"""


def write_outputs(report: dict) -> None:
    json_path = Path(str(MC_JSON).format(date=report["target_date"]))
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(report)
    MC_HTML.write_text(render_html(report), encoding="utf-8")
    print(f"wrote {json_path}")
    print(f"wrote {Path(str(MC_CSV).format(date=report['target_date']))}")
    print(f"wrote {MC_HTML}")
    print(f"monte_carlo_games={len(report['games'])}")
    print(f"simulations_per_game={report['simulations_per_game']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate MLB Monte Carlo simulation report.")
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--simulations", type=int, default=10000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.simulations < 100:
        raise SystemExit("--simulations must be at least 100")
    write_outputs(build_report(args.date, args.simulations))


if __name__ == "__main__":
    main()
