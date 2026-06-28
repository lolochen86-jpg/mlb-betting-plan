#!/usr/bin/env python3
"""Generate an operational status report for the MLB betting plan."""

from __future__ import annotations

import csv
import html
import json
import re
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"

STATUS_JSON = DATA_DIR / "project_status.json"
STATUS_HTML = DOCS_DIR / "status.html"


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def latest_file(pattern: str) -> Path | None:
    files = sorted(DATA_DIR.glob(pattern))
    return files[-1] if files else None


def odds_status() -> list[dict]:
    rows = []
    game_status = {}
    for settlement_path in sorted(DATA_DIR.glob("prediction_settlement_*.json")):
        settlement = read_json(settlement_path)
        for row in settlement.get("settlements", []):
            game_status[str(row.get("game_pk", ""))] = row.get("status", "")
    for path in sorted((DATA_DIR / "odds").glob("mlb_moneyline_*.csv")):
        date_text = path.stem.replace("mlb_moneyline_", "")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_text):
            continue
        data = read_csv(path)
        filled = [row for row in data if row.get("away_moneyline") and row.get("home_moneyline")]
        missing = []
        for row in data:
            if row.get("away_moneyline") and row.get("home_moneyline"):
                continue
            matchup = f"{row.get('away_zh', '')} @ {row.get('home_zh', '')}"
            status = game_status.get(str(row.get("game_pk", "")), "")
            missing.append(f"{matchup} ({status or '未取得狀態'})")
        rows.append(
            {
                "date": date_text,
                "rows": len(data),
                "filled": len(filled),
                "missing": missing,
            }
        )
    return rows


def settlement_status() -> list[dict]:
    rows = []
    for path in sorted(DATA_DIR.glob("prediction_settlement_*.json")):
        data = read_json(path)
        summary = data.get("summary", {})
        rows.append(
            {
                "date": data.get("target_date", path.stem.replace("prediction_settlement_", "")),
                "predictions": summary.get("predictions", 0),
                "final_games": summary.get("final_games", 0),
                "pending_games": summary.get("pending_games", 0),
                "accuracy_pct": summary.get("accuracy_pct", 0),
            }
        )
    return rows


def roi_status() -> list[dict]:
    rows = []
    for path in sorted(DATA_DIR.glob("betting_roi_*.json")):
        data = read_json(path)
        summary = data.get("summary", {})
        rows.append(
            {
                "date": data.get("target_date", path.stem.replace("betting_roi_", "")),
                "bets": summary.get("bets", 0),
                "final_bets": summary.get("final_bets", 0),
                "pending_bets": summary.get("pending_bets", 0),
                "total_pnl": summary.get("total_pnl", 0),
                "roi_pct": summary.get("roi_pct", 0),
            }
        )
    return rows


def compute_completion(status: dict) -> int:
    points = 0
    checks = [
        bool(status["history"].get("games_written")),
        bool(status["accuracy"].get("best_model")),
        bool(status["daily"].get("predictions")),
        bool(status["settlements"]),
        bool(status["odds"]),
        bool(status["roi"]),
        Path(ROOT / "scripts" / "run_daily_workflow.py").exists(),
        Path(ROOT / "docs" / "index.html").exists(),
        Path(ROOT / "docs" / "betting_roi.html").exists(),
        any((ROOT / "data" / "odds").glob("espn_moneyline_source_*.json")),
    ]
    points = sum(1 for item in checks if item)
    return int(points / len(checks) * 100)


def external_waiting_items(status: dict) -> list[str]:
    items = []
    for row in status["settlements"]:
        if int(row.get("pending_games", 0)) > 0:
            items.append(f"{row['date']} 尚有 {row['pending_games']} 場比賽未結算")
    for row in status["odds"]:
        for missing in row.get("missing", []):
            if "Scheduled" in missing:
                items.append(f"{row['date']} {missing} 尚未開打或來源尚未開盤")
            elif "Final" not in missing:
                items.append(f"{row['date']} {missing} 尚未取得真實盤口")
    for row in status["roi"]:
        if int(row.get("pending_bets", 0)) > 0:
            items.append(f"{row['date']} 尚有 {row['pending_bets']} 注 ROI 待賽果結算")
    return items


def build_status() -> dict:
    provenance = read_json(DATA_DIR / "real_data_provenance.json")
    accuracy_rows = read_csv(DATA_DIR / "real_mlb_prediction_accuracy_summary.csv")
    daily_path = latest_file("daily_predictions_*.json")
    daily = read_json(daily_path) if daily_path else {}
    auto_runner = read_json(DATA_DIR / "auto_runner_status.json")
    status = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "history": {
            "games_written": provenance.get("games_written", 0),
            "actual_first_game_date": provenance.get("actual_first_game_date"),
            "actual_last_game_date": provenance.get("actual_last_game_date"),
        },
        "accuracy": {
            "best_model": accuracy_rows[0]["模型"] if accuracy_rows else "",
            "predictions": int(accuracy_rows[0]["預測場次"]) if accuracy_rows else 0,
            "correct": int(accuracy_rows[0]["正確"]) if accuracy_rows else 0,
            "accuracy_pct": float(accuracy_rows[0]["準確率%"]) if accuracy_rows else 0,
        },
        "daily": {
            "target_date": daily.get("target_date"),
            "predictions": len(daily.get("all_predictions", [])),
            "high_confidence": len(daily.get("high_confidence_predictions", [])),
        },
        "settlements": settlement_status(),
        "odds": odds_status(),
        "roi": roi_status(),
        "auto_runner": {
            "mode": auto_runner.get("mode", "not_started"),
            "target_date": auto_runner.get("target_date", ""),
            "last_started_at_tw": auto_runner.get("last_started_at_tw", ""),
            "last_finished_at_tw": auto_runner.get("last_finished_at_tw", ""),
            "last_return_code": auto_runner.get("last_return_code", ""),
            "log": auto_runner.get("log", ""),
        },
        "next_commands": [
            ".\\open_dashboard.cmd",
            ".\\start_auto_runner.cmd",
            ".\\install_windows_auto_runner.cmd",
            ".\\uninstall_windows_auto_runner.cmd",
            "python scripts\\run_daily_workflow.py --date YYYY-MM-DD --all-predictions --skip-backtest-refresh",
            "python scripts\\run_daily_workflow.py --date YYYY-MM-DD --all-predictions",
        ],
    }
    status["system_completion_pct"] = compute_completion(status)
    status["external_waiting_items"] = external_waiting_items(status)
    status["project_state"] = "系統完成，等待外部賽果/盤口" if status["external_waiting_items"] else "系統與資料皆完成"
    return status


def render_list(items: list[str]) -> str:
    if not items:
        return "<span>無</span>"
    return "<ul>" + "".join(f"<li>{html.escape(item)}</li>" for item in items) + "</ul>"


def render_html(status: dict) -> str:
    auto = status.get("auto_runner", {})
    odds_rows = "\n".join(
        f"""
        <tr>
          <td>{row['date']}</td>
          <td>{row['filled']} / {row['rows']}</td>
          <td>{render_list(row['missing'])}</td>
        </tr>"""
        for row in status["odds"]
    )
    settlement_rows = "\n".join(
        f"""
        <tr>
          <td>{row['date']}</td>
          <td>{row['predictions']}</td>
          <td>{row['final_games']}</td>
          <td>{row['pending_games']}</td>
          <td>{row['accuracy_pct']:.2f}%</td>
        </tr>"""
        for row in status["settlements"]
    )
    roi_rows = "\n".join(
        f"""
        <tr>
          <td>{row['date']}</td>
          <td>{row['bets']}</td>
          <td>{row['final_bets']}</td>
          <td>{row['pending_bets']}</td>
          <td>{float(row['total_pnl']):.2f}</td>
          <td>{float(row['roi_pct']):.2f}%</td>
        </tr>"""
        for row in status["roi"]
    )
    commands = "".join(f"<code>{html.escape(command)}</code>" for command in status["next_commands"])
    waiting = render_list(status.get("external_waiting_items", []))
    auto_mode = html.escape(str(auto.get("mode", "not_started")))
    auto_log = html.escape(str(auto.get("log", "")))
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>MLB 投注計畫狀態</title>
  <style>
    body {{ margin: 0; background: #f7f8f6; color: #202421; font-family: "Microsoft JhengHei", "Noto Sans TC", system-ui, sans-serif; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 28px; }}
    h1 {{ margin: 0 0 8px; font-size: 30px; }}
    h2 {{ margin: 24px 0 12px; font-size: 18px; }}
    .grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 18px 0; }}
    .card {{ background: white; border: 1px solid #dfe5df; border-radius: 8px; padding: 16px; }}
    .label {{ color: #68736d; font-size: 13px; margin-bottom: 8px; }}
    .value {{ font-size: 26px; font-weight: 800; }}
    table {{ width: 100%; border-collapse: collapse; background: white; border: 1px solid #dfe5df; border-radius: 8px; overflow: hidden; margin-bottom: 16px; }}
    th, td {{ text-align: left; border-bottom: 1px solid #dfe5df; padding: 12px 10px; vertical-align: top; font-size: 14px; }}
    th {{ color: #68736d; font-size: 12px; }}
    ul {{ margin: 0; padding-left: 18px; }}
    code {{ display: block; background: #fff; border: 1px solid #dfe5df; border-radius: 8px; padding: 10px 12px; margin-bottom: 8px; overflow-x: auto; }}
    @media (max-width: 800px) {{ main {{ padding: 18px; }} .grid {{ grid-template-columns: 1fr; }} table {{ display: block; overflow-x: auto; }} }}
  </style>
</head>
<body>
  <main>
    <h1>MLB 投注計畫狀態</h1>
    <div>產生時間：{html.escape(status['generated_at'])}</div>
    <section class="grid">
      <div class="card"><div class="label">系統完成度</div><div class="value">{status['system_completion_pct']}%</div></div>
      <div class="card"><div class="label">真實比分</div><div class="value">{status['history']['games_written']}</div></div>
      <div class="card"><div class="label">最佳準確率</div><div class="value">{status['accuracy']['accuracy_pct']:.2f}%</div></div>
      <div class="card"><div class="label">最新每日預測</div><div class="value">{status['daily']['predictions']}</div></div>
    </section>
    <h2>目前狀態</h2>
    <table><thead><tr><th>專案狀態</th><th>外部等待項目</th></tr></thead><tbody><tr><td>{html.escape(status['project_state'])}</td><td>{waiting}</td></tr></tbody></table>
    <h2>自動更新</h2>
    <table>
      <thead><tr><th>狀態</th><th>日期</th><th>開始時間</th><th>完成時間</th><th>返回碼</th><th>Log</th></tr></thead>
      <tbody><tr>
        <td>{auto_mode}</td>
        <td>{html.escape(str(auto.get('target_date', '')))}</td>
        <td>{html.escape(str(auto.get('last_started_at_tw', '')))}</td>
        <td>{html.escape(str(auto.get('last_finished_at_tw', '')))}</td>
        <td>{html.escape(str(auto.get('last_return_code', '')))}</td>
        <td>{auto_log}</td>
      </tr></tbody>
    </table>
    <h2>盤口完整度</h2>
    <table><thead><tr><th>日期</th><th>已填 / 場次</th><th>缺盤口</th></tr></thead><tbody>{odds_rows}</tbody></table>
    <h2>準確率結算</h2>
    <table><thead><tr><th>日期</th><th>預測</th><th>已完賽</th><th>待結算</th><th>準確率</th></tr></thead><tbody>{settlement_rows}</tbody></table>
    <h2>投注 ROI</h2>
    <table><thead><tr><th>日期</th><th>追蹤注數</th><th>已結算</th><th>待結算</th><th>PnL</th><th>ROI</th></tr></thead><tbody>{roi_rows}</tbody></table>
    <h2>下一步指令</h2>
    {commands}
  </main>
</body>
</html>"""


def main() -> None:
    DOCS_DIR.mkdir(exist_ok=True)
    DATA_DIR.mkdir(exist_ok=True)
    status = build_status()
    STATUS_JSON.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    STATUS_HTML.write_text(render_html(status), encoding="utf-8")
    print(f"wrote {STATUS_JSON}")
    print(f"wrote {STATUS_HTML}")
    print(f"system_completion={status['system_completion_pct']}%")


if __name__ == "__main__":
    main()
