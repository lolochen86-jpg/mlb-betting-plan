#!/usr/bin/env python3
"""Generate a browser-only plate appearance simulator for the daily MLB slate."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from mlb_player_context import fetch_game_player_context, fetch_pitcher_profile, fetch_projected_lineup
from run_real_mlb_backtest import DEFAULT_GAMES_CSV, load_games


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"

DAILY_JSON = DATA_DIR / "daily_predictions_{date}.json"
SIM_DATA_JSON = DATA_DIR / "game_simulator_{date}.json"
SIM_HTML = DOCS_DIR / "game_simulator.html"


LINEUP_POSITIONS = ["CF", "2B", "1B", "C", "DH", "LF", "3B", "RF", "SS"]


def avg(values: list[float], fallback: float) -> float:
    return sum(values) / len(values) if values else fallback


def team_profiles(history: list[dict]) -> dict[str, dict]:
    by_team = defaultdict(lambda: {"rs": [], "ra": [], "totals": []})
    for game in history:
        away = game["away_zh"]
        home = game["home_zh"]
        away_runs = float(game["away_score"])
        home_runs = float(game["home_score"])
        by_team[away]["rs"].append(away_runs)
        by_team[away]["ra"].append(home_runs)
        by_team[away]["totals"].append(away_runs + home_runs)
        by_team[home]["rs"].append(home_runs)
        by_team[home]["ra"].append(away_runs)
        by_team[home]["totals"].append(away_runs + home_runs)
    profiles = {}
    for team, rows in by_team.items():
        season_rs = avg(rows["rs"], 4.4)
        season_ra = avg(rows["ra"], 4.4)
        recent_rs = avg(rows["rs"][-20:], season_rs)
        recent_ra = avg(rows["ra"][-20:], season_ra)
        offense = 0.55 * recent_rs + 0.45 * season_rs
        prevention = 0.55 * recent_ra + 0.45 * season_ra
        profiles[team] = {
            "offense": round(offense, 3),
            "prevention": round(prevention, 3),
            "power": round(max(0.75, min(1.25, offense / 4.45)), 3),
            "contact": round(max(0.75, min(1.25, 1.12 - max(0, prevention - 4.4) / 8)), 3),
        }
    return profiles


def pseudo_lineup(team: str) -> list[dict]:
    # Batting-order roles are intentionally stable, not real lineups.
    roles = [
        ("開路先鋒", 1.08, 0.92, 1.10, 0.95),
        ("二棒推進", 1.03, 0.96, 1.04, 1.02),
        ("中心打者", 0.98, 1.18, 0.95, 1.00),
        ("重砲四棒", 0.92, 1.30, 0.88, 1.05),
        ("長打火力", 0.95, 1.20, 0.90, 1.03),
        ("左外野手", 1.00, 1.02, 1.00, 1.00),
        ("三壘手", 0.98, 1.05, 0.98, 1.05),
        ("右外野手", 0.96, 1.03, 0.98, 1.02),
        ("游擊守備", 1.02, 0.86, 1.04, 1.08),
    ]
    return [
        {
            "id": None,
            "name": f"{team}{label}",
            "pos": LINEUP_POSITIONS[idx],
            "contact": contact,
            "power": power,
            "patience": patience,
            "gidp": gidp,
            "avg": 0.245,
            "obp": 0.315,
            "slg": 0.400,
            "k_rate": 0.22,
            "bb_rate": 0.08,
            "sample_pa": 0,
            "source": "fallback_role_lineup",
        }
        for idx, (label, contact, power, patience, gidp) in enumerate(roles)
    ]


def default_pitcher_profile() -> dict:
    return {
        "era": 4.50,
        "whip": 1.350,
        "k_factor": 1.0,
        "bb_factor": 1.0,
        "hr_factor": 1.0,
        "gb_factor": 1.0,
        "run_prevention_factor": 1.0,
        "sample_ip": 0,
    }


def build_sim_data(target_date: str) -> dict:
    daily_path = Path(str(DAILY_JSON).format(date=target_date))
    if not daily_path.exists():
        raise SystemExit(f"Missing daily predictions: {daily_path}")
    daily = json.loads(daily_path.read_text(encoding="utf-8"))
    history = [game for game in load_games(DEFAULT_GAMES_CSV) if game["date"] < target_date]
    profiles = team_profiles(history)
    season = target_date[:4]
    games = []
    for row in daily.get("all_predictions", []):
        away = row.get("away_zh", "")
        home = row.get("home_zh", "")
        game_pk = str(row.get("game_pk", ""))
        away_lineup = pseudo_lineup(away)
        home_lineup = pseudo_lineup(home)
        lineup_source = "fallback_role_lineup"
        try:
            context = fetch_game_player_context(game_pk, season)
            if context.get("away_lineup") and context.get("home_lineup"):
                away_lineup = context["away_lineup"]
                home_lineup = context["home_lineup"]
                lineup_source = context["lineup_source"]
            else:
                projected_away = fetch_projected_lineup(row.get("away_team_id"), season, target_date)
                projected_home = fetch_projected_lineup(row.get("home_team_id"), season, target_date)
                if projected_away and projected_home:
                    away_lineup = projected_away
                    home_lineup = projected_home
                    lineup_source = "projected_recent_lineup_order" if any(p.get("source") == "projected_recent_lineup_order" for p in projected_away + projected_home) else "projected_roster_stats_lineup"
        except Exception as exc:
            lineup_source = f"fallback_role_lineup: {exc}"
        away_pitcher_profile = default_pitcher_profile()
        home_pitcher_profile = default_pitcher_profile()
        try:
            away_pitcher_profile = {**away_pitcher_profile, **fetch_pitcher_profile(row.get("away_probable_pitcher_id"), season)}
            home_pitcher_profile = {**home_pitcher_profile, **fetch_pitcher_profile(row.get("home_probable_pitcher_id"), season)}
        except Exception:
            pass
        games.append(
            {
                "date": target_date,
                "game_pk": game_pk,
                "game_time_tw": row.get("game_time_tw", ""),
                "game_time_utc": row.get("game_time_utc", ""),
                "status": row.get("status", ""),
                "away": away,
                "home": home,
                "away_pitcher": row.get("away_probable_pitcher_zh") or "未公布",
                "home_pitcher": row.get("home_probable_pitcher_zh") or "未公布",
                "away_pitcher_id": row.get("away_probable_pitcher_id"),
                "home_pitcher_id": row.get("home_probable_pitcher_id"),
                "away_pitcher_profile": away_pitcher_profile,
                "home_pitcher_profile": home_pitcher_profile,
                "lineup_source": lineup_source,
                "prediction": row.get("prediction_zh", ""),
                "confidence": row.get("confidence", 0),
                "away_profile": profiles.get(away, {"offense": 4.4, "prevention": 4.4, "power": 1, "contact": 1}),
                "home_profile": profiles.get(home, {"offense": 4.4, "prevention": 4.4, "power": 1, "contact": 1}),
                "away_lineup": away_lineup,
                "home_lineup": home_lineup,
            }
        )
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "target_date": target_date,
        "games": games,
        "note": "This is a stochastic plate-appearance simulator using MLB official lineups when available, season hitter stats, probable pitcher style, and team run profiles. It is not official MLB play-by-play.",
    }


def render_html(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>MLB 逐打席賽程模擬</title>
  <style>
    :root {{
      --bg: #f3f5f2;
      --surface: #ffffff;
      --ink: #151a18;
      --muted: #66716b;
      --line: #d9e0da;
      --accent: #165f56;
      --orange: #c95f20;
      --blue: #133a5e;
      --green: #5f7f3f;
      --clay: #b58b67;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--bg); color: var(--ink); font-family: "Microsoft JhengHei", "Noto Sans TC", system-ui, sans-serif; letter-spacing: 0; }}
    .top-strip {{ display: flex; align-items: stretch; gap: 10px; padding: 10px 14px; background: #fff; border-bottom: 1px solid var(--line); overflow-x: auto; }}
    .date-box {{ min-width: 76px; border: 1px solid var(--line); border-radius: 8px; display: grid; place-items: center; font-weight: 800; line-height: 1.15; }}
    .game-tab {{ min-width: 190px; border: 1px solid var(--line); border-radius: 8px; background: #fff; padding: 8px 10px; cursor: pointer; text-align: left; }}
    .game-tab.active {{ border-color: #183950; box-shadow: inset 0 0 0 1px #183950; }}
    .tab-row {{ display: flex; justify-content: space-between; gap: 8px; font-size: 13px; margin: 2px 0; }}
    .layout {{ display: grid; grid-template-columns: 380px minmax(460px, 1fr) 760px; gap: 14px; padding: 14px; min-height: calc(100vh - 88px); }}
    .panel {{ background: var(--surface); border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }}
    .panel-head {{ padding: 14px 16px; border-bottom: 1px solid var(--line); display: flex; align-items: center; justify-content: space-between; gap: 12px; }}
    .nav-tabs {{ display: flex; gap: 20px; font-weight: 800; color: #808780; font-size: 20px; }}
    .nav-tabs span:first-child {{ color: #111; }}
    .field-wrap {{ padding: 14px 16px; }}
    .venue {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; color: #2b332f; font-weight: 700; }}
    .hide-btn, button, select {{ border: 1px solid var(--line); background: white; border-radius: 8px; padding: 8px 10px; font: inherit; }}
    button {{ cursor: pointer; font-weight: 800; }}
    button.primary {{ background: var(--accent); color: white; border-color: var(--accent); }}
    button.warn {{ background: #fff3df; color: #8b4d12; border-color: #efc38c; }}
    .diamond {{ position: relative; height: 205px; background: linear-gradient(#647843, #596f3a); clip-path: polygon(50% 4%, 95% 36%, 82% 95%, 18% 95%, 5% 36%); border: 8px solid #324133; }}
    .infield {{ position: absolute; left: 118px; top: 86px; width: 128px; height: 90px; background: #b99675; clip-path: polygon(50% 0, 100% 50%, 50% 100%, 0 50%); }}
    .base {{ position: absolute; width: 18px; height: 18px; background: #eef1e9; transform: rotate(45deg); border: 1px solid #3e473e; }}
    .base.on {{ background: #f1b642; }}
    .b1 {{ left: 224px; top: 128px; }} .b2 {{ left: 173px; top: 82px; }} .b3 {{ left: 122px; top: 128px; }} .hp {{ left: 173px; top: 170px; background: white; }}
    .pos {{ position: absolute; font-size: 10px; font-weight: 800; background: white; padding: 2px 5px; border-radius: 3px; }}
    .lf {{ left: 70px; top: 66px; }} .cf {{ left: 171px; top: 42px; }} .rf {{ left: 270px; top: 70px; }} .ss {{ left: 150px; top: 106px; }} .twob {{ left: 210px; top: 110px; }} .threeb {{ left: 124px; top: 142px; }} .oneb {{ left: 245px; top: 145px; }} .p {{ left: 178px; top: 137px; }} .c {{ left: 178px; top: 176px; }}
    .event-log {{ height: 420px; overflow-y: auto; border-top: 1px solid var(--line); }}
    .event {{ display: grid; grid-template-columns: 46px 1fr; gap: 10px; padding: 12px 14px; border-bottom: 1px solid var(--line); }}
    .event-num {{ width: 28px; height: 28px; border-radius: 999px; background: #7257b9; color: white; display: grid; place-items: center; font-weight: 900; }}
    .event small {{ color: var(--muted); font-weight: 800; }}
    .score-head {{ display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; padding: 16px 24px; gap: 16px; }}
    .team-side {{ display: grid; grid-template-columns: 1fr auto; align-items: center; gap: 12px; }}
    .team-side.right {{ grid-template-columns: auto 1fr; text-align: right; }}
    .score {{ font-size: 36px; font-weight: 900; }}
    .inning {{ font-weight: 900; color: #111; text-align: center; }}
    .stadium {{ position: relative; min-height: 470px; background: linear-gradient(#060606 0 26%, #1b2730 26% 56%, #7b633f 56% 68%, #497238 68% 100%); overflow: hidden; border-radius: 0 0 8px 8px; }}
    .lights {{ position: absolute; width: 70px; height: 70px; background: radial-gradient(#fff, #f4f4e4 45%, transparent 70%); filter: blur(2px); top: 32px; }}
    .lights.left {{ left: 190px; }} .lights.right {{ right: 190px; }}
    .jumbotron {{ position: absolute; left: 50%; top: 90px; transform: translateX(-50%); width: 220px; height: 105px; background: #151515; border: 8px solid #2d2d2d; color: #f4b04f; display: grid; place-items: center; font-size: 28px; font-weight: 900; }}
    .batter {{ position: absolute; right: 180px; bottom: 62px; width: 98px; height: 210px; }}
    .batter::before {{ content: ""; position: absolute; left: 36px; top: 0; width: 46px; height: 46px; border-radius: 50%; background: #1c1c1c; }}
    .batter::after {{ content: ""; position: absolute; left: 18px; top: 48px; width: 72px; height: 112px; border-radius: 32px 32px 18px 18px; background: var(--orange); }}
    .bat {{ position: absolute; right: 130px; bottom: 230px; width: 160px; height: 12px; background: #e4d0b2; border-radius: 12px; transform: rotate(-25deg); transform-origin: right center; }}
    .pitch-zone {{ position: absolute; left: 50%; bottom: 115px; transform: translateX(-50%); width: 92px; height: 118px; border: 2px solid rgba(255,255,255,.65); display: grid; grid-template-columns: repeat(3, 1fr); grid-template-rows: repeat(3, 1fr); }}
    .pitch-zone div {{ border: 1px solid rgba(255,255,255,.25); }}
    .caption {{ position: absolute; left: 50%; bottom: 18px; transform: translateX(-50%); width: min(540px, 86%); background: white; border-radius: 8px; padding: 12px 14px; box-shadow: 0 10px 28px rgba(0,0,0,.18); font-size: 16px; }}
    .due-up {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; padding: 14px 18px; border-top: 1px solid var(--line); }}
    .player-card {{ display: grid; grid-template-columns: 46px 1fr; gap: 10px; align-items: center; border-right: 1px solid var(--line); min-height: 70px; }}
    .avatar {{ width: 42px; height: 42px; border-radius: 50%; background: var(--blue); color: white; display: grid; place-items: center; font-weight: 900; }}
    .scorebox {{ padding: 14px 16px; overflow-x: auto; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ padding: 8px 9px; border-bottom: 1px solid var(--line); text-align: right; white-space: nowrap; }}
    th:first-child, td:first-child {{ text-align: left; }}
    .split {{ display: grid; grid-template-columns: 1fr 1fr; border-top: 1px solid var(--line); }}
    .split > div {{ padding: 12px 14px; border-right: 1px solid var(--line); overflow-x: auto; }}
    .controls {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
    @media (max-width: 1250px) {{ .layout {{ grid-template-columns: 360px 1fr; }} .right-panel {{ grid-column: 1 / -1; }} }}
    @media (max-width: 800px) {{ .layout {{ grid-template-columns: 1fr; padding: 8px; }} .stadium {{ min-height: 390px; }} .right-panel {{ grid-column: auto; }} .split {{ grid-template-columns: 1fr; }} .batter, .bat {{ display:none; }} }}
  </style>
</head>
<body>
  <div class="top-strip" id="gameStrip"></div>
  <main class="layout">
    <section class="panel">
      <div class="panel-head"><div class="nav-tabs"><span>Live</span><span>Summary</span><span>Insights</span></div><button class="hide-btn">Hide</button></div>
      <div class="field-wrap">
        <div class="venue"><span id="venueName">模擬球場</span><span id="baseStateText">0 出局</span></div>
        <div class="diamond">
          <div class="infield"></div>
          <div class="base b1" id="base1"></div><div class="base b2" id="base2"></div><div class="base b3" id="base3"></div><div class="base hp"></div>
          <span class="pos lf">LF</span><span class="pos cf">CF</span><span class="pos rf">RF</span><span class="pos ss">SS</span><span class="pos twob">2B</span><span class="pos threeb">3B</span><span class="pos oneb">1B</span><span class="pos p">P</span><span class="pos c">C</span>
        </div>
      </div>
      <div class="event-log" id="eventLog"></div>
    </section>

    <section class="panel">
      <div class="score-head">
        <div class="team-side"><div><strong id="awayName"></strong><br><small id="awayPitcher"></small></div><div class="score" id="awayScore">0</div></div>
        <div class="inning" id="inningLabel">TOP 1</div>
        <div class="team-side right"><div class="score" id="homeScore">0</div><div><strong id="homeName"></strong><br><small id="homePitcher"></small></div></div>
      </div>
      <div class="stadium">
        <div class="lights left"></div><div class="lights right"></div>
        <div class="jumbotron" id="parkBoard">MLB SIM</div>
        <div class="bat"></div><div class="batter"></div>
        <div class="pitch-zone"><div></div><div></div><div></div><div></div><div></div><div></div><div></div><div></div><div></div></div>
        <div class="caption" id="caption">選擇賽事後按「下一打席」。</div>
      </div>
      <div class="panel-head">
        <div class="controls">
          <button class="primary" id="nextPaBtn">下一打席</button>
          <button id="halfInningBtn">跑完半局</button>
          <button id="fullGameBtn">跑完整場</button>
          <button class="warn" id="resetBtn">重設</button>
        </div>
        <select id="speedSelect"><option value="0">即時</option><option value="180">慢速</option></select>
      </div>
      <div class="due-up" id="dueUp"></div>
    </section>

    <section class="panel right-panel">
      <div class="scorebox" id="lineScore"></div>
      <div class="split"><div id="awayBox"></div><div id="homeBox"></div></div>
    </section>
  </main>

  <script>
    const SIM_DATA = {payload};
    const state = {{
      gameIndex: 0, inning: 1, half: 'top', outs: 0, bases: [null, null, null],
      score: {{away: 0, home: 0}}, battingIndex: {{away: 0, home: 0}},
      lines: {{away: Array(9).fill(0), home: Array(9).fill(0)}}, hits: {{away: 0, home: 0}}, errors: {{away: 0, home: 0}},
      events: [], completed: false, box: {{away: [], home: []}}
    }};

    const eventTexts = {{
      single: ['擊出一壘安打', '穿越內野形成安打', '反方向安打上壘'],
      double: ['掃出二壘安打', '打穿右外野形成二壘打'],
      triple: ['擊出深遠三壘安打'],
      homer: ['轟出全壘打', '把球送出大牆'],
      walk: ['選到四壞保送', '耐心選球上壘'],
      strikeout: ['遭到三振', '揮空三振'],
      groundout: ['擊出滾地球出局', '滾地球傳一壘出局'],
      flyout: ['飛球遭接殺', '高飛球出局'],
      lineout: ['平飛球遭接殺'],
      gidp: ['擊出滾地雙殺打']
    }};

    function cloneLineup(lineup) {{
      return lineup.map(p => ({{...p, AB:0, R:0, H:0, RBI:0, BB:0, K:0, TB:0, HR:0}}));
    }}

    function loadGame(index) {{
      state.gameIndex = index; state.inning = 1; state.half = 'top'; state.outs = 0; state.bases = [null,null,null];
      state.score = {{away:0, home:0}}; state.battingIndex = {{away:0, home:0}};
      state.lines = {{away:Array(9).fill(0), home:Array(9).fill(0)}}; state.hits={{away:0, home:0}}; state.errors={{away:0, home:0}};
      state.events=[]; state.completed=false;
      const g = currentGame();
      state.box = {{away: cloneLineup(g.away_lineup), home: cloneLineup(g.home_lineup)}};
      render();
    }}

    function currentGame() {{ return SIM_DATA.games[state.gameIndex]; }}
    function battingSide() {{ return state.half === 'top' ? 'away' : 'home'; }}
    function fieldingSide() {{ return state.half === 'top' ? 'home' : 'away'; }}
    function sideName(side) {{ return side === 'away' ? currentGame().away : currentGame().home; }}
    function pitcherName(side) {{ return side === 'away' ? currentGame().away_pitcher : currentGame().home_pitcher; }}
    function batter() {{ const side = battingSide(); return state.box[side][state.battingIndex[side] % 9]; }}
    function randPick(arr) {{ return arr[Math.floor(Math.random()*arr.length)]; }}

    function pitcherStyle(side) {{
      const g = currentGame();
      return side === 'away' ? (g.away_pitcher_profile || {{}}) : (g.home_pitcher_profile || {{}});
    }}

    function weightedOutcome(b, battingProfile, pitchingProfile, pitcherProfile, runners) {{
      const offense = battingProfile.offense / 4.45;
      const prevention = (pitchingProfile.prevention / 4.45) * (pitcherProfile.run_prevention_factor || 1);
      const kFactor = pitcherProfile.k_factor || 1;
      const bbFactor = pitcherProfile.bb_factor || 1;
      const hrFactor = pitcherProfile.hr_factor || 1;
      const gbFactor = pitcherProfile.gb_factor || 1;
      let weights = {{
        single: 15.2 * offense * b.contact,
        double: 4.7 * offense * b.power,
        triple: 0.5 * offense * b.power,
        homer: 3.3 * offense * b.power * battingProfile.power * hrFactor,
        walk: 8.2 * b.patience * bbFactor,
        strikeout: 22.0 * (1.02 / b.contact) * prevention * kFactor,
        groundout: 20.0 * prevention * gbFactor,
        flyout: 17.0,
        lineout: 7.0
      }};
      if (runners[0] && state.outs < 2) weights.gidp = 5.8 * b.gidp * prevention * gbFactor;
      const total = Object.values(weights).reduce((a,v)=>a+v,0);
      let roll = Math.random()*total;
      for (const [key,val] of Object.entries(weights)) {{ roll -= val; if (roll <= 0) return key; }}
      return 'groundout';
    }}

    function forceWalk(runner) {{
      let runs = 0;
      if (!state.bases[0]) state.bases[0] = runner;
      else if (!state.bases[1]) {{ state.bases[1] = state.bases[0]; state.bases[0] = runner; }}
      else if (!state.bases[2]) {{ state.bases[2] = state.bases[1]; state.bases[1] = state.bases[0]; state.bases[0] = runner; }}
      else {{ runs++; state.bases[2] = state.bases[1]; state.bases[1] = state.bases[0]; state.bases[0] = runner; }}
      return runs;
    }}

    function advanceBases(b, bases) {{
      let runs = 0, rbi = 0;
      for (let i=2; i>=0; i--) {{
        const runner = state.bases[i];
        if (!runner) continue;
        state.bases[i] = null;
        const target = i + bases;
        if (target >= 3) {{ runs++; rbi++; runner.R++; }}
        else state.bases[target] = runner;
      }}
      if (bases >= 4) {{ runs++; rbi++; b.R++; }}
      else state.bases[bases-1] = b;
      b.RBI += rbi;
      return runs;
    }}

    function addRuns(side, runs) {{
      state.score[side] += runs;
      state.lines[side][Math.min(state.inning,9)-1] += runs;
    }}

    function nextHalf() {{
      state.outs = 0; state.bases = [null,null,null];
      if (state.half === 'top') state.half = 'bottom';
      else {{ state.half = 'top'; state.inning++; }}
      if (state.inning > 9 && state.score.home !== state.score.away && state.half === 'top') state.completed = true;
      if (state.inning > 12) state.completed = true;
    }}

    function simulatePA() {{
      if (state.completed) return;
      const g = currentGame(), side = battingSide(), field = fieldingSide();
      const b = batter();
      const battingProfile = side === 'away' ? g.away_profile : g.home_profile;
      const pitchingProfile = field === 'away' ? g.away_profile : g.home_profile;
      const outcome = weightedOutcome(b, battingProfile, pitchingProfile, pitcherStyle(field), state.bases);
      let runs = 0, desc = '', pitch = ['速球','滑球','曲球','變速球','伸卡球'][Math.floor(Math.random()*5)];
      if (['single','double','triple','homer'].includes(outcome)) {{
        b.AB++; b.H++; b.TB += outcome === 'single' ? 1 : outcome === 'double' ? 2 : outcome === 'triple' ? 3 : 4;
        if (outcome === 'homer') b.HR++;
        state.hits[side]++;
        runs = advanceBases(b, outcome === 'single' ? 1 : outcome === 'double' ? 2 : outcome === 'triple' ? 3 : 4);
      }} else if (outcome === 'walk') {{ b.BB++; runs = forceWalk(b); b.RBI += runs; }}
      else if (outcome === 'strikeout') {{ b.AB++; b.K++; state.outs++; }}
      else if (outcome === 'gidp') {{ b.AB++; state.outs += Math.min(2, 3-state.outs); state.bases[0] = null; }}
      else {{ b.AB++; state.outs++; }}
      if (runs) addRuns(side, runs);
      desc = b.name + ' ' + randPick(eventTexts[outcome]) + (runs ? '，' + runs + ' 分進帳' : '') + '。';
      state.events.unshift({{inning: inningText(), side, batter: b.name, outcome, pitch, desc, outs: state.outs, score: state.score.away + '-' + state.score.home}});
      state.battingIndex[side] = (state.battingIndex[side] + 1) % 9;
      if (state.outs >= 3) nextHalf();
      if (state.half === 'bottom' && state.inning >= 9 && state.score.home > state.score.away) state.completed = true;
      render();
    }}

    function inningText() {{ return (state.half === 'top' ? 'TOP' : 'BOT') + ' ' + state.inning; }}

    function runHalf() {{ const startHalf = state.half, startInning = state.inning; while(!state.completed && state.half === startHalf && state.inning === startInning) simulatePA(); }}
    function runGame() {{ let guard = 0; while(!state.completed && guard++ < 110) simulatePA(); }}

    function renderStrip() {{
      const date = SIM_DATA.target_date.slice(5).replace('-', '<br>');
      const tabs = SIM_DATA.games.map((g,i)=>'<button class="game-tab ' + (i===state.gameIndex?'active':'') + '" onclick="loadGame(' + i + ')"><div class="tab-row"><strong>' + (g.game_time_tw || '未公布') + '</strong><span>' + (i===state.gameIndex?'Viewing':'SIM') + '</span></div><div class="tab-row"><strong>' + g.away + '</strong><span>' + Math.round(g.confidence*100) + '%</span></div><div class="tab-row"><strong>' + g.home + '</strong><span>' + (g.status || '') + '</span></div></button>').join('');
      document.getElementById('gameStrip').innerHTML = '<div class="date-box">JUN<br>' + (date.split('<br>')[1] || '') + '</div>' + tabs;
    }}

    function renderLineScore() {{
      const innings = Array.from({{length:9}},(_,i)=>i+1);
      const row = side => '<tr><td><strong>' + sideName(side) + '</strong></td>' + innings.map((_,i)=>'<td>' + (state.lines[side][i]||0) + '</td>').join('') + '<td><strong>' + state.score[side] + '</strong></td><td>' + state.hits[side] + '</td><td>' + state.errors[side] + '</td></tr>';
      document.getElementById('lineScore').innerHTML = '<table><thead><tr><th></th>' + innings.map(i=>'<th>' + i + '</th>').join('') + '<th>R</th><th>H</th><th>E</th></tr></thead><tbody>' + row('away') + row('home') + '</tbody></table>';
    }}

    function renderBox(side) {{
      const title = side === 'away' ? 'Batters - Away' : 'Batters - Home';
      const rows = state.box[side].map(p=>'<tr><td><strong>' + p.name + '</strong> <small>' + p.pos + '</small></td><td>' + p.AB + '</td><td>' + p.R + '</td><td>' + p.H + '</td><td>' + p.RBI + '</td><td>' + p.BB + '</td><td>' + p.K + '</td><td>' + p.HR + '</td></tr>').join('');
      return '<h3>' + title + '</h3><table><thead><tr><th>打者</th><th>AB</th><th>R</th><th>H</th><th>RBI</th><th>BB</th><th>K</th><th>HR</th></tr></thead><tbody>' + rows + '</tbody></table>';
    }}

    function renderDueUp() {{
      const side = battingSide();
      const cards = [0,1,2].map(offset => {{
        const p = state.box[side][(state.battingIndex[side]+offset)%9];
        return '<div class="player-card"><div class="avatar">' + p.pos + '</div><div><strong>' + p.name + '</strong><br><small>' + p.AB + '-' + p.H + ' / BB ' + p.BB + ' / K ' + p.K + '</small></div></div>';
      }}).join('');
      document.getElementById('dueUp').innerHTML = cards;
    }}

    function render() {{
      const g=currentGame();
      renderStrip();
      document.getElementById('awayName').textContent = g.away;
      document.getElementById('homeName').textContent = g.home;
      document.getElementById('awayPitcher').textContent = g.away_pitcher;
      document.getElementById('homePitcher').textContent = g.home_pitcher;
      document.getElementById('awayScore').textContent = state.score.away;
      document.getElementById('homeScore').textContent = state.score.home;
      document.getElementById('inningLabel').textContent = state.completed ? 'FINAL' : inningText();
      document.getElementById('parkBoard').textContent = g.home;
      document.getElementById('venueName').textContent = g.home + ' 主場模擬';
      document.getElementById('baseStateText').textContent = state.outs + ' 出局';
      document.getElementById('base1').classList.toggle('on', !!state.bases[0]);
      document.getElementById('base2').classList.toggle('on', !!state.bases[1]);
      document.getElementById('base3').classList.toggle('on', !!state.bases[2]);
      const sourceText = g.lineup_source === 'official_mlb_boxscore' ? '官方先發打線' : (g.lineup_source === 'projected_recent_lineup_order' ? '先發未公布，使用近期固定打序預估' : (g.lineup_source === 'projected_roster_stats_lineup' ? '先發未公布，使用球員本季打擊資料預估打線' : '先發未公布，使用角色打線'));
      document.getElementById('caption').textContent = (state.events[0] && state.events[0].desc) || ('預測勝方：' + g.prediction + '，信心 ' + (Math.round(g.confidence*1000)/10) + '% / ' + sourceText);
      document.getElementById('eventLog').innerHTML = state.events.map((e,i)=>'<div class="event"><div class="event-num">' + (state.events.length-i) + '</div><div><small>' + e.inning + ' · ' + e.pitch + ' · ' + e.score + ' · ' + e.outs + ' 出局</small><br>' + e.desc + '</div></div>').join('');
      renderLineScore(); renderDueUp();
      document.getElementById('awayBox').innerHTML = renderBox('away');
      document.getElementById('homeBox').innerHTML = renderBox('home');
    }}

    document.getElementById('nextPaBtn').onclick = simulatePA;
    document.getElementById('halfInningBtn').onclick = runHalf;
    document.getElementById('fullGameBtn').onclick = runGame;
    document.getElementById('resetBtn').onclick = () => loadGame(state.gameIndex);
    loadGame(0);
  </script>
</body>
</html>"""


def write_outputs(data: dict) -> None:
    json_path = Path(str(SIM_DATA_JSON).format(date=data["target_date"]))
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    SIM_HTML.write_text(render_html(data), encoding="utf-8")
    print(f"wrote {json_path}")
    print(f"wrote {SIM_HTML}")
    print(f"sim_games={len(data['games'])}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate daily MLB plate appearance simulator.")
    parser.add_argument("--date", default=date.today().isoformat())
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    write_outputs(build_sim_data(args.date))


if __name__ == "__main__":
    main()
