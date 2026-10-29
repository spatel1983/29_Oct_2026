"use strict";

const dateInput = document.querySelector("#match-date");
const scopeInput = document.querySelector("#scope");
const teamInput = document.querySelector("#team-search");
const favoriteTeamInput = document.querySelector("#favorite-team");
const statusInput = document.querySelector("#status-filter");
const retrieveButton = document.querySelector("#retrieve");
const reportFormatInput = document.querySelector("#report-format");
const reportButton = document.querySelector("#download-report");
const tableBody = document.querySelector("#matches");
const summary = document.querySelector("#summary");
const lastUpdated = document.querySelector("#last-updated");
const toast = document.querySelector("#toast");

let matches = [];
let fetchedAt = "";
let durationMs = 0;
let toastTimer;

if (document.body.dataset.defaultScope === "day") scopeInput.value = "day";
if (["All", "Scheduled", "Played", "Live"].includes(document.body.dataset.defaultStatus)) {
  statusInput.value = document.body.dataset.defaultStatus;
}
if (["csv", "json"].includes(document.body.dataset.defaultReport)) {
  reportFormatInput.value = document.body.dataset.defaultReport;
}
const configuredFavoriteTeam = document.body.dataset.favoriteTeam || "";

const today = new Date();
dateInput.value = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;

function showToast(message, isError = false) {
  toast.textContent = message;
  toast.classList.toggle("error", isError);
  toast.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("visible"), 3500);
}

function addCell(row, value, className = "") {
  const cell = document.createElement("td");
  cell.textContent = value;
  if (className) cell.className = className;
  row.append(cell);
}

function visibleMatches() {
  const team = teamInput.value.trim().toLocaleLowerCase();
  const status = statusInput.value;
  return matches.filter((match) => {
    if (status === "Scheduled" && (match.completed || match.state === "in")) return false;
    if (status === "Played" && !match.completed) return false;
    if (status === "Live" && match.state !== "in") return false;
    return !team
      || match.home_team.toLocaleLowerCase().includes(team)
      || match.away_team.toLocaleLowerCase().includes(team);
  });
}

function renderMatches() {
  const filtered = visibleMatches();
  tableBody.replaceChildren();
  if (!filtered.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 7;
    cell.className = "empty";
    cell.textContent = matches.length ? "No matches match those filters." : "No matches found for this selection.";
    row.append(cell);
    tableBody.append(row);
  } else {
    for (const match of filtered) {
      const row = document.createElement("tr");
      addCell(row, match.status, "status");
      addCell(row, match.home_team);
      addCell(row, `${match.home_score} - ${match.away_score}`, "score");
      addCell(row, match.away_team);
      addCell(row, match.state === "in" ? match.clock : match.kickoff);
      addCell(row, match.venue || "—");
      const comment = matchComment(match, favoriteTeamInput.value);
      addCell(row, comment || "—", comment ? "fan-comment" : "");
      tableBody.append(row);
    }
  }
  const played = filtered.filter((match) => match.completed).length;
  const live = filtered.filter((match) => match.state === "in").length;
  const scheduled = filtered.length - played - live;
  summary.textContent = `Showing ${filtered.length} match(es): ${scheduled} scheduled, ${played} played, ${live} live.`;
  reportButton.disabled = !fetchedAt;
}

function matchComment(match, favoriteTeam) {
  if (!favoriteTeam) return "";
  let favoriteScore;
  let opponentScore;
  if (match.home_team.toLocaleLowerCase() === favoriteTeam.toLocaleLowerCase()) {
    favoriteScore = match.home_score;
    opponentScore = match.away_score;
  } else if (match.away_team.toLocaleLowerCase() === favoriteTeam.toLocaleLowerCase()) {
    favoriteScore = match.away_score;
    opponentScore = match.home_score;
  } else {
    return "";
  }
  const score = `${favoriteScore}-${opponentScore}`;
  if (match.completed) {
    if (favoriteScore > opponentScore) {
      return `${favoriteTeam} win ${score}! Congratulations, ${favoriteTeam} fans!`;
    }
    if (favoriteScore === opponentScore) return `${favoriteTeam} draw ${score} - a point earned.`;
    return `Tough result: ${favoriteTeam} lose ${score}. Next match!`;
  }
  if (match.state === "in") {
    if (favoriteScore > opponentScore) return `${favoriteTeam} lead ${score} - keep it going!`;
    if (favoriteScore === opponentScore) return `${favoriteTeam} level ${score} - push for the winner!`;
    return `${favoriteTeam} trail ${score} - come on, turn it around!`;
  }
  return `Upcoming fixture - backing ${favoriteTeam}!`;
}

function updateFavoriteTeams() {
  const previous = favoriteTeamInput.value;
  const teams = [...new Set(matches.flatMap((match) => [match.home_team, match.away_team]))]
    .sort((left, right) => left.localeCompare(right));
  favoriteTeamInput.replaceChildren(new Option("No bias", ""));
  for (const team of teams) favoriteTeamInput.add(new Option(team, team));
  if (teams.includes(previous)) {
    favoriteTeamInput.value = previous;
  } else if (teams.includes(configuredFavoriteTeam)) {
    favoriteTeamInput.value = configuredFavoriteTeam;
  } else {
    favoriteTeamInput.value = "";
  }
  favoriteTeamInput.disabled = teams.length === 0;
}

async function retrieveMatches() {
  const selectedDate = dateInput.value;
  if (!selectedDate) {
    showToast("Select a date or season cutoff first.", true);
    dateInput.focus();
    return;
  }
  retrieveButton.disabled = true;
  retrieveButton.textContent = "Retrieving…";
  summary.textContent = "Retrieving matches from ESPN…";
  try {
    const parameters = new URLSearchParams({ date: selectedDate, scope: scopeInput.value });
    const response = await fetch(`/api/matches?${parameters}`, { cache: "no-store" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `Request failed with HTTP ${response.status}`);
    matches = data.matches;
    updateFavoriteTeams();
    fetchedAt = data.fetched_at;
    durationMs = data.duration_ms;
    lastUpdated.textContent = `Retrieved ${fetchedAt} · ${durationMs} ms`;
    renderMatches();
    showToast(`Retrieved ${matches.length} match(es).`);
  } catch (error) {
    summary.textContent = "Match retrieval failed.";
    showToast(error.message, true);
  } finally {
    retrieveButton.disabled = false;
    retrieveButton.textContent = "Retrieve matches";
  }
}

async function downloadReport(format) {
  const reportMatches = visibleMatches();
  const filters = {
    date: dateInput.value,
    scope: scopeInput.value,
    status: statusInput.value,
    team: teamInput.value.trim(),
    bias_team: favoriteTeamInput.value,
  };
  try {
    const response = await fetch("/api/report", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ format, filters, fetched_at: fetchedAt, duration_ms: durationMs, matches: reportMatches }),
    });
    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.error || `Report failed with HTTP ${response.status}`);
    }
    const blob = await response.blob();
    const disposition = response.headers.get("Content-Disposition") || "";
    const filename = disposition.match(/filename="([^"]+)"/)?.[1] || `football_report.${format}`;
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    showToast(`Downloaded ${filename}.`);
  } catch (error) {
    showToast(error.message, true);
  }
}

retrieveButton.addEventListener("click", retrieveMatches);
teamInput.addEventListener("input", renderMatches);
statusInput.addEventListener("change", renderMatches);
favoriteTeamInput.addEventListener("change", renderMatches);
reportButton.addEventListener("click", () => downloadReport(reportFormatInput.value));
retrieveMatches();
