#!/usr/bin/env python3
"""
db_publish.py — Push fantasy.db + generated views to the fantasy-snapshots GitHub repo.

Reuses the same Contents-API pattern as league_snapshot.py — token from config.json,
no local git clone needed. Pages serves both:

    https://willrphillips.github.io/fantasy-snapshots/data/fantasy.db
    https://willrphillips.github.io/fantasy-snapshots/views/team_review.md
    ... etc

Usage:
    python3 db_publish.py                  # push db + all views
    python3 db_publish.py --db-only        # push only the .db
    python3 db_publish.py --views-only     # push only the markdown views

Cron-friendly. Idempotent — files commit only if SHA changed.
"""
import argparse
import base64
import datetime as dt
import json
import logging
import os
import sqlite3
import time
from pathlib import Path

import requests

# HTTP statuses worth retrying (transient GitHub / network hiccups). 409 = sha
# conflict, handled by refetching the sha on the next attempt.
_RETRYABLE = {408, 409, 429, 500, 502, 503, 504}

# GitHub also returns 403 for things that ARE transient: the push-rule validator timing out on a
# large blob, and secondary rate limits. A permission failure is 403 too, so match on the message
# rather than the status, and let anything else 403 stay fatal.
_RETRYABLE_403 = ("timed out validating", "secondary rate limit",
                  "abuse detection", "please try again", "try again later")

DB_PATH = Path(os.path.expanduser("~/fantasy-bot/fantasy.db"))
VIEWS_DIR = Path(os.path.expanduser("~/fantasy-bot/public/views"))
CONFIG_PATH = Path(os.path.expanduser("~/fantasy-bot/config.json"))
LOG_PATH = Path(os.path.expanduser("~/fantasy-bot/publish.log"))

REPO_OWNER = "willrphillips"
REPO_NAME = "fantasy-snapshots"
BRANCH = "main"

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("publish")


def _gh_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _get_existing_sha(token, path):
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{path}"
    r = requests.get(url, headers=_gh_headers(token), params={"ref": BRANCH}, timeout=20)
    if r.status_code == 200:
        return r.json().get("sha")
    return None


def _alert_publish(msg: str, subject: str = "FAILURE: db_publish.py"):
    """Failure-only notification. Throttled one/day by notify.alert."""
    try:
        from notify import alert
        alert("db_publish", subject, msg)
    except Exception as e:
        log.error(f"alert dispatch failed: {e}")


def _check_freshness():
    """Bail if stats are stale — prevents publishing yesterday-or-older
    data when the 3:30 AM ingest failed. Returns the latest date string
    if fresh; alerts and returns None if stale. An empty / missing db
    is treated as 'not stale' so init / first-run scenarios don't
    block."""
    if not DB_PATH.exists():
        return ""
    try:
        with sqlite3.connect(DB_PATH) as c:
            row = c.execute(
                "SELECT MAX(date_pulled) FROM hitting_stats"
            ).fetchone()
    except Exception as e:
        log.error(f"freshness check failed to read db: {e}")
        return ""  # don't block on a read error; let main path try
    latest = row[0] if row else None
    if not latest:
        return ""
    yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    if latest < yesterday:
        msg = (
            f"hitting_stats latest date = {latest}, expected >= "
            f"{yesterday}. Refusing to push stale data to GitHub. "
            f"Investigate why mlb_ingest didn't run or didn't write "
            f"yesterday's snapshot."
        )
        log.error(f"STALE: {msg}")
        _alert_publish(
            msg,
            subject=f"STALE: db_publish.py refused to run (latest={latest})",
        )
        return None
    return latest


def commit_file(token, local_path: Path, repo_path: str, message: str,
                timeout: int = 30, attempts: int = 4):
    """
    PUT a file to repo at `repo_path`. Binary or text — we base64 either way.
    Retries transient failures (timeouts, 5xx, sha conflicts) with exponential
    backoff so one network blip on the large fantasy.db doesn't skip the whole
    push. Returns True if committed, None if local file missing, False if it
    still fails after `attempts`.

    NB: the db is base64'd into a single JSON body (~1.35x its size). If this
    ever exceeds GitHub's Contents-API ceiling for good (not just a blip),
    escalate to gzipping the db or the Git Data (blob) API — retries won't help
    a hard size rejection.
    """
    if not local_path.exists():
        log.warning(f"missing local file: {local_path}")
        return None
    data = local_path.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{repo_path}"

    delay, last = 3, ""
    for attempt in range(1, attempts + 1):
        sha = _get_existing_sha(token, repo_path)      # refetch each try (handles 409)
        body = {"message": message, "content": b64, "branch": BRANCH}
        if sha:
            body["sha"] = sha
        try:
            r = requests.put(url, headers=_gh_headers(token),
                             data=json.dumps(body), timeout=timeout)
        except requests.RequestException as e:          # timeout / connection reset
            last = f"{type(e).__name__}: {e}"
            log.warning(f"{repo_path} attempt {attempt}/{attempts} network error: {last}")
        else:
            if r.status_code in (200, 201):
                log.info(f"committed {repo_path} ({len(data)} bytes) on attempt {attempt}")
                return True
            last = f"HTTP {r.status_code} {r.text[:200]}"
            body_low = r.text.lower()
            transient = (r.status_code in _RETRYABLE
                         or (r.status_code == 403
                             and any(s in body_low for s in _RETRYABLE_403)))
            if not transient:
                log.error(f"commit failed {repo_path}: {last} (non-retryable)")
                return False
            log.warning(f"{repo_path} attempt {attempt}/{attempts}: {last}")
        if attempt < attempts:
            time.sleep(delay)
            delay = min(delay * 2, 30)
    log.error(f"commit failed {repo_path} after {attempts} attempts: {last}")
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-only", action="store_true")
    ap.add_argument("--views-only", action="store_true")
    ap.add_argument("--force-stale", action="store_true",
                    help="bypass the freshness gate (for manual publish)")
    args = ap.parse_args()

    if not args.force_stale and _check_freshness() is None:
        return 1

    try:
        cfg = json.loads(CONFIG_PATH.read_text())
        token = cfg.get("github_token") or cfg.get("gh_token")
        if not token:
            log.error("no github_token in config.json")
            _alert_publish("no github_token in config.json")
            return 1
    except Exception as e:
        log.error(f"config.json unreadable: {e}")
        _alert_publish(f"config.json unreadable: {e}")
        return 1

    pushed = 0
    failed = []

    if not args.views_only:
        # The db is the one file big enough to trip GitHub's push-rule validator, so it gets more
        # patience than a 6 KB markdown view does.
        r = commit_file(token, DB_PATH, "data/fantasy.db",
                         "nightly: update fantasy.db", timeout=120, attempts=6)
        if r is True:
            pushed += 1
        elif r is False:
            failed.append("data/fantasy.db")

    if not args.db_only and VIEWS_DIR.exists():
        for md in sorted(VIEWS_DIR.glob("*.md")):
            r = commit_file(token, md, f"views/{md.name}",
                            f"nightly: update {md.name}")
            if r is True:
                pushed += 1
            elif r is False:
                failed.append(f"views/{md.name}")

    log.info(f"publish done: {pushed} file(s) committed")
    print(f"OK: {pushed} files pushed")

    if failed:
        _alert_publish(
            "GitHub commit failed (HTTP error) for:\n  "
            + "\n  ".join(failed)
            + f"\n\n{pushed} file(s) did push. See publish.log."
        )
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:
        import traceback
        _alert_publish(
            f"db_publish.py crashed: {e}\n\n{traceback.format_exc()[-3000:]}"
        )
        raise
