# Fantasy Bot — Data Layer

SQLite-based MLB + ESPN fantasy ingest. Builds a daily-snapshot time series
of every active MLB player (~1110) plus the ESPN league roster + FA pool.
Computes L7 / L14 / L30 / any window on demand via subtraction of
season-to-date snapshots.

**Related repo:** generated data + nightly markdown views are published to
[willrphillips/fantasy-snapshots](https://github.com/willrphillips/fantasy-snapshots) —
that's where `db_publish.py` and `league_snapshot.py` push their output every
night. The chat project and Claude Code on mobile read from that repo's
GitHub Pages URLs. See [`CLAUDE.md`](CLAUDE.md) in this repo for the
canonical project context, schema, and load-bearing decisions.

## Files

| File | Purpose |
|------|---------|
| `db_init.py` | One-time schema setup |
| `mlb_ingest.py` | Daily + backfill ingest (MLB API + Savant + ESPN) |
| `fantasy_lib.py` | Query helper for Claude Code / interactive use |
| `views.py` | Generate pre-baked Markdown reports |
| `db_publish.py` | Push fantasy.db + views to GitHub |

## Initial setup

```bash
# On Cocky-Claude (the iMac)
cd ~/fantasy-bot

# 1. Make sure deps are installed in the existing venv
./venv/bin/pip install requests   # espn_api already there

# 2. Drop the files into place (SCP from your laptop)
#    db_init.py, mlb_ingest.py, fantasy_lib.py, views.py, db_publish.py

# 3. Create the schema
./venv/bin/python3 db_init.py

# 4. ONE-TIME BACKFILL — walks Opening Day to yesterday.
#    Takes ~30-90 min depending on roster + FA pool size.
#    Run overnight or in a screen session.
nohup ./venv/bin/python3 mlb_ingest.py --backfill > ~/fantasy-bot/backfill.log 2>&1 &

# 5. After backfill finishes, generate views + push once to verify
./venv/bin/python3 views.py
./venv/bin/python3 db_publish.py

# 6. Add the nightly cron entry (see below)
```

## Cron entry

Add to `crontab -e`:

```
# Existing 3 AM jobs (nightly_moves + snapshot) stay as-is.

# 3:30 AM ET — daily MLB+ESPN ingest
30 3 * * * cd /Users/claudeserver/fantasy-bot && /Users/claudeserver/fantasy-bot/venv/bin/python3 mlb_ingest.py >> /Users/claudeserver/fantasy-bot/ingest.log 2>&1

# 3:55 AM ET — regenerate pre-baked views
55 3 * * * cd /Users/claudeserver/fantasy-bot && /Users/claudeserver/fantasy-bot/venv/bin/python3 views.py >> /Users/claudeserver/fantasy-bot/views.log 2>&1

# 4:00 AM ET — push db + views to GitHub
0 4 * * * cd /Users/claudeserver/fantasy-bot && /Users/claudeserver/fantasy-bot/venv/bin/python3 db_publish.py >> /Users/claudeserver/fantasy-bot/publish.log 2>&1
```

## Daily flow

```
3:00 AM  league_snapshot.py runs (existing)            -> snapshot.md
3:30 AM  mlb_ingest.py runs                            -> fantasy.db row per player
3:55 AM  views.py runs                                 -> public/views/*.md
4:00 AM  db_publish.py pushes to GitHub                -> data/fantasy.db + views/*.md
```

## Public URLs

After publish, all data is fetchable without auth:

```
Database file:
  https://willrphillips.github.io/fantasy-snapshots/data/fantasy.db
  (raw fallback: https://raw.githubusercontent.com/willrphillips/fantasy-snapshots/main/data/fantasy.db)

Pre-baked views:
  https://willrphillips.github.io/fantasy-snapshots/views/team_review.md
  https://willrphillips.github.io/fantasy-snapshots/views/waiver_hitters.md
  https://willrphillips.github.io/fantasy-snapshots/views/waiver_pitchers.md
  https://willrphillips.github.io/fantasy-snapshots/views/regression_watch.md
  https://willrphillips.github.io/fantasy-snapshots/views/trade_targets.md
  https://willrphillips.github.io/fantasy-snapshots/views/category_standings.md
  https://willrphillips.github.io/fantasy-snapshots/views/roster_optimize.md
  https://willrphillips.github.io/fantasy-snapshots/views/pull_status.md
```

## Claude Code workflow (on PC, not iMac)

```bash
git clone https://github.com/willrphillips/fantasy-snapshots
cd fantasy-snapshots
# data/fantasy.db is right there

# In a Python session next to fantasy_lib.py:
FANTASY_DB=$(pwd)/data/fantasy.db python3
>>> from fantasy_lib import *
>>> health()
>>> hot_bats(days=14, n=20, fa_only=True)
>>> window_stats("Juan Soto", days=14)
>>> regression_watch('up')
```

`fantasy_lib.py` honors `FANTASY_DB` env var so it works against the downloaded
copy without modification.

## Manual operations

```bash
# Force-refresh fantasy state only (skip MLB pull)
./venv/bin/python3 mlb_ingest.py --only-fantasy

# Backfill one specific player from Opening Day (new call-up etc.)
./venv/bin/python3 mlb_ingest.py --player "Roman Anthony"

# Backfill custom range
./venv/bin/python3 mlb_ingest.py --backfill --from-date 2026-04-15 --to-date 2026-05-01

# Generate just one view
./venv/bin/python3 views.py --only team_review

# Push only the db (skip views)
./venv/bin/python3 db_publish.py --db-only

# Health check
./venv/bin/python3 fantasy_lib.py
```

## Notes

- **Windows are computed by subtraction**: today's season-to-date row minus
  the row from N days ago. For the first 30 days after backfill, longer
  windows depend on backfill history.
- **New call-ups** are auto-discovered when they appear in the FA pool or on
  any fantasy roster. First night they're tracked, the script mini-backfills
  them from Opening Day forward.
- **Statcast** is season-to-date snapshots only — Savant doesn't expose
  historical daily data. Time series builds forward from first ingest.
- **DB size**: ~5 MB after a full season. Comfortably under GitHub's 100 MB
  file limit for years.
- **The 100 MB hard limit is per file.** SQLite stays well below this. If
  pull_log or rosters tables grow unexpectedly, VACUUM the file.
- **Reuses `~/fantasy-bot/config.json`** — same `espn_s2`, `swid`, `github_token`
  as `league_snapshot.py`. No new credentials needed.

## Troubleshooting

```bash
# Did tonight's pull run?
tail -50 ~/fantasy-bot/ingest.log

# What did it pull?
sqlite3 ~/fantasy-bot/fantasy.db \
  "SELECT * FROM pull_log ORDER BY id DESC LIMIT 1"

# How many days of history do I have for Soto?
sqlite3 ~/fantasy-bot/fantasy.db \
  "SELECT COUNT(DISTINCT date_pulled) FROM hitting_stats \
   WHERE mlb_id = (SELECT mlb_id FROM players WHERE name = 'Juan Soto')"

# Force-rerun views without re-ingesting
./venv/bin/python3 views.py && ./venv/bin/python3 db_publish.py --views-only
```
