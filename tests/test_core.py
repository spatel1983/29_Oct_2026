from datetime import date
import unittest
from unittest.mock import patch

from football_monitor.core import (
    evaluate_scoreboard,
    fetch_season_to_date,
    normalize_matches,
    scoreboard_url,
    season_dates,
)


def make_event(event_id, state, home_score, away_score):
    return {
        "id": event_id,
        "name": "Home FC at Away FC",
        "date": "2026-10-29T12:00Z",
        "competitions": [
            {
                "status": {
                    "displayClock": "72'",
                    "type": {"state": state, "shortDetail": "Second Half"},
                },
                "competitors": [
                    {"homeAway": "home", "score": str(home_score), "team": {"displayName": "Home FC"}},
                    {"homeAway": "away", "score": str(away_score), "team": {"displayName": "Away FC"}},
                ],
            }
        ],
    }


class ScoreboardTests(unittest.TestCase):
    def test_normalizes_competitors_and_status(self):
        matches = normalize_matches({"events": [make_event("1", "in", 2, 1)]})

        self.assertEqual(matches[0].home_team, "Home FC")
        self.assertEqual(matches[0].away_score, 1)
        self.assertEqual(matches[0].state, "in")
        self.assertFalse(matches[0].completed)
        self.assertEqual(matches[0].goal_difference, 1)

    def test_completed_match_is_identified_as_a_result(self):
        event = make_event("final", "post", 2, 1)
        event["competitions"][0]["status"]["type"]["completed"] = True

        match = normalize_matches({"events": [event]})[0]

        self.assertTrue(match.completed)
        self.assertEqual((match.home_score, match.away_score), (2, 1))

    def test_alerts_only_live_matches_within_configured_goal_difference(self):
        payload = {
            "events": [
                make_event("live-close", "in", 1, 0),
                make_event("live-wide", "in", 3, 0),
                make_event("scheduled", "pre", 0, 0),
            ]
        }

        result = evaluate_scoreboard(payload, max_goal_difference_for_alert=1, duration_ms=25)

        self.assertEqual([match.event_id for match in result.alerts], ["live-close"])
        self.assertEqual(len(result.matches), 3)
        self.assertEqual(result.duration_ms, 25)

    def test_empty_scoreboard_is_valid(self):
        result = evaluate_scoreboard({"events": []}, max_goal_difference_for_alert=1, duration_ms=0)

        self.assertEqual(result.matches, [])
        self.assertEqual(result.alerts, [])

    def test_invalid_event_data_is_reported(self):
        event = make_event("bad", "in", 1, 0)
        event["competitions"][0]["competitors"].pop()
        with self.assertRaisesRegex(ValueError, "exactly one home and away"):
            normalize_matches({"events": [event]})

    def test_scoreboard_url_selects_date_and_preserves_existing_query(self):
        url = scoreboard_url(
            "https://example.com/scoreboard?limit=100",
            date(2026, 9, 19),
        )

        self.assertEqual(url, "https://example.com/scoreboard?limit=100&dates=20260919")

    def test_season_dates_include_only_calendar_days_through_cutoff(self):
        payload = {
            "leagues": [
                {
                    "calendar": [
                        "2026-08-21T07:00Z",
                        "2026-08-22T07:00Z",
                        "2026-08-21T07:00Z",
                    ]
                }
            ]
        }

        self.assertEqual(
            season_dates(payload, date(2026, 8, 21)),
            [date(2026, 8, 21)],
        )

    def test_season_fetch_aggregates_calendar_days_and_deduplicates_events(self):
        seed_event = make_event("second", "post", 2, 1)
        first_event = make_event("first", "post", 1, 0)
        metadata = {
            "day": {"date": "2026-08-22"},
            "leagues": [
                {"calendar": ["2026-08-21T07:00Z", "2026-08-22T07:00Z", "2026-08-23T07:00Z"]}
            ],
            "events": [seed_event],
        }

        with patch(
            "football_monitor.core.fetch_scoreboard",
            side_effect=[(metadata, 10), ({"events": [first_event, seed_event]}, 10)],
        ) as fetch:
            payload, _ = fetch_season_to_date(
                "https://example.com/scoreboard", 10, date(2026, 8, 22)
            )

        self.assertEqual([event["id"] for event in payload["events"]], ["first", "second"])
        self.assertEqual(fetch.call_count, 2)


if __name__ == "__main__":
    unittest.main()
