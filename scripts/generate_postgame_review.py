#!/usr/bin/env python3
"""Generate a postgame review page comparing pregame picks with final results."""

from __future__ import annotations

import csv
import html
import json
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"

POSTGAME_JSON = DATA_DIR / "postgame_review.json"
POSTGAME_HTML = DOCS_DIR / "postgame_review.html"
DAILY_JSON = DATA_DIR / "daily_predictions_{date}.json"


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def pct(part: int, total: int) -> float:
    return round(part / total * 100, 2) if total else 0.0


def parse_score(score: str) -> tuple[int | None, int | None, int | None]:
    try:
        away, home = str(score or "").split("-", 1)
        away_score = int(away)
        home_score = int(home)
        return away_score, home_score, away_score + home_score
    except Exception:
        return None, None, None


def side_from_total(row: dict) -> str:
    if not row or row.get("predicted_total") in (None, "") or row.get("line") in (None, ""):
        return ""
    try:
        return "over" if float(row.get("predicted_total", 0)) > float(row.get("line", 0)) else "under"
    except Exception:
        return ""


def side_zh(side: str) -> str:
    return {"over": "大分", "under": "小分", "home": "主隊", "away": "客隊"}.get(side, side or "-")


def result_zh(value: bool | None) -> str:
    if value is True:
        return "正確"
    if value is False:
        return "錯誤"
    return "待結算"


def load_indexed(path: Path, rows_key: str, key: str = "game_pk") -> dict[str, dict]:
    data = read_json(path)
    rows = data.get(rows_key, []) if data else []
    return {str(row.get(key, "")): row for row in rows if str(row.get(key, ""))}


def totals_index(target_date: str) -> dict[str, dict]:
    data = read_json(DATA_DIR / f"totals_predictions_{target_date}.json")
    rows = data.get("all_predictions", []) if data else []
    return {str(row.get("game_pk", "")): row for row in rows if str(row.get("game_pk", ""))}


def roi_index(target_date: str) -> dict[str, dict]:
    data = read_json(DATA_DIR / f"betting_roi_{target_date}.json")
    rows = data.get("bets", []) if data else []
    return {str(row.get("game_pk", "")): row for row in rows if str(row.get("game_pk", ""))}


def daily_time_index(target_date: str) -> dict[str, dict]:
    data = read_json(Path(str(DAILY_JSON).format(date=target_date)))
    rows = data.get("all_predictions", []) if data else []
    return {
        str(row.get("game_pk", "")): {
            "game_time_tw": row.get("game_time_tw", ""),
            "game_time_utc": row.get("game_time_utc", ""),
        }
        for row in rows
        if str(row.get("game_pk", ""))
    }


def review_game(row: dict, total_row: dict | None, roi_row: dict | None, time_row: dict | None = None) -> dict:
    away_score, home_score, actual_total = parse_score(row.get("score", ""))
    is_final = row.get("is_final") is True or str(row.get("is_final")).lower() == "true"
    winner_correct = row.get("settlement") == "correct" if is_final else None
    total_pick = side_from_total(total_row or {})
    total_correct = None
    total_line = None
    predicted_total = None
    if total_row and actual_total is not None:
        try:
            total_line = float(total_row.get("line"))
            predicted_total = float(total_row.get("predicted_total"))
            actual_side = "over" if actual_total > total_line else "under" if actual_total < total_line else "push"
            total_correct = total_pick == actual_side if actual_side != "push" else None
        except Exception:
            pass
    notes = []
    confidence = float(row.get("confidence") or 0)
    if winner_correct is True and confidence >= 0.55:
        notes.append("高信心方向有命中，這類條件可列為穩定樣本繼續追蹤。")
    elif winner_correct is False and confidence >= 0.55:
        notes.append("高信心失準，需要回看先發投手、牛棚或打線是否有臨場變化。")
    elif winner_correct is False and confidence < 0.53:
        notes.append("信心接近五五波，賽前應降權或避免當主推。")
    elif winner_correct is True:
        notes.append("方向正確，但信心不高，較適合作為觀察而非重注。")
    if away_score is not None and home_score is not None and abs(away_score - home_score) <= 1:
        notes.append("一分差比賽，模型即使方向錯也屬高波動結果。")
    if total_correct is False and predicted_total is not None and actual_total is not None:
        notes.append(f"大小分偏差 {abs(predicted_total - actual_total):.1f} 分，需檢查投手/天氣/打線係數。")
    if roi_row:
        if roi_row.get("settlement") == "loss":
            notes.append("實際投注單虧損，後續應提高 edge 門檻或降低此類下注權重。")
        elif roi_row.get("settlement") == "win":
            notes.append("實際投注單獲利，可追蹤同類盤口條件。")
    return {
        "date": row.get("date", ""),
        "game_pk": row.get("game_pk", ""),
        "game_time_tw": row.get("game_time_tw", "") or (time_row or {}).get("game_time_tw", ""),
        "game_time_utc": row.get("game_time_utc", "") or (time_row or {}).get("game_time_utc", ""),
        "matchup_zh": row.get("matchup_zh", ""),
        "prediction_zh": row.get("prediction_zh", ""),
        "confidence": confidence,
        "score": row.get("score", ""),
        "actual_winner_zh": row.get("actual_winner_zh", ""),
        "winner_correct": winner_correct,
        "total_line": total_line,
        "predicted_total": predicted_total,
        "actual_total": actual_total,
        "total_pick": total_pick,
        "total_correct": total_correct,
        "roi_settlement": roi_row.get("settlement", "") if roi_row else "",
        "pnl": float(roi_row.get("pnl", 0) or 0) if roi_row else 0.0,
        "notes": notes,
    }


def build_report() -> dict:
    days = []
    for path in sorted(DATA_DIR.glob("prediction_settlement_*.json")):
        settlement = read_json(path)
        if not settlement:
            continue
        target_date = settlement.get("target_date", path.stem.replace("prediction_settlement_", ""))
        total_rows = totals_index(target_date)
        roi_rows = roi_index(target_date)
        time_rows = daily_time_index(target_date)
        games = [
            review_game(
                row,
                total_rows.get(str(row.get("game_pk", ""))),
                roi_rows.get(str(row.get("game_pk", ""))),
                time_rows.get(str(row.get("game_pk", ""))),
            )
            for row in settlement.get("settlements", [])
        ]
        final_games = [game for game in games if game["winner_correct"] is not None]
        winner_correct = [game for game in final_games if game["winner_correct"] is True]
        totals_final = [game for game in games if game["total_correct"] is not None]
        totals_correct = [game for game in totals_final if game["total_correct"] is True]
        roi_games = [game for game in games if game["roi_settlement"]]
        pnl = round(sum(game["pnl"] for game in roi_games), 2)
        highlights = []
        misses = []
        for game in final_games:
            if game["winner_correct"] is True and game["confidence"] >= 0.55:
                highlights.append(f"{game['matchup_zh']}：高信心勝方命中。")
            if game["winner_correct"] is False and game["confidence"] >= 0.53:
                misses.append(f"{game['matchup_zh']}：{game['prediction_zh']} 未命中，比分 {game['score']}。")
        if not highlights and winner_correct:
            highlights.append("勝方有命中，但多數信心不高，應保守看待。")
        if not misses and final_games:
            misses.append("沒有明顯高信心失準場。")
        day = (
            {
                "date": target_date,
                "summary": {
                    "games": len(games),
                    "final_games": len(final_games),
                    "pending_games": len(games) - len(final_games),
                    "winner_correct": len(winner_correct),
                    "winner_accuracy_pct": pct(len(winner_correct), len(final_games)),
                    "totals_games": len(totals_final),
                    "totals_correct": len(totals_correct),
                    "totals_accuracy_pct": pct(len(totals_correct), len(totals_final)),
                    "roi_bets": len(roi_games),
                    "pnl": pnl,
                },
                "highlights": highlights[:3],
                "misses": misses[:4],
                "games": games,
            }
        )
        if final_games:
            days.append(day)
    latest = next((day for day in reversed(days) if day["summary"]["final_games"] > 0), days[-1] if days else None)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "latest_review_date": latest.get("date") if latest else "",
        "days": days,
    }


def render_day_tabs(days: list[dict], latest_date: str) -> str:
    return "\n".join(
        f'<a class="tab {"active" if day["date"] == latest_date else ""}" href="#day-{day["date"]}">{day["date"]}</a>'
        for day in reversed(days)
    )


def render_summary_cards(day: dict) -> str:
    s = day["summary"]
    return f"""
    <section class="kpis">
      <div class="kpi"><div class="label">勝方準確率</div><div class="value">{s['winner_accuracy_pct']:.2f}%</div><div class="hint">{s['winner_correct']} / {s['final_games']} 已完賽</div></div>
      <div class="kpi"><div class="label">大小分準確率</div><div class="value">{s['totals_accuracy_pct']:.2f}%</div><div class="hint">{s['totals_correct']} / {s['totals_games']} 有盤口</div></div>
      <div class="kpi"><div class="label">投注損益</div><div class="value {'good' if s['pnl'] > 0 else 'bad' if s['pnl'] < 0 else ''}">{s['pnl']:.0f}</div><div class="hint">{s['roi_bets']} 筆投注紀錄</div></div>
      <div class="kpi"><div class="label">待結算</div><div class="value muted">{s['pending_games']}</div><div class="hint">延賽或尚未完賽</div></div>
    </section>"""


def render_game_rows(games: list[dict]) -> str:
    rows = []
    for game in games:
        rows.append(
            f"""
            <tr>
              <td>{html.escape(game.get('game_time_tw') or '未公布')}</td>
              <td>{html.escape(game['matchup_zh'])}<span>GamePk {game['game_pk']}</span></td>
              <td>{html.escape(game['prediction_zh'])}<span>{game['confidence'] * 100:.1f}%</span></td>
              <td>{game['score'] or '-'}</td>
              <td>{html.escape(game['actual_winner_zh'] or '-')}</td>
              <td><span class="badge {'ok' if game['winner_correct'] is True else 'bad' if game['winner_correct'] is False else 'wait'}">{result_zh(game['winner_correct'])}</span></td>
              <td>{side_zh(game['total_pick'])}<span>{'-' if game['total_line'] is None else game['total_line']}</span></td>
              <td><span class="badge {'ok' if game['total_correct'] is True else 'bad' if game['total_correct'] is False else 'wait'}">{result_zh(game['total_correct'])}</span></td>
              <td>{'<br>'.join(html.escape(note) for note in game['notes'][:2])}</td>
            </tr>"""
        )
    return "\n".join(rows)


def render_html(report: dict) -> str:
    latest_date = report.get("latest_review_date", "")
    day_sections = []
    for day in reversed(report["days"]):
        day_sections.append(
            f"""
            <section class="day" id="day-{day['date']}">
              <div class="day-head">
                <div>
                  <h2>{day['date']} 賽後檢討</h2>
                  <p>賽前預測與賽後結果對照，包含勝方、大小分與投注結果。</p>
                </div>
              </div>
              {render_summary_cards(day)}
              <div class="discussion">
                <div><h3>命中重點</h3><ul>{''.join(f'<li>{html.escape(item)}</li>' for item in day['highlights'])}</ul></div>
                <div><h3>失準檢討</h3><ul>{''.join(f'<li>{html.escape(item)}</li>' for item in day['misses'])}</ul></div>
              </div>
              <div class="table-wrap">
                <table>
                  <thead><tr><th>台灣時間</th><th>對戰</th><th>賽前勝方</th><th>比分</th><th>實際勝方</th><th>勝方</th><th>大小分</th><th>大小分結果</th><th>檢討重點</th></tr></thead>
                  <tbody>{render_game_rows(day['games'])}</tbody>
                </table>
              </div>
            </section>"""
        )
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>MLB 賽後檢討</title>
  <style>
    :root {{ --bg:#f5f7f6; --surface:#fff; --ink:#17201c; --muted:#66736d; --line:#dfe6e1; --soft:#eef4f1; --green:#176454; --red:#a33b33; --amber:#9a6719; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--ink); font-family:"Microsoft JhengHei","Noto Sans TC",system-ui,sans-serif; letter-spacing:0; }}
    main {{ max-width:1240px; margin:0 auto; padding:28px 18px 44px; }}
    h1 {{ margin:0 0 8px; font-size:30px; }}
    h2 {{ margin:0; font-size:22px; }}
    h3 {{ margin:0 0 10px; font-size:16px; }}
    p {{ color:var(--muted); line-height:1.7; margin:0; }}
    .hero {{ background:var(--surface); border:1px solid var(--line); border-radius:8px; padding:22px; margin-bottom:14px; display:flex; justify-content:space-between; gap:16px; align-items:flex-start; }}
    .tabs {{ display:flex; flex-wrap:wrap; gap:8px; margin:12px 0 20px; }}
    .tab {{ text-decoration:none; color:var(--muted); border:1px solid var(--line); background:#fff; border-radius:8px; padding:8px 11px; font-weight:800; font-size:13px; }}
    .tab.active, .tab:hover {{ color:var(--green); background:#e8f3ee; border-color:#c7ded3; }}
    .day {{ margin-top:22px; }}
    .day-head {{ display:flex; justify-content:space-between; align-items:flex-end; gap:16px; margin-bottom:12px; }}
    .kpis {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin-bottom:14px; }}
    .kpi {{ background:#fff; border:1px solid var(--line); border-radius:8px; padding:15px; }}
    .label {{ color:var(--muted); font-size:13px; font-weight:800; }}
    .value {{ font-size:28px; font-weight:900; margin:7px 0 3px; }}
    .value.good {{ color:var(--green); }} .value.bad {{ color:var(--red); }} .value.muted {{ color:var(--amber); }}
    .hint {{ color:var(--muted); font-size:13px; }}
    .discussion {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; margin:14px 0; }}
    .discussion > div {{ background:#fff; border:1px solid var(--line); border-radius:8px; padding:16px; }}
    ul {{ margin:0; padding-left:20px; color:var(--muted); line-height:1.8; }}
    .table-wrap {{ overflow-x:auto; }}
    table {{ width:100%; border-collapse:separate; border-spacing:0; background:#fff; border:1px solid var(--line); border-radius:8px; overflow:hidden; }}
    th,td {{ padding:11px 12px; text-align:left; border-bottom:1px solid #e8eee9; font-size:14px; vertical-align:top; white-space:nowrap; }}
    th {{ background:var(--soft); color:#405049; font-size:12px; font-weight:900; }}
    td span {{ display:block; color:var(--muted); font-size:12px; margin-top:3px; }}
    tr:last-child td {{ border-bottom:0; }}
    .badge {{ display:inline-flex; border-radius:999px; padding:4px 9px; font-size:12px; font-weight:900; margin:0; }}
    .badge.ok {{ color:#126048; background:#dff2ea; }} .badge.bad {{ color:#9b2f29; background:#f9dddd; }} .badge.wait {{ color:#7a4b12; background:#fff2d6; }}
    @media(max-width:900px) {{ .hero,.day-head {{ display:block; }} .kpis,.discussion {{ grid-template-columns:1fr; }} main {{ padding:18px 12px 34px; }} }}
  </style>
</head>
<body>
<main>
  <section class="hero">
    <div>
      <h1>MLB 賽後檢討</h1>
      <p>把賽前預測和賽後結果放在同一張檢討表，追蹤勝方、大小分、投注 ROI 與失準原因。</p>
    </div>
    <p>最近檢討日：{latest_date}<br />產生時間：{report['generated_at']}</p>
  </section>
  <nav class="tabs">{render_day_tabs(report['days'], latest_date)}</nav>
  {''.join(day_sections)}
</main>
</body>
</html>"""


def write_outputs(report: dict) -> None:
    POSTGAME_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    POSTGAME_HTML.write_text(render_html(report), encoding="utf-8")
    print(f"wrote {POSTGAME_JSON}")
    print(f"wrote {POSTGAME_HTML}")
    print(f"postgame_days={len(report['days'])}")


def main() -> None:
    write_outputs(build_report())


if __name__ == "__main__":
    main()
