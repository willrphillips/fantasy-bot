#!/usr/bin/env python3
"""Pull historical matchup data from ESPN for Captain Phillips league."""

import csv
import json
import base64
import sys
from datetime import datetime
from pathlib import Path

import requests
from espn_api.baseball import League

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
LOCAL_CSV = SCRIPT_DIR / "historical_matchups.csv"

LEAGUE_ID = 2057904545
YEAR = 2026

REPO_OWNER = "willrphillips"
REPO_NAME = "fantasy-snapshots"
REPO_PATH = "historical_matchups.csv"
REPO_BRANCH = "main"

ALL_CATS = ["AVG", "R", "HR", "RBI", "SB", "K", "W", "SV", "HLD", "ERA", "WHIP"]


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def extract_value(stats_dict, cat):
    """Stats come as {'AVG': {'value': 0.229, 'result': 'WIN'}, ...}"""
    if cat not in stats_dict:
        return None
    entry = stats_dict[cat]
    if isinstance(entry, dict):
        return entry.get("value")
    return entry


def extract_result(stats_dict, cat):
    """Returns 'WIN' / 'LOSS' / 'TIE' / None for a given category."""
    if cat not in stats_dict:
        return None
    entry = stats_dict[cat]
    if isinstance(entry, dict):
        return entry.get("result")
    return None


def count_results(stats_dict):
    """ESPN already tells us who won each cat. Trust their answer."""
    wins = losses = ties = 0
    for cat in ALL_CATS:
        r = extract_result(stats_dict, cat)
        if r == "WIN":
            wins += 1
        elif r == "LOSS":
            losses += 1
        elif r == "TIE":
            ties += 1
    return wins, losses, ties


def commit_to_repo(content_bytes, github_token):
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{REPO_PATH}"
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
    }
    sha = None
    r = requests.get(url, headers=headers, params={"ref": REPO_BRANCH})
    if r.status_code == 200:
        sha = r.json().get("sha")
    elif r.status_code != 404:
        print(f"WARN: GitHub GET {r.status_code}: {r.text}")

    payload = {
        "message": f"Update historical matchups ({datetime.utcnow().isoformat()}Z)",
        "content": base64.b64encode(content_bytes).decode("ascii"),
        "branch": REPO_BRANCH,
    }
    if sha:
        payload["sha"] = sha

    r = requests.put(url, headers=headers, json=payload)
    if r.status_code in (200, 201):
        print(f"OK: committed {REPO_PATH} to GitHub")
        return True
    else:
        print(f"FAIL: GitHub PUT {r.status_code} -> {r.text}")
        return False


def main():
    print("Loading config...")
    cfg = load_config()
    espn_s2 = cfg["espn_s2"]
    swid = cfg["swid"]
    github_token = cfg.get("github_token")

    print(f"Connecting to league {LEAGUE_ID} for year {YEAR}...")
    league = League(
        league_id=LEAGUE_ID,
        year=YEAR,
        espn_s2=espn_s2,
        swid=swid,
    )
    print(f"League: {league.settings.name}")
    print(f"Current matchup period: {league.currentMatchupPeriod}")

    last_completed = league.currentMatchupPeriod - 1
    if last_completed < 1:
        print("No completed periods yet.")
        return

    print(f"Pulling periods 1 through {last_completed}...")

    rows = []
    for period in range(1, last_completed + 1):
        try:
            box_scores = league.box_scores(matchup_period=period)
        except Exception as e:
            print(f"  period {period}: ERROR {e}")
            continue

        print(f"  period {period}: {len(box_scores)} matchups")
        for bs in box_scores:
            home_team = bs.home_team
            away_team = bs.away_team
            if home_team is None or away_team is None:
                continue

            home_stats = bs.home_stats or {}
            away_stats = bs.away_stats or {}

            home_wins, home_losses, home_ties = count_results(home_stats)
            away_wins, away_losses, away_ties = count_results(away_stats)

            home_name = getattr(home_team, "team_name", str(home_team))
            away_name = getattr(away_team, "team_name", str(away_team))

            home_row = {
                "period": period,
                "team": home_name,
                "opponent": away_name,
                "is_home": 1,
                "cats_won": home_wins,
                "cats_lost": home_losses,
                "cats_tied": home_ties,
            }
            for c in ALL_CATS:
                home_row[f"team_{c}"] = extract_value(home_stats, c)
                home_row[f"opp_{c}"] = extract_value(away_stats, c)
            rows.append(home_row)

            away_row = {
                "period": period,
                "team": away_name,
                "opponent": home_name,
                "is_home": 0,
                "cats_won": away_wins,
                "cats_lost": away_losses,
                "cats_tied": away_ties,
            }
            for c in ALL_CATS:
                away_row[f"team_{c}"] = extract_value(away_stats, c)
                away_row[f"opp_{c}"] = extract_value(home_stats, c)
            rows.append(away_row)

    if not rows:
        print("No data extracted.")
        sys.exit(1)

    fieldnames = (
        ["period", "team", "opponent", "is_home", "cats_won", "cats_lost", "cats_tied"]
        + [f"team_{c}" for c in ALL_CATS]
        + [f"opp_{c}" for c in ALL_CATS]
    )

    with open(LOCAL_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {len(rows)} rows to {LOCAL_CSV}")
    print("\nFirst row:")
    for k, v in rows[0].items():
        print(f"  {k}: {v}")

    if github_token:
        with open(LOCAL_CSV, "rb") as f:
            content = f.read()
        commit_to_repo(content, github_token)
    else:
        print("\nNo github_token in config; skipping repo push.")


if __name__ == "__main__":
    main()
