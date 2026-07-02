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
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def render_html(report: dict) -> str:
    rows = report.get("bets", [])
    summary = report.get("summary", {})
    body = "\n".join(
        f"""
        <tr>
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
          <td>{'待結算' if row.get('settlement') == 'pending' else row.get('settlement', '')}</td>
        </tr>"""
        for row in rows
    )
    if not rows:
        body = '<tr><td colspan="11">目前沒有符合真實盤口與 edge 條件的投注單。</td></tr>'
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
    .meta {{ color: #68736d; line-height: 1.6; font-size: 14px; margin-bottom: 18px; }}
    table {{ width: 100%; border-collapse: collapse; background: white; border: 1px solid #dfe5df; border-radius: 8px; overflow: hidden; }}
    th, td {{ text-align: left; border-bottom: 1px solid #dfe5df; padding: 12px 10px; white-space: nowrap; font-size: 14px; }}
    th {{ color: #68736d; font-size: 12px; }}
    .note {{ margin-top: 16px; padding: 12px 14px; border: 1px solid #dfe5df; border-radius: 8px; background: white; color: #68736d; line-height: 1.6; }}
    @media (max-width: 720px) {{ main {{ padding: 18px; }} table {{ display: block; overflow-x: auto; }} }}
  </style>
</head>
<body>
  <main>
    <h1>MLB 今日投注單</h1>
    <div class="meta">
      日期：{html.escape(report.get('target_date', ''))}<br />
      真實盤口列數：{summary.get('odds_rows', 0)} / 投注單：{summary.get('bets', 0)} / 待結算：{summary.get('pending_bets', 0)}<br />
      推薦來源限制：{html.escape(report.get('settings', {}).get('require_sportsbook') or '不限')}<br />
      來源：{html.escape(report.get('source_files', {}).get('odds', ''))}
    </div>
    <table>
      <thead><tr><th>GamePk</th><th>台灣時間</th><th>對戰</th><th>投注隊伍</th><th>盤口來源</th><th>賠率</th><th>模型信心</th><th>市場隱含</th><th>Edge</th><th>單位</th><th>狀態</th></tr></thead>
      <tbody>{body}</tbody>
    </table>
    <div class="note">此投注單只列入台灣運彩官方盤口且 edge 通過門檻的場次；沒有台灣運彩盤口就完全不推薦。</div>
  </main>
</body>
</html>"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate daily betting ticket from ROI candidates.")
    parser.add_argument("--date", default=date.today().isoformat())
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = load_ticket(args.date)
    csv_path = write_csv(args.date, report.get("bets", []))
    TICKET_HTML.write_text(render_html(report), encoding="utf-8")
    print(f"wrote {csv_path}")
    print(f"wrote {TICKET_HTML}")
    print(f"tickets={len(report.get('bets', []))}")


if __name__ == "__main__":
    main()
