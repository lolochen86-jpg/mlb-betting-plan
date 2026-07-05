#!/usr/bin/env python3
"""Generate a clear daily betting ticket page from ROI candidates."""

from __future__ import annotations

import argparse
import csv
import html
import json
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"

ROI_JSON = DATA_DIR / "betting_roi_{date}.json"
TICKET_CSV = DATA_DIR / "betting_ticket_{date}.csv"
TICKET_HTML = DOCS_DIR / "betting_ticket.html"

OFFICIAL_MIN_CONFIDENCE = 0.55
OFFICIAL_MIN_EDGE = 0.02


def is_official_bet(row: dict) -> bool:
    try:
        confidence = float(row.get("confidence") or 0)
        edge = float(row.get("edge") or 0)
    except Exception:
        return False
    return (
        confidence >= OFFICIAL_MIN_CONFIDENCE
        and edge >= OFFICIAL_MIN_EDGE
        and bool(row.get("confirmation_same_direction", True))
    )


def classify_ticket(report: dict) -> tuple[list[dict], list[dict], list[dict]]:
    bets = []
    watchlist = []
    no_market = []
    for row in report.get("bets", []):
        if is_official_bet(row):
            row["ticket_tier"] = "正式下注"
            bets.append(row)
        else:
            row["ticket_tier"] = "觀察"
            watchlist.append(row)
    for row in report.get("skipped", []):
        reason = str(row.get("skip_reason", ""))
        if "找不到真實盤口" in reason or "盤口" in reason:
            row["ticket_tier"] = "無台灣運彩盤口"
            no_market.append(row)
        elif row.get("sportsbook") == "台灣運彩" or row.get("moneyline"):
            row["ticket_tier"] = "觀察"
            watchlist.append(row)
    watchlist.sort(key=lambda row: (float(row.get("edge") or -99), float(row.get("confidence") or 0)), reverse=True)
    no_market.sort(key=lambda row: (row.get("game_time_utc", ""), row.get("game_pk", "")))
    return bets, watchlist, no_market[:12]


def load_ticket(target_date: str) -> dict:
    path = Path(str(ROI_JSON).format(date=target_date))
    if not path.exists():
        raise SystemExit(f"Missing ROI file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv(target_date: str, rows: list[dict]) -> Path:
    path = Path(str(TICKET_CSV).format(date=target_date))
    fields = [
        "date",
        "game_pk",
        "game_time_tw",
        "game_time_utc",
        "sportsbook",
        "captured_at_tw",
        "matchup_zh",
        "prediction_zh",
        "moneyline",
        "confidence",
        "market_implied_prob",
        "edge",
        "unit",
        "status",
        "settlement",
        "ticket_tier",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def render_html(report: dict) -> str:
    official_rows, watch_rows, no_market_rows = classify_ticket(report)
    summary = report.get("summary", {})
    official_body = render_bet_rows(official_rows)
    watch_body = render_bet_rows(watch_rows, include_reason=True)
    no_market_body = render_no_market_rows(no_market_rows)
    if not official_rows:
        official_body = '<tr><td colspan="12">目前沒有通過正式下注門檻的場次。</td></tr>'
    if not watch_rows:
        watch_body = '<tr><td colspan="12">目前沒有觀察名單。</td></tr>'
    if not no_market_rows:
        no_market_body = '<tr><td colspan="5">目前沒有缺台灣運彩盤口的顯示項目。</td></tr>'
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>MLB 今日投注單</title>
  <style>
    body {{ margin: 0; background: #f7f8f6; color: #202421; font-family: "Microsoft JhengHei", "Noto Sans TC", system-ui, sans-serif; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 28px; }}
    h1 {{ margin: 0 0 6px; font-size: 30px; }}
    h2 {{ margin: 24px 0 10px; font-size: 18px; }}
    .meta {{ color: #68736d; line-height: 1.6; font-size: 14px; margin-bottom: 18px; }}
    .kpis {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin: 16px 0 18px; }}
    .kpi {{ background: white; border: 1px solid #dfe5df; border-radius: 8px; padding: 14px; }}
    .kpi strong {{ display: block; font-size: 26px; }}
    .kpi span {{ color: #68736d; font-size: 13px; }}
    table {{ width: 100%; border-collapse: collapse; background: white; border: 1px solid #dfe5df; border-radius: 8px; overflow: hidden; }}
    th, td {{ text-align: left; border-bottom: 1px solid #dfe5df; padding: 12px 10px; white-space: nowrap; font-size: 14px; }}
    th {{ color: #68736d; font-size: 12px; }}
    .note {{ margin-top: 16px; padding: 12px 14px; border: 1px solid #dfe5df; border-radius: 8px; background: white; color: #68736d; line-height: 1.6; }}
    .badge {{ display: inline-flex; border-radius: 999px; padding: 4px 9px; font-size: 12px; font-weight: 900; background: #e8f3ee; color: #176454; }}
    .badge.watch {{ background: #fff2d6; color: #7a4b12; }}
    @media (max-width: 720px) {{ main {{ padding: 18px; }} .kpis {{ grid-template-columns: 1fr; }} table {{ display: block; overflow-x: auto; }} }}
  </style>
</head>
<body>
  <main>
    <h1>MLB 今日投注單</h1>
    <div class="meta">
      MLB日期：{html.escape(report.get('target_date', ''))}<br />
      真實盤口列數：{summary.get('odds_rows', 0)} / 原始候選：{summary.get('bets', 0)} / 待結算：{summary.get('pending_bets', 0)}<br />
      正式下注門檻：信心 >= {OFFICIAL_MIN_CONFIDENCE * 100:.0f}%、Edge >= {OFFICIAL_MIN_EDGE * 100:.0f}%、確認模型同方向、必須有台灣運彩盤口<br />
      來源：{html.escape(report.get('source_files', {}).get('odds', ''))}
    </div>
    <section class="kpis">
      <div class="kpi"><strong>{len(official_rows)}</strong><span>正式下注</span></div>
      <div class="kpi"><strong>{len(watch_rows)}</strong><span>觀察名單</span></div>
      <div class="kpi"><strong>{len(no_market_rows)}</strong><span>缺台灣運彩盤口</span></div>
    </section>
    <h2>正式下注</h2>
    <table>
      <thead><tr><th>層級</th><th>GamePk</th><th>台灣開賽時間</th><th>對戰</th><th>投注隊伍</th><th>盤口來源</th><th>賠率</th><th>模型信心</th><th>市場隱含</th><th>Edge</th><th>單位</th><th>狀態</th></tr></thead>
      <tbody>{official_body}</tbody>
    </table>
    <h2>觀察名單</h2>
    <table>
      <thead><tr><th>層級</th><th>GamePk</th><th>台灣開賽時間</th><th>對戰</th><th>投注隊伍</th><th>盤口來源</th><th>賠率</th><th>模型信心</th><th>市場隱含</th><th>Edge</th><th>單位</th><th>原因</th></tr></thead>
      <tbody>{watch_body}</tbody>
    </table>
    <h2>無台灣運彩盤口，不推薦</h2>
    <table>
      <thead><tr><th>GamePk</th><th>台灣開賽時間</th><th>對戰</th><th>模型預測</th><th>原因</th></tr></thead>
      <tbody>{no_market_body}</tbody>
    </table>
    <div class="note">正式下注只採用台灣運彩官方盤口。觀察名單不等於下注；缺台灣運彩盤口的比賽完全不推薦。</div>
  </main>
</body>
</html>"""


def render_bet_rows(rows: list[dict], include_reason: bool = False) -> str:
    return "\n".join(
        f"""
        <tr>
          <td><span class="badge {'watch' if row.get('ticket_tier') == '觀察' else ''}">{html.escape(str(row.get('ticket_tier', '正式下注')))}</span></td>
          <td>{html.escape(str(row.get('game_pk', '')))}</td>
          <td>{html.escape(str(row.get('game_time_tw', '') or '未公布'))}</td>
          <td>{html.escape(str(row.get('matchup_zh', '')))}</td>
          <td>{html.escape(str(row.get('prediction_zh', '')))}</td>
          <td>{html.escape(str(row.get('sportsbook', '')))}</td>
          <td>{row.get('moneyline', '')}</td>
          <td>{float(row.get('confidence') or 0) * 100:.1f}%</td>
          <td>{float(row.get('market_implied_prob') or 0) * 100:.1f}%</td>
          <td>{float(row.get('edge') or 0) * 100:.1f}%</td>
          <td>{float(row.get('unit') or 0):.0f}</td>
          <td>{html.escape(str(row.get('skip_reason') or ('待結算' if row.get('settlement') == 'pending' else row.get('settlement', ''))))}</td>
        </tr>"""
        for row in rows
    )


def render_no_market_rows(rows: list[dict]) -> str:
    return "\n".join(
        f"""
        <tr>
          <td>{html.escape(str(row.get('game_pk', '')))}</td>
          <td>{html.escape(str(row.get('game_time_tw', '') or '未公布'))}</td>
          <td>{html.escape(str(row.get('matchup_zh', '')))}</td>
          <td>{html.escape(str(row.get('prediction_zh', '')))} / {float(row.get('confidence') or 0) * 100:.1f}%</td>
          <td>{html.escape(str(row.get('skip_reason', '找不到真實盤口')))}</td>
        </tr>"""
        for row in rows
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate daily betting ticket from ROI candidates.")
    parser.add_argument("--date", default=date.today().isoformat())
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = load_ticket(args.date)
    official_rows, watch_rows, _ = classify_ticket(report)
    csv_path = write_csv(args.date, official_rows + watch_rows)
    TICKET_HTML.write_text(render_html(report), encoding="utf-8")
    print(f"wrote {csv_path}")
    print(f"wrote {TICKET_HTML}")
    print(f"official={len(official_rows)} watch={len(watch_rows)}")


if __name__ == "__main__":
    main()
