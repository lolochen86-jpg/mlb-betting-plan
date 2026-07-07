#!/usr/bin/env python3
"""Generate a Chinese daily MLB winner prediction plan from real saved scores."""

from __future__ import annotations

import argparse
import csv
import html
import json
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fetch_real_mlb_data import MLB_SCHEDULE_URL
from name_localization import player_zh, team_zh
from run_real_mlb_backtest import (
    DEFAULT_GAMES_CSV,
    ModelA,
    ModelB,
    ModelC,
    ModelD,
    ModelE,
    TeamStats,
    load_games,
)


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"
DAILY_PLAN_JSON = DATA_DIR / "daily_predictions_{date}.json"
DAILY_PLAN_CSV = DATA_DIR / "daily_predictions_{date}.csv"
DAILY_PLAN_HTML = DOCS_DIR / "daily_predictions.html"
MONTE_CARLO_JSON = DATA_DIR / "monte_carlo_{date}.json"
TOTALS_JSON = DATA_DIR / "totals_predictions_{date}.json"
TW_TZ = ZoneInfo("Asia/Taipei")
REMOVED_PENDING_STATUSES = {"postponed", "cancelled", "canceled"}


def request_json(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "betting-plan-daily/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_schedule(target_date: str) -> list[dict]:
    params = {
        "sportId": "1",
        "date": target_date,
        "hydrate": "team,probablePitcher,linescore",
    }
    payload = request_json(f"{MLB_SCHEDULE_URL}?{urllib.parse.urlencode(params)}")
    games = []
    for day in payload.get("dates", []):
        for game in day.get("games", []):
            if game.get("gameType") != "R":
                continue
            home = game.get("teams", {}).get("home", {})
            away = game.get("teams", {}).get("away", {})
            home_team = home.get("team", {})
            away_team = away.get("team", {})
            home_pitcher = home.get("probablePitcher", {}) or {}
            away_pitcher = away.get("probablePitcher", {}) or {}
            games.append(
                {
                    "date": target_date,
                    "game_pk": str(game.get("gamePk") or ""),
                    "game_time_utc": game.get("gameDate", ""),
                    "game_time_tw": game_time_tw(game.get("gameDate", "")),
                    "status": game.get("status", {}).get("detailedState", ""),
                    "home": home_team.get("name", ""),
                    "home_team_id": home_team.get("id"),
                    "home_zh": team_zh(home_team.get("name", "")),
                    "away": away_team.get("name", ""),
                    "away_team_id": away_team.get("id"),
                    "away_zh": team_zh(away_team.get("name", "")),
                    "home_probable_pitcher": home_pitcher.get("fullName", ""),
                    "home_probable_pitcher_id": home_pitcher.get("id"),
                    "home_probable_pitcher_zh": player_zh(home_pitcher.get("fullName", "")),
                    "away_probable_pitcher": away_pitcher.get("fullName", ""),
                    "away_probable_pitcher_id": away_pitcher.get("id"),
                    "away_probable_pitcher_zh": player_zh(away_pitcher.get("fullName", "")),
                }
            )
    games.sort(key=lambda row: (row.get("game_time_utc") or "", int(row["game_pk"] or 0)))
    return games


def game_time_tw(game_date: str) -> str:
    if not game_date:
        return "未公布"
    try:
        parsed = datetime.fromisoformat(game_date.replace("Z", "+00:00"))
    except ValueError:
        return "未公布"
    return parsed.astimezone(TW_TZ).strftime("%Y-%m-%d %H:%M")


def train_models(games: list[dict]) -> tuple[TeamStats, dict[str, object]]:
    stats = TeamStats()
    base_models = [ModelA(), ModelB(), ModelC(), ModelD()]
    ensemble = ModelE(base_models)
    models = [*base_models, ensemble]
    by_date: dict[str, list[dict]] = defaultdict(list)
    for game in games:
        by_date[game["date"]].append(game)

    for index, day in enumerate(sorted(by_date)):
        games_today = by_date[day]
        if index % 7 == 0:
            ensemble.recalibrate()
        for model in models:
            for game in games_today:
                prob_home = model.predict(game["home"], game["away"], stats)
                if prob_home is None:
                    continue
                picked_home = prob_home >= 0.5
                home_win = game["home_score"] > game["away_score"]
                model.history.append(picked_home == home_win)
        for game in games_today:
            stats.update(game["home"], game["away"], game["home_score"], game["away_score"])
    ensemble.recalibrate()
    return stats, {model.name: model for model in models}


def pick_from_probability(home_zh: str, away_zh: str, prob_home: float) -> dict:
    if prob_home >= 0.5:
        return {"side": "home", "team_zh": home_zh, "confidence": prob_home}
    return {"side": "away", "team_zh": away_zh, "confidence": 1 - prob_home}


def load_score_predictions(target_date: str) -> dict[str, dict]:
    path = Path(str(MONTE_CARLO_JSON).format(date=target_date))
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {}
    rows = payload.get("games", [])
    return {str(row.get("game_pk", "")): row for row in rows if str(row.get("game_pk", ""))}


def load_daily_prediction_index(target_date: str) -> dict[str, dict]:
    path = Path(str(DAILY_PLAN_JSON).format(date=target_date))
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {}
    return {str(row.get("game_pk", "")): row for row in payload.get("all_predictions", []) if str(row.get("game_pk", ""))}


def load_totals_prediction_index(target_date: str) -> dict[str, dict]:
    path = Path(str(TOTALS_JSON).format(date=target_date))
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {}
    rows = [*payload.get("all_predictions", []), *payload.get("skipped", [])]
    return {str(row.get("game_pk", "")): row for row in rows if str(row.get("game_pk", ""))}


def merge_score_predictions(rows: list[dict], target_date: str) -> None:
    scores = load_score_predictions(target_date)
    for row in rows:
        score = scores.get(str(row.get("game_pk", "")))
        if not score:
            row.update(
                {
                    "predicted_away_score": None,
                    "predicted_home_score": None,
                    "predicted_total": None,
                    "score_prediction_zh": "-",
                    "total_prediction_zh": "-",
                    "monte_carlo_pick_zh": "-",
                }
            )
            continue
        away_score = score.get("avg_away_score")
        home_score = score.get("avg_home_score")
        total = score.get("avg_total")
        row.update(
            {
                "predicted_away_score": away_score,
                "predicted_home_score": home_score,
                "predicted_total": total,
                "score_prediction_zh": f"{row['away_zh']} {away_score:.2f} : {row['home_zh']} {home_score:.2f}",
                "total_prediction_zh": f"{total:.2f}",
                "monte_carlo_pick_zh": score.get("moneyline_pick", "-"),
                "monte_carlo_total_line": score.get("total_line"),
                "monte_carlo_totals_pick_zh": score.get("totals_pick", "-"),
                "monte_carlo_over_prob": score.get("over_prob"),
                "monte_carlo_under_prob": score.get("under_prob"),
            }
        )


def annotate_score_alignment(rows: list[dict]) -> None:
    for row in rows:
        away_score = row.get("predicted_away_score")
        home_score = row.get("predicted_home_score")
        score_side = ""
        score_pick = "-"
        if away_score not in (None, "") and home_score not in (None, ""):
            away_value = float(away_score)
            home_value = float(home_score)
            if home_value > away_value:
                score_side = "home"
                score_pick = str(row.get("home_zh", ""))
            elif away_value > home_value:
                score_side = "away"
                score_pick = str(row.get("away_zh", ""))
            else:
                score_side = "tie"
                score_pick = "平手"

        row["score_pick_side"] = score_side
        row["score_pick_zh"] = score_pick
        if not score_side:
            row["score_alignment"] = "無比分模型"
        elif score_side == "tie":
            row["score_alignment"] = "比分平手"
        elif score_side == row.get("pick_side"):
            row["score_alignment"] = "一致"
        else:
            row["score_alignment"] = "模型分歧"
            row["decision"] = "模型分歧"


def score_total_pick(total: object, line: object) -> str:
    if total in (None, "") or line in (None, ""):
        return ""
    try:
        total_value = float(total)
        line_value = float(line)
    except Exception:
        return ""
    if total_value > line_value:
        return "大分"
    if total_value < line_value:
        return "小分"
    return "平盤"


def annotate_totals_alignment(rows: list[dict], target_date: str) -> None:
    totals_index = load_totals_prediction_index(target_date)
    for row in rows:
        totals_row = totals_index.get(str(row.get("game_pk", "")), {})
        official_pick = str(totals_row.get("pick") or "")
        official_line = totals_row.get("line")
        official_predicted = totals_row.get("predicted_total")
        official_prob = float(totals_row.get("model_prob") or 0)
        official_edge = float(totals_row.get("edge") or 0)
        monte_pick = str(row.get("monte_carlo_totals_pick_zh") or "")
        monte_total = row.get("predicted_total")

        row["official_totals_pick_zh"] = official_pick or "-"
        row["official_totals_line"] = official_line
        row["official_totals_predicted"] = official_predicted
        row["official_totals_prob"] = official_prob if official_prob else None
        row["official_totals_edge"] = official_edge if official_edge else None
        row["score_totals_pick_zh"] = score_total_pick(monte_total, official_line)

        if not official_pick or official_line in (None, ""):
            row["totals_alignment"] = "無台灣運彩大小分盤口"
        elif monte_pick in {"大分", "小分"} and official_pick in {"大分", "小分"} and monte_pick != official_pick:
            row["totals_alignment"] = "大小分分歧"
        elif monte_total not in (None, "") and abs(float(monte_total) - float(official_line)) < 0.75:
            row["totals_alignment"] = "接近盤口"
        elif official_prob < 0.57 or official_edge < 0.02:
            row["totals_alignment"] = "大小分信心不足"
        else:
            row["totals_alignment"] = "大小分一致"


def annotate_unified_direction(rows: list[dict]) -> None:
    for row in rows:
        winner_ok = (
            row.get("decision") != "模型分歧"
            and row.get("score_alignment") == "一致"
            and bool(row.get("confirmation_same_direction"))
        )
        totals_ok = (
            row.get("official_totals_pick_zh") in {"大分", "小分"}
            and row.get("totals_alignment") == "大小分一致"
            and row.get("score_totals_pick_zh") == row.get("official_totals_pick_zh")
        )
        row["unified_winner_zh"] = row.get("prediction_zh", "") if winner_ok else "不推薦勝方"
        row["unified_totals_pick_zh"] = row.get("official_totals_pick_zh", "") if totals_ok else "不推薦大小分"
        if winner_ok and totals_ok:
            row["unified_direction"] = f"{row['unified_winner_zh']} / {row['unified_totals_pick_zh']}"
            row["unified_decision"] = "整合推薦"
        else:
            row["unified_direction"] = "不推薦"
            if not winner_ok and not totals_ok:
                row["unified_decision"] = "勝方與大小分未統一"
            elif not winner_ok:
                row["unified_decision"] = "勝方未統一"
            else:
                row["unified_decision"] = "大小分未統一"


def is_removed_pending_status(status: str) -> bool:
    normalized = str(status or "").strip().lower()
    return any(item in normalized for item in REMOVED_PENDING_STATUSES)


def pending_totals_text(settlement_row: dict, daily_row: dict, totals_row: dict) -> str:
    daily_total = daily_row.get("total_prediction_zh") or daily_row.get("predicted_total") or "-"
    totals_predicted = totals_row.get("predicted_total")
    line = totals_row.get("line")
    pick = totals_row.get("pick")
    if line not in (None, "") and pick:
        return f"{pick} {line} / 預測總分 {totals_predicted if totals_predicted not in (None, '') else daily_total}"
    if daily_total not in (None, "", "-"):
        return f"預測總分 {daily_total}"
    return "尚無大小分預測"


def pending_score_text(daily_row: dict) -> str:
    score = daily_row.get("score_prediction_zh")
    if score not in (None, "", "-"):
        return str(score)
    away = daily_row.get("away_zh")
    home = daily_row.get("home_zh")
    away_score = daily_row.get("predicted_away_score")
    home_score = daily_row.get("predicted_home_score")
    if away and home and away_score not in (None, "") and home_score not in (None, ""):
        return f"{away} {float(away_score):.2f} : {home} {float(home_score):.2f}"
    return "尚無雙方得分預測"


def load_pending_settlements(target_date: str) -> list[dict]:
    pending = []
    daily_cache: dict[str, dict[str, dict]] = {}
    totals_cache: dict[str, dict[str, dict]] = {}
    for path in sorted(DATA_DIR.glob("prediction_settlement_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError):
            continue
        settlement_date = str(payload.get("target_date") or path.stem.replace("prediction_settlement_", ""))
        if settlement_date > target_date:
            continue
        for row in payload.get("settlements", []):
            if str(row.get("settlement", "")).lower() != "pending":
                continue
            if is_removed_pending_status(str(row.get("status", ""))):
                continue
            item = dict(row)
            item["date"] = str(item.get("date") or settlement_date)
            daily_cache.setdefault(item["date"], load_daily_prediction_index(item["date"]))
            totals_cache.setdefault(item["date"], load_totals_prediction_index(item["date"]))
            game_pk = str(item.get("game_pk", ""))
            daily_row = daily_cache[item["date"]].get(game_pk, {})
            totals_row = totals_cache[item["date"]].get(game_pk, {})
            item["score_prediction_zh"] = pending_score_text(daily_row)
            item["totals_prediction_zh"] = pending_totals_text(item, daily_row, totals_row)
            pending.append(item)
    pending.sort(
        key=lambda row: (
            str(row.get("game_time_utc") or ""),
            str(row.get("date") or ""),
            str(row.get("game_pk") or ""),
        ),
        reverse=True,
    )
    return pending


def build_daily_plan(target_date: str, games_csv: Path, min_confidence: float) -> dict:
    history = [game for game in load_games(games_csv) if game["date"] < target_date]
    schedule = fetch_schedule(target_date)
    stats, models = train_models(history)
    production_name = "A-畢氏勝率"
    confirmation_name = "E-對照組(Ensemble)"
    production_model = models[production_name]
    confirmation_model = models[confirmation_name]
    candidates = []

    for game in schedule:
        prod_prob = production_model.predict(game["home"], game["away"], stats)
        conf_prob = confirmation_model.predict(game["home"], game["away"], stats)
        if prod_prob is None:
            continue
        prod_pick = pick_from_probability(game["home_zh"], game["away_zh"], prod_prob)
        conf_pick = pick_from_probability(game["home_zh"], game["away_zh"], conf_prob) if conf_prob is not None else None
        same_direction = bool(conf_pick and conf_pick["side"] == prod_pick["side"])
        confidence_pass = prod_pick["confidence"] >= min_confidence
        candidates.append(
            {
                "date": target_date,
                "game_pk": game["game_pk"],
                "game_time_utc": game.get("game_time_utc", ""),
                "game_time_tw": game.get("game_time_tw", "未公布"),
                "status": game["status"],
                "matchup_zh": f"{game['away_zh']} @ {game['home_zh']}",
                "away_zh": game["away_zh"],
                "away_team_id": game.get("away_team_id"),
                "home_zh": game["home_zh"],
                "home_team_id": game.get("home_team_id"),
                "away_probable_pitcher_zh": game["away_probable_pitcher_zh"] or "未公布",
                "away_probable_pitcher_id": game.get("away_probable_pitcher_id"),
                "home_probable_pitcher_zh": game["home_probable_pitcher_zh"] or "未公布",
                "home_probable_pitcher_id": game.get("home_probable_pitcher_id"),
                "prediction_zh": prod_pick["team_zh"],
                "pick_side": prod_pick["side"],
                "confidence": round(prod_pick["confidence"], 4),
                "confirmation_pick_zh": conf_pick["team_zh"] if conf_pick else "未通過",
                "confirmation_same_direction": same_direction,
                "confidence_pass": confidence_pass,
                "decision": "高信心預測" if same_direction and confidence_pass else "一般預測",
            }
        )

    merge_score_predictions(candidates, target_date)
    annotate_score_alignment(candidates)
    annotate_totals_alignment(candidates, target_date)
    annotate_unified_direction(candidates)
    candidates.sort(key=lambda row: (row["decision"] == "高信心預測", row["confidence"]), reverse=True)
    recommendations = [row for row in candidates if row["decision"] == "高信心預測" and row["unified_decision"] == "整合推薦"]
    watchlist = [row for row in candidates if row not in recommendations]
    last_training_date = history[-1]["date"] if history else None
    expected_training_cutoff = (date.fromisoformat(target_date) - timedelta(days=1)).isoformat()
    stale_training = bool(last_training_date and last_training_date < expected_training_cutoff)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "target_date": target_date,
        "models": {
            "production": production_name,
            "confirmation": confirmation_name,
        },
        "settings": {
            "min_confidence": min_confidence,
            "odds_status": "not_used_for_accuracy_first",
        },
        "data_source": {
            "history_csv": str(games_csv.relative_to(ROOT)) if games_csv.is_relative_to(ROOT) else str(games_csv),
            "training_games": len(history),
            "last_training_date": last_training_date,
            "expected_training_cutoff": expected_training_cutoff,
            "schedule_source": "MLB Stats API schedule endpoint",
            "schedule_games": len(schedule),
            "warning": (
                f"模型訓練資料只到 {last_training_date}，低於目標日前一天 {expected_training_cutoff}；請先重抓歷史比分。"
                if stale_training
                else None
            ),
            "freshness_note": (
                f"訓練資料已更新到目標日前一天 {expected_training_cutoff}。"
                if last_training_date == expected_training_cutoff
                else ""
            ),
        },
        "high_confidence_predictions": recommendations,
        "all_predictions": candidates,
        "watchlist": watchlist,
        "pending_unsettled_predictions": load_pending_settlements(target_date),
    }


def write_outputs(plan: dict) -> None:
    target_date = plan["target_date"]
    json_path = Path(str(DAILY_PLAN_JSON).format(date=target_date))
    csv_path = Path(str(DAILY_PLAN_CSV).format(date=target_date))
    json_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = plan["all_predictions"]
    fields = [
        "date",
        "game_pk",
        "game_time_tw",
        "game_time_utc",
        "decision",
        "matchup_zh",
        "away_probable_pitcher_zh",
        "home_probable_pitcher_zh",
        "prediction_zh",
        "unified_decision",
        "unified_direction",
        "unified_winner_zh",
        "unified_totals_pick_zh",
        "confidence",
        "confirmation_pick_zh",
        "confirmation_same_direction",
        "score_prediction_zh",
        "predicted_away_score",
        "predicted_home_score",
        "predicted_total",
        "monte_carlo_pick_zh",
        "score_pick_zh",
        "score_alignment",
        "monte_carlo_totals_pick_zh",
        "official_totals_pick_zh",
        "official_totals_line",
        "official_totals_predicted",
        "score_totals_pick_zh",
        "totals_alignment",
        "status",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    DAILY_PLAN_HTML.write_text(render_html(plan), encoding="utf-8")
    print(f"wrote {json_path}")
    print(f"wrote {csv_path}")
    print(f"wrote {DAILY_PLAN_HTML}")
    print(f"high_confidence_predictions={len(plan['high_confidence_predictions'])} all_predictions={len(plan['all_predictions'])}")


def render_rows(rows: list[dict]) -> str:
    if not rows:
        return '<tr><td colspan="15">目前沒有符合條件的預測。</td></tr>'
    parts = []
    for row in rows:
        parts.append(
            f"""
            <tr>
              <td>{row['decision']}</td>
              <td>{row.get('game_time_tw', '未公布')}</td>
              <td>{row['matchup_zh']}</td>
              <td>{row['away_probable_pitcher_zh']} / {row['home_probable_pitcher_zh']}</td>
              <td>{row['prediction_zh']}</td>
              <td>{row.get('unified_direction', '-')}<span>{row.get('unified_decision', '-')}</span></td>
              <td>{row.get('score_prediction_zh', '-')}</td>
              <td>{row.get('total_prediction_zh', '-')}</td>
              <td>{row.get('score_pick_zh', '-')}</td>
              <td>{row.get('score_alignment', '-')}</td>
              <td>{row.get('monte_carlo_totals_pick_zh', '-')}</td>
              <td>{row.get('official_totals_pick_zh', '-')} {row.get('official_totals_line') or ''}<span>比分總分方向 {row.get('score_totals_pick_zh', '-') or '-'}</span></td>
              <td>{row.get('totals_alignment', '-')}</td>
              <td>{row['confidence'] * 100:.1f}%</td>
              <td>{row['confirmation_pick_zh']}</td>
            </tr>"""
        )
    return "\n".join(parts)


def render_schedule_rows(rows: list[dict]) -> str:
    if not rows:
        return '<tr><td colspan="17">目前沒有賽程資料。</td></tr>'
    parts = []
    for row in rows:
        parts.append(
            f"""
            <tr>
              <td>{row['game_pk']}</td>
              <td>{row.get('game_time_tw', '未公布')}</td>
              <td>{row['status']}</td>
              <td>{row['matchup_zh']}</td>
              <td>{row['away_probable_pitcher_zh']} / {row['home_probable_pitcher_zh']}</td>
              <td>{row['prediction_zh']}</td>
              <td>{row.get('unified_direction', '-')}<span>{row.get('unified_decision', '-')}</span></td>
              <td>{row.get('score_prediction_zh', '-')}</td>
              <td>{row.get('total_prediction_zh', '-')}</td>
              <td>{row.get('score_pick_zh', '-')}</td>
              <td>{row.get('score_alignment', '-')}</td>
              <td>{row.get('monte_carlo_totals_pick_zh', '-')}</td>
              <td>{row.get('official_totals_pick_zh', '-')} {row.get('official_totals_line') or ''}<span>比分總分方向 {row.get('score_totals_pick_zh', '-') or '-'}</span></td>
              <td>{row.get('totals_alignment', '-')}</td>
              <td>{row['confidence'] * 100:.1f}%</td>
              <td>{row['confirmation_pick_zh']}</td>
              <td>{row['decision']}</td>
            </tr>"""
        )
    return "\n".join(parts)


def render_pending_rows(rows: list[dict]) -> str:
    if not rows:
        return '<tr><td colspan="11">目前沒有符合條件的預測。</td></tr>'
    parts = []
    for row in rows:
        confidence = float(row.get("confidence") or 0) * 100
        parts.append(
            f"""
            <tr>
              <td>{html.escape(str(row.get('date', '')))}</td>
              <td>{html.escape(str(row.get('game_time_tw', '')))}</td>
              <td>{html.escape(str(row.get('status', '待結算')))}</td>
              <td>{html.escape(str(row.get('matchup_zh', '')))}</td>
              <td>{html.escape(str(row.get('prediction_zh', '')))}</td>
              <td>{html.escape(str(row.get('score_prediction_zh', '尚無雙方得分預測')))}</td>
              <td>{html.escape(str(row.get('totals_prediction_zh', '尚無大小分預測')))}</td>
              <td>{confidence:.1f}%</td>
              <td>待結算</td>
            </tr>"""
        )
    return "\n".join(parts)


def render_html(plan: dict) -> str:
    rec_rows = render_rows(plan["high_confidence_predictions"])
    watch_rows = render_rows(plan["watchlist"])
    pending_rows = render_pending_rows(plan.get("pending_unsettled_predictions", []))
    schedule_rows_recommendation = render_schedule_rows(plan["all_predictions"])
    schedule_rows_time = render_schedule_rows(
        sorted(plan["all_predictions"], key=lambda row: (row.get("game_time_utc") or "", int(row.get("game_pk") or 0)))
    )
    schedule_payload = json.dumps(
        {"recommendation": schedule_rows_recommendation, "time": schedule_rows_time},
        ensure_ascii=False,
    )
    warning = plan["data_source"].get("warning") or ""
    freshness_note = plan["data_source"].get("freshness_note") or ""
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>每日 MLB 勝方預測</title>
  <style>
    body {{ margin: 0; background: #f7f8f6; color: #202421; font-family: "Microsoft JhengHei", "Noto Sans TC", system-ui, sans-serif; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 28px; }}
    h1 {{ margin: 0 0 6px; font-size: 30px; }}
    h2 {{ margin: 24px 0 12px; font-size: 18px; }}
    .meta {{ color: #68736d; line-height: 1.6; font-size: 14px; }}
    .warning {{ margin-top: 16px; padding: 12px 14px; border: 1px solid #e2c47a; background: #fff8e6; border-radius: 8px; color: #765315; }}
    .toolbar {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin: 14px 0 10px; }}
    .toolbar span {{ color: #68736d; font-size: 13px; font-weight: 700; }}
    .sort-btn {{ border: 1px solid #dfe5df; border-radius: 8px; background: white; color: #24433b; padding: 8px 10px; font: inherit; font-weight: 800; cursor: pointer; }}
    .sort-btn.active {{ background: #165f56; border-color: #165f56; color: white; }}
    table {{ width: 100%; border-collapse: collapse; background: white; border: 1px solid #dfe5df; border-radius: 8px; overflow: hidden; }}
    th, td {{ text-align: left; border-bottom: 1px solid #dfe5df; padding: 12px 10px; white-space: nowrap; font-size: 14px; }}
    th {{ color: #68736d; font-size: 12px; }}
    @media (max-width: 720px) {{ main {{ padding: 18px; }} table {{ display: block; overflow-x: auto; }} h1 {{ font-size: 25px; }} }}
  </style>
</head>
<body>
  <main>
    <h1>每日 MLB 勝方預測</h1>
    <div class="meta">
      MLB日期：{plan['target_date']}<br />
      主模型：{plan['models']['production']} / 確認模型：{plan['models']['confirmation']}<br />
      訓練場數：{plan['data_source']['training_games']} / 訓練截止：{plan['data_source']['last_training_date']} / 賽程場數：{plan['data_source']['schedule_games']}<br />
      產生時間：{plan['generated_at']}<br />
      {freshness_note}
    </div>
    {f'<div class="warning">{warning}</div>' if warning else ''}
    <h2>未結算預測追蹤</h2>
    <table>
      <thead><tr><th>MLB日期</th><th>台灣開賽時間</th><th>狀態</th><th>對戰</th><th>預測勝方</th><th>預測比分</th><th>大小分預測</th><th>信心</th><th>結算</th></tr></thead>
      <tbody>{pending_rows}</tbody>
    </table>
    <h2>完整賽程表</h2>
    <div class="toolbar">
      <span>排序方式</span>
      <button class="sort-btn active" id="sortRecommendation" type="button">推薦高低</button>
      <button class="sort-btn" id="sortTime" type="button">比賽時間</button>
    </div>
    <table>
      <thead><tr><th>GamePk</th><th>台灣開賽時間</th><th>狀態</th><th>對戰</th><th>先發投手</th><th>模型預測</th><th>整合方向</th><th>預測比分</th><th>預測總分</th><th>比分模型</th><th>勝方一致性</th><th>蒙地卡羅大小</th><th>台灣運彩大小</th><th>大小分一致性</th><th>信心</th><th>確認模型</th><th>分類</th></tr></thead>
      <tbody id="scheduleRows">{schedule_rows_recommendation}</tbody>
    </table>
    <div class="warning">投注單請看 <a href="betting_ticket.html">今日投注單</a>。該頁只列入真實盤口與 edge 條件通過的場次。</div>
    <h2>高信心預測</h2>
    <table>
      <thead><tr><th>決策</th><th>台灣開賽時間</th><th>對戰</th><th>先發投手</th><th>預測勝方</th><th>整合方向</th><th>預測比分</th><th>預測總分</th><th>比分模型</th><th>勝方一致性</th><th>蒙地卡羅大小</th><th>台灣運彩大小</th><th>大小分一致性</th><th>信心</th><th>確認模型</th></tr></thead>
      <tbody>{rec_rows}</tbody>
    </table>
    <h2>一般預測</h2>
    <table>
      <thead><tr><th>決策</th><th>台灣開賽時間</th><th>對戰</th><th>先發投手</th><th>預測勝方</th><th>整合方向</th><th>預測比分</th><th>預測總分</th><th>比分模型</th><th>勝方一致性</th><th>蒙地卡羅大小</th><th>台灣運彩大小</th><th>大小分一致性</th><th>信心</th><th>確認模型</th></tr></thead>
      <tbody>{watch_rows}</tbody>
    </table>
    <div class="warning">比分預測取自蒙地卡羅 10,000 次單場模擬平均值；完整模擬分布請看 <a href="monte_carlo.html">蒙地卡羅模擬</a>。投注單請看 <a href="betting_ticket.html">今日投注單</a>。該頁只列入真實盤口與 edge 條件通過的場次。</div>
    <div class="warning">之前做的預測驗證：<a href="prediction_log.html">結算紀錄</a> 看每場命中/錯誤；<a href="postgame_review.html">賽後檢討</a> 看每日總結；<a href="winner_model_search.html">模型搜尋</a> 看歷史模型驗證。</div>
  </main>
  <script>
    const SCHEDULE_ROWS = {schedule_payload};
    const buttons = {{
      recommendation: document.getElementById('sortRecommendation'),
      time: document.getElementById('sortTime')
    }};
    function setScheduleSort(mode) {{
      document.getElementById('scheduleRows').innerHTML = SCHEDULE_ROWS[mode];
      buttons.recommendation.classList.toggle('active', mode === 'recommendation');
      buttons.time.classList.toggle('active', mode === 'time');
    }}
    buttons.recommendation.addEventListener('click', () => setScheduleSort('recommendation'));
    buttons.time.addEventListener('click', () => setScheduleSort('time'));
  </script>
</body>
</html>"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a Chinese daily MLB winner prediction plan.")
    parser.add_argument("--date", default=date.today().isoformat(), help="Target date in YYYY-MM-DD.")
    parser.add_argument("--games-csv", type=Path, default=DEFAULT_GAMES_CSV)
    parser.add_argument("--min-confidence", type=float, default=0.55)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    plan = build_daily_plan(
        args.date,
        args.games_csv,
        args.min_confidence,
    )
    write_outputs(plan)


if __name__ == "__main__":
    main()
