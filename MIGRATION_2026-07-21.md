# MIGRATION 2026-07-21 — iMac → Hetzner (atlas-cloud), Edwin takes ownership

**Read this before trusting any other doc in this repo.** As of 2026-07-21 the runtime
moved off the iMac. `README.md`, `OPERATING_RUNBOOK.md`, `SCOPE_OF_WORK.md`, `CLAUDE.md`,
`INTEGRATION.md`, `CHAT_PROJECT_INSTRUCTIONS.md` and `fantasy_baseball_instructions.md` all
still describe the **old** iMac/cron/scp world. Where they disagree with this file, **this
file wins** until they are rewritten (see §5).

This document is the authoritative record of the migration. It was written the same session
the work was done.

---

## 1. What is live right now

| | Before (iMac) | Now (Hetzner) |
|---|---|---|
| Host | cocky-claude, macOS, user `claudeserver` | **atlas-cloud**, Ubuntu 26.04, user `edwincode` |
| Runtime dir | `/Users/claudeserver/fantasy-bot` (**not a git repo**) | `/home/edwincode/edwin-repos/fantasy-bot` (**real clone of this repo**), symlinked as `~/fantasy-bot` |
| Scheduler | user crontab | **systemd timers**, `fantasy-*.timer` |
| Timezone | machine-local ET | box is UTC; timers pinned `America/New_York` explicitly |
| Python | 3.9.6 (Apple system) | **3.14.4**, venv at `~/fantasy-bot/venv` |
| Alerts | email → Discord (mid-migration) | Discord only; `send_email` now `false` |
| Owner | Will via Claude Code on the PC | **Edwin**, documented in his `edwin.md` |

Verified working on the new box: ESPN cookie auth, `statsapi.mlb.com`,
`baseballsavant.mlb.com`, all 11 pinned dependencies installed on Python 3.14 with no
version changes, `views.py` (7 reports), `health_check.py`, and a live Discord alert post.

### Timers

```
fantasy-ingest.timer    03:30 ET   mlb_ingest.py     ENABLED
fantasy-views.timer     04:30 ET   views.py          ENABLED
fantasy-anomaly.timer   04:45 ET   anomaly.py        ENABLED
fantasy-health.timer    06:00 ET   health_check.py   ENABLED
fantasy-publish.timer   05:00 ET   db_publish.py     *** DISABLED ON PURPOSE ***
```

Check with `systemctl list-timers 'fantasy-*'`. Units live in `/etc/systemd/system/`.

---

## 2. The two things you must not undo

1. **`fantasy-publish.timer` is disabled deliberately.** The iMac is still running its full
   crontab and is still the authoritative publisher to `willrphillips/fantasy-snapshots`.
   If both boxes publish, they overwrite each other's `fantasy.db`. Enable the Hetzner timer
   **only** as part of decommissioning the Mac, in the same sitting.
2. **The live-write gate on `fantasy_exec.py` still stands.** It has been verified read-only
   and one real bug fixed (phantom lineup moves — `espn_api` reports `slot` as a label, not a
   numeric ID), but **no write transaction has ever been submitted to ESPN**. Submitting one
   requires Will present and saying yes. The migration does not lift this. `pending_moves.json`
   is empty (`"moves": []`).

---

## 3. Structural debt this migration fixed (do not regress)

- **The runtime is now a real git checkout.** On the Mac it never was, which meant
  `apply_pending.py --pull` ran `git pull` with `check=False` and **failed silently on every
  run** — the "approve a move in chat → box applies it" loop had never actually worked, and
  deploys were file-by-file `scp`. On Hetzner `git pull` genuinely works. Never again stand
  this up as a loose directory.
- **`requirements.txt` now exists.** There was none; the dependency set lived only in the
  Mac's venv. It is pinned from that venv and verified to install clean on 3.14.

---

## 4. Known rough edges carried over (not fixed here)

1. **Scripts hardcode `~/fantasy-bot/...`** rather than `os.path.dirname(__file__)` —
   `fantasy_lib.py:36`, `mlb_ingest.py:53`, `health_check.py:33`, `db_publish.py:35`.
   Currently papered over with the `~/fantasy-bot` symlink. The proper fix is a small commit
   changing these to resolve relative to the file. `fantasy_lib.py` also honours a
   `FANTASY_DB` env var; the others do not.
2. **Nine scripts run in production but are not in this repo.** They exist only on the Mac
   and in the handoff bundle: `espn_nightly_moves.py`, `league_snapshot.py`,
   `espn_weekly_report.py`, `name_matcher.py`, `pull_historical_matchups.py`, `serve.py`,
   `debug_cookie.py`, `daily_projections.py`, plus `historical_matchups.csv`. Three were on
   the live crontab. **They are NOT yet running on Hetzner** — the 03:00 ESPN-moves and
   Sunday weekly-report jobs have no systemd equivalent yet. Decide: commit and port them,
   or formally retire them.
3. **Three of those hardcode absolute macOS paths** and will break as-is on Linux:
   `league_snapshot.py:7` and `:219`, `debug_cookie.py:2`, and `serve.py:4,15` — all
   pointing at `/Users/claudeserver/...`.
4. **Mixed notification paths.** `notify.py` is Discord. `espn_weekly_report.py` still sends
   Gmail SMTP. `send_email` is now `false` in the Hetzner config, so if that script is ported
   it needs converting to Discord first.
5. **One missing day, 2026-06-15**, in `statcast` and `pull_log`. Hitting/pitching unaffected.
6. **`id_map` covers 476 of 1,322 players** — by policy, not bug. Unresolved beats mis-mapped.
   Do not add fuzzy fallback matching without asking Will.
7. **Two other ESPN leagues remain unwired.** "Sunday Funday Fantasy" is **football**,
   `leagueId=1068408855`, `teamId` unknown. "Cast Final Fantasy" has not been identified at
   all. Discord channels already exist for both (`#sunday-funday-fantasy`,
   `#cast-final-fantasy`). Football needs a different API endpoint, so `fantasy_exec.py`
   needs a per-league sport field before either is added.

---

## 5. Docs that are now WRONG and need rewriting

Counted 2026-07-21. Every one of these describes the iMac world as current:

| File | Stale refs | What's wrong |
|---|---:|---|
| `fantasy_baseball_instructions.md` | ~20 | The big one (63 KB). Paths, host, cron, email. |
| `SCOPE_OF_WORK.md` | 12 | "Layout" section says `~/fantasy-bot` on the iMac is the runtime home; email alerting described as current. |
| `INTEGRATION.md` | 11 | Written for iMac + scp delivery. |
| `README.md` | 8 | §"On Cocky-Claude (the iMac)" and a verbatim crontab block with `/Users/claudeserver/...` paths. |
| `CLAUDE.md` | 4 | iMac assumptions. |
| `CHAT_PROJECT_INSTRUCTIONS.md` | 2 | iMac assumptions. |
| `OPERATING_RUNBOOK.md` | 1 | Environment table row "A — iMac Cocky-Claude … full live process". |

**Rewrite rule:** the iMac is *historical*, not *current*, and must not be deleted from the
record — `SCOPE_OF_WORK.md` keeps its dated log. Add the new state; mark the old state with
its end date. Do not silently rewrite history.

---

## 6. Where things are

- **Runtime / DB:** `~/fantasy-bot` → `~/edwin-repos/fantasy-bot`, db at `fantasy.db`
  (gitignored, 32 MB, integrity verified after transfer).
- **Config:** `config.json`, mode 600, gitignored. Rebuilt on the new box from
  `secrets.env`; never committed, never printed.
- **Handoff bundle:** `~/mlb-handoff/` on both boxes, plus `~/mlb-handoff.tar.gz`
  (8,875,950 bytes, sha256 `1b6dba48…6a4ba3de`, verified identical after transfer).
  `secrets.env` travelled separately and was never inside the tarball.
- **Edwin's brief:** the `## Fantasy baseball + the MLB stats database` section of
  `~/CodeProjects/edwin/edwin.md` on atlas-cloud.

### Data as migrated

`players` 1,322 · `hitting_stats` 58,808 · `pitching_stats` 69,994 · `statcast` 38,053 ·
`rosters` 13,143 · `fa_pool` 12,803 · `matchups` 4,030 · `standings` 640 · `id_map` 476 ·
`pull_log` 68. Hitting/pitching 2026-03-26 → 07-20 (117 days, no gaps); fantasy-state tables
2026-05-19 → 07-21 (64 days, no gaps).

**Stats are cumulative season-to-date snapshots, not game logs.** Windows are computed by
subtracting two snapshots. Stat tables run one day behind the fantasy-state tables by design;
use `fantasy_lib.latest_date()` / `latest_roster_date()`. Aligning them re-introduces a bug
already fixed once on 2026-05-19.

---

## 7. Open questions for Will

1. **When do we cut the Mac over?** Until then two boxes ingest in parallel and only the Mac
   publishes. Nothing breaks, but the Hetzner DB and the published DB will drift.
2. **Port or retire the nine untracked scripts?** Specifically: do you still want the 03:00
   ESPN nightly-moves job, the nightly `league_snapshot.py`, and the Sunday 19:00 weekly
   report? They are not running on Hetzner right now.
3. **Is the Sunday email report still wanted at all**, or does it become a Discord post?
4. **`teamId` for Sunday Funday, and what is "Cast Final Fantasy"?** Both need the URL from a
   logged-in browser: `fantasy.espn.com/…/team?leagueId=…&teamId=…`.

---

## 8. Also worth knowing

The `willrphillips/edwin` repo on atlas-cloud is **46 commits ahead of origin** and its
deploy key is read-only, so it has never been pushed from that box. That is pre-existing and
unrelated to this migration, but it means Edwin's own config — including the fantasy section
added today — exists only on that machine. Worth fixing separately.
