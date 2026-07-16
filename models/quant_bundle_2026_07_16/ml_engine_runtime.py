"""Lightweight prediction runtime for Vercel serverless deployments."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

DEFAULT_MAX_KELLY_FRACTION = 0.05
DEFAULT_MIN_TEAM_EXPECTED_RUNS = 1.0
DEFAULT_MAX_TEAM_EXPECTED_RUNS = 8.5


@dataclass(frozen=True, slots=True)
class XGBoostJsonModel:
    feature_names: tuple[str, ...]
    base_margin: float
    trees: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class PoissonLiteModel:
    feature_columns: tuple[str, ...]
    home_params: dict[str, float]
    away_params: dict[str, float]


@dataclass(frozen=True, slots=True)
class ModelBundle:
    moneyline_model: XGBoostJsonModel
    totals_model: PoissonLiteModel
    trained_at: str
    metadata: dict[str, Any]


def load_model_bundle(model_dir: str | Path) -> ModelBundle:
    path = Path(model_dir)
    metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
    moneyline = _load_xgboost_json(path / "moneyline_xgboost.json")
    totals_payload = json.loads((path / "totals_poisson_lite.json").read_text(encoding="utf-8"))
    totals = PoissonLiteModel(
        feature_columns=tuple(totals_payload["feature_columns"]),
        home_params={key: float(value) for key, value in totals_payload["home_params"].items()},
        away_params={key: float(value) for key, value in totals_payload["away_params"].items()},
    )
    return ModelBundle(
        moneyline_model=moneyline,
        totals_model=totals,
        trained_at=str(metadata.get("trained_at", "")),
        metadata=dict(metadata.get("metadata", {})),
    )


def predict_xgboost_ml(
    model: XGBoostJsonModel,
    feature_matrix: pd.DataFrame,
    *,
    feature_columns: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    selected_columns = tuple(feature_columns or model.feature_names)
    x_pred = _prepare_feature_frame(feature_matrix, selected_columns)
    predictions: list[dict[str, Any]] = []

    for idx, row in feature_matrix.reset_index(drop=True).iterrows():
        values = x_pred.iloc[idx].to_dict()
        home_prob = _predict_xgboost_probability(model, values)
        predictions.append(
            {
                "game_id": _row_value(row, "game_id"),
                "mlb_game_pk": _row_value(row, "mlb_game_pk"),
                "game_date": _serialize_date(_row_value(row, "game_date")),
                "true_win_probability": {
                    "home": home_prob,
                    "away": 1.0 - home_prob,
                },
            }
        )
    return predictions


def predict_poisson_ou(
    model: PoissonLiteModel,
    feature_matrix: pd.DataFrame,
    *,
    totals_lines: Mapping[Any, float] | float,
    max_runs: int = 20,
    min_team_expected_runs: float = DEFAULT_MIN_TEAM_EXPECTED_RUNS,
    max_team_expected_runs: float = DEFAULT_MAX_TEAM_EXPECTED_RUNS,
) -> list[dict[str, Any]]:
    if max_runs < 8:
        raise ValueError("max_runs must be at least 8.")

    x_pred = _prepare_feature_frame(feature_matrix, model.feature_columns)
    predictions: list[dict[str, Any]] = []

    for idx, row in feature_matrix.reset_index(drop=True).iterrows():
        game_id = _row_value(row, "game_id")
        values = x_pred.iloc[idx].to_dict()
        line = _resolve_line(totals_lines, game_id, idx)
        home_lambda = _clip(
            _predict_poisson_lambda(model.home_params, values),
            min_team_expected_runs,
            max_team_expected_runs,
        )
        away_lambda = _clip(
            _predict_poisson_lambda(model.away_params, values),
            min_team_expected_runs,
            max_team_expected_runs,
        )
        distribution = _simulate_total_distribution(home_lambda, away_lambda, max_runs=max_runs)
        over_prob, under_prob, push_prob = _total_market_probabilities(distribution, line)

        predictions.append(
            {
                "game_id": game_id,
                "mlb_game_pk": _row_value(row, "mlb_game_pk"),
                "game_date": _serialize_date(_row_value(row, "game_date")),
                "totals_line": float(line),
                "expected_runs": {
                    "home": home_lambda,
                    "away": away_lambda,
                    "total": home_lambda + away_lambda,
                },
                "probability": {
                    "over": over_prob,
                    "under": under_prob,
                    "push": push_prob,
                },
            }
        )
    return predictions


def calibrate_expected_runs_to_win_probability(
    expected_runs: Mapping[str, float],
    home_win_probability: float,
    *,
    max_runs: int = 20,
    min_team_expected_runs: float = DEFAULT_MIN_TEAM_EXPECTED_RUNS,
) -> dict[str, float]:
    """Preserve the Poisson total while matching the score split to ML win probability."""
    total = float(expected_runs["total"])
    if total <= min_team_expected_runs * 2:
        return {"home": total / 2.0, "away": total / 2.0, "total": total}

    target = _clip(float(home_win_probability), 0.01, 0.99)
    lower = min_team_expected_runs
    upper = total - min_team_expected_runs
    for _ in range(36):
        home_lambda = (lower + upper) / 2.0
        away_lambda = total - home_lambda
        current = _home_win_probability_without_ties(
            home_lambda,
            away_lambda,
            max_runs=max_runs,
        )
        if current < target:
            lower = home_lambda
        else:
            upper = home_lambda

    home_lambda = (lower + upper) / 2.0
    return {
        "home": home_lambda,
        "away": total - home_lambda,
        "total": total,
    }


def calculate_bet_size(
    true_prob: float,
    odds: int | float,
    *,
    kelly_fraction: float = 0.5,
    max_fraction: float = DEFAULT_MAX_KELLY_FRACTION,
) -> float:
    probability = _validate_probability(true_prob)
    decimal_odds = american_to_decimal(odds)
    profit_multiple = decimal_odds - 1.0
    expected_value = probability * profit_multiple - (1.0 - probability)
    if expected_value <= 0:
        return 0.0
    return _clip((expected_value / profit_multiple) * kelly_fraction, 0.0, max_fraction)


def build_moneyline_trade_decisions(
    ml_predictions: Sequence[Mapping[str, Any]],
    odds_by_game: Mapping[Any, Mapping[str, int | float]],
    *,
    min_ev: float = 0.0,
    max_fraction: float = DEFAULT_MAX_KELLY_FRACTION,
) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    for prediction in ml_predictions:
        game_id = prediction.get("game_id")
        mlb_game_pk = prediction.get("mlb_game_pk")
        odds = _lookup_odds(odds_by_game, game_id, mlb_game_pk)
        probabilities = prediction["true_win_probability"]
        side_decisions = {
            side: _build_side_decision(
                side=side,
                true_prob=float(probabilities[side]),
                american_odds=float(odds[side]),
                min_ev=min_ev,
                max_fraction=max_fraction,
            )
            for side in ("home", "away")
        }
        recommended = max(side_decisions.values(), key=lambda item: item["expected_value"])
        if recommended["expected_value"] <= min_ev or recommended["bet_fraction"] <= 0:
            recommended_side = "none"
            bet_fraction = 0.0
        else:
            recommended_side = recommended["side"]
            bet_fraction = recommended["bet_fraction"]

        decisions.append(
            {
                "game_id": game_id,
                "mlb_game_pk": mlb_game_pk,
                "market_type": "moneyline",
                "recommended_side": recommended_side,
                "true_win_probability": probabilities,
                "expected_value": {
                    "home": side_decisions["home"]["expected_value"],
                    "away": side_decisions["away"]["expected_value"],
                },
                "current_odds_american": {
                    "home": float(odds["home"]),
                    "away": float(odds["away"]),
                },
                "bet_fraction": bet_fraction,
                "side_details": side_decisions,
            }
        )
    return decisions


def build_ou_trade_decisions(
    ou_predictions: Sequence[Mapping[str, Any]],
    odds_by_game: Mapping[Any, Mapping[str, int | float]],
    *,
    min_ev: float = 0.0,
    max_fraction: float = DEFAULT_MAX_KELLY_FRACTION,
) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    for prediction in ou_predictions:
        game_id = prediction.get("game_id")
        mlb_game_pk = prediction.get("mlb_game_pk")
        odds = _lookup_odds(odds_by_game, game_id, mlb_game_pk)
        probabilities = prediction["probability"]
        side_decisions = {
            side: _build_side_decision(
                side=side,
                true_prob=float(probabilities[side]),
                american_odds=float(odds[side]),
                min_ev=min_ev,
                max_fraction=max_fraction,
            )
            for side in ("over", "under")
        }
        recommended = max(side_decisions.values(), key=lambda item: item["expected_value"])
        if recommended["expected_value"] <= min_ev or recommended["bet_fraction"] <= 0:
            recommended_side = "none"
            bet_fraction = 0.0
        else:
            recommended_side = recommended["side"]
            bet_fraction = recommended["bet_fraction"]

        decisions.append(
            {
                "game_id": game_id,
                "mlb_game_pk": mlb_game_pk,
                "market_type": "total",
                "totals_line": prediction["totals_line"],
                "recommended_side": recommended_side,
                "true_probability": {
                    "over": probabilities["over"],
                    "under": probabilities["under"],
                    "push": probabilities["push"],
                },
                "expected_value": {
                    "over": side_decisions["over"]["expected_value"],
                    "under": side_decisions["under"]["expected_value"],
                },
                "current_odds_american": {
                    "over": float(odds["over"]),
                    "under": float(odds["under"]),
                },
                "bet_fraction": bet_fraction,
                "expected_runs": prediction["expected_runs"],
                "side_details": side_decisions,
            }
        )
    return decisions


def american_to_decimal(odds: int | float) -> float:
    american = float(odds)
    if american == 0:
        raise ValueError("American odds cannot be 0.")
    if american > 0:
        return 1.0 + american / 100.0
    return 1.0 + 100.0 / abs(american)


def implied_probability_from_american(odds: int | float) -> float:
    return 1.0 / american_to_decimal(odds)


def _load_xgboost_json(path: Path) -> XGBoostJsonModel:
    payload = json.loads(path.read_text(encoding="utf-8"))
    learner = payload["learner"]
    base_score = float(learner["learner_model_param"]["base_score"].strip("[]"))
    base_margin = math.log(base_score / (1.0 - base_score))
    return XGBoostJsonModel(
        feature_names=tuple(learner["feature_names"]),
        base_margin=base_margin,
        trees=tuple(learner["gradient_booster"]["model"]["trees"]),
    )


def _prepare_feature_frame(feature_matrix: pd.DataFrame, feature_columns: Sequence[str]) -> pd.DataFrame:
    missing = [column for column in feature_columns if column not in feature_matrix.columns]
    if missing:
        raise ValueError(f"Feature matrix is missing columns: {missing}")
    return feature_matrix.loc[:, list(feature_columns)].copy().replace([math.inf, -math.inf], math.nan).fillna(0.0)


def _predict_xgboost_probability(model: XGBoostJsonModel, row: Mapping[str, Any]) -> float:
    margin = model.base_margin
    for tree in model.trees:
        node = 0
        while int(tree["left_children"][node]) != -1:
            feature_name = model.feature_names[int(tree["split_indices"][node])]
            value = float(row.get(feature_name, 0.0) or 0.0)
            if math.isnan(value):
                go_left = bool(tree["default_left"][node])
            else:
                go_left = value < float(tree["split_conditions"][node])
            node = int(tree["left_children"][node] if go_left else tree["right_children"][node])
        margin += float(tree["split_conditions"][node])
    return _clip(1.0 / (1.0 + math.exp(-margin)), 0.0, 1.0)


def _predict_poisson_lambda(params: Mapping[str, float], row: Mapping[str, Any]) -> float:
    linear = float(params.get("const", 0.0))
    for name, coefficient in params.items():
        if name == "const":
            continue
        linear += float(coefficient) * float(row.get(name, 0.0) or 0.0)
    return math.exp(_clip(linear, -20.0, 20.0))


def _simulate_total_distribution(home_lambda: float, away_lambda: float, *, max_runs: int) -> dict[int, float]:
    home_probs = [_poisson_pmf(k, home_lambda) for k in range(max_runs + 1)]
    away_probs = [_poisson_pmf(k, away_lambda) for k in range(max_runs + 1)]
    distribution: dict[int, float] = {}
    for home_runs, home_prob in enumerate(home_probs):
        for away_runs, away_prob in enumerate(away_probs):
            distribution[home_runs + away_runs] = distribution.get(home_runs + away_runs, 0.0) + home_prob * away_prob
    mass = sum(distribution.values())
    if mass > 0:
        distribution = {total: prob / mass for total, prob in distribution.items()}
    return distribution


def _home_win_probability_without_ties(
    home_lambda: float,
    away_lambda: float,
    *,
    max_runs: int,
) -> float:
    home_probs = [_poisson_pmf(k, home_lambda) for k in range(max_runs + 1)]
    away_probs = [_poisson_pmf(k, away_lambda) for k in range(max_runs + 1)]
    home_win = 0.0
    tie = 0.0
    mass = 0.0
    for home_runs, home_prob in enumerate(home_probs):
        for away_runs, away_prob in enumerate(away_probs):
            probability = home_prob * away_prob
            mass += probability
            if home_runs > away_runs:
                home_win += probability
            elif home_runs == away_runs:
                tie += probability
    non_tie_mass = mass - tie
    return home_win / non_tie_mass if non_tie_mass > 0 else 0.5


def _poisson_pmf(k: int, lam: float) -> float:
    return math.exp(-lam + k * math.log(lam) - math.lgamma(k + 1))


def _total_market_probabilities(distribution: Mapping[int, float], line: float) -> tuple[float, float, float]:
    over = 0.0
    under = 0.0
    push = 0.0
    for total_runs, probability in distribution.items():
        if total_runs > line:
            over += probability
        elif total_runs < line:
            under += probability
        else:
            push += probability
    return over, under, push


def _build_side_decision(
    *,
    side: str,
    true_prob: float,
    american_odds: float,
    min_ev: float,
    max_fraction: float,
) -> dict[str, float | str]:
    probability = _validate_probability(true_prob)
    decimal_odds = american_to_decimal(american_odds)
    profit_multiple = decimal_odds - 1.0
    expected_value = probability * profit_multiple - (1.0 - probability)
    bet_fraction = 0.0
    if expected_value > min_ev:
        bet_fraction = calculate_bet_size(
            probability,
            american_odds,
            max_fraction=max_fraction,
        )
    return {
        "side": side,
        "true_probability": probability,
        "implied_probability": implied_probability_from_american(american_odds),
        "american_odds": float(american_odds),
        "expected_value": float(expected_value),
        "bet_fraction": float(bet_fraction),
    }


def _lookup_odds(
    odds_by_game: Mapping[Any, Mapping[str, int | float]],
    game_id: Any,
    mlb_game_pk: Any,
) -> Mapping[str, int | float]:
    for key in (game_id, mlb_game_pk, str(game_id), str(mlb_game_pk)):
        if key in odds_by_game:
            return odds_by_game[key]
    raise KeyError(f"Missing odds for game_id={game_id!r} mlb_game_pk={mlb_game_pk!r}")


def _resolve_line(totals_lines: Mapping[Any, float] | float, game_id: Any, idx: int) -> float:
    if isinstance(totals_lines, Mapping):
        for key in (game_id, str(game_id), idx, str(idx)):
            if key in totals_lines:
                return float(totals_lines[key])
        raise KeyError(f"Missing totals line for game_id={game_id!r}")
    return float(totals_lines)


def _validate_probability(value: float) -> float:
    probability = float(value)
    if not 0.0 <= probability <= 1.0:
        raise ValueError(f"Probability must be within [0, 1], got {value!r}.")
    return probability


def _row_value(row: Mapping[str, Any], column: str) -> Any:
    value = row.get(column)
    if pd.isna(value):
        return None
    return value


def _serialize_date(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _clip(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, float(value)))
