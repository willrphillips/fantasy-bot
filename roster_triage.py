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
     for a marginal upgrade — that churn is the 4am job's business, not a 30-minute one. If nobody
     eligible has a game today either, the slot is never left empty: the best bench bat by the
     week-ahead forecast (season/projected OPS, ignoring today's schedule) takes it instead, so a
     dead process or a total off-day never shows up as a hole in the lineup.

  2. Pitchers — full rebuild every cycle, because "who's starting" and "whose team plays" are both
     day-specific facts that can't be patched incrementally the way a hitter's game-or-no-game
     status can. Every rostered pitcher who is probable to start today MUST be seated (Will's rule:
     a starter never sits). Remaining pitcher slots go to relievers whose team plays today, ranked
     by season quality, but a reliever can never bump a starter out of a slot to make room — only
     another reliever or an empty/no-game arm can be bumped for one. Both must-start and in-action
     relievers are drawn from the FULL roster (bench included), not just today's active arms, so a
     starter announced after the last cycle gets pulled in from the bench automatically. Same rule
     as hitters: if that still leaves a P slot empty, it's filled from the rest of the staff by
     week-ahead forecast (ERA/WHIP + upcoming-start count) rather than left vacant.

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
    forecast_score_hitter, forecast_score_pitcher,
)
from daily_projections import build_team_matchups, get_pitcher_starts_in_window  # noqa: E402
from name_matcher import normalize  # noqa: E402

DRY_RUN = (os.getenv("FANTASY_TRIAGE_DRY_RUN") or "").strip() in ("1", "true", "yes")
PITCHER_CAPACITY = len(fantasy_exec.DEFAULT_PITCHER_SLOTS)  # 7 in this league


def _triage(players, matchups, score, forecast_score, is_starting_today, seatable):
    """Decide the two sub-rosters (hitters, pitchers) that should be active. Returns
    (hitter_names, pitcher_names, human-readable lines, forecast_fallback_ids).

    `seatable(names)` reports whether a full candidate roster can actually be placed within this
    league's real position slots (C/1B/2B/3B/SS/3xOF/UTIL) — a bench player scoring well is
    worthless as a replacement if his eligibility doesn't cover the slot going vacant (e.g. a
    2B-only bat can't replace a 3B). Pitchers skip this: every P slot in this league accepts any
    pitcher, so there is nothing positional to check there.

    `forecast_fallback_ids` is the set of player_ids seated purely on the week-ahead forecast
    because nobody eligible had a game today — deliberately active-with-no-game, not a sanity
    violation, so the caller can exclude them from the no-game sanity check below."""
    lines = []
    forecast_fallback_ids = set()

    def has_game(p):
        return bool(matchups.get(p["pro_team"], {}).get("has_game"))

    def locked(p):
        """ESPN freezes a player's slot the moment his game starts. A move involving him is
        answered with HTTP 200 and then silently ignored, so proposing one produces a phantom
        change that is announced as done, never happens, and comes back every 30 minutes for the
        rest of the day. Found 2026-08-30, with two Final games swapping arms all evening."""
        return bool(matchups.get(p["pro_team"], {}).get("started"))

    non_il = [p for p in players if not p["on_il"]]
    hitters_all = [p for p in non_il if not is_pitcher(p)]
    pitchers_all = [p for p in non_il if is_pitcher(p)]

    # --- Hitters: patch only ---------------------------------------------------------------
    active_hitters = [p for p in hitters_all if p["is_active"]]
    bench_hitters = [p for p in hitters_all if p["on_bench"]]

    # A locked bat stays put even if he is hurt or his game is over: ESPN will not move him,
    # and pretending otherwise is how the phantom-change loop starts.
    ok_ids = {p["player_id"] for p in active_hitters
              if locked(p) or (p["injury"] not in IL_STATUSES and has_game(p))}
    keep_hitters = [p for p in active_hitters if p["player_id"] in ok_ids]
    flagged_hitters = [p for p in active_hitters if p["player_id"] not in ok_ids]

    used_ids = {p["player_id"] for p in keep_hitters}
    for p in flagged_hitters:
        reason = p["injury"] if p["injury"] in IL_STATUSES else "no game today"
        cands = sorted(
            [b for b in bench_hitters
             if b["player_id"] not in used_ids and b["injury"] not in IL_STATUSES
             and has_game(b) and not locked(b)],
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
            continue

        # No seatable replacement with a game today. Don't leave the slot empty — fall back to
        # the best bench bat by week-ahead forecast (has_game ignored) so a total off-day, or
        # this process dying mid-run, never shows up as a hole in the lineup.
        fallback_cands = sorted(
            [b for b in bench_hitters if b["player_id"] not in used_ids and not locked(b)],
            key=forecast_score, reverse=True,
        )
        fallback = next(
            (c for c in fallback_cands
             if seatable([x["name"] for x in keep_hitters] + [c["name"]])),
            None,
        )
        if fallback:
            used_ids.add(fallback["player_id"])
            keep_hitters.append(fallback)
            forecast_fallback_ids.add(fallback["player_id"])
            lines.append(f"{p['name']} ({reason}) comes out; {fallback['name']} takes the slot "
                         "on next week's forecast — nobody eligible has a game today")
        else:
            # Truly nobody else can take the slot (empty bench, or nobody eligible — e.g. an OF
            # slot when every other bench bat is infield-only). Per Will's rule, an active roster
            # slot must never go empty: an incumbent who can't play today still beats a hole, so
            # he stays exactly where he was rather than getting benched into a vacancy. Not logged
            # or posted — nothing is actually changing, and re-flagging the same stuck player
            # every 30 minutes all day would just be noise.
            used_ids.add(p["player_id"])
            keep_hitters.append(p)
            forecast_fallback_ids.add(p["player_id"])

    # --- Pitchers: full daily rebuild -------------------------------------------------------
    # Candidacy excludes anyone showing an IL-caliber injury even if ESPN hasn't moved him to the
    # IL slot yet — the same protection the hitter pass gets. `pitchers_all` (unfiltered) is kept
    # around only to label removal reasons below.
    # The rebuild happens AROUND the arms whose games have started: an active one keeps his slot
    # and spends capacity, a benched one is simply unavailable today. Without this the nightly
    # rebuild keeps re-ranking finished games and asking ESPN for swaps it will never make.
    locked_active = [p for p in pitchers_all if p["is_active"] and locked(p)]
    locked_ids = {p["player_id"] for p in pitchers_all if locked(p)}

    pitchers_eligible = [p for p in pitchers_all
                         if p["injury"] not in IL_STATUSES and p["player_id"] not in locked_ids]
    must_start = [p for p in pitchers_eligible if is_starting_today(p)]
    must_start_ids = {p["player_id"] for p in must_start}
    relief_in_action = sorted(
        [p for p in pitchers_eligible if p["player_id"] not in must_start_ids and has_game(p)],
        key=score, reverse=True,
    )

    open_capacity = max(0, PITCHER_CAPACITY - len(locked_active))
    if len(must_start) > open_capacity:
        must_start.sort(key=score, reverse=True)
        overflow = must_start[open_capacity:]
        must_start = must_start[:open_capacity]
        lines.append("More probable starters today than pitcher slots (" +
                     ", ".join(p["name"] for p in overflow) + " couldn't all fit)")

    pitcher_starters = list(locked_active) + list(must_start)
    for p in relief_in_action:
        if len(pitcher_starters) >= PITCHER_CAPACITY:
            break
        pitcher_starters.append(p)

    # Still short a slot (a total off-day, etc.) — don't leave it empty. Fill from the rest of
    # the eligible staff by week-ahead forecast, has_game ignored, same rule as hitters.
    if len(pitcher_starters) < PITCHER_CAPACITY:
        chosen_ids = {p["player_id"] for p in pitcher_starters}
        fallback_pool = sorted(
            [p for p in pitchers_eligible if p["player_id"] not in chosen_ids],
            key=lambda pl: forecast_score(pl), reverse=True,
        )
        for p in fallback_pool:
            if len(pitcher_starters) >= PITCHER_CAPACITY:
                break
            pitcher_starters.append(p)
            forecast_fallback_ids.add(p["player_id"])

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
        if pid in must_start_ids:
            reason = "starting today"
        elif has_game(p):
            reason = "in relief, team plays today"
        else:
            reason = "on next week's forecast — no game today, but a slot can't sit empty"
        lines.append(f"{p['name']} takes a pitching slot ({reason})")

    hitter_names = [p["name"] for p in keep_hitters]
    pitcher_names = [p["name"] for p in pitcher_starters]
    return hitter_names, pitcher_names, lines, forecast_fallback_ids


def triage():
    """Returns (list of human-readable action lines, error string or None)."""
    cfg = load_config()
    league = get_league(cfg)
    my_team = next((t for t in league.teams if t.team_id == TEAM_ID), None)
    if not my_team:
        return [], f"team {TEAM_ID} not found in league"

    players = parse_roster(my_team)
    # force_refresh, always: the schedule is cached once a day, and a copy written at 4am says
    # every game is still in Preview, which would hide every lock this pass depends on.
    matchups = build_team_matchups(force_refresh=True)

    def is_starting_today(p):
        probable = matchups.get(p["pro_team"], {}).get("probable_pitcher")
        return bool(probable) and normalize(probable) == normalize(p["name"])

    two_starts = get_pitcher_starts_in_window(days=7)
    opp_sp_lookup = build_opp_sp_era_lookup(league)

    def score(p):
        return (score_pitcher(p, matchups, two_starts) if is_pitcher(p)
                else score_hitter(p, matchups, opp_sp_lookup))

    def forecast_score(p):
        return (forecast_score_pitcher(p, two_starts) if is_pitcher(p)
                else forecast_score_hitter(p))

    fx = fantasy_exec.get_roster()
    if not fx.get("ok"):
        return [], fx.get("error") or "could not read the roster for eligibility checking"
    fx_roster = fx["roster"]

    def seatable(names):
        _, err = fantasy_exec.compute_moves(fx_roster, names)
        return err is None

    hitter_names, pitcher_names, lines, forecast_fallback_ids = _triage(
        players, matchups, score, forecast_score, is_starting_today, seatable,
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

    # ESPN answers 200 to a move it then ignores, so a successful POST is not evidence the
    # lineup changed. set_lineup now reads the roster back and reports `stuck`; believe that,
    # not `ok`. Same lesson as the morning brief, which stopped claiming an add/drop was done
    # before ESPN confirmed it.
    if not DRY_RUN:
        wanted = res.get("moves") or []
        stuck = res.get("stuck") or []
        if wanted and stuck:
            names = ", ".join(m["name"] for m in stuck)
            if len(stuck) == len(wanted):
                # Nothing took. Say nothing rather than post a change that did not happen.
                log(f"ESPN accepted the request and applied none of it: {names}")
                return [], None
            lines.append(f"ESPN did not apply: {names}")

    # Sanity check — log-only, nothing here should ever fire given the passes above. Forecast
    # fallback picks are deliberately active with no game today, so they're excluded here.
    final_ids = {p["player_id"] for p in players if p["name"] in set(starters)}
    for p in players:
        if p["player_id"] not in final_ids:
            continue
        if p["injury"] in IL_STATUSES:
            lines.append(f"SANITY: {p['name']} is active and still shows {p['injury']}")
        elif p["player_id"] not in forecast_fallback_ids and \
                not matchups.get(p["pro_team"], {}).get("has_game"):
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
