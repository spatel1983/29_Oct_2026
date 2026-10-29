"""Filtering and report export for the desktop scoreboard."""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from football_monitor.core import Match

STATUS_FILTERS = ("All", "Scheduled", "Played", "Live")


def match_comment(match: Match, favorite_team: str = "") -> str:
    """Generate score-aware fan commentary without changing the reported score."""
    team = favorite_team.strip()
    if not team:
        return ""
    if team.casefold() == match.home_team.casefold():
        favorite_score, opponent_score = match.home_score, match.away_score
    elif team.casefold() == match.away_team.casefold():
        favorite_score, opponent_score = match.away_score, match.home_score
    else:
        return ""

    score = f"{favorite_score}-{opponent_score}"
    if match.completed:
        if favorite_score > opponent_score:
            return f"{team} win {score}! Congratulations, {team} fans!"
        if favorite_score == opponent_score:
            return f"{team} draw {score} - a point earned."
        return f"Tough result: {team} lose {score}. Next match!"
    if match.state == "in":
        if favorite_score > opponent_score:
            return f"{team} lead {score} - keep it going!"
        if favorite_score == opponent_score:
            return f"{team} level {score} - push for the winner!"
        return f"{team} trail {score} - come on, turn it around!"
    return f"Upcoming fixture - backing {team}!"


def filter_matches(matches: list[Match], status_filter: str, team_query: str = "") -> list[Match]:
    if status_filter not in STATUS_FILTERS:
        raise ValueError(f"Unknown status filter: {status_filter}")
    query = team_query.strip().casefold()
    filtered: list[Match] = []
    for match in matches:
        if status_filter == "Scheduled" and (match.completed or match.state == "in"):
            continue
        if status_filter == "Played" and not match.completed:
            continue
        if status_filter == "Live" and match.state != "in":
            continue
        if query and query not in match.home_team.casefold() and query not in match.away_team.casefold():
            continue
        filtered.append(match)
    return filtered


def create_report(
    matches: list[Match],
    filters: dict[str, str],
    fetched_at: str,
    duration_ms: int,
    favorite_team: str = "",
) -> dict[str, Any]:
    results = sum(match.completed for match in matches)
    live = sum(match.state == "in" for match in matches)
    fixtures = sum(not match.completed and match.state != "in" for match in matches)
    return {
        "report_generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "fetched_at": fetched_at,
        "duration_ms": duration_ms,
        "filters": filters,
        "summary": {
            "match_count": len(matches),
            "fixture_count": fixtures,
            "result_count": results,
            "live_match_count": live,
        },
        "matches": [
            {**match.to_dict(), "comment": match_comment(match, favorite_team)}
            for match in matches
        ],
    }


def save_report(path: Path, report: dict[str, Any]) -> None:
    if path.suffix.casefold() == ".json":
        with path.open("w", encoding="utf-8") as output_file:
            json.dump(report, output_file, indent=2)
            output_file.write("\n")
        return
    if path.suffix.casefold() != ".csv":
        raise ValueError("Report filename must end in .csv or .json")

    fields = (
        "event_id", "name", "home_team", "home_score", "away_score", "away_team",
        "state", "completed", "status", "clock", "kickoff", "venue", "comment",
    )
    with path.open("w", encoding="utf-8-sig", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(report["matches"])
