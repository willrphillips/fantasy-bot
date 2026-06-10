#!/usr/bin/env python3
"""Set the Captain Phillips lineup on ESPN — always respecting eligibility.

Runs on the iMac ("Cocky-Claude"), where ESPN is reachable and config.json
holds the espn_s2 / swid cookies. It CANNOT run from Claude Code on the web —
that environment can't reach ESPN.

An approved proposal = a list of desired STARTERS (your 9 hitters + the
pitchers you want active). compute_moves() seats each starter in a slot they
are ELIGIBLE for (bipartite matching against ESPN's eligibleSlots), benches
everyone else, and returns the minimal set of moves. If a starter can't be
legally seated it returns an error instead — it never makes an illegal move.

Usage:
  python3 set_lineup.py --starters starters.json            # dry run (default)
  python3 set_lineup.py --starters starters.json --apply    # actually submit
  python3 set_lineup.py --example                           # print a template
"""
from __future__ import annotations
import argparse, json, sys
import espn_utils as eu

HITTER_SLOTS  = [0, 1, 2, 3, 4, 5, 6, 7, 12]   # C 1B 2B 3B SS OF OF OF UTIL
PITCHER_SLOTS = [11, 13, 14, 15, 16, 17, 18]    # 7 pitcher slots
SLOT_GROUP    = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 5, 7: 5, 12: None}

EXAMPLE = {"starters": [
    "Shea Langeliers", "Freddie Freeman", "Casey Schmitt", "Max Muncy",
    "Kevin McGonigle", "Juan Soto", "JJ Bleday", "Brandon Marsh",
    "Willson Contreras", "Yoshinobu Yamamoto", "Emerson Hancock", "Bryce Elder",
    "Trey Yesavage", "Braxton Ashcraft", "Gregory Soto", "Ben Brown",
]}


def is_pitcher(p: dict) -> bool:
    return 11 in p["eligible"]


def hitter_can_fill(elig: list[int], slot: int) -> bool:
    g = SLOT_GROUP[slot]
    if g is None:        # UTIL — any hitter
        return True
    if g == 5:           # OF slots — eligible if OF (5) in eligibleSlots
        return 5 in elig
    return g in elig


def match(starters: list[dict], slots: list[int], can_fill) -> dict | None:
    """Kuhn's bipartite matching. Returns {starter_index: slot_id} or None."""
    slot_to_starter: dict[int, int] = {}

    def try_assign(i: int, seen: set[int]) -> bool:
        for s in slots:
            if s not in seen and can_fill(starters[i]["eligible"], s):
                seen.add(s)
                if s not in slot_to_starter or try_assign(slot_to_starter[s], seen):
                    slot_to_starter[s] = i
                    return True
        return False

    for i in range(len(starters)):
        if not try_assign(i, set()):
            return None
    return {i: s for s, i in slot_to_starter.items()}


def resolve(name: str, roster: list[dict]) -> dict | None:
    low = name.strip().lower()
    for p in roster:
        if p["name"].lower() == low:
            return p
    hits = [p for p in roster if low in p["name"].lower()]
    return hits[0] if len(hits) == 1 else None


def compute_moves(roster: list[dict], starter_names: list[str]):
    """Return (moves, error). Eligibility-safe; error is a string or None."""
    starters, missing = [], []
    for n in starter_names:
        p = resolve(n, roster)
        (starters.append(p) if p else missing.append(n))
    if missing:
        return None, f"Not on roster (or ambiguous): {', '.join(missing)}"

    il = [p for p in starters if p["on_il"]]
    if il:
        return None, f"Can't start IL players: {', '.join(p['name'] for p in il)}"

    hitters  = [p for p in starters if not is_pitcher(p)]
    pitchers = [p for p in starters if is_pitcher(p)]
    if len(hitters) > len(HITTER_SLOTS):
        return None, f"{len(hitters)} hitters named; only {len(HITTER_SLOTS)} hitter slots."
    if len(pitchers) > len(PITCHER_SLOTS):
        return None, f"{len(pitchers)} pitchers named; only {len(PITCHER_SLOTS)} P slots."

    h_assign = match(hitters, HITTER_SLOTS, hitter_can_fill)
    if h_assign is None:
        return None, ("Can't seat all hitters within their eligible positions. "
                      "Check your 9 cover C/1B/2B/3B/SS/3×OF/UTIL.")
    p_assign = match(pitchers, PITCHER_SLOTS, lambda e, s: True)  # any P slot

    target: dict[int, int] = {}
    for i, slot in h_assign.items():
        target[hitters[i]["player_id"]] = slot
    for i, slot in p_assign.items():
        target[pitchers[i]["player_id"]] = slot
    for p in roster:                     # everyone else (non-IL) -> bench
        if p["player_id"] not in target and not p["on_il"]:
            target[p["player_id"]] = eu.BENCH_SLOT

    moves = [
        {"player_id": p["player_id"], "name": p["name"],
         "from_slot": p["slot"], "to_slot": target[p["player_id"]]}
        for p in roster
        if p["player_id"] in target and p["slot"] != target[p["player_id"]]
    ]
    return moves, None


def main() -> int:
    ap = argparse.ArgumentParser(description="Set the ESPN lineup, eligibility-safe.")
    ap.add_argument("--starters", help="JSON file with a 'starters' list")
    ap.add_argument("--apply", action="store_true", help="submit (default is dry run)")
    ap.add_argument("--example", action="store_true", help="print a starters.json template")
    ap.add_argument("--scoring-period", type=int, default=None)
    args = ap.parse_args()

    if args.example:
        print(json.dumps(EXAMPLE, indent=2))
        return 0
    if not args.starters:
        ap.error("--starters is required (or use --example)")

    with open(args.starters) as f:
        want_names = json.load(f)["starters"]

    cfg = eu.load_config()
    cookies = eu.cookies_from_cfg(cfg)
    league = eu.get_league(cfg)
    sp = args.scoring_period or getattr(league, "scoringPeriodId", None)
    if sp is None:
        eu.log("❌ Could not determine scoringPeriodId — pass --scoring-period N.")
        return 1

    my_team = next((t for t in league.teams if t.team_id == eu.TEAM_ID), None)
    if my_team is None:
        eu.log(f"❌ Team id {eu.TEAM_ID} not found in league.")
        return 1
    roster = eu.parse_roster(my_team)

    moves, err = compute_moves(roster, want_names)
    if err:
        eu.log("❌ " + err)
        return 1

    eu.log(f"Lineup for scoringPeriod {sp} — {'APPLY' if args.apply else 'DRY RUN'}:")
    ok = eu.apply_lineup_moves(cookies, sp, moves, dry_run=not args.apply)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
