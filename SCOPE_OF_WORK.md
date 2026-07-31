# SCOPE_OF_WORK

Dated status log for the fantasy-bot data pipeline (the iMac MLB
ingest + publish system). The pre-existing `espn_nightly_moves`,
`league_snapshot`, and `espn_weekly_report` jobs are out of scope.

> **STATUS: COMPLETE & LIVE (2026-06-05).** Nightly cron runs
> ingest → views → anomaly digest → publish → health check on the iMac;
> `fantasy.db` + 8 markdown views publish to GitHub Pages each morning
> and return 200. All known data-correctness defects are fixed and
> verified against live ESPN (see entries below).
> Future changes must respect the locked decisions recorded here and in
> `CLAUDE.md`.
>
> ⚠️ **OPEN WORK ITEM (2026-06-10): ESPN write tooling is UNVALIDATED.**
> The roster-automation layer (`set_lineup.py`, `waiver_move.py`,
> `apply_pending.py`, plus `espn_utils.apply_lineup_moves` /
> `waiver_move`) is written and merged but has **never run against the
> live ESPN API** — this session's environment couldn't reach ESPN. It
> must be dry-run-validated, then `--apply`-tested once, on the iMac
> before any automation is trusted. See the 2026-06-10 entry below.
>
> 📋 **DESIGN LOCKED, BUILD DEFERRED (2026-07-20): daily auto-lineup +
> waiver-scan-to-Edwin.** Three decisions captured in the 2026-07-20 entry
> below; no code written yet (Will out of usage). The build depends on the
> write path above being validated first.
>
> 🧹 **QUEUED CLEANUP (2026-07-21, deferred until after usage resets):**
> remove the now-dead email alerting path — `send_email()` in
> `espn_utils.py`, and `gmail_address` / `gmail_app_password` /
> `smtp_host` / `smtp_port` / `recipient_email` from the iMac's
> `config.json`. Discord has fully replaced it for baseball (see the
> 2026-07-21 entry below); these are unused, not broken. Don't touch
> until asked.

## 2026-07-21 — baseball Discord alerting live; write path partially validated

Deployed to the iMac (`~/fantasy-bot`, reachable via Tailscale SSH as
`claudeserver@100.79.105.6` — **not a git repo**, deploy is `scp` file-by-file
until a real deploy mechanism is decided). Session covered:

- **Fixed the live stale-db bug.** `db_publish.py`'s retry logic (from
  `ad0e16b`, previously undeployed) fixed a real failure: a 502 on the 32 MB
  Contents-API PUT had left GitHub's `fantasy.db` a day stale while views kept
  publishing. Re-pushed; confirmed the Pages copy is byte-identical to local
  (32,174,080 bytes).
- **Deployed the write-tooling layer for the first time.** `fantasy_exec.py`
  (new portable module — see `INTEGRATION.md`), `set_lineup.py`,
  `waiver_move.py`, `apply_pending.py` were on GitHub but had never reached
  the iMac. Now deployed, syntax-checked on the box's Python 3.9.6.
- **Found and fixed a real bug via live testing.** Dry-running the *current*
  lineup back through `set_lineup()`/`fantasy_exec.set_lineup()` produced 7
  phantom moves (`Soto OF->OF`, `Yamamoto P->P`, ...). Cause: `espn_api`
  reports `lineupSlot` as a label, not a numeric ID, so every P collapses to
  slot 11 and every OF to slot 5 on read — the matcher was free to shuffle
  between them. Fixed in `fantasy_exec.py` (pin-then-match, plus a
  from-label==to-label filter); `set_lineup.py` on the iMac has the same
  underlying issue and was **left as-is** (see `INTEGRATION.md` §6 — two
  copies of the eligibility solver now exist; collapse onto `fantasy_exec`
  once it's fully proven).
- **Write path status: read verified, POST still unverified.** `whoami()`,
  `get_roster()`, and a `set_lineup(..., dry_run=True)` round-trip all ran
  live and correctly. **No transaction has ever been submitted** — `add_drop`
  and a real (non-empty) lineup change remain untested against live ESPN.
  Per standing rule, first `--apply` must happen with Will present.
- **Baseball Discord alerting is live**, replacing email. Added
  `discord_webhooks.alerts` to `config.json`, deployed the current
  `notify.py` (Discord-first, no email fallback — this is why deploying it
  *before* the webhook existed would have made alerts silently log-only), and
  confirmed with a real posted test message. `db_publish` / `mlb_ingest` /
  `health_check` throttle state preserved; only the manual test key was
  cleared.
- **Sunday Funday confirmed ESPN Fantasy Football** (`league_id
  1068408855`), not baseball — found via Gmail archaeology, `team_id` still
  unknown. **Do not wire this league's ids into `fantasy_exec.py` as-is**:
  `LeagueCtx.base_url` is hardcoded to the `flb` (baseball) API segment; a
  `sport` field must be added to the per-league config first or its
  transactions will target the wrong sport's endpoint. Cast Final Fantasy has
  no trace anywhere yet — fully unresourced. Both leagues **parked** per
  Will's explicit instruction; baseball only for now.
- **No deploy mechanism exists yet.** `~/fantasy-bot` has no `.git`; a push to
  GitHub `main` never reaches the iMac. Manual `scp` is the only path today.
  Decision on `deploy.sh` vs. converting to a real git checkout is **open,
  deliberately unmade** this session.

## 2026-07-20 — auto-lineup + waiver-scan design locked (NOT YET BUILT)

Will asked for two things: (1) a function that adjusts the roster
automatically each day based on who is playing and matchup projections,
and (2) Edwin to text him any waiver-wire moves he should make. Design
and the load-bearing choices were locked this session; **no code was
written — Will was out of usage and asked to record everything and
close.** Resume from here.

- **Decision 1 — lineup apply: auto, after one validation run.** The
  daily lineup computes and **auto-applies each morning on the iMac**
  (hands-off), EXCEPT the very first run is dry-run only so Will eyeballs
  the moves once — because the ESPN write path is still UNVALIDATED (see
  the open item above / 2026-06-10 entry). After one clean dry run, flip
  to auto. Rationale: a daily lineup is reversible next morning, so
  auto-apply is low-risk *once the write path is proven*. Lineup is the
  ONLY piece that auto-applies.
- **Decision 2 — start/sit logic: ESPN gates, fantasy.db ranks.** Use
  ESPN's per-day projection to decide **who is playing today** (bench any
  eligible player projected ~0 — off day / not playing), then **rank the
  players who are playing by fantasy.db L14 form** (`window_stats`)
  weighted for Strategy C (HR/RBI up, protect W/ERA/WHIP/K, HLD scores,
  punt SV/SB). Seat the top eligible into slots via the existing
  eligibility-safe `set_lineup.compute_moves()` — never bypass it.
  UNVERIFIED data question for the build: `espn_utils.parse_roster`
  currently reads the *season* projected breakdown from `stats[0]`
  (`projected_breakdown`). Confirm on the iMac that ESPN exposes a
  *per-scoring-period (daily)* projection — likely a different `stats`
  key — before trusting the "who's playing today" gate. If no reliable
  daily projection exists, fall back to ESPN's lineup-lock / start status
  as the "is he playing" signal.
- **Decision 3 — waiver delivery: Edwin relay on atlas-cloud. DEFERRED.**
  Waivers stay **propose-only / gated** (matches the handbook's
  always-gated principle and Will's own "moves I *should make*" framing —
  they are NOT auto-executed). Delivery path: the scan writes a
  recommendation file into the Dropbox-synced workspace; **Edwin**
  (Discord assistant on atlas-cloud, `edwin.service`) polls it and DMs
  Will in his own voice. **Queued for later — not this session.** For a
  first cut, the existing `espn_utils.send_email()` can carry the
  recommendations until the Edwin hook is wired.
- **Planned files (not yet written).** `auto_lineup.py` — pulls the
  roster, applies Decision 2 to pick starters, calls `compute_moves()`,
  applies via `apply_lineup_moves()` (dry-run on the first run).
  `waiver_scan.py` — ranks add/drops from `fantasy_lib` (`hot_bats` /
  `cold_bats` / `regression_watch`, FA-only) under Strategy C, writes the
  recommendation for Edwin. Both run from a new iMac cron line each
  morning **before the lineup lock**, after the nightly ingest/publish
  chain.
- **Approved starter list is the approved output.** An approved lineup
  remains a list of desired starters fed to `compute_moves()`;
  `auto_lineup.py` just generates that list instead of hand-authoring
  `starters.json`. The eligibility-safe planner and the gated
  `apply_pending.py` queue are unchanged and still authoritative.

## 2026-06-10 — ESPN write tooling added (NEEDS LIVE VALIDATION)

Built an opt-in roster-automation layer so an approved proposal can be
executed on the ESPN team. **Status: code merged to `main`, but NOT yet
validated against live ESPN.** This is the one open item on the project.

- **What was added.** `espn_utils.apply_lineup_moves()` (atomic multi-slot
  `LINEUP` transaction) and `espn_utils.waiver_move()` (add/drop in one
  transaction); `set_lineup.py` (seats approved starters into *eligible*
  slots via bipartite matching, aborts rather than ever making an illegal
  move; `compute_moves()` is the reusable planner); `waiver_move.py` (CLI
  add/drop); `apply_pending.py` (iMac cron executor — reads
  `pending_moves.json`, runs each approved entry once by `id`, logs to
  `applied_log.jsonl`, emails a confirmation; `--pull` git-pulls first so
  an approval committed to the repo flows through hands-off).
- **Why unvalidated.** This was built from Claude Code on the web (Regime
  B), which cannot reach `fantasy.espn.com` — the transaction POST payloads
  are written to ESPN's documented private-API shape but were never
  executed. The `move_player()` LINEUP payload is the proven reference;
  the add/drop (`FREEAGENT`/`WAIVER`) payload is the higher-risk unknown.
- **TO COMPLETE (run on the iMac, Regime A):**
  1. `python3 set_lineup.py --starters starters.json` (dry run) → confirm
     the printed moves match intent.
  2. `python3 set_lineup.py --starters starters.json --apply` → confirm it
     took in the ESPN app. If HTTP error, capture `resp.text` and fix the
     payload in `espn_utils.apply_lineup_moves`.
  3. Repeat the dry-run → apply cycle for `waiver_move.py` (the add/drop
     shape is the most likely to need a tweak).
  4. Only after both succeed: install the `apply_pending.py --pull` cron
     (every few minutes) and decide the approval-delivery channel
     (commit `pending_moves.json` to this repo; the iMac pulls it).
- **Locked decision (write path).** Lineup moves must stay
  eligibility-safe: never POST a player to a slot they aren't eligible for.
  `set_lineup.compute_moves` enforces this via matching and returns an
  error instead of guessing. Don't bypass it.

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
