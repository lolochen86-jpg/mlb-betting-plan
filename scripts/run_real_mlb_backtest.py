#!/usr/bin/env python3
"""Run the MLB betting-model backtest against saved real MLB game results."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from name_localization import team_zh


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DEFAULT_GAMES_CSV = DATA_DIR / "real_mlb_games.csv"
RESULTS_JSON = DATA_DIR / "real_mlb_backtest_results.json"
SUMMARY_CSV = DATA_DIR / "real_mlb_backtest_summary.csv"
PROVENANCE_JSON = DATA_DIR / "real_data_provenance.json"


def payout(unit: float, odds: int) -> float:
    return unit * odds / 100 if odds > 0 else unit * 100 / abs(odds)


def implied_prob(odds: int) -> float:
    return 100 / (odds + 100) if odds > 0 else abs(odds) / (abs(odds) + 100)


@dataclass
class TeamStats:
    season_rs: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))
    season_ra: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))
    rolling10: dict[str, list[tuple[int, int]]] = field(default_factory=lambda: defaultdict(list))
    elo: dict[str, float] = field(default_factory=lambda: defaultdict(lambda: 1500.0))

    def update(self, home: str, away: str, home_score: int, away_score: int) -> None:
        self.season_rs[home].append(home_score)
        self.season_ra[home].append(away_score)
        self.season_rs[away].append(away_score)
        self.season_ra[away].append(home_score)
        self.rolling10[home].append((home_score, away_score))
        self.rolling10[away].append((away_score, home_score))
        self.rolling10[home] = self.rolling10[home][-10:]
        self.rolling10[away] = self.rolling10[away][-10:]
        expected_home = 1 / (1 + 10 ** ((self.elo[away] - self.elo[home]) / 400))
        actual_home = 1 if home_score > away_score else 0
        k = 20
        self.elo[home] += k * (actual_home - expected_home)
        self.elo[away] += k * ((1 - actual_home) - (1 - expected_home))

    def has_data(self, team: str) -> bool:
        return len(self.season_rs[team]) >= 5

    def pythagorean_pct(self, team: str, exponent: float = 1.83) -> float:
        rs = sum(self.season_rs[team])
        ra = sum(self.season_ra[team])
        if rs <= 0 and ra <= 0:
            return 0.5
        return rs**exponent / (rs**exponent + ra**exponent)

    def form_pct(self, team: str) -> float:
        games = self.rolling10[team]
        if not games:
            return 0.5
        wins = sum(1 for scored, allowed in games if scored > allowed)
        return wins / len(games)

    def elo_win_prob(self, home: str, away: str) -> float:
        return 1 / (1 + 10 ** ((self.elo[away] - self.elo[home]) / 400))

    def avg_allowed_last5(self, team: str) -> float:
        games = self.rolling10[team][-5:]
        if not games:
            return 4.5
        return sum(allowed for _, allowed in games) / len(games)


class BaseModel:
    name = ""

    def __init__(self) -> None:
        self.history: list[bool] = []

    def predict(self, home: str, away: str, stats: TeamStats) -> float | None:
        raise NotImplementedError


class ModelA(BaseModel):
    name = "A-畢氏勝率"

    def predict(self, home: str, away: str, stats: TeamStats) -> float | None:
        if not (stats.has_data(home) and stats.has_data(away)):
            return None
        home_pct = stats.pythagorean_pct(home)
        away_pct = stats.pythagorean_pct(away)
        prob = home_pct / (home_pct + away_pct)
        return max(0.35, min(0.75, prob + 0.02))


class ModelB(BaseModel):
    name = "B-近期狀態"

    def predict(self, home: str, away: str, stats: TeamStats) -> float | None:
        home_form = stats.form_pct(home)
        away_form = stats.form_pct(away)
        if len(stats.rolling10[home]) < 5 or len(stats.rolling10[away]) < 5:
            return None
        prob = home_form / (home_form + away_form) if home_form + away_form else 0.5
        return max(0.35, min(0.75, prob + 0.015))


class ModelC(BaseModel):
    name = "C-ELO評分"

    def predict(self, home: str, away: str, stats: TeamStats) -> float:
        return max(0.35, min(0.75, stats.elo_win_prob(home, away) + 0.015))


class ModelD(BaseModel):
    name = "D-近期失分代理"

    def predict(self, home: str, away: str, stats: TeamStats) -> float | None:
        if not (stats.has_data(home) and stats.has_data(away)):
            return None
        home_ra = stats.avg_allowed_last5(home)
        away_ra = stats.avg_allowed_last5(away)
        prob = away_ra / (home_ra + away_ra) if home_ra + away_ra else 0.5
        return max(0.38, min(0.70, prob + 0.02))


class ModelE(BaseModel):
    name = "E-對照組(Ensemble)"

    def __init__(self, models: list[BaseModel]) -> None:
        super().__init__()
        self.models = models
        self.weights = {m.name: 1.0 for m in models}

    def recalibrate(self) -> None:
        for model in self.models:
            recent = model.history[-30:]
            accuracy = sum(recent) / len(recent) if recent else 0.5
            self.weights[model.name] = max(0.1, accuracy**2)

    def predict(self, home: str, away: str, stats: TeamStats) -> float | None:
        weighted = 0.0
        total_weight = 0.0
        for model in self.models:
            prob = model.predict(home, away, stats)
            if prob is None:
                continue
            weight = self.weights.get(model.name, 1.0)
            weighted += prob * weight
            total_weight += weight
        if total_weight == 0:
            return None
        return max(0.35, min(0.75, weighted / total_weight))


def load_games(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    games = []
    for row in rows:
        if not row.get("home_score") or not row.get("away_score"):
            continue
        games.append(
            {
                "date": row["date"],
                "game_pk": row.get("game_pk", ""),
                "home": row["home"],
                "home_zh": row.get("home_zh") or team_zh(row["home"]),
                "away": row["away"],
                "away_zh": row.get("away_zh") or team_zh(row["away"]),
                "home_score": int(row["home_score"]),
                "away_score": int(row["away_score"]),
                "status": row.get("status", ""),
                "home_probable_pitcher": row.get("home_probable_pitcher", ""),
                "home_probable_pitcher_zh": row.get("home_probable_pitcher_zh", ""),
                "away_probable_pitcher": row.get("away_probable_pitcher", ""),
                "away_probable_pitcher_zh": row.get("away_probable_pitcher_zh", ""),
            }
        )
    games.sort(key=lambda r: (r["date"], int(r["game_pk"] or 0)))
    return games


def select_bets(
    model: BaseModel,
    games_today: list[dict],
    stats: TeamStats,
    max_bets: int,
    min_confidence: float,
    min_edge: float,
    market_prob: float,
) -> list[dict]:
    candidates = []
    for game in games_today:
        prob_home = model.predict(game["home"], game["away"], stats)
        if prob_home is None:
            continue
        home_edge = prob_home - market_prob
        away_prob = 1 - prob_home
        away_edge = away_prob - market_prob
        if prob_home >= min_confidence and home_edge >= min_edge:
            candidates.append({**game, "pick": "home", "pick_team": game["home"], "confidence": prob_home, "edge": home_edge})
        elif away_prob >= min_confidence and away_edge >= min_edge:
            candidates.append({**game, "pick": "away", "pick_team": game["away"], "confidence": away_prob, "edge": away_edge})
    candidates.sort(key=lambda r: (r["confidence"], r["edge"]), reverse=True)
    return candidates[:max_bets]


def summarize(records: list[dict], unit: float) -> dict:
    total = sum(r["bets"] for r in records)
    wins = sum(r["wins"] for r in records)
    pnl = round(sum(r["pnl"] for r in records), 2)
    return {
        "total_bets": total,
        "wins": wins,
        "losses": total - wins,
        "win_rate": round(wins / total, 4) if total else 0,
        "total_pnl": pnl,
        "roi_pct": round(pnl / (total * unit) * 100, 2) if total else 0,
        "daily_records": records,
    }


def run_backtest(games: list[dict], unit: float, odds: int, max_bets: int, min_confidence: float, min_edge: float) -> dict:
    stats = TeamStats()
    base_models: list[BaseModel] = [ModelA(), ModelB(), ModelC(), ModelD()]
    ensemble = ModelE(base_models)
    models: list[BaseModel] = [*base_models, ensemble]
    market_prob = implied_prob(odds)
    win_payout = payout(unit, odds)
    ledger: dict[str, list[dict]] = {m.name: [] for m in models}
    by_date: dict[str, list[dict]] = defaultdict(list)
    for game in games:
        by_date[game["date"]].append(game)

    for index, day in enumerate(sorted(by_date)):
        games_today = by_date[day]
        if index % 7 == 0:
            ensemble.recalibrate()
        for model in models:
            bets = select_bets(model, games_today, stats, max_bets, min_confidence, min_edge, market_prob)
            day_wins = 0
            day_losses = 0
            day_pnl = 0.0
            bet_rows = []
            for bet in bets:
                picked_home = bet["pick"] == "home"
                home_win = bet["home_score"] > bet["away_score"]
                won = picked_home == home_win
                model.history.append(won)
                if won:
                    day_wins += 1
                    day_pnl += win_payout
                else:
                    day_losses += 1
                    day_pnl -= unit
                bet_rows.append(
                    {
                        "game_pk": bet["game_pk"],
                        "home": bet["home"],
                        "home_zh": bet.get("home_zh", bet["home"]),
                        "away": bet["away"],
                        "away_zh": bet.get("away_zh", bet["away"]),
                        "home_score": bet["home_score"],
                        "away_score": bet["away_score"],
                        "pick": bet["pick_team"],
                        "pick_zh": bet.get("home_zh", bet["home"]) if bet["pick"] == "home" else bet.get("away_zh", bet["away"]),
                        "home_probable_pitcher_zh": bet.get("home_probable_pitcher_zh", ""),
                        "away_probable_pitcher_zh": bet.get("away_probable_pitcher_zh", ""),
                        "confidence": round(bet["confidence"], 4),
                        "edge": round(bet["edge"], 4),
                        "result": "win" if won else "loss",
                    }
                )
            ledger[model.name].append(
                {
                    "date": day,
                    "bets": len(bets),
                    "wins": day_wins,
                    "losses": day_losses,
                    "pnl": round(day_pnl, 2),
                    "bet_details": bet_rows,
                }
            )
        for game in games_today:
            stats.update(game["home"], game["away"], game["home_score"], game["away_score"])

    first_date = games[0]["date"] if games else None
    last_date = games[-1]["date"] if games else None
    provenance = {}
    if PROVENANCE_JSON.exists():
        provenance = json.loads(PROVENANCE_JSON.read_text(encoding="utf-8"))
    return {
        "backtest_period": {"start": first_date, "end": last_date},
        "settings": {
            "unit": unit,
            "odds": odds,
            "max_bets_per_day": max_bets,
            "min_confidence": min_confidence,
            "min_edge": min_edge,
        },
        "data_source": {
            "type": "real_mlb_final_scores",
            "games_csv": str(DEFAULT_GAMES_CSV.relative_to(ROOT)),
            "games_evaluated": len(games),
            "actual_first_game_date": first_date,
            "actual_last_game_date": last_date,
            "game_types": provenance.get("game_types"),
            "coverage_warning": provenance.get("coverage_warning"),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "note": "Backtest uses real MLB final scores from saved MLB Stats API schedule data. Odds are still the configured fixed market assumption.",
        },
        "models": {model.name: summarize(ledger[model.name], unit) for model in models},
    }


def write_summary(results: dict) -> None:
    rows = []
    for name, model in results["models"].items():
        rows.append(
            {
                "模型": name,
                "總注": model["total_bets"],
                "勝": model["wins"],
                "敗": model["losses"],
                "勝率%": round(model["win_rate"] * 100, 2),
                "總損益$": model["total_pnl"],
                "ROI%": model["roi_pct"],
            }
        )
    with SUMMARY_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["模型", "總注", "勝", "敗", "勝率%", "總損益$", "ROI%"])
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run model backtest with saved real MLB final scores.")
    parser.add_argument("--games-csv", type=Path, default=DEFAULT_GAMES_CSV)
    parser.add_argument("--unit", type=float, default=100)
    parser.add_argument("--odds", type=int, default=-110)
    parser.add_argument("--max-bets-per-day", type=int, default=5)
    parser.add_argument("--min-confidence", type=float, default=0.55)
    parser.add_argument("--min-edge", type=float, default=0.03)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    games = load_games(args.games_csv)
    if not games:
        raise SystemExit(f"No completed games found in {args.games_csv}")
    results = run_backtest(games, args.unit, args.odds, args.max_bets_per_day, args.min_confidence, args.min_edge)
    RESULTS_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    write_summary(results)
    print(f"wrote {RESULTS_JSON}")
    print(f"wrote {SUMMARY_CSV}")
    print(f"games_evaluated={results['data_source']['games_evaluated']}")
    for name, model in results["models"].items():
        print(f"{name}: bets={model['total_bets']} wins={model['wins']} roi={model['roi_pct']}%")


if __name__ == "__main__":
    main()
