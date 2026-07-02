#!/usr/bin/env python3
"""Generate a Taiwan Sports Lottery market research page.

Only official Taiwan Sports Lottery markets are allowed into betting candidates.
Markets that are captured but do not have a tested model are listed as
observation-only so they can be studied without being recommended.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from fetch_taiwan_sportslottery_markets import MARKETS_CSV, build_report as build_market_file
from schedule_time import attach_game_time, load_time_index, time_sort_key


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"

DAILY_PREDICTIONS_JSON = DATA_DIR / "daily_predictions_{date}.json"
TOTALS_JSON = DATA_DIR / "totals_predictions_{date}.json"
MONTE_CARLO_JSON = DATA_DIR / "monte_carlo_{date}.json"
RESEARCH_JSON = DATA_DIR / "market_research_{date}.json"
RESEARCH_HTML = DOCS_DIR / "market_research.html"

SPORTSBOOK = "台灣運彩"
MIN_MONEYLINE_PROB = 0.56
MIN_TOTALS_PROB = 0.58
MIN_EDGE = 0.03


def load_json(path: Path, fallback: dict) -> dict:
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def load_market_rows(target_date: str) -> list[dict]:
    path = Path(str(MARKETS_CSV).format(date=target_date))
    if not path.exists():
        build_market_file(target_date, refresh=False)
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def as_float(value: object) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.1f}%"


def implied_prob(odds: float | None) -> float | None:
    if not odds or odds <= 0:
        return None
    return 1 / odds


def market_key(row: dict) -> tuple[str, str]:
    return (str(row.get("game_pk", "")), str(row.get("market_ti", "")))


def index_markets(rows: list[dict]) -> dict[tuple[str, str], list[dict]]:
    indexed: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        indexed[market_key(row)].append(row)
    return indexed


def team_selection(rows: list[dict], side: str) -> dict | None:
    for row in rows:
        if row.get("selection_side") == side and row.get("odds_decimal"):
            return row
    return None


def market_overview(rows: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row.get("market_ti", ""), row.get("market_name", ""))
        item = grouped.setdefault(
            key,
            {
                "market_ti": key[0],
                "market_name": key[1],
                "games": set(),
                "selections": 0,
                "model_status": model_status(key[0], key[1]),
            },
        )
        item["games"].add(row.get("official_event_id", "") or row.get("game_pk", ""))
        item["selections"] += 1
    overview = []
    for item in grouped.values():
        overview.append({**item, "games": len(item["games"])})
    overview.sort(key=lambda item: (item["model_status"] != "已啟用", -item["games"], item["market_ti"], item["market_name"]))
    return overview


def model_status(market_ti: str, market_name: str) -> str:
    if market_ti in {"354", "360"}:
        return "已啟用"
    if "讓分" in market_name or "大小" in market_name or "總分" in market_name:
        return "可研究"
    return "觀察中"


def build_moneyline_candidates(daily_rows: list[dict], monte_by_game: dict[str, dict], markets: dict[tuple[str, str], list[dict]]) -> list[dict]:
    candidates = []
    for row in daily_rows:
        game_pk = str(row.get("game_pk", ""))
        market_rows = markets.get((game_pk, "354"), [])
        if not market_rows:
            continue
        pick_side = row.get("pick_side")
        selection = team_selection(market_rows, "A" if pick_side == "away" else "H")
        if not selection:
            continue
        odds = as_float(selection.get("odds_decimal"))
        market_prob = implied_prob(odds)
        model_prob = as_float(row.get("confidence"))
        mc = monte_by_game.get(game_pk, {})
        if pick_side == "away":
            mc_prob = as_float(mc.get("away_win_prob"))
        else:
            mc_prob = as_float(mc.get("home_win_prob"))
        if mc_prob is not None:
            model_prob = max(model_prob or 0, mc_prob)
        edge = (model_prob - market_prob) if model_prob is not None and market_prob is not None else None
        if model_prob is None or edge is None:
            continue
        decision = "候選" if model_prob >= MIN_MONEYLINE_PROB and edge >= MIN_EDGE else "觀察"
        candidates.append(
            attach_game_time(
                {
                "type": "不讓分",
                "date": row.get("date", ""),
                "game_pk": game_pk,
                "matchup_zh": row.get("matchup_zh", ""),
                "pick": row.get("prediction_zh", selection.get("selection_name", "")),
                "line": "",
                "odds": odds,
                "model_prob": round(model_prob, 4),
                "market_prob": round(market_prob, 4),
                "edge": round(edge, 4),
                "decision": decision,
                "reason": "勝方模型/蒙地卡羅高於台灣運彩隱含機率",
                },
                {game_pk: {"game_time_tw": row.get("game_time_tw", ""), "game_time_utc": row.get("game_time_utc", "")}},
            )
        )
    return candidates


def build_totals_candidates(totals_rows: list[dict], markets: dict[tuple[str, str], list[dict]]) -> list[dict]:
    candidates = []
    for row in totals_rows:
        game_pk = str(row.get("game_pk", ""))
        if not markets.get((game_pk, "360")):
            continue
        odds = as_float(row.get("odds"))
        model_prob = as_float(row.get("model_prob"))
        market_prob = as_float(row.get("market_implied_prob")) or implied_prob(odds)
        edge = as_float(row.get("edge"))
        if edge is None and model_prob is not None and market_prob is not None:
            edge = model_prob - market_prob
        if model_prob is None or edge is None:
            continue
        decision = "候選" if model_prob >= MIN_TOTALS_PROB and edge >= MIN_EDGE else "觀察"
        candidates.append(
            {
                "type": "全場大小",
                "date": row.get("date", ""),
                "game_pk": game_pk,
                "game_time_tw": row.get("game_time_tw", ""),
                "game_time_utc": row.get("game_time_utc", ""),
                "matchup_zh": row.get("matchup_zh", ""),
                "pick": row.get("pick", ""),
                "line": row.get("line", ""),
                "odds": odds,
                "model_prob": round(model_prob, 4),
                "market_prob": round(market_prob, 4) if market_prob is not None else None,
                "edge": round(edge, 4),
                "decision": decision,
                "reason": "大小分模型高於台灣運彩隱含機率",
            }
        )
    return candidates


def build_research(target_date: str) -> dict:
    market_rows = load_market_rows(target_date)
    time_index = load_time_index(target_date)
    daily = load_json(Path(str(DAILY_PREDICTIONS_JSON).format(date=target_date)), {"all_predictions": []})
    totals = load_json(Path(str(TOTALS_JSON).format(date=target_date)), {"all_predictions": []})
    monte = load_json(Path(str(MONTE_CARLO_JSON).format(date=target_date)), {"games": []})
    markets = index_markets(market_rows)
    monte_by_game = {str(row.get("game_pk", "")): row for row in monte.get("games", [])}

    candidates = []
    candidates.extend(build_moneyline_candidates(daily.get("all_predictions", []), monte_by_game, markets))
    candidates.extend(build_totals_candidates(totals.get("all_predictions", []), markets))
    for row in candidates:
        attach_game_time(row, time_index)
    candidates.sort(key=lambda row: (row["decision"] != "候選", time_sort_key(row), -row["edge"], -row["model_prob"]))

    overview = market_overview(market_rows)
    active_candidates = [row for row in candidates if row["decision"] == "候選"]
    matched_events = {
        row.get("official_event_id") or f"{row.get('game_pk')}:{row.get('matchup_zh')}"
        for row in market_rows
        if row.get("game_pk")
    }
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "target_date": target_date,
        "sportsbook": SPORTSBOOK,
        "rules": {
            "require_official_market": True,
            "moneyline_min_model_prob": MIN_MONEYLINE_PROB,
            "totals_min_model_prob": MIN_TOTALS_PROB,
            "min_edge": MIN_EDGE,
            "no_market_no_recommendation": True,
        },
        "summary": {
            "official_selections": len(market_rows),
            "market_types": len(overview),
            "candidate_count": len(active_candidates),
            "watch_count": len(candidates) - len(active_candidates),
            "official_events_matched": len(matched_events),
            "official_game_pks_matched": len({row.get("game_pk") for row in market_rows if row.get("game_pk")}),
        },
        "candidates": active_candidates,
        "watchlist": [row for row in candidates if row["decision"] != "候選"],
        "market_overview": overview,
    }


def write_outputs(report: dict) -> None:
    json_path = Path(str(RESEARCH_JSON).format(date=report["target_date"]))
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    RESEARCH_HTML.write_text(render_html(report), encoding="utf-8")
    print(f"wrote {json_path}")
    print(f"wrote {RESEARCH_HTML}")
    print(
        f"market_types={report['summary']['market_types']} "
        f"candidates={report['summary']['candidate_count']} watch={report['summary']['watch_count']}"
    )


def rows_html(rows: list[dict], empty: str) -> str:
    if not rows:
        return f'<tr><td colspan="11">{html.escape(empty)}</td></tr>'
    return "\n".join(
        f"""
        <tr>
          <td>{html.escape(row.get('type', ''))}</td>
          <td>{html.escape(str(row.get('game_time_tw', '') or '未公布'))}</td>
          <td>{html.escape(row.get('matchup_zh', ''))}</td>
          <td>{html.escape(str(row.get('pick', '')))}</td>
          <td>{html.escape(str(row.get('line', '') or '-'))}</td>
          <td>{html.escape(str(row.get('odds', '') or '-'))}</td>
          <td>{pct(row.get('model_prob'))}</td>
          <td>{pct(row.get('market_prob'))}</td>
          <td>{pct(row.get('edge'))}</td>
          <td>{html.escape(row.get('decision', ''))}</td>
          <td>{html.escape(row.get('reason', ''))}</td>
        </tr>"""
        for row in rows
    )


def overview_html(rows: list[dict]) -> str:
    if not rows:
        return '<tr><td colspan="5">尚未抓到台灣運彩盤口</td></tr>'
    return "\n".join(
        f"""
        <tr>
          <td>{html.escape(row.get('market_ti', ''))}</td>
          <td>{html.escape(row.get('market_name', ''))}</td>
          <td>{row.get('games', '')}</td>
          <td>{row.get('selections', '')}</td>
          <td>{html.escape(row.get('model_status', ''))}</td>
        </tr>"""
        for row in rows
    )


def render_html(report: dict) -> str:
    candidate_rows = rows_html(report["candidates"], "目前沒有符合保守門檻的台灣運彩候選。")
    watch_rows = rows_html(report["watchlist"], "目前沒有觀察項目。")
    overview_rows = overview_html(report["market_overview"])
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>MLB 台灣運彩盤口研究</title>
  <style>
    :root {{ --bg:#f5f7f4; --surface:#fff; --ink:#18211d; --muted:#67736d; --line:#dfe6df; --accent:#155f56; --good:#0f6848; --warn:#9a5a12; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--ink); font-family:"Microsoft JhengHei","Noto Sans TC",system-ui,sans-serif; letter-spacing:0; }}
    main {{ max-width:1240px; margin:0 auto; padding:28px; }}
    h1 {{ margin:0 0 8px; font-size:30px; }}
    h2 {{ margin:28px 0 12px; font-size:20px; }}
    p {{ color:var(--muted); line-height:1.65; margin:0; }}
    .cards {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin:18px 0; }}
    .card {{ background:var(--surface); border:1px solid var(--line); border-radius:8px; padding:15px; }}
    .card span {{ display:block; color:var(--muted); font-size:13px; font-weight:800; }}
    .card strong {{ display:block; margin-top:4px; font-size:28px; }}
    .note {{ background:#fff8ea; border:1px solid #edd4a2; border-radius:8px; padding:12px 14px; color:#61400d; margin:12px 0 18px; line-height:1.65; }}
    table {{ width:100%; border-collapse:collapse; background:var(--surface); border:1px solid var(--line); border-radius:8px; overflow:hidden; }}
    th,td {{ border-bottom:1px solid var(--line); padding:11px 10px; text-align:left; vertical-align:top; font-size:14px; white-space:nowrap; }}
    th {{ color:var(--muted); font-size:12px; }}
    td:nth-child(2),td:nth-child(10) {{ white-space:normal; min-width:180px; }}
    .rules {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:12px; }}
    .rules span {{ border:1px solid var(--line); border-radius:999px; padding:7px 10px; background:#fff; color:#33433c; font-size:13px; font-weight:800; }}
    @media (max-width:900px) {{ main {{ padding:18px; }} .cards {{ grid-template-columns:1fr 1fr; }} table {{ display:block; overflow-x:auto; }} }}
    @media (max-width:560px) {{ .cards {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <main>
    <h1>MLB 台灣運彩盤口研究</h1>
    <p>目標日期：{html.escape(report['target_date'])} / 盤口來源：{html.escape(report['sportsbook'])} / 產生時間：{html.escape(report['generated_at'])}</p>
    <div class="rules">
      <span>沒有台灣運彩盤口就不推薦</span>
      <span>獨贏門檻 {pct(report['rules']['moneyline_min_model_prob'])}</span>
      <span>大小分門檻 {pct(report['rules']['totals_min_model_prob'])}</span>
      <span>最低 Edge {pct(report['rules']['min_edge'])}</span>
    </div>
    <section class="cards">
      <div class="card"><span>官方選項數</span><strong>{report['summary']['official_selections']}</strong></div>
      <div class="card"><span>玩法種類</span><strong>{report['summary']['market_types']}</strong></div>
      <div class="card"><span>推薦候選</span><strong>{report['summary']['candidate_count']}</strong></div>
      <div class="card"><span>已對上官方事件</span><strong>{report['summary']['official_events_matched']}</strong></div>
    </section>
    <div class="note">這頁只做盤口研究和候選排序。全玩法會完整抓取，但目前只讓已接模型的「不讓分」和「全場大小」進入候選；其他玩法先列為可研究或觀察中。</div>

    <h2>比較有把握的候選</h2>
    <table>
      <thead><tr><th>玩法</th><th>台灣時間</th><th>對戰</th><th>選項</th><th>盤口</th><th>賠率</th><th>模型機率</th><th>盤口隱含</th><th>Edge</th><th>狀態</th><th>理由</th></tr></thead>
      <tbody>{candidate_rows}</tbody>
    </table>

    <h2>觀察項目</h2>
    <table>
      <thead><tr><th>玩法</th><th>台灣時間</th><th>對戰</th><th>選項</th><th>盤口</th><th>賠率</th><th>模型機率</th><th>盤口隱含</th><th>Edge</th><th>狀態</th><th>理由</th></tr></thead>
      <tbody>{watch_rows}</tbody>
    </table>

    <h2>全玩法盤口總覽</h2>
    <table>
      <thead><tr><th>TI</th><th>玩法</th><th>場次</th><th>選項數</th><th>模型狀態</th></tr></thead>
      <tbody>{overview_rows}</tbody>
    </table>
  </main>
</body>
</html>"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Taiwan Sports Lottery market research page.")
    parser.add_argument("--date", default=date.today().isoformat())
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    write_outputs(build_research(args.date))


if __name__ == "__main__":
    main()
