import json
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from football_monitor.web import create_server


def make_event(
    event_id,
    state,
    completed,
    home_score,
    away_score,
    home_team="Home FC",
    away_team="Away FC",
):
    return {
        "id": event_id,
        "name": f"{away_team} at {home_team}",
        "date": "2026-10-29T12:00Z",
        "competitions": [
            {
                "status": {
                    "displayClock": "72'",
                    "type": {
                        "state": state,
                        "completed": completed,
                        "shortDetail": "Final" if completed else "Scheduled",
                    },
                },
                "competitors": [
                    {"homeAway": "home", "score": str(home_score), "team": {"displayName": home_team}},
                    {"homeAway": "away", "score": str(away_score), "team": {"displayName": away_team}},
                ],
            }
        ],
    }


class WebApplicationTests(unittest.TestCase):
    def setUp(self):
        config = {
            "endpoint": "https://example.com/scoreboard",
            "timeout_seconds": 5,
            "max_goal_difference_for_alert": 1,
            "default_match_scope": "day",
            "default_status_filter": "Played",
            "default_report_format": "json",
            "favorite_team": "Liverpool",
        }
        self.server = create_server("127.0.0.1", 0, config)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_serves_web_interface_and_assets(self):
        with urlopen(self.base_url, timeout=2) as response:
            page = response.read().decode("utf-8")
        self.assertIn("Match centre", page)
        self.assertIn("/static/app.js", page)
        self.assertIn('data-default-scope="day"', page)
        self.assertIn('data-default-status="Played"', page)
        self.assertIn('data-default-report="json"', page)
        self.assertIn('data-favorite-team="Liverpool"', page)

        with urlopen(f"{self.base_url}/static/app.js", timeout=2) as response:
            self.assertIn("visibleMatches", response.read().decode("utf-8"))

    @patch("football_monitor.web.fetch_scoreboard")
    def test_matches_endpoint_returns_fixtures_and_results(self, fetch):
        fetch.return_value = (
            {
                "events": [
                    make_event("fixture", "pre", False, 0, 0),
                    make_event("result", "post", True, 2, 1, "Liverpool", "Everton"),
                ]
            },
            12,
        )

        with urlopen(f"{self.base_url}/api/matches?date=2026-10-29&scope=day", timeout=2) as response:
            data = json.load(response)

        self.assertEqual(data["match_count"], 2)
        self.assertEqual(data["fixture_count"], 1)
        self.assertEqual(data["result_count"], 1)
        self.assertTrue(data["matches"][1]["completed"])
        self.assertIn("dates=20261029", fetch.call_args.args[0])

    def test_report_endpoint_returns_filtered_csv_download(self):
        match = {
            "event_id": "result",
            "name": "Everton at Liverpool",
            "home_team": "Liverpool",
            "away_team": "Everton",
            "home_score": 2,
            "away_score": 1,
            "state": "post",
            "completed": True,
            "status": "Final",
            "clock": "90'",
            "kickoff": "2026-10-29T12:00Z",
            "venue": "",
        }
        body = json.dumps(
            {
                "format": "csv",
                "matches": [match],
                "filters": {"status": "Played", "team": "Liverpool", "bias_team": "Liverpool"},
                "fetched_at": "2026-10-29T11:00:00+00:00",
                "duration_ms": 12,
            }
        ).encode("utf-8")
        request = Request(
            f"{self.base_url}/api/report",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urlopen(request, timeout=2) as response:
            report = response.read().decode("utf-8-sig")
            self.assertIn('filename="football_report.csv"', response.headers["Content-Disposition"])

        self.assertIn("Liverpool", report)
        self.assertIn("Liverpool win 2-1! Congratulations, Liverpool fans!", report)
        self.assertIn("2,1", report)

    def test_report_endpoint_rejects_invalid_format(self):
        request = Request(
            f"{self.base_url}/api/report",
            data=b'{"format":[]}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with self.assertRaises(HTTPError) as error:
            urlopen(request, timeout=2)

        self.assertEqual(error.exception.code, 400)
        self.assertIn("Report format must be csv or json", error.exception.read().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
