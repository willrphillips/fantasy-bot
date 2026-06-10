# SCOPE_OF_WORK

Dated status log for the fantasy-bot data pipeline (the iMac MLB
ingest + publish system). The pre-existing `espn_nightly_moves`,
`league_snapshot`, and `espn_weekly_report` jobs are out of scope.

> **STATUS: COMPLETE & LIVE (2026-06-05).** Nightly cron runs
> ingest → views → anomaly digest → publish → health check on the iMac;
> `fantasy.db` + 8 markdown views publish to GitHub Pages each morning
> and return 200. All known data-correctness defects are fixed and
> verified against live ESPN (see entries below). No open work items.
> Future changes must respect the locked decisions recorded here and in
> `CLAUDE.md`.

## 2026-06-05 — matchups + standings defects fixed (the two that "stood")

The two data-correctness defects flagged on 2026-06-04 are now fixed in
`mlb_ingest.py` `fetch_fantasy_state`, verified against live ESPN.

- **`matchups` table populated.** Root cause: the box-score parser read
  a nonexistent `score` key, so every `home_value`/`away_value` landed
  NULL and every `leader` defaulted to `'tied'`. espn_api exposes
  `home_stats`/`away_stats` as `{CAT: {"value": float, "result":
  "WIN"|"LOSS"|"TIE"|None}}`. Fix: read `value`; derive `leader` from
  the home team's `result` (this correctly handles lower-is-better cats
  like ERA/WHIP — a raw value compare would invert them); and **skip the
  6 component stats** (AB, H, OUTS, ER, P_H, P_BB) which carry
  `result=None` and are not scored categories. 11 scored cats × 5
  matchups = 55 rows/day. Verified: values present, leaders correct.
- **`standings.rank` matches ESPN.** rank is now pinned to ESPN's
  authoritative `team.standing` (not the loop index), and `pct` counts
  ties as half a win `(wins + 0.5*ties)/gp` — matching ESPN's H2H
  category win%. Verified: CP rank 6, pct .520 (was .465 ignoring ties).
- **One-time scrub.** The morning's buggy nightly had already written 30
  stale NULL component-cat rows for 2026-06-05; deleted them
  (`DELETE FROM matchups WHERE date_pulled=MAX AND home_value IS NULL`)
  and republished. Future nightlies are clean by construction.
- **Diagnostic-first.** Wrote a throwaway `_diag_espn.py` to print the
  real espn_api `box_scores`/standings shapes before editing — fixed
  against ground truth, not a guess. Removed after.
- **Locked decision:** matchup `leader` derives from ESPN's `result`
  field, not a value comparison. Don't revert to value-compare; it
  silently inverts ratio categories.

## 2026-06-04 — finalize: anomaly digest, repo cleanup, heartbeat confirmed

Ingest/publish confirmed live: nightly pull 21 (2026-06-04, mode
nightly, 0 errors, 14.4 min), db + views published to GitHub Pages at
09:01 GMT, public URLs return 200. NOTE — "0 errors" means the run
finalized; it does not mean every derived table is correct.
`OPERATING_RUNBOOK.md` (added in parallel work) records two real
data-correctness defects that, as of this entry, still stood: the
`matchups` table was empty (all `leader='tied'`, values NULL) and
`standings.rank` did not match ESPN's tiebreakers. **Both were fixed the
next day — see the 2026-06-05 entry above.** Flagged here so the "live"
claim wasn't read as "everything correct." The stat / window / waiver /
anomaly paths were already sound. Three finishing items completed.

- **Anomaly digest added (`anomaly.py`).** Builds an 8th view,
  `anomaly_digest.md` — the standout single-game hitting and pitching
  lines from the most recent game day, each shown against the player's
  season-to-date baseline. Implemented as a deterministic Python script
  (chosen over a Claude-Code agent: consistent with the all-Python
  pipeline, no per-run LLM cost, version-controlled, deployable to the
  iMac). Daily line = latest snapshot minus the prior snapshot.
  **Load-bearing: INNER JOIN to the prior snapshot, never
  COALESCE-to-zero** — a LEFT JOIN would report a player's whole season
  as one game whenever the prior-day row is missing. Honesty: baselines
  are season-to-date only (no career data in this db; never claims
  "career best"). Writes into `public/views/`, so `db_publish.py`'s
  `*.md` glob and `health_check.py`'s freshness glob both pick it up
  with no change. New cron line at 4:45 (after views, before publish).
  Verified live against the published 2026-06-03 db: sensible output,
  correct slash baselines. Fixed a latent cross-platform bug — writes
  now force `encoding="utf-8"` (the digest uses em dash / ≤; the iMac is
  UTF-8 but `write_text` defaults to the platform codec).
- **Repo cleanup.** Removed two superseded tracked docs:
  `fantasy baseball instructions.txt` and
  `fantasy_baseball_project_context.md`. The canonical
  `fantasy_baseball_instructions.md` states verbatim that it supersedes
  the `.txt`; the `project_context.md` was the same older content.
  Kept `fantasy_baseball_instructions.md` (canonical chat context) and
  `CHAT_PROJECT_INSTRUCTIONS.md` (the distinct claude.ai paste). Also
  committed the previously-untracked `SUGGESTED_AGENTS.md`. `files.zip`
  stays gitignored (build artifact). Parallel work's
  `OPERATING_RUNBOOK.md` referenced the deleted `.txt` by name in three
  places; repointed all three to the canonical
  `fantasy_baseball_instructions.md` (which preserves the `.txt`'s rules
  verbatim) so no reference dangles.
- **Heartbeat decision confirmed, not changed.** Reviewed the
  failure-only alerting design and kept it. Silence = green is
  acceptable because `health_check.py` is the independent watchdog and
  is the one thing that catches "cron never fired at all." No daily or
  weekly heartbeat added — see the locked decision below, which stands.

## 2026-05-21 — universe expansion, statcast fix

- **Statcast BOM bug fixed.** Savant's CSV ships a UTF-8 BOM
  (`﻿`) that broke `csv.DictReader`'s detection of the leading
  quoted field (`"last_name, first_name"`). Header columns shifted by
  one, `player_id` ended up holding the year ("2026") for every row,
  and the UNIQUE constraint collapsed all 643 daily inserts into one
  row per side. `fetch_savant_csv` now strips the BOM before parsing.
  Verified live: 266 unique batter ids in post-fix output, statcast
  table now grows by 643 rows/day. The 6 garbage rows
  (`mlb_id=2026`) were deleted.
- **Universe expanded to all active MLB players.** New helpers in
  `mlb_ingest.py`: `fetch_mlb_player_records()` (one API call returns
  every season-roster player) and `populate_mlb_universe(cur, today,
  records, counts)` (upserts every MLB player into `players` and
  refreshes `last_tracked`). `tracked_player_ids` unions the universe
  with rosters + fa_pool. Smoke-tested on 5 players; full bulk
  backfill launched via nohup (PID 31804, ETA ~8 hours).
- **Cron rescheduled.** Ingest at ~1110 players will run ~25-30 min,
  so the chain was bumped: 3:30 ingest, 4:30 views, 5:00 publish,
  6:00 health_check. Existing 3:00 nightly_moves+snapshot and Sun
  7PM weekly email jobs unchanged.

## 2026-05-19 — initial deploy + three defect fixes

- **Three latent defects in the chat-built code identified and fixed
  in production:**
  1. `cfg["league_id"]` KeyError — `config.json` never had the key.
     Added the field to config and a `LEAGUE_ID_FALLBACK` constant in
     `mlb_ingest.py`. `load_league()` now degrades gracefully on any
     missing key.
  2. ESPN `playerId` is not the MLBAM id. New `id_map` crosswalk
     table + resolver using the MLB season-roster index, name-
     normalized, team-disambiguated. **Policy: ambiguous-or-no-match
     is left UNRESOLVED (mlb_id NULL) and logged WARNING; never
     silently mis-mapped.**
  3. Date skew between fantasy-state tables (run date) and stat
     tables (snapshot date) made every roster/FA report empty.
     Added per-table `latest_*_date()` helpers in `fantasy_lib` and
     decoupled the joins. `trade_scout` rebuilt to anchor rosters at
     `latest_roster_date()` and stats at `latest_date()`.
- **Initial backfill complete.** 393 players × ~54 days → 10,945
  hitting + 8,699 pitching rows, errors 0, duration 2h54m
  (README's "30-90 min" estimate was wrong).
- **Failure-alert layer.** `notify.py` (per-script throttled email,
  ASCII-only because `send_email` chokes on non-ASCII subjects).
  Crash + degradation alerts wired into `mlb_ingest.py`, `views.py`,
  and `db_publish.py`. Self-tested by the `league_id` KeyError
  before that defect was fixed — the alerter sent a real email.
- **`health_check.py` watchdog.** Reads only. Checks DB freshness,
  pull_log errors, Captain Phillips roster coverage (the "ace
  silently missing" guard), views freshness < 26h, and the public
  GitHub Pages URL HTTP 200. Verified green on first run.
- **Cron installed** (3:30 / 3:55 / 4:00 / 5:00) preserving the
  existing jobs. Updated 2026-05-21 to the new times.
- **GitHub Pages URL verified live.** db + views publish nightly and
  the public URL returns 200.

## Locked decisions

These are the load-bearing architectural choices for this pipeline.
Any reversal must be logged here with a date and a reason.

- **Failure-only alerting.** No daily heartbeats. One throttled email
  per script per day via `~/fantasy-bot/.alert_state`. The state file
  is updated only on successful send, so a transient SMTP failure
  doesn't burn the daily slot. Health check is the independent
  watchdog; in-script alerts cover their own scripts only.
- **Unresolved beats mis-mapped.** When the ESPN→MLB resolver can't
  uniquely identify a player (ambiguous duplicate name with no team
  disambiguation), it leaves `mlb_id` NULL and logs a WARNING rather
  than guessing. The watchdog roster-coverage check then surfaces
  the gap explicitly. The user's stated priority ("an ace getting
  silently missed") drove this.
- **Per-table date cadence in fantasy_lib.** Fantasy-state queries
  use their own `latest_*_date()`; stat windows stay anchored on
  `hitting_stats.latest_date()`. The two feeds are not assumed to
  share a date.
- **All-MLB universe.** Tracked set is every active MLB player from
  the season-roster index, unioned with ESPN rosters + FA pool.
  Pipeline serves more than fantasy needs; Claude Chat / Code can
  query any current MLB player.
- **Snapshots, not game-by-game.** `hitting_stats` and
  `pitching_stats` store season-to-date totals per (player, date).
  Windows are computed by subtraction. Statcast is current-snapshot
  only; history builds forward from 2026-05-21.

## Layout

- `~/fantasy-bot/` on the iMac is the runtime home. Cron jobs run
  from there. `config.json` is gitignored — never commit.
- This repo holds source code only. Data files (`fantasy.db`, views)
  publish to `willrphillips/fantasy-snapshots` via `db_publish.py`.
- Public URLs:
  - `https://willrphillips.github.io/fantasy-snapshots/data/fantasy.db`
  - `https://willrphillips.github.io/fantasy-snapshots/views/*.md`
