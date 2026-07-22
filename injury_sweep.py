#!/usr/bin/env python3
"""
Fantasy baseball — hourly daytime injury sweep.

ESPN publishes no webhook and no push of any kind for injury designations: the endpoint this repo
talks to is the same private one the app uses, read-on-request only. So a status that flips at 1pm
is invisible until something asks. This asks, once an hour.

Scheduled in-process by edwin's bot.py (same no-root reasoning as the other loops), on the hour
from 12:00 to 20:00 ET by Will's instruction. Outside that window it sleeps: the 03:30 ingest and
the 04:00 advisor own the small hours, nothing designated at 6am needs answering before lunch, and
a status flipping after the last first pitch keeps till morning.

Narrow on purpose. It reacts to injury status and nothing else: if an OUT/IL-designated player is
sitting in the active lineup, he comes out and the best healthy bench man of the same kind takes
the slot. It never re-scores the lineup for marginal upgrades — that is the 4am job's work, and
hourly re-scoring would churn the roster all afternoon on noise.

The seating goes through fantasy_exec.set_lineup, the same path every hand-made move uses, which
does the real eligibility matching against this league's actual slot layout and leaves IL players
where they are. Deliberately NOT espn_nightly_moves.optimize_lineup: that script's empty-slot step
assumes lineup slot ids 0-15 all exist, which is not this league's roster, so it invents vacancies
and would shuffle a full lineup every hour.

Silent unless it actually moved someone.

Config (env):
  FANTASY_INJURY_DRY_RUN=1   run the sweep, print what it would do, submit and post nothing
"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

import fantasy_exec  # noqa: E402
from espn_utils import (  # noqa: E402
    load_config, get_league, parse_roster, IL_STATUSES, TEAM_ID, log,
)
from espn_nightly_moves import (  # noqa: E402
    is_pitcher, score_hitter, score_pitcher, build_opp_sp_era_lookup,
)
from daily_projections import build_team_matchups, get_pitcher_starts_in_window  # noqa: E402

DRY_RUN = (os.getenv("FANTASY_INJURY_DRY_RUN") or "").strip() in ("1", "true", "yes")


def sweep():
    """Returns (list of human-readable action lines, error string or None)."""
    cfg = load_config()
    league = get_league(cfg)
    my_team = next((t for t in league.teams if t.team_id == TEAM_ID), None)
    if not my_team:
        return [], f"team {TEAM_ID} not found in league"

    players = parse_roster(my_team)
    hurt = [p for p in players if p["is_active"] and p["injury"] in IL_STATUSES]
    if not hurt:
        log("No injured player in the active lineup.")
        return [], None

    log("Injured and starting: " + ", ".join(f"{p['name']} ({p['injury']})" for p in hurt))

    # Only now is it worth the MLB API calls for scoring — most hours end above.
    matchups = build_team_matchups()
    two_starts = get_pitcher_starts_in_window(days=7)
    opp_sp_lookup = build_opp_sp_era_lookup(league)

    def score(p):
        return (score_pitcher(p, matchups, two_starts) if is_pitcher(p)
                else score_hitter(p, matchups, opp_sp_lookup))

    # The replacement has to be one ESPN will actually seat, not merely the best score. A bench
    # outfielder cannot cover a hurt second baseman when all three OF slots and UTIL are already
    # taken, so every candidate is run through the same matcher set_lineup uses before it's picked.
    fx = fantasy_exec.get_roster()
    if not fx.get("ok"):
        return [], fx.get("error") or "could not read the roster for eligibility checking"
    fx_roster = fx["roster"]

    def seatable(names):
        _, err = fantasy_exec.compute_moves(fx_roster, names)
        return err is None

    hurt_ids = {p["player_id"] for p in hurt}
    keep = [p for p in players if p["is_active"] and p["player_id"] not in hurt_ids]
    bench = [p for p in players if p["on_bench"] and p["injury"] not in IL_STATUSES]

    lines = []
    for p in hurt:
        cands = sorted([b for b in bench if is_pitcher(b) == is_pitcher(p) and score(b) > 0],
                       key=score, reverse=True)
        chosen = next((c for c in cands
                       if seatable([x["name"] for x in keep] + [c["name"]])), None)
        if not chosen:
            lines.append(f"{p['name']} is {p['injury']} and comes out of the lineup, "
                         "with nobody on the bench able to fill the slot")
            continue
        bench.remove(chosen)
        keep.append(chosen)
        lines.append(f"{p['name']} ({p['injury']}) came out; {chosen['name']} takes the slot")

    starters = [p["name"] for p in keep]
    res = fantasy_exec.set_lineup(starters, dry_run=DRY_RUN)
    if not res.get("ok"):
        return lines, (res.get("error") or res.get("detail") or "set_lineup refused")
    log(res.get("detail") or "lineup set")
    return lines, None


def main():
    log("=" * 55)
    log("Injury sweep — Captain Phillips")
    if DRY_RUN:
        log("*** DRY RUN — nothing submitted, nothing posted ***")

    try:
        lines, err = sweep()
    except Exception:
        log("ERROR during injury sweep:")
        traceback.print_exc()
        # An hourly job that shouts every failure would be worse than silence; notify.py
        # throttles this to one alert a day on its own.
        try:
            from notify import alert
            alert("injury_sweep", "Hourly injury sweep failed", traceback.format_exc()[-1500:])
        except Exception as e:  # noqa: BLE001
            log(f"could not post failure alert: {e}")
        sys.exit(1)

    if not lines and not err:
        log("Nothing to do. Staying quiet.")
        return

    body = "**Injury sweep** — the lineup has changed.\n" + "\n".join(f"• {ln}" for ln in lines)
    if err:
        body += f"\n\nThe change did not take: {err}"
    if DRY_RUN:
        log("Would have posted:\n" + body)
        return
    from nightly_advisor import post_message  # lazy: silent most hours, no sense loading it
    post_message(body)
    log(f"Posted {len(lines)} change(s).")


if __name__ == "__main__":
    main()
