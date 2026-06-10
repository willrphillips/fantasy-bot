#!/usr/bin/env python3
"""iMac executor — apply approved roster moves from pending_moves.json.

This closes the "approve in chat -> roster updates" loop. Cron it on the iMac
every few minutes. Each run: (optionally) git-pulls this repo, reads the queue,
runs any move whose id it hasn't applied yet, records the id so it never repeats,
logs the result, and emails a confirmation.

Delivery: when you approve a move in chat, the approved entry is appended to
pending_moves.json and committed to this repo. The iMac's `--pull` cron picks it
up on its next run and executes it. You never touch the terminal.

pending_moves.json schema:
  {"moves": [
     {"id": "2026-06-10-lineup", "type": "lineup", "apply": true,
      "starters": ["...16 names..."]},
     {"id": "2026-06-10-benbrown", "type": "waiver", "apply": true,
      "add": "Ben Brown", "drop": "Landen Roupp", "txn_type": "WAIVER"}
  ]}

`apply: false` (or omitting it) makes that entry a DRY RUN — logged, not submitted,
and NOT marked done, so you can flip it to true later.

Usage:
  python3 apply_pending.py                 # process queue once
  python3 apply_pending.py --pull          # git pull first, then process
  python3 apply_pending.py --dry-run       # force every entry to dry run
"""
from __future__ import annotations
import argparse, json, os, subprocess, sys
from datetime import datetime
import espn_utils as eu
import set_lineup as sl
import waiver_move as wm

HERE        = os.path.dirname(os.path.abspath(__file__))
QUEUE       = os.path.join(HERE, "pending_moves.json")
APPLIED_IDS = os.path.join(HERE, ".applied_moves")
APPLIED_LOG = os.path.join(HERE, "applied_log.jsonl")


def load_applied() -> set[str]:
    if not os.path.exists(APPLIED_IDS):
        return set()
    with open(APPLIED_IDS) as f:
        return {ln.strip() for ln in f if ln.strip()}


def record(move_id: str, ok: bool, detail: str):
    with open(APPLIED_IDS, "a") as f:
        f.write(move_id + "\n")
    with open(APPLIED_LOG, "a") as f:
        f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"),
                            "id": move_id, "ok": ok, "detail": detail}) + "\n")


def run_lineup(mv, roster, cookies, sp, dry) -> tuple[bool, str]:
    moves, err = sl.compute_moves(roster, mv["starters"])
    if err:
        return False, err
    ok = eu.apply_lineup_moves(cookies, sp, moves, dry_run=dry)
    return ok, f"{len(moves)} lineup move(s)"


def run_waiver(mv, league, roster, cookies, sp, dry) -> tuple[bool, str]:
    add_p = wm.find_fa(league, mv["add"])
    if add_p is None:
        return False, f"free agent '{mv['add']}' not found"
    drop_p = wm.find_rostered(roster, mv["drop"])
    if drop_p is None:
        return False, f"'{mv['drop']}' not on roster"
    ok = eu.waiver_move(cookies, sp, add_p.playerId, add_p.name,
                        drop_p["player_id"], drop_p["name"],
                        txn_type=mv.get("txn_type", "WAIVER"), dry_run=dry)
    return ok, f"add {add_p.name} / drop {drop_p['name']}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Apply approved roster moves.")
    ap.add_argument("--pull", action="store_true", help="git pull this repo first")
    ap.add_argument("--dry-run", action="store_true", help="force every entry to dry run")
    args = ap.parse_args()

    if args.pull:
        subprocess.run(["git", "-C", HERE, "pull", "--quiet"], check=False)

    if not os.path.exists(QUEUE):
        return 0
    with open(QUEUE) as f:
        queue = json.load(f).get("moves", [])

    applied = load_applied()
    pending = [m for m in queue if m.get("id") and m["id"] not in applied]
    if not pending:
        return 0

    cfg = eu.load_config()
    cookies = eu.cookies_from_cfg(cfg)
    league = eu.get_league(cfg)
    sp = getattr(league, "scoringPeriodId", None)
    my_team = next((t for t in league.teams if t.team_id == eu.TEAM_ID), None)
    if sp is None or my_team is None:
        eu.log("❌ Could not load league / scoringPeriodId — aborting.")
        return 1
    roster = eu.parse_roster(my_team)

    results = []
    for mv in pending:
        mid = mv["id"]
        dry = args.dry_run or not mv.get("apply", False)
        eu.log(f"Processing {mid} ({mv.get('type')}) — {'DRY RUN' if dry else 'APPLY'}:")
        try:
            if mv.get("type") == "lineup":
                ok, detail = run_lineup(mv, roster, cookies, sp, dry)
            elif mv.get("type") == "waiver":
                ok, detail = run_waiver(mv, league, roster, cookies, sp, dry)
            else:
                ok, detail = False, f"unknown type {mv.get('type')!r}"
        except Exception as e:                       # noqa: BLE001 — log and continue
            ok, detail = False, f"exception: {e}"

        # Mark done only when a real APPLY succeeds; dry runs and failures stay
        # queued so you can flip apply->true or fix and rerun.
        if ok and not dry:
            record(mid, ok, detail)
        results.append(f"{'✅' if ok else ('•' if dry else '❌')} {mid}: {detail}")

    summary = "\n".join(results)
    eu.log("Run summary:\n" + summary)
    try:
        eu.send_email(cfg, "Fantasy: roster moves applied", summary)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
