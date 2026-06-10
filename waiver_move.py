#!/usr/bin/env python3
"""Add/drop on ESPN — approve a proposal, run one command. Runs on the iMac.

Adds a free-agent / waiver player and drops a rostered player in one atomic
ESPN transaction. The add lands on your bench; run set_lineup.py afterward to
slot it (which is eligibility-safe).

Usage:
  python3 waiver_move.py --add "Ben Brown" --drop "Landen Roupp"            # dry run
  python3 waiver_move.py --add "Ben Brown" --drop "Landen Roupp" --apply
  python3 waiver_move.py --add "X" --drop "Y" --type FREEAGENT --apply      # instant add (not on waivers)
"""
from __future__ import annotations
import argparse, sys
import espn_utils as eu


def find_fa(league, name: str):
    low = name.strip().lower()
    pool = eu.get_free_agents(league, limit=400)
    exact = [p for p in pool if getattr(p, "name", "").lower() == low]
    if exact:
        return exact[0]
    part = [p for p in pool if low in getattr(p, "name", "").lower()]
    return part[0] if len(part) == 1 else None


def find_rostered(roster: list[dict], name: str):
    low = name.strip().lower()
    exact = [p for p in roster if p["name"].lower() == low]
    if exact:
        return exact[0]
    part = [p for p in roster if low in p["name"].lower()]
    return part[0] if len(part) == 1 else None


def main() -> int:
    ap = argparse.ArgumentParser(description="ESPN add/drop, one transaction.")
    ap.add_argument("--add", required=True, help="free-agent/waiver player to ADD")
    ap.add_argument("--drop", required=True, help="rostered player to DROP")
    ap.add_argument("--type", default="WAIVER", choices=["WAIVER", "FREEAGENT"])
    ap.add_argument("--apply", action="store_true", help="submit (default is dry run)")
    ap.add_argument("--scoring-period", type=int, default=None)
    args = ap.parse_args()

    cfg = eu.load_config()
    cookies = eu.cookies_from_cfg(cfg)
    league = eu.get_league(cfg)
    sp = args.scoring_period or getattr(league, "scoringPeriodId", None)
    if sp is None:
        eu.log("❌ Could not determine scoringPeriodId — pass --scoring-period N.")
        return 1

    my_team = next((t for t in league.teams if t.team_id == eu.TEAM_ID), None)
    if my_team is None:
        eu.log(f"❌ Team id {eu.TEAM_ID} not found.")
        return 1
    roster = eu.parse_roster(my_team)

    add_p = find_fa(league, args.add)
    if add_p is None:
        eu.log(f"❌ Couldn't find an available free agent matching '{args.add}'.")
        return 1
    drop_p = find_rostered(roster, args.drop)
    if drop_p is None:
        eu.log(f"❌ '{args.drop}' isn't on your roster (or is ambiguous).")
        return 1

    eu.log(f"Add/drop for scoringPeriod {sp} — {'APPLY' if args.apply else 'DRY RUN'}:")
    ok = eu.waiver_move(
        cookies, sp,
        add_id=add_p.playerId, add_name=add_p.name,
        drop_id=drop_p["player_id"], drop_name=drop_p["name"],
        txn_type=args.type, dry_run=not args.apply,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
