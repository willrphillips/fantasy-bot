#!/usr/bin/env python3
"""Set the Captain Phillips lineup on ESPN — always respecting eligibility.

This is now a thin CLI over `fantasy_exec`, which holds the one real
implementation. It used to carry its own copy of the slot table and the
matcher, and that copy was wrong in two ways that made every submission fail:

  * the pitcher slots were guessed as 11/14..18 when this league's P slots are
    all id 13, and slot 19 (IF) has a capacity of 0 here, so ESPN answered 409;
  * the matcher keyed on slot *id*, but repeated slots share an id (three OF
    slots are all 5). It seated one outfielder and declared the rest unplaceable.

Rather than re-fix them in parallel, the logic is gone and this file delegates.

An approved proposal = a list of desired STARTERS (your 9 hitters + the
pitchers you want active). Everyone else who isn't on IL goes to the bench. If
a starter can't be legally seated, nothing is submitted and you get the reason.

Usage:
  python3 set_lineup.py --starters starters.json            # dry run (default)
  python3 set_lineup.py --starters starters.json --apply    # actually submit
  python3 set_lineup.py --example                           # print a template
"""
from __future__ import annotations
import argparse, json, sys
import fantasy_exec as fx

# Kept as module-level names because other scripts import them from here.
HITTER_SLOTS = fx.DEFAULT_HITTER_SLOTS
PITCHER_SLOTS = fx.DEFAULT_PITCHER_SLOTS
SLOT_GROUP = fx.SLOT_GROUP

EXAMPLE = {"starters": [
    "Shea Langeliers", "Freddie Freeman", "Casey Schmitt", "Max Muncy",
    "Kevin McGonigle", "Juan Soto", "JJ Bleday", "Brandon Marsh",
    "Willson Contreras", "Yoshinobu Yamamoto", "Emerson Hancock", "Bryce Elder",
    "Trey Yesavage", "Braxton Ashcraft", "Gregory Soto", "Ben Brown",
]}


def compute_moves(roster: list[dict], starter_names: list[str]):
    """Return (moves, error). Compatibility shim — apply_pending.py calls this.

    Both parse_roster implementations emit the same player dicts, so a roster
    built by espn_utils can be passed straight in.
    """
    return fx.compute_moves(roster, starter_names)


def main() -> int:
    ap = argparse.ArgumentParser(description="Set the ESPN lineup, eligibility-safe.")
    ap.add_argument("--starters", help="JSON file with a 'starters' list")
    ap.add_argument("--apply", action="store_true", help="submit (default is dry run)")
    ap.add_argument("--example", action="store_true", help="print a starters.json template")
    ap.add_argument("--league", default=None, help="league key from config.json")
    args = ap.parse_args()

    if args.example:
        print(json.dumps(EXAMPLE, indent=2))
        return 0
    if not args.starters:
        ap.error("--starters is required (or use --example)")

    with open(args.starters) as f:
        want_names = json.load(f)["starters"]

    res = fx.set_lineup(want_names, dry_run=not args.apply, league=args.league)
    print(res["detail"])
    if res.get("error"):
        print(res["error"], file=sys.stderr)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
