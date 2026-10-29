import json
import tempfile
import unittest
from pathlib import Path

from football_monitor.core import normalize_matches
from football_monitor.reports import create_report, filter_matches, match_comment, save_report


def make_event(event_id, state, home_team, away_team, home_score=2, away_score=1):
    return {
        "id": event_id,
        "date": "2026-10-29T12:00Z",
        "competitions": [
            {
                "status": {
                    "displayClock": "72'",
                    "type": {
                        "state": state,
                        "completed": state == "post",
                        "shortDetail": "Final" if state == "post" else "Scheduled",
                    },
                },
                "competitors": [
                    {"homeAway": "home", "score": str(home_score), "team": {"displayName": home_team}},
                    {"homeAway": "away", "score": str(away_score), "team": {"displayName": away_team}},
                ],
            }
        ],
    }


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.matches = normalize_matches(
            {
                "events": [
                    make_event("fixture", "pre", "Chelsea", "Arsenal"),
                    make_event("result", "post", "Liverpool", "Everton"),
                    make_event("live", "in", "Manchester City", "Chelsea"),
                ]
            }
        )

    def test_filters_by_status_and_case_insensitive_team(self):
        self.assertEqual([match.event_id for match in filter_matches(self.matches, "Scheduled")], ["fixture"])
        self.assertEqual([match.event_id for match in filter_matches(self.matches, "Played")], ["result"])
        self.assertEqual([match.event_id for match in filter_matches(self.matches, "Live")], ["live"])
        self.assertEqual([match.event_id for match in filter_matches(self.matches, "All", "chel")], ["fixture", "live"])

    def test_rejects_unknown_status_filter(self):
        with self.assertRaisesRegex(ValueError, "Unknown status filter"):
            filter_matches(self.matches, "Upcoming")

    def test_selected_team_gets_comments_for_win_draw_loss_and_live_match(self):
        liverpool_win = normalize_matches(
            {"events": [make_event("win", "post", "Liverpool", "Everton")]}
        )[0]
        away_team_win = normalize_matches(
            {"events": [make_event("away-win", "post", "Liverpool", "Everton", 1, 2)]}
        )[0]
        draw_event = make_event("draw", "post", "Arsenal", "Chelsea")
        draw_event["competitions"][0]["competitors"][0]["score"] = "1"
        away_team_draw = normalize_matches({"events": [draw_event]})[0]
        live_event = make_event("live", "in", "Arsenal", "Chelsea")
        away_team_live = normalize_matches({"events": [live_event]})[0]
        unrelated_match = self.matches[0]

        self.assertEqual(
            match_comment(liverpool_win, "Liverpool"),
            "Liverpool win 2-1! Congratulations, Liverpool fans!",
        )
        self.assertEqual(
            match_comment(away_team_win, "Everton"),
            "Everton win 2-1! Congratulations, Everton fans!",
        )
        self.assertEqual(
            match_comment(away_team_draw, "Chelsea"),
            "Chelsea draw 1-1 - a point earned.",
        )
        self.assertEqual(
            match_comment(away_team_live, "Chelsea"),
            "Chelsea trail 1-2 - come on, turn it around!",
        )
        self.assertEqual(match_comment(unrelated_match), "")
        self.assertEqual(match_comment(liverpool_win), "")

    def test_selected_team_comment_reflects_live_trail_and_upcoming_status(self):
        live_trail_event = make_event("trail", "in", "Arsenal", "Chelsea")
        scheduled = self.matches[0]

        live_trail = normalize_matches({"events": [live_trail_event]})[0]

        self.assertEqual(
            match_comment(live_trail, "Chelsea"),
            "Chelsea trail 1-2 - come on, turn it around!",
        )
        self.assertEqual(
            match_comment(scheduled, "Chelsea"),
            "Upcoming fixture - backing Chelsea!",
        )

    def test_saves_json_and_csv_reports(self):
        report = create_report(
            self.matches,
            {"status": "All"},
            "2026-10-29T11:00:00+00:00",
            30,
            favorite_team="Liverpool",
        )
        with tempfile.TemporaryDirectory() as directory:
            json_path = Path(directory) / "report.json"
            csv_path = Path(directory) / "report.csv"

            save_report(json_path, report)
            save_report(csv_path, report)

            saved_json = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(saved_json["summary"]["fixture_count"], 1)
            self.assertEqual(saved_json["summary"]["result_count"], 1)
            self.assertEqual(saved_json["summary"]["live_match_count"], 1)
            self.assertEqual(saved_json["matches"][0]["home_team"], "Chelsea")
            self.assertIn("Liverpool win 2-1", saved_json["matches"][1]["comment"])
            self.assertIn("event_id", csv_path.read_text(encoding="utf-8-sig"))


if __name__ == "__main__":
    unittest.main()
