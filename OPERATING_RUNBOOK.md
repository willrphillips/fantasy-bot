# Captain Phillips — Fantasy Operating Runbook

Operating procedure for Claude when handling Captain Phillips fantasy-baseball
requests. Read this **and** `fantasy_baseball_instructions.md` before answering.
This file adds the **environment-aware execution layer** and records **known
breakage** discovered in the pipeline.

League: ESPN H2H categories, ID 2057904545, 2026 season.
**Strategy C: hard punt SV + SB.** Target cats: AVG, R, HR, RBI, K, W, HLD, ERA, WHIP.

---

## STEP 0 — Environment gate (run FIRST, every session)

Network access differs by where Claude runs. Probe before trusting any URL.

| Regime | Reachable | Live MLB pulls? |
|---|---|---|
| **A — iMac "Cocky-Claude"** | github.io, statsapi.mlb.com, baseballsavant | ✅ Yes — full live process |
| **B — Claude Code on web / cloud container** | **only** `github.com` + `raw.githubusercontent.com` | ❌ No — statsapi / Savant / github.io all return 403 |

**Regime B URL swaps** (`github.io` is blocked → use raw):
- snapshot → `raw.githubusercontent.com/willrphillips/fantasy-snapshots/main/snapshot.md`
- db → `…/main/data/fantasy.db`
- views → `…/main/views/<name>.md`

**Regime B consequence:** live game logs, Savant, and the 13 untracked-FA live
pulls are impossible. Per the HARD RULES, **admit "no live data for X" — never
fabricate, never substitute training-data stats.** Freshest available =
nightly `snapshot.md` + `fantasy.db` (~03:00 ET, **stats lag ~1 day**) + the
user's screenshots for anything intraday.

**WebSearch vs WebFetch (Regime B):** `WebSearch` works (routes through
Anthropic); `WebFetch` and direct host fetches are blocked (403). Use WebSearch
ONLY for the real MLB **schedule / which teams are off** — its player data
(probables, stats) is real-world MLB and **diverges from this simulated league**
(e.g. it lists injured-FA Cole as a starter). Never use web player data for
roster decisions; keep stats on `fantasy.db`/`snapshot.md`.

**Playoff odds:** run `python3 playoff_odds.py` (Monte Carlo off snapshot's
Season-Long Category Totals; TOP 4 make playoffs). Update its `T`/`REC` dicts
from a fresh snapshot; trust the scenario *deltas* over absolute levels.

---

## STEP 1 — League state: `snapshot.md` is the source of truth

Pull first. Carries standings, **live matchup category-by-category**,
**Season-Long Category Totals + "You rank"**, all 10 rosters, top-50 FAs.
Verify `_Generated:` < 25h.

- ⚠️ **Do NOT use the db `matchups` table or `category_standings` view for
  matchup/category state — it is empty (all values NULL).** Use `snapshot.md`.
- ⚠️ **Do NOT compute homemade category ranks.** Use snapshot's *Season-Long
  Category Totals*. A roster-sum proxy badly misranks the team.
- ⚠️ **Do NOT trust the db `standings.rank`** — it does not match ESPN
  tiebreakers. Use `snapshot.md` standings.

## STEP 2 — Performance data: `fantasy.db` + views + `fantasy_lib`

Repo is already cloned at `/home/user/fantasy-bot` (skip `git clone` in Regime B).
```bash
cd /tmp && curl -sSL -o fantasy.db \
  https://raw.githubusercontent.com/willrphillips/fantasy-snapshots/main/data/fantasy.db
export FANTASY_DB=/tmp/fantasy.db PYTHONPATH=/home/user/fantasy-bot
python3 -c "import fantasy_lib as fl; print(fl.health())"
```
`window_stats(name, days=14/30)`, `hot_bats(...)`, `regression_watch('up'/'down')`.
Anchors: `latest_roster_date()` (rosters), `latest_date()` (stats). Confirm
`pull_status` fresh + `errors=0`. **Position eligibility: trust the ESPN app,
NOT the db `eligible_pos` strings** (combo labels like "2B/SS" mislead). No app
→ flag eligibility unconfirmed.

## STEP 3 — Durable league facts (don't re-derive, don't assume)

- 11 cats. Target 9 (AVG, R, HR, RBI, K, W, HLD, ERA, WHIP). Punt SV, SB.
- **Playoffs = TOP 4.**
- Authoritative ranks (snapshot): strengths **W, ERA, WHIP**; weak **HR, RBI**
  (+ SV punted). Improvement priority within targets: **HR / RBI / AVG**, then HLD.
- Roster: 19 active (C/1B/2B/3B/SS/3×OF/UTIL + 7 P + 3 BE) + 3 IL. Daily lineups.
- **Rolling 24h waivers, no FAAB. Ownership % meaningless** (closed 10-team).
- **Acquisition cap: 7 add/drops per matchup week** (ESPN "Matchup Acquisitions
  X/7"). Don't burn the limit on low-EV churn — bank moves for real upgrades.

## STEP 4 — Hard rules

- Verify BOTH sides of every swap with fresh data; no training-data stats; no
  articles >48h; FIP/xERA over surface ERA; game log for streaks (Regime A).
- Availability = name not on any of the 10 snapshot rosters (ignore ownership %).
- **Strategy C: no save-dependent closers.** Test: "If saves didn't count, top-30
  RP?" No → pass. HLD + K + ERA/WHIP setup men are the sweet spot. **HLD is a
  scoring cat** (Sabrowski = keep).
- Walk every slot (active+bench+IL), no skips. Bench ≠ expendable. Name value ≠
  data. No "check later" if verifiable now. Consider cross-positional swaps.

## STEP 5 — Deliverable on open-ended asks (Standing Advisory Mandate)

1. Roster optimizations (lineup vs schedule, IL management, pre-lock moves)
2. Waiver adds/drops (Strategy-C filtered, cross-ref 10 rosters, forced template)
3. Trade scan (1–2 fit teams, realistic frameworks)

## STEP 6 — Forced Output Template (every add/drop; shown in output)

See `fantasy_baseball_instructions.md`. In Regime B, fill `source` as
`snapshot.md`/`fantasy.db (gen DATE, ~1-day lag)`; if a field needs a live pull
that's unavailable, write **"need to verify [X] live before recommending."**

---

## KNOWN BREAKAGE (verified June 2026)

**Pipeline / database**
1. **`matchups` table is empty** — all rows `leader='tied'`, `home_value`/
   `away_value` NULL. System 2 never captures matchup category state.
   `category_standings.md`'s "Current Matchup" section is broken as a result.
   `snapshot.md` (System 1) DOES carry it → the two pipelines diverge.
2. **db `standings.rank` ≠ ESPN standings** — db ranks the .515 cluster by raw
   pct/wins (different leader + different seed order than ESPN's tiebreakers).
   db put Captain Phillips #6; ESPN/snapshot has #7. Use snapshot.
3. **Statcast series starts 2026-05-21** (earlier rows absent/deleted).
4. **13 untracked FAs** (no 2026 games): Cole, Burnes, Bieber, Musgrove, Steele,
   Hader, Profar, Greene, Jones, Schwellenbach, Pepiot, Westburg, Teel.
5. db lags MLB by ~1 day; no schedule / probable pitchers / lineup cards anywhere
   in the data — those require the app or a live pull.

**Environment (Regime B / web)**
6. `willrphillips.github.io` returns 403 — the documented fetch URLs in both the
   spec and `fantasy_baseball_instructions.md` are unreachable here.
7. `statsapi.mlb.com` returns 403 — the "live pull only" rule is unenforceable;
   game logs, intraday hot/cold, and untracked-FA pulls are impossible.
8. `baseballsavant.mlb.com` returns 403 — no live Statcast/advanced pulls.
