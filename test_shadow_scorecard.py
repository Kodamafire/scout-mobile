import unittest
from datetime import datetime

from shadow_scorecard import (load_scorecard, pending_symbols, record_signals,
                              scorecard_summary, settle_outcomes)


class ScorecardTests(unittest.TestCase):
    def test_first_signal_per_session_and_later_outcomes(self):
        card = {"version": 1, "signals": []}
        shadow = {"status": "OK", "evidence": [
            dict(symbol="ABC", direction="LONG", decision="WAIT — ENTRY NOT READY",
                 main_score=8, setup_score=90, entry_score=65, price=100),
            dict(symbol="XYZ", direction="SHORT", decision="QUALIFIED",
                 main_score=None, setup_score=80, entry_score=85, price=50),
        ]}
        at = datetime.fromisoformat("2026-09-29T11:00:00-07:00")
        self.assertEqual(record_signals(card, shadow, at, True), 2)
        self.assertEqual(record_signals(card, shadow, at, True), 0)
        self.assertEqual(pending_symbols(card), ["ABC", "XYZ"])
        # The forming 9/30 bar is excluded even when returned by the data API.
        closes = {"ABC": [("2026-09-30", 110), ("2026-10-01", 105)],
                  "XYZ": [("2026-09-30", 45), ("2026-10-01", 55)]}
        self.assertEqual(settle_outcomes(card, closes, "2026-09-30"), 0)
        self.assertEqual(settle_outcomes(card, closes, "2026-10-01"), 2)
        self.assertEqual(card["signals"][0]["outcomes"]["1"]["directional_move_pct"], 10)
        self.assertEqual(card["signals"][1]["outcomes"]["1"]["directional_move_pct"], 10)
        self.assertEqual(settle_outcomes(card, closes, "2026-10-01"), 0)
        self.assertEqual(scorecard_summary(card)["one_day_complete"], 2)
        self.assertEqual(scorecard_summary(card)["comparison"]["wait"]["average_one_day_pct"], 10)

    def test_closed_market_does_not_invent_signal(self):
        card = load_scorecard("/a/path/that/does/not/exist.json")
        shadow = {"status": "OK", "evidence": [dict(symbol="ABC", direction="LONG",
                  decision="QUALIFIED", main_score=9, setup_score=90, entry_score=80, price=100)]}
        self.assertEqual(record_signals(card, shadow,
                         datetime.fromisoformat("2026-09-29T16:00:00-07:00"), False), 0)
        self.assertFalse(card["signals"])


if __name__ == "__main__":
    unittest.main()
