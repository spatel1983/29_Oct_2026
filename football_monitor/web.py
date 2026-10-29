"""Local web application for the ESPN Premier League monitor."""

from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import sys
import tempfile
import webbrowser
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from html import escape
from urllib.parse import parse_qs, urlsplit

from football_monitor.cli import DEFAULT_CONFIG, load_config
from football_monitor.core import (
    Match,
    evaluate_scoreboard,
    fetch_scoreboard,
    fetch_season_to_date,
    scoreboard_url,
)
from football_monitor.reports import create_report, save_report

LOGGER = logging.getLogger("football_monitor.web")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASSETS = Path(__file__).resolve().parent / "web_assets"
LOG_PATH = PROJECT_ROOT / "logs" / "football_monitor.log"
MAX_REQUEST_BYTES = 2_000_000


def configure_file_logging(config: dict[str, Any]) -> None:
    log_path = Path(config.get("log_file", str(LOG_PATH)))
    if not log_path.is_absolute():
        log_path = PROJECT_ROOT / log_path
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_path,
        maxBytes=config.get("log_max_bytes", 1_000_000),
        backupCount=config.get("log_backup_count", 3),
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    LOGGER.setLevel(getattr(logging, config.get("log_level", "INFO")))
    LOGGER.addHandler(handler)
    LOGGER.propagate = False


def _match_from_dict(item: Any) -> Match:
    if not isinstance(item, dict):
        raise ValueError("Each report match must be an object")
    string_fields = (
        "event_id", "name", "home_team", "away_team", "state", "status", "clock", "kickoff", "venue"
    )
    if any(not isinstance(item.get(field), str) for field in string_fields):
        raise ValueError("Report match is missing required text fields")
    for field in ("home_score", "away_score"):
        if isinstance(item.get(field), bool) or not isinstance(item.get(field), int) or item[field] < 0:
            raise ValueError(f"Report match {field} must be a non-negative integer")
    if not isinstance(item.get("completed"), bool):
        raise ValueError("Report match completed flag must be a boolean")
    return Match(**{field: item[field] for field in (*string_fields, "home_score", "away_score", "completed")})


def _make_handler(config: dict[str, Any]) -> type[BaseHTTPRequestHandler]:
    class FootballMonitorHandler(BaseHTTPRequestHandler):
        server_version = "FootballMonitor/1.0"

        def log_message(self, format_string: str, *args: Any) -> None:
            LOGGER.info("http_client=%s %s", self.client_address[0], format_string % args)

        def _send(self, status: HTTPStatus, body: bytes, content_type: str, extra_headers: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if extra_headers:
                for name, value in extra_headers.items():
                    self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status: HTTPStatus, data: dict[str, Any]) -> None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            if parsed.path == "/":
                self._send_asset("index.html", config)
            elif parsed.path.startswith("/static/"):
                name = parsed.path.removeprefix("/static/")
                if name not in {"app.js", "style.css"}:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "Asset not found"})
                else:
                    self._send_asset(name)
            elif parsed.path == "/api/matches":
                self._get_matches(parse_qs(parsed.query, strict_parsing=False))
            else:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "Route not found"})

        def _send_asset(self, name: str, app_config: dict[str, Any] | None = None) -> None:
            try:
                content = (ASSETS / name).read_text(encoding="utf-8") if name == "index.html" else None
            except OSError as exc:
                LOGGER.error("asset_read_failed name=%s error=%s", name, exc)
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Web asset unavailable"})
                return
            if content is not None and app_config is not None:
                scope = "day" if app_config.get("default_match_scope") == "day" else "season"
                status = app_config.get("default_status_filter", "All")
                content = content.replace(
                    'data-default-scope="season"',
                    f'data-default-scope="{scope}"',
                    1,
                )
                content = content.replace(
                    'data-default-status="All"',
                    f'data-default-status="{status}"',
                    1,
                )
                content = content.replace(
                    'data-default-report="csv"',
                    f'data-default-report="{app_config.get("default_report_format", "csv")}"',
                    1,
                )
                content = content.replace(
                    'data-favorite-team=""',
                    f'data-favorite-team="{escape(str(app_config.get("favorite_team", "")), quote=True)}"',
                    1,
                )
                body = content.encode("utf-8")
            else:
                try:
                    body = (ASSETS / name).read_bytes()
                except OSError as exc:
                    LOGGER.error("asset_read_failed name=%s error=%s", name, exc)
                    self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Web asset unavailable"})
                    return
            content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
            charset = "; charset=utf-8" if content_type.startswith(("text/", "application/javascript")) else ""
            self._send(HTTPStatus.OK, body, content_type + charset)

        def _get_matches(self, query: dict[str, list[str]]) -> None:
            try:
                date_text = query.get("date", [None])[0]
                scope = query.get("scope", ["season"])[0]
                if date_text is None:
                    raise ValueError("Query parameter 'date' is required in YYYY-MM-DD format")
                match_date = date.fromisoformat(date_text)
                if scope == "season":
                    payload, duration_ms = fetch_season_to_date(
                        config["endpoint"], config["timeout_seconds"], match_date
                    )
                elif scope == "day":
                    payload, duration_ms = fetch_scoreboard(
                        scoreboard_url(config["endpoint"], match_date), config["timeout_seconds"]
                    )
                else:
                    raise ValueError("Query parameter 'scope' must be 'season' or 'day'")
                result = evaluate_scoreboard(
                    payload, config["max_goal_difference_for_alert"], duration_ms
                )
                matches = [match.to_dict() for match in result.matches]
                results_count = sum(match.completed for match in result.matches)
                live_count = sum(match.state == "in" for match in result.matches)
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "fetched_at": result.fetched_at,
                        "duration_ms": result.duration_ms,
                        "match_count": len(matches),
                        "fixture_count": sum(
                            not match.completed and match.state != "in" for match in result.matches
                        ),
                        "result_count": results_count,
                        "live_match_count": live_count,
                        "matches": matches,
                    },
                )
                LOGGER.info(
                    "matches_retrieved scope=%s cutoff=%s matches=%d fixtures=%d results=%d live=%d",
                    scope,
                    match_date.isoformat(),
                    len(matches),
                    len(matches) - results_count - live_count,
                    results_count,
                    live_count,
                )
            except (ValueError, RuntimeError) as exc:
                LOGGER.error("matches_retrieval_failed error=%s", exc)
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

        def do_POST(self) -> None:
            if urlsplit(self.path).path != "/api/report":
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "Route not found"})
                return
            try:
                length_text = self.headers.get("Content-Length", "")
                if not length_text.isdigit():
                    raise ValueError("A valid Content-Length header is required")
                length = int(length_text)
                if length > MAX_REQUEST_BYTES:
                    self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Report request is too large"})
                    return
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("Report request must be a JSON object")
                file_format = payload.get("format")
                if not isinstance(file_format, str) or file_format not in {"csv", "json"}:
                    raise ValueError("Report format must be csv or json")
                matches_data = payload.get("matches")
                filters = payload.get("filters")
                if not isinstance(matches_data, list) or not isinstance(filters, dict):
                    raise ValueError("Report request must include matches and filters")
                if any(not isinstance(value, str) for value in filters.values()):
                    raise ValueError("Report filter values must be text")
                favorite_team = filters.get("bias_team", "")
                fetched_at = payload.get("fetched_at")
                duration_ms = payload.get("duration_ms")
                if not isinstance(fetched_at, str):
                    raise ValueError("Report request must include the scoreboard fetch time")
                if isinstance(duration_ms, bool) or not isinstance(duration_ms, int) or duration_ms < 0:
                    raise ValueError("Report duration must be a non-negative integer")
                matches = [_match_from_dict(item) for item in matches_data]
                report = create_report(matches, filters, fetched_at, duration_ms, favorite_team)
                with tempfile.TemporaryDirectory() as temporary_directory:
                    report_path = Path(temporary_directory) / f"football_report.{file_format}"
                    save_report(report_path, report)
                    body = report_path.read_bytes()
                content_type = "text/csv; charset=utf-8" if file_format == "csv" else "application/json; charset=utf-8"
                self._send(
                    HTTPStatus.OK,
                    body,
                    content_type,
                    {"Content-Disposition": f'attachment; filename="football_report.{file_format}"'},
                )
                LOGGER.info(
                    "report_generated format=%s matches=%d status=%s team=%s bias_team=%s",
                    file_format,
                    len(matches),
                    filters.get("status", "All"),
                    filters.get("team", ""),
                    filters.get("bias_team", ""),
                )
            except (ValueError, json.JSONDecodeError, OSError) as exc:
                LOGGER.error("report_generation_failed error=%s", exc)
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    return FootballMonitorHandler


def create_server(host: str, port: int, config: dict[str, Any]) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), _make_handler(config))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Football Monitor web application.")
    parser.add_argument("--host", default=None, help="Interface to bind to (overrides web_host in config).")
    parser.add_argument("--port", type=int, default=None, help="HTTP port (overrides web_port in config).")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser automatically.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Path to JSON configuration.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = load_config(args.config)
        configure_file_logging(config)
        host = args.host or config.get("web_host", "127.0.0.1")
        port = args.port if args.port is not None else config.get("web_port", 8000)
        if not 1 <= port <= 65535:
            raise ValueError("Port must be between 1 and 65535")
        server = create_server(host, port, config)
    except (OSError, ValueError) as exc:
        print(f"Could not start Football Monitor web app: {exc}", file=sys.stderr)
        return 1

    address, port = server.server_address[:2]
    url = f"http://{address if ':' not in address else f'[{address}]'}:{port}/"
    LOGGER.info("web_application_started url=%s", url)
    print(f"Football Monitor is available at {url}. Press Ctrl+C to stop.")
    if not args.no_browser and config.get("open_browser", True):
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("web_application_stopping")
    finally:
        server.server_close()
        for handler in LOGGER.handlers[:]:
            handler.close()
            LOGGER.removeHandler(handler)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
