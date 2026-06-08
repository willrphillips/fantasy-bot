# fantasy-bot — Claude project notes

Read this first. It captures the load-bearing decisions and the
non-obvious constraints. The README documents how to run the pipeline;
this file documents how to reason about it.

## Operating preferences (how to respond)

These apply to every session, both Claude Code and Claude Chat.

- **Timestamp every reply.** Begin each response with a short timestamp
  in US Eastern time, e.g. `[2026-06-08 7:21 PM EDT]` (EDT in summer,
  EST in winter — let the clock decide).
- **Lead with the recommendation, then the numbers.** Be brief. Give the
  call (start/sit, add/drop, trade verdict) first, justify with data
  after. No hedging, no fantasy-is-random disclaimers.
- **Strategy C stands.** Hard-punt SV + SB. Prioritize HR/RBI (the weak
  cats); protect the W / ERA / WHIP / K edge. HLD is a scoring cat.
- **Confirm everything; never fabricate.** Ground every stat in
  `fantasy.db` / `snapshot.md`. Do NOT use live web stats to value
  players — this league is a simulated universe and diverges from
  real-world MLB. Cross-check db (System 2) against snapshot (System 1)
  instead; if a number can't be confirmed, say so.
- **Quantify roster/trade/waiver moves.** Run `playoff_odds.py` (refresh
  its `T`/`REC` from the latest snapshot first) and report the playoff-
  odds delta. Trust the scenario deltas over absolute levels.
- **Trust the ESPN app for position eligibility,** not the db
  `eligible_pos` strings (combo labels like "2B/SS" mislead).

## What this is

A data layer for ESPN fantasy baseball. The iMac "Cocky-Claude" pulls
MLB Stats API + Baseball Savant + ESPN league state every night and
stores a daily season-to-date snapshot per player. `fantasy.db` plus
seven pre-baked markdown views are published to a public GitHub repo
(`willrphillips/fantasy-snapshots`) at the end of every nightly cron
run. From there, both Claude Chat and Claude Code can read the data
without auth.

The owner of the league is "Captain Phillips" (team_id=9, league_id
2057904545, season=2026, 10-team head-to-head categories).

## Data flow

```
3:00  espn_nightly_moves.py + league_snapshot.py   (pre-existing, untouched)
3:30  mlb_ingest.py            -> fantasy.db rows for every tracked player
4:30  views.py                  -> public/views/*.md
4:45  anomaly.py                -> public/views/anomaly_digest.md
5:00  db_publish.py             -> push fantasy.db + views to GitHub
6:00  health_check.py           -> independent watchdog, fails -> email
```

The cron lives on the iMac. Failures are reported by email (failure-only,
throttled to one alert per script per day via `~/fantasy-bot/.alert_state`).

## Universe

Tracked players = every active MLB player from the season-roster index
(`/api/v1/sports/1/players?season=2026`, ~1100 players) UNIONed with
anyone on a Captain Phillips roster or in the top-200 ESPN FA pool
within the last 30 days. The pipeline covers more than just the
fantasy league; you can query any active MLB player.

## Schema (fantasy.db)

| Table | What it holds |
|---|---|
| `players` | bio per player (`mlb_id` PK, `name`, `team`, `primary_pos`, `last_tracked`) |
| `hitting_stats` | one row per (player, date) — **season-to-date** as of that date |
| `pitching_stats` | one row per (player, date) — **season-to-date** as of that date |
| `statcast` | season-to-date Statcast snapshot per (player, date, side) |
| `rosters` | snapshot of every ESPN team's roster, one row per (date, team, player) |
| `standings` | one row per (date, team) |
| `matchups` | one row per (date, period, home, away, category) |
| `fa_pool` | top-200 free agents per (date, player) |
| `id_map` | ESPN `playerId` → MLBAM `mlb_id` crosswalk + resolution cache |
| `pull_log` | one row per ingest run (mode, duration, counts, errors) |

**Critical:** `hitting_stats` and `pitching_stats` are
**cumulative**, not per-game. To get a window (L7, L14, L30, custom),
**subtract two snapshots** — today's row minus the row N days ago.
`fantasy_lib.window_stats(name, days=14)` does this for you.

`statcast` is current-snapshot only. Savant doesn't expose historical
daily data, so the statcast time series builds forward from
2026-05-21 (the day the BOM bug was fixed; earlier rows are absent
or were garbage and have been deleted).

## How to query

```python
import os
os.environ["FANTASY_DB"] = "/path/to/fantasy.db"   # downloaded copy
from fantasy_lib import (
    my_roster, roster, standings, matchups, fa_pool_latest,
    window_stats, windows_all, player, player_history,
    hot_bats, hot_arms, cold_bats,
    regression_watch, fip_era_gap,
    trade_scout, teams_list,
    health, latest_date, latest_roster_date,
)

my_roster()                                  # Captain Phillips
window_stats("Juan Soto", days=14)           # L14 by subtraction
hot_bats(days=14, n=20, fa_only=True)        # waiver targets
trade_scout("Bay County Buccaneers", sort="hr")
regression_watch("up", n=15)                 # xwOBA - wOBA gaps
health()                                     # freshness + row counts
```

`fantasy_lib` honors the `FANTASY_DB` env var so the same code works on
the iMac (live db) or on any machine with a downloaded copy.

## Three defects fixed (load-bearing — don't undo)

These were latent in the original chat-built code and were corrected on
2026-05-19. Any future change that touches the same areas must respect
the constraints below.

1. **`load_league()` requires `cfg["league_id"]`.** config.json didn't
   originally have the key, which made the script hard-crash. The fix
   added a `LEAGUE_ID_FALLBACK` constant and a try/except that returns
   `None` instead of raising on any missing key. Don't go back to
   raw subscript access for required cfg fields.

2. **ESPN `playerId` is not the MLBAM id.** The original code stored
   ESPN ids in `rosters.mlb_id`, then fed them to the MLB Stats API
   (404s) and joined them against statcast (which uses real MLB ids).
   The fix introduced the `id_map` crosswalk and a resolver
   (`resolve_mlb_id`) that uses the MLB season-roster index with team
   disambiguation. **Policy: ambiguous or no-match leaves `mlb_id`
   NULL and logs a WARNING.** Never silently mis-map. The watchdog
   surfaces roster gaps explicitly. The 13 unresolved players are
   injured/suspended FAs absent from the 2026 season index; they
   self-heal when they play.

3. **Date cadence is per-table.** Fantasy-state tables (`rosters`,
   `fa_pool`, `standings`, `matchups`) are tagged with the run date
   (today). Stat tables are tagged with the snapshot date (yesterday
   for nightly). `fantasy_lib` exposes `latest_roster_date()`,
   `latest_fa_date()`, `latest_standings_date()`,
   `latest_matchup_date()`, and `latest_date()` (the stats max). Use
   the right anchor for each query. `trade_scout` joins rosters at
   `latest_roster_date()` and stats at `latest_date()`.

## Other constraints to know

- **Savant CSV has a UTF-8 BOM** that breaks `csv.DictReader` quoted-
  field parsing. `fetch_savant_csv` strips it. If you ever change the
  Savant pull, keep the strip.
- **Alert subjects and bodies are ASCII-only.** `espn_utils.send_email`
  uses `smtplib.sendmail(msg.as_string())` which fails on non-ASCII in
  the Subject header.
- **Statcast inserts are `INSERT OR REPLACE` on UNIQUE(mlb_id, date,
  side).** A bad `mlb_id` value (e.g., the year "2026") will collapse
  every row of a side into one. If statcast row count looks tiny,
  check column extraction.
- **Matchup ingest reads `value`, not `score`, and `leader` comes from
  ESPN's `result` field.** espn_api box scores are `{CAT: {"value":
  float, "result": "WIN"|"LOSS"|"TIE"|None}}`. `fetch_fantasy_state`
  reads `value`, sets `leader` from the home `result` (so lower-is-
  better cats ERA/WHIP are correct — never re-derive `leader` from a raw
  value comparison), and **skips component stats** (AB/H/OUTS/ER/P_H/
  P_BB, `result=None`) so only the 11 scored cats persist. `standings`
  rank is pinned to ESPN's `team.standing`; `pct = (wins+0.5*ties)/gp`.
  Fixed 2026-06-05; don't undo.
- **fantasy.db is gitignored** in this repo. The data lives at
  `willrphillips/fantasy-snapshots`. Don't add it here.
- **`config.json` is gitignored.** It contains `espn_s2`, `swid`,
  `github_token`, `gmail_app_password`. Never commit. If it leaks,
  rotate immediately (log out of ESPN to invalidate cookies; revoke
  the GitHub token; generate a new Gmail app password).

## Public URLs

Data:
- `https://willrphillips.github.io/fantasy-snapshots/data/fantasy.db`

Views:
- `https://willrphillips.github.io/fantasy-snapshots/views/team_review.md`
- `https://willrphillips.github.io/fantasy-snapshots/views/waiver_hitters.md`
- `https://willrphillips.github.io/fantasy-snapshots/views/waiver_pitchers.md`
- `https://willrphillips.github.io/fantasy-snapshots/views/regression_watch.md`
- `https://willrphillips.github.io/fantasy-snapshots/views/trade_targets.md`
- `https://willrphillips.github.io/fantasy-snapshots/views/category_standings.md`
- `https://willrphillips.github.io/fantasy-snapshots/views/pull_status.md`
- `https://willrphillips.github.io/fantasy-snapshots/views/anomaly_digest.md`

## File map

| File | Purpose |
|---|---|
| `db_init.py` | One-time schema setup. `--reset` drops everything (destructive). |
| `mlb_ingest.py` | Daily + backfill ingest. Modes: `--backfill`, `--only-fantasy`, `--player NAME`, `--limit N`. |
| `fantasy_lib.py` | Query helper. Honors `FANTASY_DB` env var. |
| `views.py` | Generate the seven core markdown reports. |
| `anomaly.py` | Build `anomaly_digest.md` — standout single-game lines vs season baseline. Daily delta via 1-day snapshot subtraction (INNER JOIN to prior snapshot, never COALESCE-to-zero). Writes into the views dir; `db_publish` globs it. |
| `db_publish.py` | Push fantasy.db + views to GitHub via Contents API. |
| `health_check.py` | Independent watchdog. Reads only; alerts on freshness, coverage, errors, URL reachability. |
| `notify.py` | `alert(script, subject, body)`. Failure-only, throttled. |
| `espn_utils.py` | ESPN league plumbing (cookies, transactions, `send_email`). Lives on the iMac; included here for reference. |

## When something breaks

1. Check `~/fantasy-bot/ingest.log` (or `views.log`, `publish.log`,
   `health.log`) — every cron run appends.
2. `pull_log` table has structured counts per run. The most recent row
   tells you what happened last night.
3. `health_check.py` is the canonical "is everything OK" command —
   prints `OK: health check passed (DATE)` and exit 0 when green.
4. If you've changed the schema, drop the db (`rm fantasy.db`),
   re-init (`python3 db_init.py`), and re-run a backfill. The
   `id_map` and `players` tables rebuild from the MLB index on the
   next ingest.
