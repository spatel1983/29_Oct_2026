"""Fetch, normalize, and evaluate ESPN Premier League scoreboard data."""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Match:
    event_id: str
    name: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    state: str
    completed: bool
    status: str
    clock: str
    kickoff: str
    venue: str

    @property
    def goal_difference(self) -> int:
        return abs(self.home_score - self.away_score)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Scoreboard:
    matches: list[Match]
    alerts: list[Match]
    fetched_at: str
    duration_ms: int


def fetch_scoreboard(url: str, timeout_seconds: float) -> tuple[dict[str, Any], int]:
    """Fetch and decode an ESPN scoreboard response, returning payload and latency."""
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "FootballMonitor/1.0"})
    started = time.monotonic()
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            if response.status != 200:
                raise RuntimeError(f"ESPN returned HTTP {response.status}")
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not retrieve a valid ESPN scoreboard: {exc}") from exc

    duration_ms = round((time.monotonic() - started) * 1000)
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise ValueError("ESPN response must be an object containing an events list")
    return payload, duration_ms


def scoreboard_url(endpoint: str, match_date: date | None = None) -> str:
    """Build an ESPN scoreboard URL, optionally selecting a specific calendar day."""
    if match_date is None:
        return endpoint
    parts = urlsplit(endpoint)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["dates"] = match_date.strftime("%Y%m%d")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def season_dates(payload: dict[str, Any], through_date: date) -> list[date]:
    """Return unique league calendar dates on or before the requested season cutoff."""
    leagues = payload.get("leagues")
    if not isinstance(leagues, list) or not leagues:
        raise ValueError("ESPN response is missing league calendar information")
    calendar = leagues[0].get("calendar") if isinstance(leagues[0], dict) else None
    if not isinstance(calendar, list):
        raise ValueError("ESPN response is missing the league match calendar")
    dates: set[date] = set()
    for item in calendar:
        try:
            match_date = date.fromisoformat(str(item)[:10])
        except ValueError as exc:
            raise ValueError(f"ESPN league calendar contains an invalid date: {item!r}") from exc
        if match_date <= through_date:
            dates.add(match_date)
    return sorted(dates)


def fetch_season_to_date(
    endpoint: str, timeout_seconds: float, through_date: date
) -> tuple[dict[str, Any], int]:
    """Fetch every calendar day in the current season through the chosen date."""
    started = time.monotonic()
    metadata, _ = fetch_scoreboard(endpoint, timeout_seconds)
    match_dates = season_dates(metadata, through_date)
    events_by_id: dict[str, dict[str, Any]] = {}

    scoreboard_day = metadata.get("day", {})
    seed_day_text = scoreboard_day.get("date") if isinstance(scoreboard_day, dict) else None
    try:
        seed_day = date.fromisoformat(str(seed_day_text)[:10])
    except (TypeError, ValueError):
        seed_day = None
    if seed_day in match_dates:
        for event in metadata["events"]:
            if isinstance(event, dict) and event.get("id") is not None:
                events_by_id[str(event["id"])] = event

    dates_to_fetch = [match_date for match_date in match_dates if match_date != seed_day]
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(
                fetch_scoreboard,
                scoreboard_url(endpoint, match_date),
                timeout_seconds,
            ): match_date
            for match_date in dates_to_fetch
        }
        for future in as_completed(futures):
            payload, _ = future.result()
            for event in payload["events"]:
                if isinstance(event, dict) and event.get("id") is not None:
                    events_by_id[str(event["id"])] = event

    merged = dict(metadata)
    merged["events"] = sorted(
        events_by_id.values(),
        key=lambda event: (str(event.get("date", "")), str(event.get("id", ""))),
    )
    duration_ms = round((time.monotonic() - started) * 1000)
    LOGGER.info(
        "season_fetch status=success through=%s calendar_days=%d matches=%d duration_ms=%d",
        through_date.isoformat(),
        len(match_dates),
        len(merged["events"]),
        duration_ms,
    )
    return merged, duration_ms


def _score(value: Any, event_id: str, team: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Event {event_id} has an invalid score for {team}: {value!r}") from exc


def normalize_matches(payload: dict[str, Any]) -> list[Match]:
    """Convert ESPN's nested events into stable match records."""
    events = payload.get("events")
    if not isinstance(events, list):
        raise ValueError("Scoreboard payload must contain an events list")

    matches: list[Match] = []
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("Scoreboard event must be an object")
        event_id = str(event.get("id", ""))
        competitions = event.get("competitions")
        if not event_id or not isinstance(competitions, list) or not competitions:
            raise ValueError(f"Scoreboard event is missing its id or competition: {event!r}")

        competition = competitions[0]
        if not isinstance(competition, dict):
            raise ValueError(f"Event {event_id} has an invalid competition")
        competitors = competition.get("competitors")
        if not isinstance(competitors, list):
            raise ValueError(f"Event {event_id} is missing competitors")

        teams: dict[str, tuple[str, int]] = {}
        for competitor in competitors:
            if not isinstance(competitor, dict) or competitor.get("homeAway") not in {"home", "away"}:
                raise ValueError(f"Event {event_id} has an invalid competitor")
            team = competitor.get("team")
            if not isinstance(team, dict) or not team.get("displayName"):
                raise ValueError(f"Event {event_id} has a competitor without a team name")
            side = competitor["homeAway"]
            if side in teams:
                raise ValueError(f"Event {event_id} contains duplicate {side} teams")
            name = str(team["displayName"])
            teams[side] = (name, _score(competitor.get("score", 0), event_id, name))
        if set(teams) != {"home", "away"}:
            raise ValueError(f"Event {event_id} must have exactly one home and away team")

        status_data = competition.get("status", {})
        status_type = status_data.get("type", {}) if isinstance(status_data, dict) else {}
        if not isinstance(status_type, dict):
            status_type = {}
        venue_data = competition.get("venue", {})
        venue = venue_data.get("fullName", "") if isinstance(venue_data, dict) else ""
        matches.append(
            Match(
                event_id=event_id,
                name=str(event.get("name", f"{teams['away'][0]} at {teams['home'][0]}")),
                home_team=teams["home"][0],
                away_team=teams["away"][0],
                home_score=teams["home"][1],
                away_score=teams["away"][1],
                state=str(status_type.get("state", "unknown")),
                completed=bool(status_type.get("completed", status_type.get("state") == "post")),
                status=str(status_type.get("shortDetail", status_type.get("description", "Unknown"))),
                clock=str(status_data.get("displayClock", "")) if isinstance(status_data, dict) else "",
                kickoff=str(event.get("date", "")),
                venue=str(venue),
            )
        )
    return matches


def evaluate_scoreboard(
    payload: dict[str, Any], max_goal_difference_for_alert: int, duration_ms: int
) -> Scoreboard:
    if max_goal_difference_for_alert < 0:
        raise ValueError("max_goal_difference_for_alert must be zero or greater")
    matches = normalize_matches(payload)
    alerts = [match for match in matches if match.state == "in" and match.goal_difference <= max_goal_difference_for_alert]
    fetched_at = datetime.now().astimezone().isoformat(timespec="seconds")
    return Scoreboard(matches, alerts, fetched_at, duration_ms)
