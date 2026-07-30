"""
ESPN Nightly Lineup Optimizer — Captain Phillips
Now with actual optimization based on:
  - Has-game-today (MLB Stats API)
  - Probable pitcher detection
  - Two-start week detection
  - Season + recent + projected stats from ESPN
  - Opponent SP quality (basic ace detection by season ERA)
"""
from __future__ import annotations

import sys
import traceback
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from espn_utils import (
    load_config, cookies_from_cfg, get_league, parse_roster, move_player,
    SLOT_NAMES, ACTIVE_SLOTS, BENCH_SLOT, IL_SLOTS,
    IL_STATUSES, ACTIVE_STATUSES,
    TEAM_ID, log,
)
from daily_projections import build_team_matchups, get_pitcher_starts_in_window
from name_matcher import normalize, best_match

# Hard cap on moves per night — safety bail-out if something's wrong
MAX_MOVES = 8

# Pitcher-eligible slot IDs (we treat these specially)
PITCHER_SLOT_NAMES = {"P", "SP", "RP"}


def is_pitcher(player):
    """True if player is pitcher-eligible (any P slot in eligibility list)."""
    return any(SLOT_NAMES.get(s) in PITCHER_SLOT_NAMES for s in player["eligible"])


def opp_sp_difficulty(opp_sp_name, all_pitcher_seasons):
    """Returns -0.15 (ace), 0, or +0.05 (TBD/weak) for hitter score adjustment.
    all_pitcher_seasons: dict {normalized_name: season_ERA} from league rosters."""
    if not opp_sp_name:
        return 0.05  # TBD / opener / bullpen day = slight bonus for hitters
    norm = normalize(opp_sp_name)
    era = all_pitcher_seasons.get(norm)
    if era is None:
        return 0  # unknown, assume average
    if era < 3.00:
        return -0.15  # ace
    if era > 4.50:
        return 0.05  # below avg
    return 0


def score_hitter(player, matchups, opp_sp_era_lookup):
    """Score a hitter for today. Higher = start them.
    0 if no game. Otherwise blend recent/season/projected OPS + matchup."""
    team = player["pro_team"]
    matchup = matchups.get(team, {})
    if not matchup.get("has_game"):
        return 0.0

    # OPS blend - recent gets most weight if it has any sample, else lean season
    recent_ops = player["recent_stats"].get("OPS", 0) or 0
    recent_pa = player["recent_stats"].get("PA", 0) or 0
    season_ops = player["season_stats"].get("OPS", 0) or 0
    proj_ops = player["proj_stats"].get("OPS", 0) or 0

    if recent_pa >= 10:
        ops_blend = 0.5 * recent_ops + 0.3 * season_ops + 0.2 * proj_ops
    else:
        # too small a recent sample - lean season
        ops_blend = 0.6 * season_ops + 0.4 * proj_ops

    score = 1.0 + ops_blend  # baseline 1.0 for "playing today" + OPS bonus
    score += opp_sp_difficulty(matchup.get("opp_probable_pitcher"), opp_sp_era_lookup)

    # Penalize DTD/Q
    if player["injury"] in ("DTD", "QUESTIONABLE"):
        score -= 0.3

    return score


def score_pitcher(player, matchups, two_start_counts):
    """Score a pitcher for today. Higher = start them.
    For pitchers, MUST be probable today to get full credit."""
    team = player["pro_team"]
    matchup = matchups.get(team, {})
    pname_norm = normalize(player["name"])
    probable_norm = normalize(matchup.get("probable_pitcher", "") or "")

    is_starting_today = (probable_norm == pname_norm) if probable_norm else False
    is_relief = "RP" in [SLOT_NAMES.get(s) for s in player["eligible"]] and "SP" not in [SLOT_NAMES.get(s) for s in player["eligible"]]

    if is_relief:
        # Relievers - just need team to be playing
        if not matchup.get("has_game"):
            return 0.0
        season_era = player["season_stats"].get("ERA", 5.0) or 5.0
        score = 0.8 + max(0, (5.0 - season_era) * 0.1)
        if player["injury"] in ("DTD", "QUESTIONABLE"):
            score -= 0.3
        return score

    # Starting pitcher logic
    if not is_starting_today:
        # SP not pitching today — score very low so probable SPs always beat them
        return 0.0

    # SP is probable today
    season_era = player["season_stats"].get("ERA", 5.0) or 5.0
    season_whip = player["season_stats"].get("WHIP", 1.5) or 1.5
    score = 1.5  # base for probable today
    score += max(0, (5.0 - season_era) * 0.2)  # ERA bonus, big
    score += max(0, (1.50 - season_whip) * 0.5)  # WHIP bonus

    # Two-start bonus (over the next 7 days)
    if two_start_counts.get(player["name"], 0) >= 2:
        score += 0.3

    if player["injury"] in ("DTD", "QUESTIONABLE"):
        score -= 0.5

    return score


def forecast_score_hitter(player):
    """Score a hitter for the week ahead, ignoring today's game entirely. Last-resort fallback
    for roster_triage: when nobody eligible has a game today, we'd rather seat the best bet for
    the days ahead than leave the slot empty."""
    season_ops = player["season_stats"].get("OPS", 0) or 0
    proj_ops = player["proj_stats"].get("OPS", 0) or 0
    score = 0.6 * season_ops + 0.4 * proj_ops
    if player["injury"] in ("DTD", "QUESTIONABLE"):
        score -= 0.3
    return score


def forecast_score_pitcher(player, two_start_counts):
    """Score a pitcher for the week ahead, ignoring today's probable/has-game status. Same
    last-resort role as forecast_score_hitter, for the pitching staff."""
    season_era = player["season_stats"].get("ERA", 5.0) or 5.0
    season_whip = player["season_stats"].get("WHIP", 1.5) or 1.5
    score = 1.0 + max(0, (5.0 - season_era) * 0.2) + max(0, (1.50 - season_whip) * 0.5)
    score += 0.3 * two_start_counts.get(player["name"], 0)
    if player["injury"] in ("DTD", "QUESTIONABLE"):
        score -= 0.3
    return score


def build_opp_sp_era_lookup(league):
    """Build {normalized_pitcher_name: season_ERA} across the entire league.
    Used to evaluate opp SP difficulty for hitter scoring."""
    lookup = {}
    for team in league.teams:
        for p in parse_roster(team):
            if not is_pitcher(p):
                continue
            era = p["season_stats"].get("ERA")
            if era is not None and era > 0:
                lookup[normalize(p["name"])] = era
    return lookup


def optimize_lineup(cfg, scoring_period, dry_run=False):
    cookies = cookies_from_cfg(cfg)
    league = get_league(cfg)

    my_team = next((t for t in league.teams if t.team_id == TEAM_ID), None)
    if not my_team:
        log(f"ERROR: Team {TEAM_ID} not found.")
        return 0

    log("Loading roster, matchups, and pitcher windows...")
    players = parse_roster(my_team)
    matchups = build_team_matchups()
    two_starts = get_pitcher_starts_in_window(days=7)
    opp_sp_lookup = build_opp_sp_era_lookup(league)

    moves_made = 0

    # ── Step 1: IL housekeeping (move healthy off IL) ──────────────────────
    for p in [x for x in players if x["on_il"] and x["injury"] in ACTIVE_STATUSES]:
        log(f"  IL→BE: {p['name']} (healthy)")
        if move_player(cookies, scoring_period, p["player_id"], p["name"],
                       p["slot"], BENCH_SLOT, dry_run):
            moves_made += 1
            p["on_il"] = False; p["on_bench"] = True; p["is_active"] = False
            p["slot"] = BENCH_SLOT
        if moves_made >= MAX_MOVES: return moves_made

    # ── Step 2: Active→IL for newly injured ────────────────────────────────
    il_used = {p["slot"] for p in players if p["on_il"]}
    available_il = [s for s in sorted(IL_SLOTS) if s not in il_used]
    for p in [x for x in players if x["injury"] in IL_STATUSES and x["is_active"]]:
        if not available_il:
            log(f"  WARN: {p['name']} needs IL, no slots open"); continue
        target = available_il.pop(0)
        log(f"  ACT→IL: {p['name']} ({p['injury']})")
        if move_player(cookies, scoring_period, p["player_id"], p["name"],
                       p["slot"], target, dry_run):
            moves_made += 1
            p["on_il"] = True; p["is_active"] = False; p["slot"] = target
        if moves_made >= MAX_MOVES: return moves_made

    # ── Step 3: Score every healthy player ─────────────────────────────────
    log("")
    log("Scoring players...")
    for p in players:
        if p["on_il"]:
            p["score"] = -1
            continue
        if is_pitcher(p):
            p["score"] = score_pitcher(p, matchups, two_starts)
        else:
            p["score"] = score_hitter(p, matchups, opp_sp_lookup)
        team = p["pro_team"]
        m = matchups.get(team, {})
        game_str = f"vs {m.get('opponent')}" if m.get("has_game") else "OFF"
        log(f"  {p['name']:25s} ({p['pro_team']:3s}) {game_str:8s} score={p['score']:.2f}")

    # ── Step 4: Optimize each active slot ──────────────────────────────────
    log("")
    log("Optimizing active slots...")
    # Refresh active vs bench after IL moves
    active_players = [p for p in players if p["is_active"]]
    bench_players = [p for p in players if p["on_bench"]]

    # For each active slot currently filled (or empty), consider bench upgrades
    # Iterate through positions: if a bench player at this slot scores higher
    # than the current occupant by margin, swap.
    SWAP_MARGIN = 0.15  # only swap if bench is materially better

    for active in list(active_players):
        if active["score"] < 0:
            continue
        slot = active["slot"]
        # Find best bench player eligible for this slot
        candidates = [b for b in bench_players if slot in b["eligible"] and b["score"] > active["score"] + SWAP_MARGIN]
        if not candidates:
            continue
        best = max(candidates, key=lambda x: x["score"])
        log(f"  SWAP {SLOT_NAMES.get(slot)}: {active['name']} ({active['score']:.2f}) -> {best['name']} ({best['score']:.2f})")
        # Bench the active, promote the bench player
        if move_player(cookies, scoring_period, active["player_id"], active["name"],
                       slot, BENCH_SLOT, dry_run):
            moves_made += 1
            active["slot"] = BENCH_SLOT; active["is_active"] = False; active["on_bench"] = True
            bench_players.append(active)
        if move_player(cookies, scoring_period, best["player_id"], best["name"],
                       BENCH_SLOT, slot, dry_run):
            moves_made += 1
            best["slot"] = slot; best["is_active"] = True; best["on_bench"] = False
            bench_players.remove(best)
        if moves_made >= MAX_MOVES:
            log(f"  Hit MAX_MOVES cap ({MAX_MOVES})")
            return moves_made

    # ── Step 5: Fill empty active slots from bench ─────────────────────────
    log("")
    log("Filling empty slots...")
    filled_slots = {p["slot"] for p in players if p["is_active"]}
    # Determine which active slots are empty by looking at all ACTIVE_SLOTS
    for slot in sorted(ACTIVE_SLOTS):
        if slot in filled_slots:
            continue
        # Find best bench player for this slot
        candidates = [b for b in bench_players if slot in b["eligible"] and b["score"] > 0]
        if not candidates:
            log(f"  No eligible bench player for empty {SLOT_NAMES.get(slot)}")
            continue
        best = max(candidates, key=lambda x: x["score"])
        log(f"  FILL {SLOT_NAMES.get(slot)}: {best['name']} ({best['score']:.2f})")
        if move_player(cookies, scoring_period, best["player_id"], best["name"],
                       BENCH_SLOT, slot, dry_run):
            moves_made += 1
            best["slot"] = slot; best["is_active"] = True; best["on_bench"] = False
            bench_players.remove(best)
            filled_slots.add(slot)
        if moves_made >= MAX_MOVES:
            log(f"  Hit MAX_MOVES cap ({MAX_MOVES})")
            return moves_made

    return moves_made


def main():
    config_path = None
    dry_run = False
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
    log("ESPN Nightly Lineup Optimizer — Captain Phillips")
    if dry_run:
        log("*** DRY RUN — no changes will be submitted ***")
    log("=" * 55)

    try:
        league = get_league(cfg)
        scoring_period = league.currentMatchupPeriod or 1
        log(f"Scoring period: {scoring_period}")

        moves = optimize_lineup(cfg, scoring_period, dry_run=dry_run)
        log("")
        log(f"Done. {moves} move(s) made.")
    except Exception:
        log("ERROR during lineup optimization:")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
