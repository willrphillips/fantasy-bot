# INTEGRATION.md — handing fantasy-bot's execution layer to Edwin

This document is the package spec for running fantasy-bot's ESPN execution
module on a box that is **not** the iMac. It is written to be handed to Edwin
(the user's separate always-on assistant on the Hetzner box) as-is.

Nothing in this repo reaches out to Edwin's box. This is a spec plus one
portable file; the user copies them across.

---

## 1. The split

| Concern | Who | Where |
|---|---|---|
| Nightly ingest, `fantasy.db`, markdown views | iMac ("Cocky-Claude") | `~/fantasy-bot`, cron |
| Publishing that data publicly | iMac | `db_publish.py` → `willrphillips/fantasy-snapshots` |
| **Reading** the data, analysis, talking to the user | **Edwin** | Hetzner, Discord |
| **Executing** the approved ESPN transaction | **Edwin** | Hetzner, via `fantasy_exec.py` |

The iMac is **not** in the execution path. It produces data and publishes it.
Edwin reads that published data over plain HTTPS (no auth) and acts.

`apply_pending.py` (the git-queue executor on the iMac) still exists as a
manual/fallback path. It is no longer the primary route.

---

## 2. Read path — how Edwin gets the data

The published data is public. No credentials.

```
https://willrphillips.github.io/fantasy-snapshots/data/fantasy.db
https://willrphillips.github.io/fantasy-snapshots/views/team_review.md
https://willrphillips.github.io/fantasy-snapshots/views/waiver_hitters.md
https://willrphillips.github.io/fantasy-snapshots/views/waiver_pitchers.md
https://willrphillips.github.io/fantasy-snapshots/views/regression_watch.md
https://willrphillips.github.io/fantasy-snapshots/views/trade_targets.md
https://willrphillips.github.io/fantasy-snapshots/views/category_standings.md
https://willrphillips.github.io/fantasy-snapshots/views/pull_status.md
https://willrphillips.github.io/fantasy-snapshots/views/anomaly_digest.md
```

For structured queries, download `fantasy.db` and point `fantasy_lib` at it:

```python
import os
os.environ["FANTASY_DB"] = "/path/to/downloaded/fantasy.db"
from fantasy_lib import my_roster, window_stats, hot_arms, health

health()                              # freshness + row counts — check this first
my_roster()
window_stats("Braxton Ashcraft", days=14)
```

Copy `fantasy_lib.py` alongside `fantasy_exec.py` if Edwin wants those helpers;
it is read-only and has no ESPN dependency.

**Two data rules that carry over from `CLAUDE.md` and are not optional:**

1. **Never value players from live web stats.** This league is a simulated
   universe that diverges from real MLB. Every number must come from
   `fantasy.db`.
2. **`hitting_stats` / `pitching_stats` are cumulative season-to-date**, one row
   per player per date. A window (L7/L14/L30) is *today's row minus the row N
   days ago*. `window_stats()` does the subtraction. Reading a row directly and
   calling it "last week" is wrong.

Check `health()` (or `pull_status.md`) before trusting anything — if the publish
failed, the db can be a day stale even when the views look current.

---

## 3. Write path — `fantasy_exec.py`

### Files to copy

| File | Required | Notes |
|---|---|---|
| `fantasy_exec.py` | yes | self-contained; imports no other repo file |
| `fantasy_lib.py` | optional | only if Edwin wants the db query helpers |

Do **not** copy `espn_utils.py` — it hardcodes the baseball league/team ids and
pulls in the iMac's email plumbing. `fantasy_exec.py` deliberately does not
import it.

### Dependencies

```bash
pip install requests espn-api
```

Both are imported lazily, so `compute_moves()` and `python3 fantasy_exec.py
selftest` work before anything is installed.

### Config

Default lookup order: the `config_path=` argument → `$FANTASY_EXEC_CONFIG` →
`config.json` next to the module.

```json
{
  "espn_s2": "<long cookie value>",
  "swid": "{XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}",
  "default_league": "baseball",
  "leagues": {
    "baseball":           {"league_id": 2057904545, "team_id": 9, "season": 2026},
    "cast_final_fantasy": {"league_id": null, "team_id": null, "season": 2026},
    "sunday_funday":      {"league_id": null, "team_id": null, "season": 2026}
  }
}
```

Required keys: `espn_s2`, `swid`, and per league `league_id` + `team_id`.
`season` defaults to 2026. Optional per-league `hitter_slots` / `pitcher_slots`
override the slot layout if another league is configured differently.

A league whose `league_id`/`team_id` are still `null` returns a clean config
error rather than acting on the wrong team.

The flat legacy shape (the iMac's existing `config.json`, with `league_id` and
`team_id` at the top level and no `leagues` map) is also accepted, so the same
module runs on the iMac unchanged.

**This file holds live ESPN session cookies. Treat it like a password:**
`chmod 600`, never commit it, keep it out of any repo. Anyone with `espn_s2` +
`swid` can transact on the user's team. If it leaks, log out of ESPN — that
invalidates the cookies.

### Function contract

```python
add_drop(add_name, drop_name, txn_type="WAIVER", dry_run=False,
         league=None, config_path=None, bid=0) -> dict

set_lineup(starters, dry_run=False, league=None, config_path=None) -> dict

get_roster(league=None, config_path=None) -> dict
whoami(league=None, config_path=None) -> dict
```

Every function returns a dict and **never raises on an ESPN or config failure** —
failures come back as data:

```python
{
  "ok": bool,          # dry runs: True means "valid and would work"
  "dry_run": bool,
  "action": str,       # "add_drop" | "set_lineup" | "get_roster" | "whoami"
  "league": str,       # which league key was used
  "detail": str,       # one-line summary, safe to post straight to Discord
  "error": str | None, # None on success
  # action-specific:
  #   add_drop   -> add_player, drop_player, scoring_period
  #   set_lineup -> moves[], scoring_period
  #   get_roster -> roster[], scoring_period
}
```

Semantics worth knowing:

- **`dry_run=True` submits nothing** but does all the work — resolves both
  names against the live roster and FA pool, checks eligibility, computes the
  exact slot moves. Run it first, show the user `detail`, then re-run with
  `dry_run=False` on a yes.
- **Name resolution is strict.** Exact match wins; a substring match is accepted
  only if it is unambiguous. Two possible matches is an error, not a coin flip.
- **`add_drop` lands the added player on the BENCH.** Slot him with a follow-up
  `set_lineup()` call.
- **`set_lineup` is eligibility-safe.** It seats each named starter in a slot
  they are actually eligible for (bipartite matching against ESPN's
  `eligibleSlots`), benches everyone else, and refuses IL players. If it can't
  seat everyone legally it submits nothing and returns the reason. All slot
  changes go up as one transaction because ESPN validates the end state.
- **`txn_type`** is `"WAIVER"` (rolling-waiver claim; this league has no FAAB so
  `bid` stays 0) or `"FREEAGENT"` (instant add of a player not on waivers).

### Example — the approve → execute flow

```python
import fantasy_exec as fx

# 0. sanity check once, after copying the config over
fx.whoami()
# -> ok=True, "Captain Phillips (team 9, league 2057904545), scoringPeriod 108, 26 players"

# 1. propose — dry run, show `detail` to the user in Discord
res = fx.add_drop("Ben Brown", "Landen Roupp", dry_run=True)
if not res["ok"]:
    post_to_discord(f"Can't do that: {res['error']}")
else:
    post_to_discord(res["detail"])   # "[DRY RUN] would WAIVER: add Ben Brown / drop Landen Roupp"

# 2. on the user's yes — execute
res = fx.add_drop("Ben Brown", "Landen Roupp", dry_run=False)
post_to_discord(res["detail"] if res["ok"] else f"Failed: {res['error']}")

# 3. the add is on the bench — slot it
res = fx.set_lineup(["Shea Langeliers", "Freddie Freeman", ...], dry_run=True)
```

### CLI (same logic, for manual checks)

Dry run is the default; `--apply` is required to submit.

```bash
python3 fantasy_exec.py selftest                              # offline, no config
python3 fantasy_exec.py whoami
python3 fantasy_exec.py roster
python3 fantasy_exec.py add-drop --add "Ben Brown" --drop "Landen Roupp"
python3 fantasy_exec.py add-drop --add "Ben Brown" --drop "Landen Roupp" --apply
python3 fantasy_exec.py lineup --starters starters.json
python3 fantasy_exec.py --league sunday_funday roster
```

---

## 4. Bring-up checklist on the Hetzner box

1. `pip install requests espn-api`
2. Copy `fantasy_exec.py` across.
3. `python3 fantasy_exec.py selftest` → expect `selftest OK`. Proves the
   eligibility solver survived the copy. Needs no config and no network.
4. Write the config JSON, `chmod 600`.
5. `python3 fantasy_exec.py whoami` → must print **Captain Phillips, team 9**.
   If it names a different team, stop — the ids are wrong.
6. `python3 fantasy_exec.py roster` → confirm it matches the ESPN app.
7. Dry-run a real add/drop. Confirm the names resolve as expected.
8. Only then run anything with `--apply` / `dry_run=False`.

---

## 5. Verification status

Tested on the iMac against the live league on **2026-07-21** (Python 3.9.6).

**Verified live (read-only calls, nothing submitted):**

- `whoami()` → `Captain Phillips (team 9, league 2057904545), scoringPeriod 119,
  21 players`. Cookies authenticate.
- `get_roster()` → 21 players, matching the ESPN app, IL players flagged.
- `set_lineup(current starters, dry_run=True)` → `no lineup changes needed —
  already optimal`. Round-trips name resolution, the solver, and slot mapping.
- Negative control: a bogus name is refused rather than silently dropped.
- `selftest` passes, covering the solver, the refusal cases (IL starter,
  over-full lineup, unknown name, unseatable C/C/C), no-op stability, and a real
  bench-for-starter swap.

**Still NOT verified:** the actual POST. No transaction has been submitted from
this module. `add_drop()` and a lineup change with real moves have only ever run
with `dry_run=True`. The payloads are ported unchanged from `espn_utils.py`
(which has been used in anger), but the port's write path is unproven.

First real use should be a single low-stakes move with Will watching.

### A limitation worth knowing about

`espn_api` exposes `lineupSlot` as a **label** (`"P"`, `"OF"`), not the numeric
slot ID. So every pitcher parses back as slot 11 and every outfielder as slot 5 —
which specific P or OF slot a player occupies is not recoverable.

Consequences:

1. The solver used to emit phantom `P->P` / `OF->OF` moves (7 of them on the
   live roster). `compute_moves` now drops any move whose from-label equals its
   to-label, since ESPN treats those slots as identical. That is why the
   round-trip above returns zero moves.
2. For a **real** change inside those groups (benching one OF, starting
   another), the incoming player's computed `toLineupSlotId` may name a slot
   that a different, unmoved OF actually occupies. ESPN validates the end state
   of the whole transaction, so this may well be accepted — but it has not been
   proven. Watch the first live OF or P swap closely.

`set_lineup.py` on the iMac has the same underlying behaviour; it was left
as-is (see section 6).

---

## 6. Known duplication (cleanup owed)

The eligibility matching in `fantasy_exec.compute_moves()` is a port of
`set_lineup.compute_moves()`. Two copies exist deliberately — the working iMac
scripts were left untouched until this module is proven live. Once it is,
collapse `set_lineup.py` and `waiver_move.py` onto `fantasy_exec` so there is
one implementation. Until then, a fix to one must be made in both.

---

## 7. Scope boundaries

- fantasy-bot posts to Discord for **pipeline failure alerts only**
  (`notify.alert` → ops webhook). The per-league conversation channels belong to
  Edwin. fantasy-bot does not post analysis or proposals.
- Roster strategy lives with Edwin, but the standing constraints from
  `CLAUDE.md` apply to any move it proposes: Strategy C (hard-punt SV + SB;
  prioritize HR/RBI; protect W/ERA/WHIP/K; HLD scores), vet any pitcher add by
  **full-season** line rather than a hot L30, and trust the ESPN app for
  position eligibility over the db's `eligible_pos` strings.
