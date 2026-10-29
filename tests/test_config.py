import json
import tempfile
import unittest
from pathlib import Path

from football_monitor.cli import load_config


class ConfigTests(unittest.TestCase):
    def test_loads_application_configuration(self):
        config = {
            "endpoint": "https://example.com/scoreboard",
            "timeout_seconds": 10,
            "max_goal_difference_for_alert": 1,
            "web_host": "127.0.0.1",
            "web_port": 9000,
            "open_browser": False,
            "default_match_scope": "day",
            "default_status_filter": "Played",
            "default_report_format": "json",
            "log_file": "logs/test.log",
            "log_max_bytes": 1000,
            "log_backup_count": 2,
            "log_level": "DEBUG",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config), encoding="utf-8")

            self.assertEqual(load_config(path), config)

    def test_rejects_invalid_web_port(self):
        config = {
            "endpoint": "https://example.com/scoreboard",
            "timeout_seconds": 10,
            "max_goal_difference_for_alert": 1,
            "web_port": 70000,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "web_port"):
                load_config(path)


if __name__ == "__main__":
    unittest.main()
