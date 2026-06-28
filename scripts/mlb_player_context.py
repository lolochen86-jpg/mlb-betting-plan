"""MLB real lineup, hitter, and pitcher context for simulations."""

from __future__ import annotations

import json
import urllib.request
from functools import lru_cache

from name_localization import player_zh


MLB_API = "https://statsapi.mlb.com/api/v1"
LINEUP_POSITIONS = ["CF", "2B", "1B", "C", "DH", "LF", "3B", "RF", "SS"]


def request_json(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "mlb-betting-plan-player-context/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def safe_float(value: object, fallback: float = 0.0) -> float:
    try:
        if value in ("", None, "-.--"):
            return fallback
        return float(str(value))
    except (TypeError, ValueError):
        return fallback


def rate(numerator: float, denominator: float, fallback: float = 0.0) -> float:
    return numerator / denominator if denominator else fallback


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def stat_split(payload: dict) -> dict:
    stats = payload.get("stats") or []
    splits = stats[0].get("splits") if stats else []
    return (splits[0].get("stat") if splits else {}) or {}


@lru_cache(maxsize=1024)
def player_stat(player_id: int, group: str, season: str) -> dict:
    if not player_id:
        return {}
    url = f"{MLB_API}/people/{player_id}/stats?stats=season&group={group}&season={season}"
    return stat_split(request_json(url))


def hitter_profile_from_stat(stat: dict) -> dict:
    at_bats = safe_float(stat.get("atBats"))
    plate_appearances = safe_float(stat.get("plateAppearances"), at_bats + safe_float(stat.get("baseOnBalls")))
    hits = safe_float(stat.get("hits"))
    doubles = safe_float(stat.get("doubles"))
    triples = safe_float(stat.get("triples"))
    homers = safe_float(stat.get("homeRuns"))
    walks = safe_float(stat.get("baseOnBalls"))
    strikeouts = safe_float(stat.get("strikeOuts"))
    gidp = safe_float(stat.get("groundIntoDoublePlay"))
    avg = safe_float(stat.get("avg"), rate(hits, at_bats, 0.245))
    obp = safe_float(stat.get("obp"), 0.315)
    slg = safe_float(stat.get("slg"), 0.400)
    total_bases = hits + doubles + 2 * triples + 3 * homers
    tb_per_ab = rate(total_bases, at_bats, 0.40)
    return {
        "avg": round(avg, 3),
        "obp": round(obp, 3),
        "slg": round(slg, 3),
        "contact": round(clamp(1 + (avg - 0.245) * 2.4 - rate(strikeouts, plate_appearances, 0.22) * 0.55, 0.72, 1.35), 3),
        "power": round(clamp(1 + (slg - 0.400) * 1.7 + (tb_per_ab - 0.40) * 0.55, 0.70, 1.45), 3),
        "patience": round(clamp(1 + (obp - 0.315) * 2.2 + rate(walks, plate_appearances, 0.08), 0.72, 1.35), 3),
        "gidp": round(clamp(1 + rate(gidp, plate_appearances, 0.02) * 8.0, 0.75, 1.35), 3),
        "k_rate": round(rate(strikeouts, plate_appearances, 0.22), 3),
        "bb_rate": round(rate(walks, plate_appearances, 0.08), 3),
        "sample_pa": int(plate_appearances),
    }


def pitcher_profile_from_stat(stat: dict) -> dict:
    innings = safe_float(stat.get("inningsPitched"))
    strikeouts = safe_float(stat.get("strikeOuts"))
    walks = safe_float(stat.get("baseOnBalls"))
    homers = safe_float(stat.get("homeRuns"))
    groundouts = safe_float(stat.get("groundOuts"))
    airouts = safe_float(stat.get("airOuts"))
    era = safe_float(stat.get("era"), 4.50)
    whip = safe_float(stat.get("whip"), 1.35)
    k9 = rate(strikeouts * 9, innings, 8.3)
    bb9 = rate(walks * 9, innings, 3.2)
    hr9 = rate(homers * 9, innings, 1.1)
    gb_rate = rate(groundouts, groundouts + airouts, 0.45)
    return {
        "era": round(era, 2),
        "whip": round(whip, 3),
        "k_factor": round(clamp(1 + (k9 - 8.3) / 16, 0.80, 1.25), 3),
        "bb_factor": round(clamp(1 + (bb9 - 3.2) / 14, 0.82, 1.22), 3),
        "hr_factor": round(clamp(1 + (hr9 - 1.1) / 5, 0.78, 1.28), 3),
        "gb_factor": round(clamp(1 + (gb_rate - 0.45) * 0.9, 0.85, 1.20), 3),
        "run_prevention_factor": round(clamp(1 + (era - 4.3) / 10 + (whip - 1.30) / 4, 0.75, 1.30), 3),
        "sample_ip": round(innings, 1),
    }


def _person_row(player_id: int, player: dict, batting_order: int, season: str) -> dict:
    person = player.get("person", {})
    position = (player.get("position") or {}).get("abbreviation") or LINEUP_POSITIONS[(batting_order - 1) % 9]
    full_name = person.get("fullName", "")
    stat = player_stat(player_id, "hitting", season)
    return {
        "id": player_id,
        "name": player_zh(full_name) or full_name,
        "name_en": full_name,
        "pos": position,
        "batting_order": batting_order,
        "source": "official_lineup",
        **hitter_profile_from_stat(stat),
    }


def _person_hitting_stat(person: dict) -> dict:
    stats = person.get("stats") or []
    splits = stats[0].get("splits") if stats else []
    return (splits[0].get("stat") if splits else {}) or {}


def _roster_player_row(entry: dict, batting_order: int) -> dict:
    person = entry.get("person") or {}
    position = (entry.get("position") or {}).get("abbreviation") or (person.get("primaryPosition") or {}).get("abbreviation") or LINEUP_POSITIONS[(batting_order - 1) % 9]
    full_name = person.get("fullName", "")
    stat = _person_hitting_stat(person)
    return {
        "id": person.get("id"),
        "name": player_zh(full_name) or full_name,
        "name_en": full_name,
        "pos": position,
        "batting_order": batting_order,
        "source": "projected_roster_stats_lineup",
        **hitter_profile_from_stat(stat),
    }


def _extract_lineup(team_box: dict, season: str) -> list[dict]:
    players = team_box.get("players") or {}
    ordered = []
    for key, player in players.items():
        order = player.get("battingOrder")
        if not order:
            continue
        try:
            batting_order = int(str(order)[:1])
            player_id = int(str(key).replace("ID", ""))
        except ValueError:
            continue
        if 1 <= batting_order <= 9:
            ordered.append(_person_row(player_id, player, batting_order, season))
    if not ordered and team_box.get("batters"):
        for idx, player_id in enumerate(team_box.get("batters", [])[:9], start=1):
            player = players.get(f"ID{player_id}", {})
            ordered.append(_person_row(int(player_id), player, idx, season))
    ordered.sort(key=lambda row: row["batting_order"])
    return ordered[:9]


def fetch_projected_lineup(team_id: int | str | None, season: str) -> list[dict]:
    try:
        tid = int(team_id or 0)
    except (TypeError, ValueError):
        tid = 0
    if not tid:
        return []
    url = f"{MLB_API}/teams/{tid}/roster?rosterType=active&hydrate=person(stats(type=season,group=hitting,season={season}))"
    payload = request_json(url)
    candidates = []
    for entry in payload.get("roster", []):
        position = entry.get("position") or {}
        if position.get("type") == "Pitcher" or position.get("abbreviation") == "P":
            continue
        stat = _person_hitting_stat(entry.get("person") or {})
        plate_appearances = safe_float(stat.get("plateAppearances"))
        ops = safe_float(stat.get("ops"), safe_float(stat.get("obp"), 0.315) + safe_float(stat.get("slg"), 0.400))
        candidates.append((plate_appearances, ops, entry))
    candidates.sort(key=lambda item: (item[0] >= 40, item[0], item[1]), reverse=True)
    return [_roster_player_row(entry, idx) for idx, (_, _, entry) in enumerate(candidates[:9], start=1)]


def fetch_game_player_context(game_pk: str, season: str) -> dict:
    url = f"{MLB_API}/game/{game_pk}/boxscore"
    payload = request_json(url)
    teams = payload.get("teams") or {}
    away_box = teams.get("away") or {}
    home_box = teams.get("home") or {}
    away_lineup = _extract_lineup(away_box, season)
    home_lineup = _extract_lineup(home_box, season)
    return {
        "game_pk": str(game_pk),
        "lineup_source": "official_mlb_boxscore" if away_lineup and home_lineup else "fallback_role_lineup",
        "away_lineup": away_lineup,
        "home_lineup": home_lineup,
    }


def fetch_pitcher_profile(player_id: int | str | None, season: str) -> dict:
    try:
        pid = int(player_id or 0)
    except (TypeError, ValueError):
        pid = 0
    if not pid:
        return {}
    stat = player_stat(pid, "pitching", season)
    return pitcher_profile_from_stat(stat) if stat else {}
