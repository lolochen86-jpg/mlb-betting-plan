#!/usr/bin/env python3
"""Settle daily MLB betting ROI with real odds.

This layer intentionally refuses to treat fixed -110 assumptions as real odds.
It requires data/odds/mlb_moneyline_YYYY-MM-DD.csv before ROI can be computed.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
from datetime import date, datetime
from pathlib import Path

from schedule_time import attach_game_time


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"
ODDS_DIR = DATA_DIR / "odds"

PREDICTIONS_JSON = DATA_DIR / "daily_predictions_{date}.json"
SETTLEMENT_JSON = DATA_DIR / "prediction_settlement_{date}.json"
ODDS_CSV = ODDS_DIR / "mlb_moneyline_{date}.csv"
ROI_JSON = DATA_DIR / "betting_roi_{date}.json"
ROI_CSV = DATA_DIR / "betting_roi_{date}.csv"
ROI_LOG_CSV = DATA_DIR / "betting_roi_log.csv"
ROI_HTML = DOCS_DIR / "betting_roi.html"

REQUIRED_ODDS_FIELDS = [
    "date",
    "game_pk",
    "sportsbook",
    "captured_at_tw",
    "away_zh",
    "home_zh",
    "away_moneyline",
    "home_moneyline",
]


def parse_moneyline(value: str) -> int | float:
    text = str(value or "").strip().replace("＋", "+").replace("－", "-")
    if not text:
        raise ValueError("盤口空白；請填入真實賠率，例如台灣運彩 1.85，或美式 +125 / -135")
    if "." in text:
        odds = float(text)
        if odds <= 1:
            raise ValueError("小數賠率必須大於 1")
        return odds
    clean_text = text[1:] if text.startswith("+") else text
    odds_int = int(clean_text)
    if text.startswith("-"):
        odds_int = -abs(odds_int)
    if odds_int == 0:
        raise ValueError("盤口不能為 0")
    if not text.startswith(("+", "-")) and 1 < odds_int <= 50:
        return float(odds_int)
    return odds_int


def implied_probability(odds: int | float) -> float:
    if isinstance(odds, float):
        return 1 / odds
    return 100 / (odds + 100) if odds > 0 else abs(odds) / (abs(odds) + 100)


def net_profit(unit: float, odds: int | float, won: bool) -> float:
    if not won:
        return -unit
    if isinstance(odds, float):
        return unit * (odds - 1)
    return unit * odds / 100 if odds > 0 else unit * 100 / abs(odds)


def load_json(path: Path, label: str) -> dict:
    if not path.exists():
        raise SystemExit(f"Missing {label}: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_odds(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(
            f"Missing real odds file: {path}\n"
            f"Copy data/odds/mlb_moneyline_template.csv to this filename and fill it with real moneyline odds."
        )
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = [field for field in REQUIRED_ODDS_FIELDS if field not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"Odds file is missing required fields: {', '.join(missing)}")
        rows = []
        for line_no, row in enumerate(reader, start=2):
            if not any(row.values()):
                continue
            if not str(row.get("away_moneyline", "")).strip() and not str(row.get("home_moneyline", "")).strip():
                continue
            try:
                row["away_moneyline"] = parse_moneyline(row["away_moneyline"])
                row["home_moneyline"] = parse_moneyline(row["home_moneyline"])
            except ValueError as exc:
                raise SystemExit(f"Invalid moneyline at {path}:{line_no}: {exc}") from exc
            rows.append(row)
    return rows


def odds_indexes(rows: list[dict]) -> tuple[dict[str, dict], dict[tuple[str, str, str], dict]]:
    by_pk = {str(row.get("game_pk", "")).strip(): row for row in rows if str(row.get("game_pk", "")).strip()}
    by_matchup = {
        (row["date"].strip(), row["away_zh"].strip(), row["home_zh"].strip()): row
        for row in rows
        if row.get("date") and row.get("away_zh") and row.get("home_zh")
    }
    return by_pk, by_matchup


def find_odds(prediction: dict, by_pk: dict[str, dict], by_matchup: dict[tuple[str, str, str], dict]) -> dict | None:
    game_pk = str(prediction.get("game_pk", "")).strip()
    if game_pk and game_pk in by_pk:
        return by_pk[game_pk]
    return by_matchup.get((prediction["date"], prediction["away_zh"], prediction["home_zh"]))


def settlement_index(settlement: dict) -> dict[str, dict]:
    return {str(row.get("game_pk", "")): row for row in settlement.get("settlements", [])}


def make_roi(
    target_date: str,
    unit: float,
    min_edge: float,
    only_high_confidence: bool,
    require_sportsbook: str,
) -> dict:
    prediction_path = Path(str(PREDICTIONS_JSON).format(date=target_date))
    settlement_path = Path(str(SETTLEMENT_JSON).format(date=target_date))
    odds_path = Path(str(ODDS_CSV).format(date=target_date))

    predictions = load_json(prediction_path, "daily prediction file")
    settlement = load_json(settlement_path, "prediction settlement file")
    odds_rows = load_odds(odds_path)
    by_pk, by_matchup = odds_indexes(odds_rows)
    settlements = settlement_index(settlement)

    rows = []
    skipped = []
    for pred in predictions.get("all_predictions", []):
        if only_high_confidence and pred.get("decision") != "高信心預測":
            skipped.append({**pred, "skip_reason": "非高信心預測"})
            continue
        odds = find_odds(pred, by_pk, by_matchup)
        if not odds:
            skipped.append({**pred, "skip_reason": "找不到真實盤口"})
            continue
        if require_sportsbook and odds.get("sportsbook", "").strip() != require_sportsbook:
            skipped.append({**pred, "skip_reason": f"盤口來源不是 {require_sportsbook}"})
            continue
        pick_side = pred.get("pick_side")
        pick_odds = odds["home_moneyline"] if pick_side == "home" else odds["away_moneyline"]
        market_prob = implied_probability(pick_odds)
        confidence = float(pred.get("confidence") or 0)
        edge = confidence - market_prob
        if edge < min_edge:
            skipped.append({**pred, "skip_reason": f"edge {edge:.4f} 低於門檻"})
            continue
        settled = settlements.get(str(pred.get("game_pk", "")), {})
        is_final = settled.get("is_final") is True or str(settled.get("is_final")).lower() == "true"
        won = settled.get("settlement") == "correct" if is_final else None
        pnl = net_profit(unit, pick_odds, won) if won is not None else 0
        rows.append(
            attach_game_time(
                {
                "date": target_date,
                "game_pk": pred.get("game_pk", ""),
                "sportsbook": odds.get("sportsbook", ""),
                "captured_at_tw": odds.get("captured_at_tw", ""),
                "decision": pred.get("decision", ""),
                "matchup_zh": pred.get("matchup_zh", ""),
                "prediction_zh": pred.get("prediction_zh", ""),
                "pick_side": pick_side,
                "moneyline": pick_odds,
                "confidence": round(confidence, 4),
                "market_implied_prob": round(market_prob, 4),
                "edge": round(edge, 4),
                "unit": unit,
                "status": settled.get("status", pred.get("status", "")),
                "score": settled.get("score", ""),
                "actual_winner_zh": settled.get("actual_winner_zh", ""),
                "settlement": "win" if won is True else "loss" if won is False else "pending",
                "pnl": round(pnl, 2),
                "is_final": is_final,
                },
                {str(pred.get("game_pk", "")): {"game_time_tw": pred.get("game_time_tw", ""), "game_time_utc": pred.get("game_time_utc", "")}},
            )
        )

    final_rows = [row for row in rows if row["is_final"]]
    wins = [row for row in final_rows if row["settlement"] == "win"]
    pnl = round(sum(float(row["pnl"]) for row in final_rows), 2)
    risked = len(final_rows) * unit
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "target_date": target_date,
        "source_files": {
            "predictions": str(prediction_path.relative_to(ROOT)),
            "settlement": str(settlement_path.relative_to(ROOT)),
            "odds": str(odds_path.relative_to(ROOT)),
        },
        "settings": {
            "unit": unit,
            "min_edge": min_edge,
            "only_high_confidence": only_high_confidence,
            "require_sportsbook": require_sportsbook,
        },
        "summary": {
            "odds_rows": len(odds_rows),
            "bets": len(rows),
            "final_bets": len(final_rows),
            "pending_bets": len(rows) - len(final_rows),
            "wins": len(wins),
            "losses": len(final_rows) - len(wins),
            "total_pnl": pnl,
            "roi_pct": round(pnl / risked * 100, 2) if risked else 0,
            "skipped": len(skipped),
        },
        "bets": rows,
        "skipped": skipped,
    "note": "ROI uses imported real odds only. Taiwan Sports Lottery decimal odds and American moneyline are supported. Fixed -110 reference results are excluded.",
    }


def write_roi(report: dict) -> None:
    target_date = report["target_date"]
    json_path = Path(str(ROI_JSON).format(date=target_date))
    csv_path = Path(str(ROI_CSV).format(date=target_date))
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = [
        "date",
        "game_pk",
        "game_time_tw",
        "game_time_utc",
        "sportsbook",
        "captured_at_tw",
        "decision",
        "matchup_zh",
        "prediction_zh",
        "moneyline",
        "confidence",
        "market_implied_prob",
        "edge",
        "unit",
        "status",
        "score",
        "actual_winner_zh",
        "settlement",
        "pnl",
        "is_final",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(report["bets"])
    rebuild_roi_log()
    print(f"wrote {json_path}")
    print(f"wrote {csv_path}")
    print(f"wrote {ROI_LOG_CSV}")
    print(f"wrote {ROI_HTML}")
    summary = report["summary"]
    print(
        f"bets={summary['bets']} final={summary['final_bets']} pending={summary['pending_bets']} "
        f"pnl={summary['total_pnl']} roi={summary['roi_pct']}% skipped={summary['skipped']}"
    )


def rebuild_roi_log() -> None:
    rows = []
    for path in sorted(DATA_DIR.glob("betting_roi_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        rows.extend(data.get("bets", []))
    rows.sort(key=lambda row: (row["date"], row.get("game_time_utc", ""), row.get("game_pk", "")), reverse=True)
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
        "settlement",
        "pnl",
        "is_final",
    ]
    with ROI_LOG_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    ROI_HTML.write_text(render_roi_html(rows), encoding="utf-8")


def render_roi_html(rows: list[dict]) -> str:
    final_rows = [row for row in rows if row.get("is_final") is True or str(row.get("is_final")).lower() == "true"]
    wins = [row for row in final_rows if row.get("settlement") == "win"]
    risked = sum(float(row.get("unit") or 0) for row in final_rows)
    pnl = round(sum(float(row.get("pnl") or 0) for row in final_rows), 2)
    roi = round(pnl / risked * 100, 2) if risked else 0
    body = "\n".join(
        f"""
        <tr>
          <td>{html.escape(str(row.get('date', '')))}</td>
          <td>{html.escape(str(row.get('game_time_tw', '') or '未公布'))}</td>
          <td>{html.escape(str(row.get('sportsbook', '')))}</td>
          <td>{html.escape(str(row.get('matchup_zh', '')))}</td>
          <td>{html.escape(str(row.get('prediction_zh', '')))}</td>
          <td>{row.get('moneyline', '')}</td>
          <td>{float(row.get('confidence') or 0) * 100:.1f}%</td>
          <td>{float(row.get('edge') or 0) * 100:.1f}%</td>
          <td>{'贏' if row.get('settlement') == 'win' else '輸' if row.get('settlement') == 'loss' else '待結算'}</td>
          <td>{float(row.get('pnl') or 0):.2f}</td>
        </tr>"""
        for row in rows
    )
    if not rows:
        body = '<tr><td colspan="10">尚未匯入真實盤口，沒有投注 ROI 紀錄。</td></tr>'
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>MLB 真實盤口投注 ROI</title>
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
    <h1>MLB 真實盤口投注 ROI</h1>
    <div class="meta">已結算注數：{len(final_rows)} / 勝：{len(wins)} / 損益：{pnl:.2f} / ROI：{roi:.2f}%</div>
    <table>
      <thead><tr><th>日期</th><th>台灣時間</th><th>來源</th><th>對戰</th><th>預測勝方</th><th>賠率</th><th>信心</th><th>Edge</th><th>結果</th><th>PnL</th></tr></thead>
      <tbody>{body}</tbody>
    </table>
    <div class="note">此頁只使用匯入的真實盤口；台灣運彩小數賠率與美式 moneyline 皆可計算，不使用固定 -110 假設。</div>
  </main>
</body>
</html>"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Settle betting ROI using real MLB moneyline odds.")
    parser.add_argument("--date", default=date.today().isoformat(), help="Target date in YYYY-MM-DD.")
    parser.add_argument("--unit", type=float, default=100)
    parser.add_argument("--min-edge", type=float, default=0.0)
    parser.add_argument("--all-predictions", action="store_true", help="Use all predictions instead of high-confidence only.")
    parser.add_argument(
        "--require-sportsbook",
        default="台灣運彩",
        help="Only recommend bets from this sportsbook. Empty string allows every real odds source.",
    )
    parser.add_argument("--rebuild-log-only", action="store_true", help="Rebuild ROI log and HTML without reading a daily odds file.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.rebuild_log_only:
        rebuild_roi_log()
        print(f"wrote {ROI_LOG_CSV}")
        print(f"wrote {ROI_HTML}")
        return
    report = make_roi(
        target_date=args.date,
        unit=args.unit,
        min_edge=args.min_edge,
        only_high_confidence=not args.all_predictions,
        require_sportsbook=args.require_sportsbook.strip(),
    )
    write_roi(report)


if __name__ == "__main__":
    main()
