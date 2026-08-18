#!/usr/bin/env python3
"""
health_check.py — Independent watchdog for the fantasy-bot data pipeline.

Runs from its own cron line AFTER ingest/views/publish. It deliberately
imports nothing from the pipeline (only stdlib + notify) so a bug in
mlb_ingest / views / fantasy_lib cannot blind the watchdog.

It is the ONLY thing that catches "cron never fired at all" — the in-script
alerts in mlb_ingest/views/db_publish can't fire if the script never ran.

Checks (all failure-only; one throttled email/day via notify.alert):
    1. fantasy.db exists
    2. Freshness        — latest stat snapshot == yesterday
    3. pull_log         — today's run finalized; ALL logged errors surfaced
    4. Roster coverage  — every Captain Phillips player tracked in the last
                           7 days has a snapshot at the latest date (catches
                           an ace silently skipped with zero logged errors)
    5. Views fresh      — public/views/*.md modified within 26h
    6. Public URL       — GitHub Pages fantasy.db returns HTTP 200

Usage:
    python3 health_check.py
"""
import datetime as dt
import os
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

DB_PATH = Path(os.path.expanduser("~/fantasy-bot/fantasy.db"))
VIEWS_DIR = Path(os.path.expanduser("~/fantasy-bot/public/views"))
PUBLIC_DB_URL = "https://willrphillips.github.io/fantasy-snapshots/data/fantasy.db.gz"
MY_TEAM = "Captain Phillips"

VIEWS_MAX_AGE_SEC = 26 * 3600
URL_TIMEOUT_SEC = 20


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _scalar(c, sql, params=()):
    row = c.execute(sql, params).fetchone()
    return row[0] if row else None


def check_db_exists(problems):
    if not DB_PATH.exists():
        problems.append(f"fantasy.db missing at {DB_PATH}")
        return False
    return True


def check_freshness(c, problems, yesterday):
    latest = _scalar(c, "SELECT MAX(date_pulled) FROM hitting_stats")
    if not latest:
        problems.append("hitting_stats is empty - no snapshots at all")
        return None
    if latest < yesterday:
        problems.append(
            f"STALE: latest stat snapshot is {latest}, expected {yesterday}. "
            f"Nightly ingest likely did not run or died before writing."
        )
    return latest


def check_pull_log(c, problems, today):
    row = c.execute(
        "SELECT * FROM pull_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row:
        problems.append("pull_log empty - ingest has never finalized a run")
        return
    d = dict(row)
    if (d.get("date_pulled") or "") < today:
        problems.append(
            f"No pull_log row for today ({today}); newest is "
            f"{d.get('date_pulled')} mode={d.get('mode')}. "
            f"Nightly cron did not fire."
        )
    if not d.get("end_ts"):
        problems.append(
            f"Last pull_log row (date={d.get('date_pulled')}) has no end_ts "
            f"- run did not finalize cleanly."
        )
    errs = d.get("errors") or 0
    if errs > 0:
        problems.append(
            f"Last pull ({d.get('date_pulled')} {d.get('mode')}) logged "
            f"{errs} error(s). Check ingest.log."
        )


def check_roster_coverage(c, problems, latest_stats):
    """An ace can be skipped with ZERO logged errors (failed fetch returns
    None -> player silently skipped). This is the safety net for that."""
    if not latest_stats:
        return
    roster_date = _scalar(
        c,
        "SELECT MAX(date_pulled) FROM rosters WHERE team_name = ?",
        (MY_TEAM,),
    )
    if not roster_date:
        problems.append(
            f"No roster snapshot for '{MY_TEAM}' - ESPN roster pull failing."
        )
        return
    players = c.execute(
        """
        SELECT DISTINCT mlb_id, player_name
        FROM rosters
        WHERE date_pulled = ? AND team_name = ? AND mlb_id IS NOT NULL
        """,
        (roster_date, MY_TEAM),
    ).fetchall()

    cutoff = (
        dt.date.fromisoformat(latest_stats) - dt.timedelta(days=7)
    ).isoformat()
    missing = []
    for p in players:
        mid = p["mlb_id"]
        tracked_recently = _scalar(
            c,
            """
            SELECT 1 FROM (
                SELECT date_pulled FROM hitting_stats
                WHERE mlb_id = ? AND date_pulled >= ?
                UNION
                SELECT date_pulled FROM pitching_stats
                WHERE mlb_id = ? AND date_pulled >= ?
            ) LIMIT 1
            """,
            (mid, cutoff, mid, cutoff),
        )
        if not tracked_recently:
            continue  # never tracked recently - not a regression
        has_latest = _scalar(
            c,
            """
            SELECT 1 FROM (
                SELECT 1 FROM hitting_stats
                WHERE mlb_id = ? AND date_pulled = ?
                UNION
                SELECT 1 FROM pitching_stats
                WHERE mlb_id = ? AND date_pulled = ?
            ) LIMIT 1
            """,
            (mid, latest_stats, mid, latest_stats),
        )
        if not has_latest:
            missing.append(p["player_name"])

    if missing:
        problems.append(
            f"ROSTER GAP: {len(missing)} of your players were tracked in the "
            f"last 7d but have NO snapshot at {latest_stats}:\n  "
            + "\n  ".join(sorted(missing))
        )


def check_views_fresh(problems):
    if not VIEWS_DIR.exists():
        problems.append(f"views dir missing: {VIEWS_DIR}")
        return
    mds = list(VIEWS_DIR.glob("*.md"))
    if not mds:
        problems.append(f"no *.md in {VIEWS_DIR} - views.py never produced output")
        return
    newest = max(m.stat().st_mtime for m in mds)
    age = time.time() - newest
    if age > VIEWS_MAX_AGE_SEC:
        hrs = age / 3600
        problems.append(
            f"views stale: newest *.md is {hrs:.1f}h old "
            f"(limit {VIEWS_MAX_AGE_SEC / 3600:.0f}h). views.py likely dead."
        )


def check_public_url(problems):
    try:
        req = urllib.request.Request(PUBLIC_DB_URL, method="GET")
        with urllib.request.urlopen(req, timeout=URL_TIMEOUT_SEC) as resp:
            code = resp.getcode()
            resp.read(1024)
        if code != 200:
            problems.append(f"public db URL returned HTTP {code}: {PUBLIC_DB_URL}")
    except Exception as e:
        problems.append(
            f"public db URL unreachable ({type(e).__name__}: {e}): "
            f"{PUBLIC_DB_URL}. Publish or GitHub Pages broken."
        )


def main():
    today = dt.date.today().isoformat()
    yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    problems = []

    if check_db_exists(problems):
        with _conn() as c:
            latest_stats = check_freshness(c, problems, yesterday)
            check_pull_log(c, problems, today)
            check_roster_coverage(c, problems, latest_stats)

    check_views_fresh(problems)
    check_public_url(problems)

    if not problems:
        print(f"OK: health check passed ({today})")
        return 0

    body = (
        f"Pipeline health check found {len(problems)} problem(s) "
        f"on {today}:\n\n"
        + "\n\n".join(f"- {p}" for p in problems)
    )
    print(body, file=sys.stderr)
    try:
        from notify import alert
        alert(
            "health_check",
            f"FAILURE: pipeline health check - {len(problems)} problem(s)",
            body,
        )
    except Exception as e:
        print(f"alert dispatch failed: {e}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
