# Football Monitor

A lightweight Python command-line monitor for English Premier League fixtures and scores from ESPN's public scoreboard API. It normalizes ESPN's nested JSON into match records and raises a close-match alert for live games whose score difference is within a configurable threshold.

## Requirements

- Python 3.10 or newer
- Internet access to `site.api.espn.com`

The project uses only the Python standard library.

## Run

From the project directory:

```powershell
.\.venv\Scripts\python.exe -m football_monitor
```

Or, with a system Python installation:

```powershell
python -m football_monitor
```

Use `--json` for machine-readable output, `--config path\to\config.json` to select a configuration file, and `--log-level DEBUG` to increase diagnostic detail. Select a match day with `--date YYYY-MM-DD`; ESPN returns the fixtures scheduled for that day as well as completed match results and their final scores. Without `--date`, the monitor uses ESPN's default scoreboard response. Normal scoreboard output goes to standard output; operational logs go to standard error. The process exits with code `0` on success and `1` on configuration, network, HTTP, or response-data failures.

Examples:

```powershell
python -m football_monitor --date 2026-09-19
python -m football_monitor --date 2026-09-19 --json
```

Launch the desktop version with `python -m football_monitor.gui`. By default, the GUI retrieves all matches in the current season through the selected cutoff date, then lets you filter the displayed matches by team or by Scheduled, Played, or Live status. Choose **Single date** to retrieve only one calendar day. Save the filtered report as CSV or JSON. Desktop activity is recorded in `logs/football_monitor.log`.

## Web application

Start the browser-based app with `python -m football_monitor.web`. It serves the interface at `http://127.0.0.1:8000` and opens that address in your browser. The web app offers the same season-to-date or single-date retrieval, team and match-status filters, and CSV/JSON report downloads. Activity is recorded in `logs/football_monitor.log`. Use `--no-browser` to suppress automatic browser launch; `--host`, `--port`, and `--config` can customize the server.

## Configuration

`config.json`:

| Key | Meaning |
| --- | --- |
| `endpoint` | HTTPS URL for the scoreboard API |
| `timeout_seconds` | Maximum time to wait for the API response |
| `max_goal_difference_for_alert` | Alert when a **live** match's absolute score difference is at or below this non-negative integer |
| `log_level` | Default Python logging level (`DEBUG`, `INFO`, `WARNING`, or `ERROR`) |
| `web_host` / `web_port` | Local web server bind address and port; CLI `--host` and `--port` override them |
| `open_browser` | Open the web app in a browser on startup |
| `default_match_scope` / `default_status_filter` | Initial GUI/web scope (`season` or `day`) and match filter |
| `default_report_format` | Initially selected web report format, `csv` or `json` |
| `favorite_team` | Default fan-bias team, selectable from the teams in retrieved matches; empty disables bias |
| `log_file` / `log_max_bytes` / `log_backup_count` | Rotating activity log destination and retention |

The output includes `fixture_count` and `result_count` alongside the combined match list. A fixture is any event not marked complete by ESPN; completed events are reported as results with their final scores. Fan comments are generated from the selected team's actual score and match state: the message distinguishes wins, draws, losses, live leads/ties/trails, and upcoming fixtures. Select any team under **Fan bias**, or choose **No bias** to remove comments. `favorite_team` sets the initial selection. Bias affects commentary only; ESPN scores and results remain unchanged. The alert is informational and does not send notifications. Scheduled and completed matches are displayed but never trigger close-match alerts.

## Automation and monitoring

Run the command from Task Scheduler or another scheduler to poll the endpoint. Use `--json` when a downstream job consumes the output, and check the exit code to detect failed runs. Each run logs `scoreboard_fetch` with success/failure, API latency, match count, live match count, and alert count. Keep the configured polling interval reasonable and follow ESPN's applicable terms and service availability.

Example:

```powershell
python -m football_monitor --json
if ($LASTEXITCODE -ne 0) { Write-Error "Football Monitor failed" }
```

## Tests

Run the unit tests without making network calls:

```powershell
python -m unittest discover -s tests -v
```

Tests cover JSON normalization, the live-match alert rule, empty scoreboards, and invalid event data.
