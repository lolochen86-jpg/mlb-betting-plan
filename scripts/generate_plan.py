#!/usr/bin/env python3
"""Generate the MLB practical betting plan dashboard from backtest artifacts."""

from __future__ import annotations

import csv
import html
import json
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"

RAW_RESULTS = DATA_DIR / "mlb_backtest_results.raw.txt"
CLEAN_RESULTS = DATA_DIR / "mlb_backtest_results.json"
SUMMARY_CSV = DATA_DIR / "mlb_backtest_summary.csv"
REAL_RESULTS = DATA_DIR / "real_mlb_backtest_results.json"
REAL_SUMMARY_CSV = DATA_DIR / "real_mlb_backtest_summary.csv"
ACCURACY_RESULTS = DATA_DIR / "real_mlb_prediction_accuracy.json"
ACCURACY_SUMMARY_CSV = DATA_DIR / "real_mlb_prediction_accuracy_summary.csv"
PLAN_JSON = DOCS_DIR / "plan.json"
INDEX_HTML = DOCS_DIR / "index.html"


def load_json_from_raw(path: Path) -> dict:
    text = path.read_text(encoding="utf-8-sig")
    decoder = json.JSONDecoder()
    data, _ = decoder.raw_decode(text.lstrip())
    return data


def load_summary(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        row["總注"] = int(row["總注"])
        row["勝"] = int(row["勝"])
        row["敗"] = int(row["敗"])
        row["勝率%"] = float(row["勝率%"])
        row["總損益$"] = float(row["總損益$"])
        row["ROI%"] = float(row["ROI%"])
    return rows


def load_accuracy_summary(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        row["預測場次"] = int(row["預測場次"])
        row["正確"] = int(row["正確"])
        row["錯誤"] = int(row["錯誤"])
        row["準確率%"] = float(row["準確率%"])
    return rows


def cumulative(records: list[dict]) -> list[dict]:
    total = 0.0
    points = []
    for record in records:
        total += float(record.get("pnl", 0))
        points.append(
            {
                "date": record["date"],
                "bets": int(record.get("bets", 0)),
                "wins": int(record.get("wins", 0)),
                "losses": int(record.get("losses", 0)),
                "pnl": round(float(record.get("pnl", 0)), 2),
                "cumulative_pnl": round(total, 2),
            }
        )
    return points


def max_drawdown(points: list[dict]) -> float:
    peak = 0.0
    worst = 0.0
    for point in points:
        value = float(point["cumulative_pnl"])
        peak = max(peak, value)
        worst = min(worst, value - peak)
    return round(worst, 2)


def recent_bets(records: list[dict], limit: int = 12) -> list[dict]:
    bets = []
    for record in records:
        for bet in record.get("bet_details", []):
            bets.append(
                {
                    "date": record["date"],
                    "matchup_zh": f"{bet.get('away_zh', bet.get('away', ''))} @ {bet.get('home_zh', bet.get('home', ''))}",
                    "pitchers_zh": " / ".join(
                        part
                        for part in [
                            bet.get("away_probable_pitcher_zh", ""),
                            bet.get("home_probable_pitcher_zh", ""),
                        ]
                        if part
                    ),
                    "pick_zh": bet.get("pick_zh", bet.get("pick", "")),
                    "score": f"{bet.get('away_score', '')}-{bet.get('home_score', '')}",
                    "confidence": bet.get("confidence", 0),
                    "edge": bet.get("edge", 0),
                    "result": bet.get("result", ""),
                }
            )
    return bets[-limit:][::-1]


def svg_line(points: list[dict], width: int = 860, height: int = 260) -> str:
    if not points:
        return ""
    values = [float(p["cumulative_pnl"]) for p in points]
    lo, hi = min(values), max(values)
    span = hi - lo or 1.0
    step = width / max(len(values) - 1, 1)
    coords = []
    for idx, value in enumerate(values):
        x = idx * step
        y = height - ((value - lo) / span * (height - 24)) - 12
        coords.append(f"{x:.1f},{y:.1f}")
    zero_y = height - ((0 - lo) / span * (height - 24)) - 12 if lo <= 0 <= hi else height - 12
    return f"""
<svg viewBox="0 0 {width} {height}" class="chart" role="img" aria-label="累積損益走勢">
  <line x1="0" y1="{zero_y:.1f}" x2="{width}" y2="{zero_y:.1f}" class="chart-zero" />
  <polyline points="{' '.join(coords)}" class="chart-line" />
  <circle cx="{(len(values)-1)*step:.1f}" cy="{height - ((values[-1] - lo) / span * (height - 24)) - 12:.1f}" r="5" class="chart-dot" />
</svg>"""


def svg_bars(summary: list[dict], width: int = 860, height: int = 240) -> str:
    if not summary:
        return ""
    max_roi = max(row["ROI%"] for row in summary) or 1
    bar_gap = 18
    bar_width = (width - bar_gap * (len(summary) + 1)) / len(summary)
    bars = []
    for idx, row in enumerate(summary):
        h = row["ROI%"] / max_roi * (height - 66)
        x = bar_gap + idx * (bar_width + bar_gap)
        y = height - h - 34
        label = html.escape(row["模型"].split("-")[0])
        bars.append(
            f'<g><rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{h:.1f}" rx="6" class="bar" />'
            f'<text x="{x + bar_width / 2:.1f}" y="{height - 12}" text-anchor="middle" class="bar-label">{label}</text>'
            f'<text x="{x + bar_width / 2:.1f}" y="{y - 8:.1f}" text-anchor="middle" class="bar-value">{row["ROI%"]:.1f}%</text></g>'
        )
    return f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" aria-label="模型 ROI 比較">{"".join(bars)}</svg>'


def svg_accuracy_bars(summary: list[dict], width: int = 860, height: int = 240) -> str:
    if not summary:
        return ""
    lo = min(row["準確率%"] for row in summary)
    hi = max(row["準確率%"] for row in summary)
    span = hi - lo or 1
    bar_gap = 18
    bar_width = (width - bar_gap * (len(summary) + 1)) / len(summary)
    bars = []
    for idx, row in enumerate(summary):
        h = ((row["準確率%"] - lo) / span * 0.75 + 0.25) * (height - 66)
        x = bar_gap + idx * (bar_width + bar_gap)
        y = height - h - 34
        label = html.escape(row["模型"].split("-")[0])
        bars.append(
            f'<g><rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{h:.1f}" rx="6" class="bar" />'
            f'<text x="{x + bar_width / 2:.1f}" y="{height - 12}" text-anchor="middle" class="bar-label">{label}</text>'
            f'<text x="{x + bar_width / 2:.1f}" y="{y - 8:.1f}" text-anchor="middle" class="bar-value">{row["準確率%"]:.2f}%</text></g>'
        )
    return f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" aria-label="模型準確率比較">{"".join(bars)}</svg>'


def build_plan(data: dict, summary: list[dict], accuracy_data: dict | None, accuracy_summary: list[dict]) -> dict:
    roi_ranked = sorted(summary, key=lambda r: (r["ROI%"], r["勝率%"], r["總注"]), reverse=True)
    accuracy_ranked = sorted(accuracy_summary, key=lambda r: (r["準確率%"], r["預測場次"]), reverse=True)
    roi_by_model = {row["模型"]: row for row in roi_ranked}
    production_accuracy = accuracy_ranked[0] if accuracy_ranked else None
    production_key = production_accuracy["模型"] if production_accuracy else roi_ranked[0]["模型"]
    confirmation_key = "E-對照組(Ensemble)" if "E-對照組(Ensemble)" in roi_by_model else production_key
    production = roi_by_model.get(production_key, roi_ranked[0])
    records = data["models"].get(production_key, data["models"][roi_ranked[0]["模型"]])["daily_records"]
    points = cumulative(records)
    active_days = [r for r in records if int(r.get("bets", 0)) > 0]
    winning_days = [r for r in active_days if float(r.get("pnl", 0)) > 0]
    losing_days = [r for r in active_days if float(r.get("pnl", 0)) < 0]
    last_14 = active_days[-14:]
    last_14_pnl = round(sum(float(r.get("pnl", 0)) for r in last_14), 2)
    settings = data["settings"]
    bankroll = 10000
    unit = int(settings["unit"])
    daily_cap = int(settings["max_bets_per_day"]) * unit
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "period": data["backtest_period"],
        "settings": settings,
        "data_source": data.get(
            "data_source",
            {
                "type": "attached_backtest_result",
                "games_evaluated": None,
                "note": "Backtest result was loaded from the user-provided attachment.",
            },
        ),
        "bankroll_assumption": bankroll,
        "production_model": production_key,
        "confirmation_model": confirmation_key,
        "ranking": roi_ranked,
        "accuracy_ranking": accuracy_ranked,
        "accuracy_source": accuracy_data.get("data_source", {}) if accuracy_data else {},
        "accuracy_metrics": {
            "predictions": production_accuracy["預測場次"] if production_accuracy else 0,
            "correct": production_accuracy["正確"] if production_accuracy else 0,
            "wrong": production_accuracy["錯誤"] if production_accuracy else 0,
            "accuracy_pct": production_accuracy["準確率%"] if production_accuracy else 0,
        },
        "production_metrics": {
            "total_bets": production["總注"],
            "wins": production["勝"],
            "losses": production["敗"],
            "win_rate_pct": production["勝率%"],
            "total_pnl": production["總損益$"],
            "roi_pct": production["ROI%"],
            "active_days": len(active_days),
            "winning_days": len(winning_days),
            "losing_days": len(losing_days),
            "positive_day_rate_pct": round(len(winning_days) / len(active_days) * 100, 2) if active_days else 0,
            "max_drawdown": max_drawdown(points),
            "last_14_active_day_pnl": last_14_pnl,
            "last_record_date": records[-1]["date"] if records else None,
        },
        "recent_bets": recent_bets(records),
        "money_rules": [
            f"每注固定 {unit}，單日最多 {settings['max_bets_per_day']} 注，單日曝險上限 {daily_cap}。",
            f"只採用 {production_key} 作為主模型；{confirmation_key} 可作為方向確認，不另外加倍。",
            "任一日達到 -2U 即停止追加下注；連續三個活躍日虧損時，隔日降為 2 注上限。",
            "當主模型信心值低於 0.55 或沒有正優勢時，不為了湊場次下注。",
            "回測是模型篩選依據，不代表未來保證獲利；實戰必須記錄盤口、時間與實際成交賠率。",
        ],
        "daily_workflow": [
            "賽前取得當日賽程、先發投手、近期戰績與盤口。",
            "第一層只做勝方預測，先追蹤模型準確率與信心值。",
            "若確認模型與主模型方向相反，該場標記為一般預測，不進入高信心名單。",
            "等真實盤口與成交賠率補齊後，再啟用投注金額、PnL 與 ROI 計算。",
            "每日結束先更新預測正確率；盤口層完成後再更新投注績效。",
        ],
        "equity_curve": points,
    }


def render_html(plan: dict, line_chart: str, bar_chart: str) -> str:
    metrics = plan["production_metrics"]
    accuracy = plan["accuracy_metrics"]
    source = plan.get("data_source", {})
    accuracy_source = plan.get("accuracy_source", {})
    games_evaluated = source.get("games_evaluated")
    source_label = "MLB Stats API 真實賽果" if source.get("type") == "real_mlb_final_scores" else "附件回測結果"
    accuracy_rows = "\n".join(
        f"""
        <tr>
          <td>{html.escape(row['模型'])}</td>
          <td>{row['預測場次']}</td>
          <td>{row['正確']}</td>
          <td>{row['錯誤']}</td>
          <td class="number good">{row['準確率%']:.2f}%</td>
        </tr>"""
        for row in plan.get("accuracy_ranking", [])
    )
    roi_rows = "\n".join(
        f"""
        <tr>
          <td>{html.escape(row['模型'])}</td>
          <td>{row['總注']}</td>
          <td>{row['勝']}-{row['敗']}</td>
          <td>{row['勝率%']:.2f}%</td>
          <td class="number {'good' if row['總損益$'] >= 0 else 'bad'}">{row['總損益$']:.2f}</td>
          <td class="number good">{row['ROI%']:.2f}%</td>
        </tr>"""
        for row in plan["ranking"]
    )
    money_rules = "\n".join(f"<li>{html.escape(rule)}</li>" for rule in plan["money_rules"])
    workflow = "\n".join(f"<li>{html.escape(step)}</li>" for step in plan["daily_workflow"])
    recent_rows = "\n".join(
        f"""
        <tr>
          <td>{html.escape(row['date'])}</td>
          <td>{html.escape(row['matchup_zh'])}</td>
          <td>{html.escape(row['pitchers_zh'] or '未公布')}</td>
          <td>{html.escape(row['pick_zh'])}</td>
          <td>{html.escape(row['score'])}</td>
          <td>{float(row['confidence']) * 100:.1f}%</td>
          <td class="number {'good' if row['result'] == 'win' else 'bad'}">{'勝' if row['result'] == 'win' else '敗'}</td>
        </tr>"""
        for row in plan.get("recent_bets", [])
    )
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>MLB 實戰投注計畫</title>
  <style>
    :root {{
      --bg: #f7f8f6;
      --panel: #ffffff;
      --ink: #202421;
      --muted: #68736d;
      --line: #dfe5df;
      --teal: #177c72;
      --teal-2: #0f5f59;
      --amber: #bd7b14;
      --red: #b84335;
      --shadow: 0 10px 30px rgba(28, 38, 32, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      background: var(--bg);
      font-family: "Microsoft JhengHei", "Noto Sans TC", system-ui, sans-serif;
      letter-spacing: 0;
      overflow-x: hidden;
    }}
    .shell {{ display: grid; grid-template-columns: 232px minmax(0, 1fr); min-height: 100vh; }}
    aside {{ border-right: 1px solid var(--line); background: #ffffff; padding: 26px 18px; position: sticky; top: 0; height: 100vh; }}
    .brand {{ font-weight: 800; font-size: 20px; line-height: 1.25; margin-bottom: 28px; }}
    nav a {{ display: flex; align-items: center; gap: 10px; color: var(--muted); text-decoration: none; padding: 10px 12px; border-radius: 8px; font-size: 14px; margin-bottom: 4px; }}
    nav a.active, nav a:hover {{ background: #e8f2ef; color: var(--teal-2); }}
    .daily-link {{ display: inline-flex; margin-top: 12px; color: var(--teal-2); font-weight: 700; text-decoration: none; font-size: 14px; }}
    main {{ padding: 28px; max-width: 1360px; width: 100%; min-width: 0; }}
    .topbar {{ display: flex; justify-content: space-between; gap: 20px; align-items: flex-start; margin-bottom: 22px; }}
    h1 {{ font-size: 32px; line-height: 1.15; margin: 0 0 8px; }}
    .sub {{ color: var(--muted); margin: 0; font-size: 14px; line-height: 1.6; }}
    .sub, .rules li, .hint, .label {{ overflow-wrap: anywhere; }}
    .stamp {{ color: var(--muted); font-size: 13px; text-align: right; line-height: 1.5; }}
    .kpis {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; margin-bottom: 16px; }}
    .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; box-shadow: var(--shadow); }}
    .kpi {{ padding: 18px; min-height: 110px; }}
    .label {{ color: var(--muted); font-size: 13px; margin-bottom: 10px; }}
    .value {{ font-size: 30px; font-weight: 800; line-height: 1; }}
    .hint {{ color: var(--muted); font-size: 12px; margin-top: 12px; }}
    .grid {{ display: grid; grid-template-columns: minmax(0, 1.55fr) minmax(320px, 0.9fr); gap: 16px; margin-bottom: 16px; }}
    .section {{ padding: 20px; }}
    h2 {{ font-size: 18px; margin: 0 0 14px; }}
    .chart {{ width: 100%; max-width: 100%; height: auto; display: block; }}
    .chart-zero {{ stroke: #cfd8d0; stroke-width: 1; }}
    .chart-line {{ fill: none; stroke: var(--teal); stroke-width: 4; stroke-linecap: round; stroke-linejoin: round; }}
    .chart-dot, .bar {{ fill: var(--teal); }}
    .bar:nth-child(even) {{ fill: var(--amber); }}
    .bar-label {{ fill: var(--muted); font-size: 14px; font-weight: 700; }}
    .bar-value {{ fill: var(--ink); font-size: 13px; font-weight: 700; }}
    .rules {{ padding-left: 18px; margin: 0; color: var(--ink); line-height: 1.75; font-size: 14px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th, td {{ border-bottom: 1px solid var(--line); text-align: left; padding: 12px 10px; white-space: nowrap; }}
    th {{ color: var(--muted); font-size: 12px; font-weight: 700; }}
    .number {{ text-align: right; font-variant-numeric: tabular-nums; }}
    .good {{ color: var(--teal-2); }}
    .bad {{ color: var(--red); }}
    .two {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
    .mini {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 10px; }}
    .mini div {{ border-top: 1px solid var(--line); padding-top: 12px; }}
    .mini strong {{ display: block; font-size: 20px; margin-bottom: 4px; }}
    footer {{ color: var(--muted); font-size: 12px; line-height: 1.6; margin-top: 18px; }}
    @media (max-width: 960px) {{
      .shell {{ grid-template-columns: 1fr; }}
      aside {{ position: static; height: auto; border-right: 0; border-bottom: 1px solid var(--line); }}
      nav {{ display: flex; flex-wrap: wrap; gap: 4px; }}
      .topbar, .grid, .two {{ display: block; }}
      .stamp {{ text-align: left; margin-top: 12px; overflow-wrap: anywhere; }}
      .kpis {{ grid-template-columns: repeat(2, 1fr); }}
      .panel {{ margin-bottom: 14px; }}
      main {{ padding: 18px; }}
    }}
    @media (max-width: 560px) {{
      .kpis {{ grid-template-columns: 1fr; }}
      h1 {{ font-size: 26px; }}
      table {{ display: block; overflow-x: auto; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <aside>
      <div class="brand">MLB<br />實戰投注計畫</div>
      <nav aria-label="主選單">
        <a class="active" href="#overview">總覽</a>
        <a href="#models">模型績效</a>
        <a href="#rules">下注規則</a>
        <a href="#workflow">每日風控</a>
        <a href="prediction_accuracy.html">準確率</a>
        <a href="daily_predictions.html">今日預測</a>
        <a href="betting_ticket.html">投注單</a>
        <a href="totals_predictions.html">大小分</a>
        <a href="advanced_factors.html">進階因子</a>
        <a href="game_simulator.html">逐打席模擬</a>
        <a href="monte_carlo.html">蒙地卡羅</a>
        <a href="lineup_fix_comparison.html">修正前後</a>
        <a href="backtest_2026_report.html">2026 回測</a>
        <a href="winner_model_search.html">模型搜尋</a>
        <a href="prediction_log.html">結算紀錄</a>
        <a href="postgame_review.html">賽後檢討</a>
        <a href="betting_roi.html">投注 ROI</a>
        <a href="status.html">狀態</a>
      </nav>
    </aside>
    <main>
      <section class="topbar" id="overview">
        <div>
          <h1>MLB 實戰投注計畫</h1>
          <p class="sub">第一層只追蹤真實勝方預測準確率；盤口、下注金額與投注 ROI 等真實賠率資料補齊後再啟用。</p>
          <a class="daily-link" href="prediction_accuracy.html">先看真實預測準確率</a>
          <a class="daily-link" href="daily_predictions.html">開啟今日勝方預測</a>
          <a class="daily-link" href="prediction_log.html">查看實戰預測結算</a>
        </div>
        <div class="stamp">資料期間 {plan['period']['start']} 至 {plan['period']['end']}<br />真實賽果 {accuracy_source.get('games_evaluated', games_evaluated)} 場<br />產生時間 {plan['generated_at']}</div>
      </section>

      <section class="kpis">
        <div class="panel kpi"><div class="label">主模型</div><div class="value">{html.escape(plan['production_model'])}</div><div class="hint">{html.escape(plan['confirmation_model'])} 僅作方向確認</div></div>
        <div class="panel kpi"><div class="label">預測準確率</div><div class="value good">{accuracy['accuracy_pct']:.2f}%</div><div class="hint">{accuracy['correct']} 正確 / {accuracy['wrong']} 錯誤</div></div>
        <div class="panel kpi"><div class="label">預測場次</div><div class="value">{accuracy['predictions']}</div><div class="hint">只用真實完賽勝方驗證</div></div>
        <div class="panel kpi"><div class="label">固定 -110 參考</div><div class="value">{metrics['roi_pct']:.2f}%</div><div class="hint">暫不作實戰 ROI 結論</div></div>
      </section>

      <section class="grid">
        <div class="panel section">
          <h2>模型準確率比較</h2>
          {bar_chart}
        </div>
        <div class="panel section" id="rules">
          <h2>準確率階段規則</h2>
          <ol class="rules">{money_rules}</ol>
          <div class="mini">
            <div><strong>{plan['settings']['unit']}</strong><span class="label">每注單位</span></div>
            <div><strong>{plan['settings']['max_bets_per_day']}</strong><span class="label">每日上限</span></div>
            <div><strong>{plan['settings']['min_confidence']}</strong><span class="label">最低信心</span></div>
            <div><strong>{plan['settings']['odds']}</strong><span class="label">回測賠率</span></div>
          </div>
        </div>
      </section>

      <section class="grid" id="models">
        <div class="panel section">
          <h2>固定 -110 ROI 暫時參考</h2>
          {line_chart}
        </div>
        <div class="panel section">
          <h2>活躍日品質</h2>
          <div class="mini">
            <div><strong>{metrics['active_days']}</strong><span class="label">有下注天數</span></div>
            <div><strong>{metrics['positive_day_rate_pct']:.2f}%</strong><span class="label">獲利日比例</span></div>
            <div><strong>{metrics['total_bets']}</strong><span class="label">總注數</span></div>
            <div><strong>{metrics['last_record_date']}</strong><span class="label">最後紀錄日</span></div>
          </div>
        </div>
      </section>

      <section class="panel section">
        <h2>真實預測準確率排名</h2>
        <table>
          <thead><tr><th>模型</th><th>預測場次</th><th>正確</th><th>錯誤</th><th class="number">準確率</th></tr></thead>
          <tbody>{accuracy_rows}</tbody>
        </table>
      </section>

      <section class="panel section">
        <h2>固定賠率回測參考</h2>
        <table>
          <thead><tr><th>模型</th><th>總注</th><th>勝敗</th><th>勝率</th><th class="number">總損益</th><th class="number">ROI</th></tr></thead>
          <tbody>{roi_rows}</tbody>
        </table>
      </section>

      <section class="panel section">
        <h2>最近模型下注明細</h2>
        <table>
          <thead><tr><th>日期</th><th>對戰</th><th>先發投手</th><th>下注隊伍</th><th>比分</th><th>信心</th><th class="number">結果</th></tr></thead>
          <tbody>{recent_rows}</tbody>
        </table>
      </section>

      <section class="two" id="workflow">
        <div class="panel section">
          <h2>每日執行流程</h2>
          <ol class="rules">{workflow}</ol>
        </div>
        <div class="panel section">
          <h2>實戰注意事項</h2>
          <ul class="rules">
            <li>此專案是投注紀律與模型驗證工具，不是獲利保證。</li>
            <li>目前優先統計真實勝方預測準確率；投注 ROI 等盤口資料補齊後再啟用。</li>
            <li>若資料來源、盤口或先發投手變動，應先更新資料再產出每日預測。</li>
          </ul>
        </div>
      </section>

      <footer>資料來源：{html.escape(source.get('note', ''))} {html.escape(source.get('coverage_warning') or '')} 可執行 scripts/fetch_real_mlb_data.py、scripts/run_real_mlb_backtest.py、scripts/generate_plan.py 重新產生真實賽果回測與本頁。</footer>
    </main>
  </div>
</body>
</html>"""


def main() -> None:
    DOCS_DIR.mkdir(exist_ok=True)
    if REAL_RESULTS.exists() and REAL_SUMMARY_CSV.exists():
        data = json.loads(REAL_RESULTS.read_text(encoding="utf-8"))
        summary = load_summary(REAL_SUMMARY_CSV)
    else:
        data = load_json_from_raw(RAW_RESULTS)
        summary = load_summary(SUMMARY_CSV)
    accuracy_data = json.loads(ACCURACY_RESULTS.read_text(encoding="utf-8")) if ACCURACY_RESULTS.exists() else None
    accuracy_summary = load_accuracy_summary(ACCURACY_SUMMARY_CSV) if ACCURACY_SUMMARY_CSV.exists() else []
    plan = build_plan(data, summary, accuracy_data, accuracy_summary)
    CLEAN_RESULTS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    PLAN_JSON.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    html_out = render_html(plan, svg_line(plan["equity_curve"]), svg_accuracy_bars(plan["accuracy_ranking"]))
    INDEX_HTML.write_text(html_out, encoding="utf-8")
    print(f"wrote {CLEAN_RESULTS}")
    print(f"wrote {PLAN_JSON}")
    print(f"wrote {INDEX_HTML}")


if __name__ == "__main__":
    main()
