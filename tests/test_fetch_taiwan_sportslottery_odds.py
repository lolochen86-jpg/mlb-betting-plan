from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import fetch_taiwan_sportslottery_odds as odds_fetcher


class FetchTaiwanSportsLotteryOddsTests(unittest.TestCase):
    def test_http_error_keeps_existing_odds_and_writes_empty_snapshot(self) -> None:
        target_date = "2026-08-06"
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            odds_pattern = temp_path / "mlb_moneyline_{date}.csv"
            source_pattern = temp_path / "taiwan_source_{date}.json"
            odds_path = Path(str(odds_pattern).format(date=target_date))
            existing = {field: "" for field in odds_fetcher.FIELDS}
            existing.update(
                {
                    "date": target_date,
                    "game_pk": "123",
                    "sportsbook": "ESPN",
                    "away_zh": "Away",
                    "home_zh": "Home",
                    "away_moneyline": "+120",
                    "home_moneyline": "-130",
                }
            )
            with odds_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=odds_fetcher.FIELDS)
                writer.writeheader()
                writer.writerow(existing)

            forbidden = HTTPError("https://example.test", 403, "Forbidden", None, None)
            with (
                patch.object(odds_fetcher, "ODDS_CSV", odds_pattern),
                patch.object(odds_fetcher, "SOURCE_JSON", source_pattern),
                patch.object(odds_fetcher, "request_json", side_effect=forbidden),
            ):
                result = odds_fetcher.fill_odds(target_date, overwrite_template=False, overwrite_existing=True)

            self.assertIn("HTTPError", result["fetch_error"])
            self.assertEqual([], json.loads(result["source_json"].read_text(encoding="utf-8")))
            with odds_path.open("r", encoding="utf-8", newline="") as handle:
                preserved = list(csv.DictReader(handle))
            self.assertEqual("ESPN", preserved[0]["sportsbook"])
            self.assertEqual("+120", preserved[0]["away_moneyline"])
            self.assertEqual("-130", preserved[0]["home_moneyline"])


if __name__ == "__main__":
    unittest.main()
