#!/usr/bin/env python3
"""
Fantasy baseball — in-game roster triage.

Scheduled in-process by edwin's bot.py, every 30 minutes, from 30 minutes before today's first
pitch through the last game of the day going Final. Dormant the rest of the day and on off-days —
the 03:30 ingest and the 04:00 advisor own the small hours, and this only exists to react to things
that change WHILE games are being played, which a once-a-day job can't do.

Runs two independent passes each cycle:

  1. Hitters — conservative, patch-only. An active hitter who is hurt (OUT/IL-status) or whose pro
     team has no game today comes out; the best in-action bench hitter of the same kind takes the
     slot. Everyone else active and in-action is left exactly where they sit. This never re-scores
     for a marginal upgrade — that churn is the 4am job's business, not a 30-minute one.

  2. Pitchers — full rebuild every cycle, because "who's starting" and "whose team plays" are both
     day-specific facts that can't be patched incrementally the way a hitter's game-or-no-game
     status can. Every rostered pitcher who is probable to start today MUST be seated (Will's rule:
     a starter never sits). Remaining pitcher slots go to relievers whose team plays today, ranked
     by season quality, but a reliever can never bump a starter out of a slot to make room — only
     another reliever or an empty/no-game arm can be bumped for one. Both must-start and in-action
     relievers are drawn from the FULL roster (bench included), not just today's active arms, so a
     starter announced after the last cycle gets pulled in from the bench automatically.

Either pass runs through fantasy_exec.set_lineup, the same eligibility-safe matcher every hand-made
move uses; it only submits a transaction for slots that actually changed, so a pass that agrees with
the status quo is a no-op, not churn. IL players are never touched — set_lineup only manages the
active/bench boundary.

A short sanity-check pass runs after the move and logs (never acts on) anything that still looks
wrong: an active player showing an IL-caliber injury, or an active player with no game today.
Either should be structurally impossible given the passes above; if one shows up, it means a data
source lied (ESPN injury flag lagging, MLB schedule flip mid-cycle) and is worth a human glance.

Silent unless it actually moved someone.

Config (env):
  FANTASY_TRIAGE_DRY_RUN=1   run the triage, print what it would do, submit and post nothing
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
from name_matcher import normalize  # noqa: E402

DRY_RUN = (os.getenv("FANTASY_TRIAGE_DRY_RUN") or "").strip() in ("1", "true", "yes")
PITCHER_CAPACITY = len(fantasy_exec.DEFAULT_PITCHER_SLOTS)  # 7 in this league


def _triage(players, matchups, score, is_starting_today, seatable):
    """Decide the two sub-rosters (hitters, pitchers) that should be active. Returns
    (hitter_names, pitcher_names, human-readable lines).

    `seatable(names)` reports whether a full candidate roster can actually be placed within this
    league's real position slots (C/1B/2B/3B/SS/3xOF/UTIL) — a bench player scoring well is
    worthless as a replacement if his eligibility doesn't cover the slot going vacant (e.g. a
    2B-only bat can't replace a 3B). Pitchers skip this: every P slot in this league accepts any
    pitcher, so there is nothing positional to check there."""
    lines = []

    def has_game(p):
        return bool(matchups.get(p["pro_team"], {}).get("has_game"))

    non_il = [p for p in players if not p["on_il"]]
    hitters_all = [p for p in non_il if not is_pitcher(p)]
    pitchers_all = [p for p in non_il if is_pitcher(p)]

    # --- Hitters: patch only ---------------------------------------------------------------
    active_hitters = [p for p in hitters_all if p["is_active"]]
    bench_hitters = [p for p in hitters_all if p["on_bench"]]

    ok_ids = {p["player_id"] for p in active_hitters
              if p["injury"] not in IL_STATUSES and has_game(p)}
    keep_hitters = [p for p in active_hitters if p["player_id"] in ok_ids]
    flagged_hitters = [p for p in active_hitters if p["player_id"] not in ok_ids]

    used_ids = {p["player_id"] for p in keep_hitters}
    for p in flagged_hitters:
        reason = p["injury"] if p["injury"] in IL_STATUSES else "no game today"
        cands = sorted(
            [b for b in bench_hitters
             if b["player_id"] not in used_ids and b["injury"] not in IL_STATUSES and has_game(b)],
            key=score, reverse=True,
        )
        chosen = next(
            (c for c in cands
             if seatable([x["name"] for x in keep_hitters] + [c["name"]])),
            None,
        )
        if chosen:
            used_ids.add(chosen["player_id"])
            keep_hitters.append(chosen)
            lines.append(f"{p['name']} ({reason}) comes out; {chosen['name']} takes the slot")
        else:
            # No seatable replacement — bench him rather than force him back into his old slot.
            # An empty slot and a gameless starter both score zero, but only one of them is
            # honest about it, and leaving him "active" is what caused the exact eligibility
            # deadlock this seatable() check exists to prevent (see 2026-07-28 postmortem).
            lines.append(f"{p['name']} is {reason} and comes out, "
                         "with nobody on the bench able to fill the slot")

    # --- Pitchers: full daily rebuild -------------------------------------------------------
    # Candidacy excludes anyone showing an IL-caliber injury even if ESPN hasn't moved him to the
    # IL slot yet — the same protection the hitter pass gets. `pitchers_all` (unfiltered) is kept
    # around only to label removal reasons below.
    pitchers_eligible = [p for p in pitchers_all if p["injury"] not in IL_STATUSES]
    must_start = [p for p in pitchers_eligible if is_starting_today(p)]
    must_start_ids = {p["player_id"] for p in must_start}
    relief_in_action = sorted(
        [p for p in pitchers_eligible if p["player_id"] not in must_start_ids and has_game(p)],
        key=score, reverse=True,
    )

    if len(must_start) > PITCHER_CAPACITY:
        must_start.sort(key=score, reverse=True)
        overflow = must_start[PITCHER_CAPACITY:]
        must_start = must_start[:PITCHER_CAPACITY]
        lines.append("More probable starters today than pitcher slots (" +
                     ", ".join(p["name"] for p in overflow) + " couldn't all fit)")

    pitcher_starters = list(must_start)
    for p in relief_in_action:
        if len(pitcher_starters) >= PITCHER_CAPACITY:
            break
        pitcher_starters.append(p)

    was_active = {p["player_id"]: p for p in pitchers_all if p["is_active"]}
    now_active = {p["player_id"]: p for p in pitcher_starters}
    for pid, p in was_active.items():
        if pid in now_active:
            continue
        if p["injury"] in IL_STATUSES:
            reason = p["injury"]
        elif not has_game(p):
            reason = "no game today"
        else:
            reason = "roster full of higher-priority arms today"
        lines.append(f"{p['name']} comes off the pitching staff ({reason})")
    for pid, p in now_active.items():
        if pid in was_active:
            continue
        reason = "starting today" if pid in must_start_ids else "in relief, team plays today"
        lines.append(f"{p['name']} takes a pitching slot ({reason})")

    hitter_names = [p["name"] for p in keep_hitters]
    pitcher_names = [p["name"] for p in pitcher_starters]
    return hitter_names, pitcher_names, lines


def triage():
    """Returns (list of human-readable action lines, error string or None)."""
    cfg = load_config()
    league = get_league(cfg)
    my_team = next((t for t in league.teams if t.team_id == TEAM_ID), None)
    if not my_team:
        return [], f"team {TEAM_ID} not found in league"

    players = parse_roster(my_team)
    matchups = build_team_matchups()

    def is_starting_today(p):
        probable = matchups.get(p["pro_team"], {}).get("probable_pitcher")
        return bool(probable) and normalize(probable) == normalize(p["name"])

    two_starts = get_pitcher_starts_in_window(days=7)
    opp_sp_lookup = build_opp_sp_era_lookup(league)

    def score(p):
        return (score_pitcher(p, matchups, two_starts) if is_pitcher(p)
                else score_hitter(p, matchups, opp_sp_lookup))

    fx = fantasy_exec.get_roster()
    if not fx.get("ok"):
        return [], fx.get("error") or "could not read the roster for eligibility checking"
    fx_roster = fx["roster"]

    def seatable(names):
        _, err = fantasy_exec.compute_moves(fx_roster, names)
        return err is None

    hitter_names, pitcher_names, lines = _triage(
        players, matchups, score, is_starting_today, seatable,
    )
    if not lines:
        log("Nothing to adjust — every active player is healthy and in action, "
            "every starter and in-action reliever is seated.")
        return [], None

    starters = hitter_names + pitcher_names
    res = fantasy_exec.set_lineup(starters, dry_run=DRY_RUN)
    if not res.get("ok"):
        return lines, (res.get("error") or res.get("detail") or "set_lineup refused")
    log(res.get("detail") or "lineup set")

    # Sanity check — log-only, nothing here should ever fire given the passes above.
    final_ids = {p["player_id"] for p in players if p["name"] in set(starters)}
    for p in players:
        if p["player_id"] not in final_ids:
            continue
        if p["injury"] in IL_STATUSES:
            lines.append(f"SANITY: {p['name']} is active and still shows {p['injury']}")
        elif not matchups.get(p["pro_team"], {}).get("has_game"):
            lines.append(f"SANITY: {p['name']} is active with no game today")

    return lines, None


def main():
    log("=" * 55)
    log("Roster triage — Captain Phillips")
    if DRY_RUN:
        log("*** DRY RUN — nothing submitted, nothing posted ***")

    try:
        lines, err = triage()
    except Exception:
        log("ERROR during roster triage:")
        traceback.print_exc()
        try:
            from notify import alert
            alert("roster_triage", "In-game roster triage failed", traceback.format_exc()[-1500:])
        except Exception as e:  # noqa: BLE001
            log(f"could not post failure alert: {e}")
        sys.exit(1)

    if not lines and not err:
        log("Nothing to do. Staying quiet.")
        return

    body = "**Roster triage** — the lineup has changed.\n" + "\n".join(f"• {ln}" for ln in lines)
    if err:
        body += f"\n\nThe change did not take: {err}"
    if DRY_RUN:
        log("Would have posted:\n" + body)
        return
    from nightly_advisor import post_message  # lazy: silent most cycles, no sense loading it
    post_message(body)
    log(f"Posted {len(lines)} change(s).")


if __name__ == "__main__":
    main()
