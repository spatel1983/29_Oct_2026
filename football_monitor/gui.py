"""Tkinter desktop interface for the ESPN Premier League scoreboard."""

from __future__ import annotations

import logging
import os
import queue
import sys
import threading
from datetime import date
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import BOTH, END, LEFT, X, Button, Entry, Frame, Label, StringVar, Tk, TclError, ttk, filedialog, messagebox
from typing import Any

from football_monitor.cli import DEFAULT_CONFIG, load_config
from football_monitor.core import (
    Match,
    evaluate_scoreboard,
    fetch_scoreboard,
    fetch_season_to_date,
    scoreboard_url,
)
from football_monitor.reports import (
    STATUS_FILTERS,
    create_report,
    filter_matches,
    match_comment,
    save_report,
)

LOGGER = logging.getLogger("football_monitor.gui")
LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "football_monitor.log"


def configure_file_logging(config: dict[str, Any]) -> None:
    log_path = Path(config.get("log_file", str(LOG_PATH)))
    if not log_path.is_absolute():
        log_path = LOG_PATH.parent.parent / log_path
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


def configure_tcl_runtime() -> None:
    tcl_directory = Path(sys.base_prefix) / "tcl"
    tcl_library = tcl_directory / "tcl8.6"
    tk_library = tcl_directory / "tk8.6"
    if (tcl_library / "init.tcl").is_file():
        os.environ.setdefault("TCL_LIBRARY", str(tcl_library))
    if (tk_library / "tk.tcl").is_file():
        os.environ.setdefault("TK_LIBRARY", str(tk_library))


class FootballMonitorApp:
    def __init__(self, root: Tk, config: dict[str, Any]) -> None:
        self.root = root
        self.config = config
        self.results: list[Match] = []
        self.filtered_matches: list[Match] = []
        self.favorite_teams: list[str] = []
        self.fetched_at = ""
        self.duration_ms = 0
        self.pending: queue.Queue[tuple[str, Any]] = queue.Queue()

        root.title("Football Monitor - Premier League")
        root.geometry("1380x620")
        root.minsize(980, 420)

        filters = Frame(root, padx=12, pady=10)
        filters.pack(fill=X)
        Label(filters, text="Date / season cutoff:").pack(side=LEFT)
        self.date_var = StringVar(value=date.today().isoformat())
        self.date_entry = Entry(filters, textvariable=self.date_var, width=13)
        self.date_entry.pack(side=LEFT, padx=(6, 14))
        self.scope_var = StringVar(
            value="Single date" if config.get("default_match_scope") == "day" else "Season to date"
        )
        self.scope_box = ttk.Combobox(
            filters,
            textvariable=self.scope_var,
            values=("Season to date", "Single date"),
            state="readonly",
            width=14,
        )
        self.scope_box.pack(side=LEFT, padx=(0, 14))
        Label(filters, text="Team:").pack(side=LEFT)
        self.team_var = StringVar()
        self.team_entry = Entry(filters, textvariable=self.team_var, width=22)
        self.team_entry.pack(side=LEFT, padx=(6, 14))
        Label(filters, text="Fan bias:").pack(side=LEFT)
        default_favorite = str(config.get("favorite_team", "Liverpool"))
        self.favorite_var = StringVar(value=default_favorite)
        self.favorite_box = ttk.Combobox(
            filters,
            textvariable=self.favorite_var,
            values=("No bias",),
            state="readonly",
            width=18,
        )
        self.favorite_box.pack(side=LEFT, padx=(6, 14))
        Label(filters, text="Match type:").pack(side=LEFT)
        self.status_var = StringVar(value=config.get("default_status_filter", "All"))
        self.status_box = ttk.Combobox(
            filters, textvariable=self.status_var, values=STATUS_FILTERS, state="readonly", width=11
        )
        self.status_box.pack(side=LEFT, padx=(6, 14))
        self.refresh_button = Button(filters, text="Retrieve matches", command=self.refresh)
        self.refresh_button.pack(side=LEFT)
        self.report_button = Button(filters, text="Save report", command=self.export_report, state="disabled")
        self.report_button.pack(side=LEFT, padx=(8, 0))

        columns = ("status", "home", "score", "away", "date", "venue", "comment")
        self.table = ttk.Treeview(root, columns=columns, show="headings", height=18)
        headings = {
            "status": ("STATUS", 125),
            "home": ("HOME", 190),
            "score": ("SCORE", 85),
            "away": ("AWAY", 190),
            "date": ("KICKOFF", 175),
            "venue": ("VENUE", 240),
            "comment": ("FAN COMMENT", 260),
        }
        for column, (heading, width) in headings.items():
            self.table.heading(column, text=heading)
            self.table.column(column, width=width, anchor="w")
        scrollbar = ttk.Scrollbar(root, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scrollbar.set)
        self.table.pack(side=LEFT, fill=BOTH, expand=True, padx=(12, 0), pady=(0, 8))
        scrollbar.pack(side=LEFT, fill="y", padx=(0, 12), pady=(0, 8))

        self.summary_var = StringVar(value="Choose a date and retrieve matches.")
        Label(root, textvariable=self.summary_var, anchor="w", padx=12, pady=8).pack(fill=X)
        self.team_var.trace_add("write", self.apply_filters)
        self.status_var.trace_add("write", self.apply_filters)
        self.favorite_var.trace_add("write", self.apply_filters)
        self.root.after(100, self.check_pending)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def refresh(self) -> None:
        date_text = self.date_var.get().strip()
        try:
            match_date = date.fromisoformat(date_text) if date_text else None
            if match_date is None:
                raise ValueError("Enter a date in YYYY-MM-DD format.")
        except ValueError as exc:
            messagebox.showerror("Invalid input", str(exc), parent=self.root)
            return

        self.refresh_button.config(state="disabled")
        self.summary_var.set("Retrieving matches from ESPN...")
        scope = self.scope_var.get()
        LOGGER.info("refresh_started scope=%s date=%s", scope, date_text)
        worker = threading.Thread(target=self.retrieve, args=(match_date, scope), daemon=True)
        worker.start()

    def retrieve(self, match_date: date, scope: str) -> None:
        try:
            if scope == "Season to date":
                payload, duration_ms = fetch_season_to_date(
                    self.config["endpoint"], self.config["timeout_seconds"], match_date
                )
            else:
                url = scoreboard_url(self.config["endpoint"], match_date)
                payload, duration_ms = fetch_scoreboard(url, self.config["timeout_seconds"])
            result = evaluate_scoreboard(
                payload, self.config["max_goal_difference_for_alert"], duration_ms
            )
            self.pending.put(("success", result))
        except (ValueError, RuntimeError) as exc:
            self.pending.put(("error", str(exc)))

    def check_pending(self) -> None:
        try:
            outcome, value = self.pending.get_nowait()
        except queue.Empty:
            self.root.after(100, self.check_pending)
            return
        self.refresh_button.config(state="normal")
        if outcome == "error":
            LOGGER.error("refresh_failed error=%s", value)
            self.summary_var.set("Retrieval failed.")
            messagebox.showerror("Scoreboard error", str(value), parent=self.root)
        else:
            self.results = value.matches
            self.favorite_teams = sorted(
                {team for match in self.results for team in (match.home_team, match.away_team)},
                key=str.casefold,
            )
            previous_favorite = self.favorite_var.get()
            options = ("No bias", *self.favorite_teams)
            self.favorite_box.configure(values=options)
            if previous_favorite in options:
                self.favorite_var.set(previous_favorite)
            elif self.config.get("favorite_team") in options:
                self.favorite_var.set(self.config["favorite_team"])
            else:
                self.favorite_var.set("No bias")
            self.fetched_at = value.fetched_at
            self.duration_ms = value.duration_ms
            self.report_button.config(state="normal")
            LOGGER.info(
                "refresh_succeeded duration_ms=%d matches=%d",
                self.duration_ms,
                len(self.results),
            )
            self.apply_filters()
        self.root.after(100, self.check_pending)

    def apply_filters(self, *_: object) -> None:
        if not self.results and not self.fetched_at:
            return
        try:
            self.filtered_matches = filter_matches(
                self.results,
                self.status_var.get(),
                self.team_var.get(),
            )
        except ValueError as exc:
            LOGGER.error("filter_failed error=%s", exc)
            self.summary_var.set(str(exc))
            return

        self.table.delete(*self.table.get_children())
        for match in self.filtered_matches:
            time_label = match.clock if match.state == "in" else match.kickoff
            self.table.insert(
                "",
                END,
                values=(
                    match.status,
                    match.home_team,
                    f"{match.home_score} - {match.away_score}",
                    match.away_team,
                    time_label,
                    match.venue,
                    match_comment(match, self.selected_favorite_team),
                ),
            )
        results_count = sum(match.completed for match in self.filtered_matches)
        fixture_count = sum(
            not match.completed and match.state != "in" for match in self.filtered_matches
        )
        self.summary_var.set(
            f"Showing {len(self.filtered_matches)} match(es): {fixture_count} fixture(s), "
            f"{results_count} result(s), {sum(match.state == 'in' for match in self.filtered_matches)} live. "
            f"Fetched {self.fetched_at} in {self.duration_ms} ms."
        )
        LOGGER.info(
            "filter_applied status=%s team=%s favorite_team=%s visible_matches=%d",
            self.status_var.get(),
            self.team_var.get().strip(),
            self.selected_favorite_team or "none",
            len(self.filtered_matches),
        )

    @property
    def selected_favorite_team(self) -> str:
        selected = self.favorite_var.get()
        return selected if selected in self.favorite_teams else ""

    def export_report(self) -> None:
        if not self.fetched_at:
            return
        path_text = filedialog.asksaveasfilename(
            parent=self.root,
            title="Save filtered match report",
            defaultextension=".csv",
            filetypes=(("CSV report", "*.csv"), ("JSON report", "*.json")),
            initialfile="football_report.csv",
        )
        if not path_text:
            return
        path = Path(path_text)
        filters = {
            "date": self.date_var.get().strip(),
            "scope": self.scope_var.get(),
            "status": self.status_var.get(),
            "team": self.team_var.get().strip(),
            "bias_team": self.selected_favorite_team,
        }
        report = create_report(
            self.filtered_matches,
            filters,
            self.fetched_at,
            self.duration_ms,
            self.selected_favorite_team,
        )
        try:
            save_report(path, report)
        except (OSError, ValueError) as exc:
            LOGGER.error("report_export_failed path=%s error=%s", path, exc)
            messagebox.showerror("Report export failed", str(exc), parent=self.root)
            return
        LOGGER.info("report_exported path=%s matches=%d", path, len(self.filtered_matches))
        messagebox.showinfo("Report saved", f"Saved {len(self.filtered_matches)} match(es) to:\n{path}", parent=self.root)

    def close(self) -> None:
        for handler in LOGGER.handlers[:]:
            handler.close()
            LOGGER.removeHandler(handler)
        self.root.destroy()


def main() -> int:
    try:
        configure_tcl_runtime()
        config = load_config(DEFAULT_CONFIG)
        configure_file_logging(config)
    except (OSError, ValueError) as exc:
        print(f"Football Monitor startup error: {exc}", file=sys.stderr)
        return 1
    LOGGER.info("application_started")
    try:
        root = Tk()
    except TclError as exc:
        LOGGER.error("application_start_failed error=%s", exc)
        print(f"Could not start the desktop window: {exc}", file=sys.stderr)
        return 1
    FootballMonitorApp(root, config)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
