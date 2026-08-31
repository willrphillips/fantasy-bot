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
| `anomaly.py` | Nightly digest of standout single-game lines (8th view) |
| `db_publish.py` | Push fantasy.db + views to GitHub |
| `health_check.py` | Independent watchdog (freshness, coverage, URL) |

## Initial setup

> **Historical.** These steps describe the original 2026-05 bring-up on the
> iMac "Cocky-Claude". The runtime moved to Hetzner (atlas-cloud) on
> 2026-07-21 and the iMac is no longer the deploy target. Kept because the
> backfill and schema steps are still the right recipe on a fresh box; read
> `MIGRATION_2026-07-21.md` first.

```bash
# On atlas-cloud, as edwincode
cd ~/fantasy-bot          # symlink to ~/edwin-repos/fantasy-bot, a real git clone

# 1. Install deps into the venv
./venv/bin/pip install -r requirements.txt

# 2. Deploy is `git pull`, not scp. The runtime IS a checkout.
git pull

# 3. Create the schema
./venv/bin/python3 db_init.py

# 4. ONE-TIME BACKFILL — walks Opening Day to yesterday.
#    Takes ~30-90 min depending on roster + FA pool size.
#    Run overnight or in a screen session.
nohup ./venv/bin/python3 mlb_ingest.py --backfill > ~/fantasy-bot/backfill.log 2>&1 &

# 5. After backfill finishes, generate views + push once to verify
./venv/bin/python3 views.py
./venv/bin/python3 db_publish.py

# 6. The schedule is already in place (see below)
```

## Schedule

No crontab. Four systemd timers plus three loops inside Edwin's bot.

```
# systemd, on atlas-cloud, pinned America/New_York.  systemctl list-timers 'fantasy-*'
fantasy-ingest.timer    03:30 ET   mlb_ingest.py
fantasy-views.timer     04:30 ET   views.py
fantasy-anomaly.timer   04:45 ET   anomaly.py
fantasy-health.timer    06:00 ET   health_check.py
fantasy-shutdown.timer  2026-10-01 end of season

# inside edwin.service, so `list-timers` will never show these
04:00       nightly_advisor.py   morning brief
05:07       db_publish.py        gzipped db + views to GitHub
every 30m   roster_triage.py     in-game lineup fixes, game window only
```

Unit files live in `/etc/systemd/system/`. The old iMac crontab is recorded
in `MIGRATION_2026-07-21.md`; do not re-create it.

`anomaly.py` writes into the same `public/views/` dir, so `db_publish.py`
(which globs `*.md`) picks it up automatically — no publish change needed.

## Daily flow

```
3:00 AM  league_snapshot.py runs (existing)            -> snapshot.md
3:30 AM  mlb_ingest.py runs                            -> fantasy.db row per player
4:30 AM  views.py runs                                 -> public/views/*.md
4:45 AM  anomaly.py runs                               -> public/views/anomaly_digest.md
5:00 AM  db_publish.py pushes to GitHub                -> data/fantasy.db + views/*.md
6:00 AM  health_check.py runs                          -> failure-only Discord alert

...then, all day:
every 30m  roster_triage.py                             -> in-game lineup fixes
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
  https://willrphillips.github.io/fantasy-snapshots/views/pull_status.md
  https://willrphillips.github.io/fantasy-snapshots/views/anomaly_digest.md
```

## Claude Code workflow (on the PC, not on atlas-cloud)

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

## Syncing your local copy (VS Code)

This code repo is **`willrphillips/fantasy-bot`**. Your local working copy may
be a folder named **`mlbstats`** — same project, different local name. Confirm
with `git remote -v` (it should show `fantasy-bot.git`). Edit in VS Code → push;
work merged on GitHub → pull.

```bash
git status                 # check for local edits first
git stash                  # only if there are uncommitted changes (CLAUDE.md often is)
git pull origin main       # bring down the latest
git stash pop              # only if you stashed; resolve any conflict
```

To force-match GitHub and discard local edits:
`git fetch origin && git reset --hard origin/main`.

The data repo (`willrphillips/fantasy-snapshots`) is separate — `db_publish.py`
pushes to it; you don't pull it into this repo.

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

# Preview tonight's anomaly digest without writing the file
./venv/bin/python3 anomaly.py --stdout

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
