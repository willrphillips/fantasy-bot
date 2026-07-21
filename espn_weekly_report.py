from __future__ import annotations

import sys
import os
import traceback
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from espn_utils import (
    load_config, get_league, parse_roster,
    get_standings, get_current_matchup, get_free_agents,
    send_email, log,
    SLOT_NAMES, BENCH_SLOT, IL_SLOTS, ACTIVE_SLOTS,
    IL_STATUSES, ACTIVE_STATUSES,
    TEAM_ID, LEAGUE_ID,
)

# ── Report sections ────────────────────────────────────────────────────────────

def section_standings(league) -> list[str]:
    lines = ["\n📊  STANDINGS\n"]
    standings = get_standings(league)
    lines.append(f"{'#':<3} {'Team':<32} {'W':>4} {'L':>4} {'T':>3} {'PCT':>6}")
    lines.append("─" * 57)
    for r in standings:
        marker = " ◀ YOU" if r["id"] == TEAM_ID else ""
        lines.append(
            f"{r['rank']:<3} {r['name'][:32]:<32} {r['W']:>4} {r['L']:>4} "
            f"{r['T']:>3} {r['pct']:>6.3f}{marker}"
        )
    my = next((r for r in standings if r["id"] == TEAM_ID), None)
    if my:
        gb_msg = ""
        if my["rank"] > 1:
            leader = standings[0]
            gb = ((leader["W"] - my["W"]) + (my["L"] - leader["L"])) / 2
            gb_msg = f", {gb:.1f} GB"
        lines.append(f"\n  → #{my['rank']} of {len(standings)}{gb_msg}")
    return lines


def section_matchup(league) -> list[str]:
    lines = ["\n⚔️   CURRENT MATCHUP\n"]
    m = get_current_matchup(league)
    if m:
        lead = ("LEADING" if m["my_pts"] > m["opp_pts"]
                else "TRAILING" if m["my_pts"] < m["opp_pts"] else "TIED")
        lines.append(f"  vs. {m['opp_name']}")
        lines.append(f"  Score: You {m['my_pts']:.1f}  —  {m['opp_pts']:.1f} {m['opp_name'][:24]}")
        lines.append(f"  Status: {lead}")
    else:
        lines.append("  No active matchup found.")
    return lines


def section_roster_health(league) -> list[str]:
    my_team = next((t for t in league.teams if t.team_id == TEAM_ID), None)
    if not my_team:
        return ["\n[Roster unavailable]"]

    players = parse_roster(my_team)
    lines   = [f"\n🏥  ROSTER HEALTH  ({len(players)} players)\n"]

    active_injured = [p for p in players if p["is_active"] and p["injury"] not in ACTIVE_STATUSES]
    il_players     = [p for p in players if p["on_il"]]
    bench_players  = [p for p in players if p["on_bench"]]
    il_healthy     = [p for p in il_players if p["injury"] in ACTIVE_STATUSES]

    if active_injured:
        lines.append("  ⚠️  INJURED IN ACTIVE SLOTS:")
        for p in active_injured:
            lines.append(f"     • {p['name']:<26}  [{p['injury']}]  ({p['slot_label']})")
    else:
        lines.append("  ✅ All active slot players are healthy.")

    if il_players:
        lines.append(f"\n  IL SLOTS ({len(il_players)}):")
        for p in il_players:
            status = p["injury"] if p["injury"] not in ACTIVE_STATUSES else "READY TO ACTIVATE"
            lines.append(f"     • {p['name']:<26}  [{status}]")

    if il_healthy:
        lines.append(f"\n  ⚡ READY TO COME OFF IL:")
        for p in il_healthy:
            lines.append(f"     • {p['name']}")

    if bench_players:
        lines.append(f"\n  BENCH ({len(bench_players)}):")
        for p in bench_players:
            inj = f"  [{p['injury']}]" if p["injury"] not in ACTIVE_STATUSES else ""
            lines.append(f"     • {p['name']}{inj}")

    return lines


def section_free_agents(league) -> list[str]:
    lines = ["\n🆓  TOP FREE AGENTS (by % owned)\n"]
    lines.append(f"  {'Name':<26} {'Pos':<10} {'%Own':>6}")
    lines.append("  " + "─" * 46)

    try:
        fa_players = get_free_agents(league, limit=25)
        shown = 0
        for p in fa_players:
            name    = p.name
            pct     = round(getattr(p, "percent_owned", 0) or 0, 1)
            pos_str = getattr(p, "position", "")[:10]
            lines.append(f"  {name:<26} {pos_str:<10} {pct:>5.1f}%")
            shown += 1
            if shown >= 20:
                break
        if shown == 0:
            lines.append("  (No free agent data returned)")
    except Exception as e:
        lines.append(f"  [Error: {e}]")

    return lines


def section_league_overview(league) -> list[str]:
    lines = ["\n🗺️   LEAGUE OVERVIEW\n"]
    standings = get_standings(league)

    try:
        box_scores  = league.box_scores()
        matchups    = {}
        teams_by_id = {t.team_id: t for t in league.teams}
        for box in box_scores:
            ht = box.home_team.team_id if hasattr(box.home_team, 'team_id') else None
            at = box.away_team.team_id if hasattr(box.away_team, 'team_id') else None
            if ht: matchups[ht] = at
            if at: matchups[at] = ht
    except Exception:
        matchups    = {}
        teams_by_id = {t.team_id: t for t in league.teams}

    for r in standings:
        opp_id   = matchups.get(r["id"])
        opp_name = teams_by_id[opp_id].team_name if opp_id and opp_id in teams_by_id else "BYE"
        marker   = " ← YOU" if r["id"] == TEAM_ID else ""
        lines.append(
            f"  #{r['rank']:>2}  {r['name'][:28]:<28}  "
            f"{r['W']}-{r['L']}  vs. {opp_name[:20]}{marker}"
        )
    return lines


def section_trade_targets(league) -> list[str]:
    lines = ["\n🔄  TRADE LANDSCAPE\n"]
    standings = get_standings(league)
    n = len(standings)

    sellers = [r for r in standings if r["rank"] > n * 0.6]
    buyers  = [r for r in standings if r["rank"] <= n * 0.3]

    if buyers:
        lines.append("  BUYER TEAMS (contenders — may want proven pieces):")
        for r in buyers:
            lines.append(f"     • #{r['rank']} {r['name']}  ({r['W']}-{r['L']})")

    if sellers:
        lines.append("\n  SELLER TEAMS (struggling — may be open to deals):")
        for r in sellers:
            lines.append(f"     • #{r['rank']} {r['name']}  ({r['W']}-{r['L']})")

    lines.append(
        "\n  Tip: Target categories you're losing in your matchup.\n"
        "  Offer bench depth to sellers for a difference-making starter."
    )
    return lines


# ── Full report ────────────────────────────────────────────────────────────────
def build_weekly_report(cfg: dict) -> str:
    now    = datetime.now().strftime("%A, %B %-d %Y  %-I:%M %p")
    lines  = []
    lines.append("⚾  CAPTAIN PHILLIPS CLUBHOUSE — Weekly Analysis")
    lines.append(f"   {now}")
    lines.append("=" * 60)

    league = get_league(cfg)

    lines += section_standings(league)
    lines += section_matchup(league)
    lines += section_roster_health(league)
    lines += section_free_agents(league)
    lines += section_league_overview(league)
    lines += section_trade_targets(league)

    lines.append(f"\n💡  WEEKLY REMINDERS")
    lines.append("  • Nightly script handles IL moves and injury swaps automatically")
    lines.append("  • Stream SPs vs weak lineups early in the week for K/W")
    lines.append("  • Monitor WHIP/ERA — pull starters after back-to-back rough outings")
    lines.append(f"  • ESPN: https://fantasy.espn.com/baseball/league?leagueId={LEAGUE_ID}")

    lines.append(f"\n{'=' * 60}")
    lines.append("Captain Phillips Bot — Weekly Report 🤖")
    lines.append("=" * 60)
    return "\n".join(lines)


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    config_path = None
    dry_run     = False
    for arg in sys.argv[1:]:
        if arg == "--dry-run":
            dry_run = True
        else:
            config_path = arg

    try:
        cfg = load_config(config_path)
    except FileNotFoundError:
        log("ERROR: config.json not found.")
        sys.exit(1)

    log("=" * 55)
    log("ESPN Weekly Report — Captain Phillips")
    log("=" * 55)

    try:
        report = build_weekly_report(cfg)
    except Exception:
        log("ERROR building report:")
        traceback.print_exc()
        sys.exit(1)

    print(report)

    if dry_run or cfg.get("dry_run", False):
        log("Dry run — skipping email.")
        return

    week_str = datetime.now().strftime("Week of %b %-d")
    subject  = f"⚾ Fantasy Baseball Weekly Report — {week_str}"

    try:
        send_email(cfg, subject, report)
        log(f"Email sent to {cfg.get('recipient_email', cfg['gmail_address'])}")
    except Exception:
        log("Email failed:")
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
