"""Command-line entry point for the scoreboard monitor."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any

from football_monitor.core import evaluate_scoreboard, fetch_scoreboard, scoreboard_url

LOGGER = logging.getLogger("football_monitor")
DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.json"


def load_config(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as config_file:
            config = json.load(config_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to load configuration {path}: {exc}") from exc
    if not isinstance(config, dict):
        raise ValueError("Configuration must be a JSON object")

    endpoint = config.get("endpoint")
    timeout = config.get("timeout_seconds")
    threshold = config.get("max_goal_difference_for_alert")
    if not isinstance(endpoint, str) or not endpoint.startswith("https://"):
        raise ValueError("Configuration endpoint must be an HTTPS URL")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValueError("timeout_seconds must be a positive number")
    if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 0:
        raise ValueError("max_goal_difference_for_alert must be a non-negative integer")
    web_host = config.get("web_host", "127.0.0.1")
    web_port = config.get("web_port", 8000)
    if not isinstance(web_host, str) or not web_host.strip():
        raise ValueError("web_host must be a non-empty string")
    if isinstance(web_port, bool) or not isinstance(web_port, int) or not 1 <= web_port <= 65535:
        raise ValueError("web_port must be an integer between 1 and 65535")
    if not isinstance(config.get("open_browser", True), bool):
        raise ValueError("open_browser must be true or false")
    favorite_team = config.get("favorite_team", "")
    if not isinstance(favorite_team, str):
        raise ValueError("favorite_team must be a team name or an empty string")
    default_scope = config.get("default_match_scope", "season")
    if not isinstance(default_scope, str) or default_scope not in {"season", "day"}:
        raise ValueError("default_match_scope must be 'season' or 'day'")
    default_status = config.get("default_status_filter", "All")
    if not isinstance(default_status, str) or default_status not in {
        "All", "Scheduled", "Played", "Live"
    }:
        raise ValueError("default_status_filter must be All, Scheduled, Played, or Live")
    report_format = config.get("default_report_format", "csv")
    if not isinstance(report_format, str) or report_format not in {"csv", "json"}:
        raise ValueError("default_report_format must be csv or json")
    log_level = config.get("log_level", "INFO")
    if not isinstance(log_level, str) or log_level not in {
        "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"
    }:
        raise ValueError("log_level must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
    log_file = config.get("log_file", "logs/football_monitor.log")
    if not isinstance(log_file, str) or not log_file.strip():
        raise ValueError("log_file must be a non-empty path")
    max_bytes = config.get("log_max_bytes", 1_000_000)
    backups = config.get("log_backup_count", 3)
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ValueError("log_max_bytes must be a positive integer")
    if isinstance(backups, bool) or not isinstance(backups, int) or backups < 0:
        raise ValueError("log_backup_count must be a non-negative integer")
    return config


def _render_table(matches: list[dict[str, Any]]) -> str:
    if not matches:
        return "No matches found."
    rows = [
        (
            match["status"],
            match["home_team"],
            f'{match["home_score"]} - {match["away_score"]}',
            match["away_team"],
            match["clock"] if match["state"] == "in" else match["kickoff"],
        )
        for match in matches
    ]
    headers = ("STATUS", "HOME", "SCORE", "AWAY", "TIME")
    widths = [max(len(headers[index]), *(len(row[index]) for row in rows)) for index in range(len(headers))]
    line = " | ".join(header.ljust(widths[index]) for index, header in enumerate(headers))
    separator = "-+-".join("-" * width for width in widths)
    body = [" | ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)) for row in rows]
    return "\n".join([line, separator, *body])


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Monitor the ESPN English Premier League scoreboard.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Path to JSON configuration.")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Print normalized results as JSON.")
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=None,
        metavar="YYYY-MM-DD",
        help="Retrieve fixtures and results for a specific date (defaults to ESPN's current scoreboard).",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default=None,
        help="Override the configured logging level.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = load_config(args.config)
        level_name = args.log_level or config.get("log_level", "INFO")
        level = getattr(logging, str(level_name).upper(), None)
        if not isinstance(level, int):
            raise ValueError(f"Invalid log level: {level_name}")
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
            stream=sys.stderr,
        )

        endpoint = scoreboard_url(config["endpoint"], args.date)
        payload, duration_ms = fetch_scoreboard(endpoint, config["timeout_seconds"])
        result = evaluate_scoreboard(payload, config["max_goal_difference_for_alert"], duration_ms)
        live_count = sum(match.state == "in" for match in result.matches)
        result_count = sum(match.completed for match in result.matches)
        fixture_count = len(result.matches) - result_count
        LOGGER.info(
            "scoreboard_fetch status=success duration_ms=%d matches=%d fixtures=%d results=%d live=%d close_match_alerts=%d",
            duration_ms,
            len(result.matches),
            fixture_count,
            result_count,
            live_count,
            len(result.alerts),
        )
        data = {
            "fetched_at": result.fetched_at,
            "duration_ms": result.duration_ms,
            "match_count": len(result.matches),
            "fixture_count": fixture_count,
            "result_count": result_count,
            "live_match_count": live_count,
            "close_match_alert_count": len(result.alerts),
            "alerts": [match.to_dict() for match in result.alerts],
            "matches": [match.to_dict() for match in result.matches],
        }
        if args.json_output:
            print(json.dumps(data, indent=2))
        else:
            print(_render_table(data["matches"]))
            if result.alerts:
                print(f"\nCLOSE MATCH ALERTS: {len(result.alerts)} live match(es) within "
                      f"{config['max_goal_difference_for_alert']} goal(s) of each other.")
                for match in result.alerts:
                    print(f"  {match.home_team} {match.home_score} - {match.away_score} {match.away_team}")
            print(
                f"\nFetched {len(result.matches)} match(es): {fixture_count} fixture(s), "
                f"{result_count} result(s), {live_count} live; "
                f"{len(result.alerts)} close-match alert(s)."
            )
        return 0
    except (ValueError, RuntimeError) as exc:
        if not logging.getLogger().handlers:
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s %(levelname)s %(name)s %(message)s",
                stream=sys.stderr,
            )
        LOGGER.error("scoreboard_fetch status=failed error=%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
