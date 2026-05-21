from __future__ import annotations

import json
import os
import smtplib
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

try:
    import requests
    from espn_api.baseball import League
except ImportError as e:
    print(f"ERROR: Missing dependency — {e}\nRun: pip install requests espn-api")
    sys.exit(1)

# ── Constants ──────────────────────────────────────────────────────────────────
LEAGUE_ID = 2057904545
TEAM_ID   = 9
SEASON    = 2026
BASE_URL  = (
    f"https://fantasy.espn.com/apis/v3/games/flb"
    f"/seasons/{SEASON}/segments/0/leagues/{LEAGUE_ID}"
)

# Lineup slot ID → label
SLOT_NAMES = {
    0:"C", 1:"1B", 2:"2B", 3:"3B", 4:"SS",
    5:"OF", 6:"OF", 7:"OF",
    11:"P", 12:"UTIL",
    13:"P", 14:"P", 15:"P", 16:"P", 17:"P", 18:"P",
    19:"BE", 20:"IL", 21:"IL+",
}

ACTIVE_SLOTS = set(range(0, 19))
BENCH_SLOT   = 19
IL_SLOTS     = {20, 21}

IL_STATUSES     = {"OUT", "INJURED", "IL10", "IL15", "IL60"}
ACTIVE_STATUSES = {"ACTIVE", "NORMAL", ""}

# ── Config ─────────────────────────────────────────────────────────────────────
def load_config(path: str = None) -> dict:
    if path is None:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(script_dir, "config.json")
    with open(path) as f:
        return json.load(f)

def cookies_from_cfg(cfg: dict) -> dict:
    return {"espn_s2": cfg["espn_s2"], "SWID": cfg["swid"]}

def get_league(cfg: dict) -> League:
    return League(
        league_id=LEAGUE_ID,
        year=SEASON,
        espn_s2=cfg["espn_s2"],
        swid=cfg["swid"],
    )

# ── Roster parsing ─────────────────────────────────────────────────────────────
def parse_roster(team) -> list[dict]:
    """Convert espn_api Team roster into our standard player dicts."""
    players = []
    for p in team.roster:
        slot_id = _slot_id_from_name(p.lineupSlot)
        injury  = (p.injuryStatus or "ACTIVE").strip().upper()

        # Extract season + recent stat breakdowns from espn_api stats dict
        # Key 0 = season-to-date, 36 = recent (last 7d), projected lives inside [0]
        stats = getattr(p, "stats", {}) or {}
        season_block = stats.get(0, {}) if isinstance(stats, dict) else {}
        recent_block = stats.get(36, {}) if isinstance(stats, dict) else {}
        season_stats = season_block.get("breakdown", {}) if isinstance(season_block, dict) else {}
        recent_stats = recent_block.get("breakdown", {}) if isinstance(recent_block, dict) else {}
        proj_stats   = season_block.get("projected_breakdown", {}) if isinstance(season_block, dict) else {}

        players.append({
            "name":         p.name,
            "player_id":    p.playerId,
            "pro_team":     getattr(p, "proTeam", "") or "",
            "position":     getattr(p, "position", "") or "",
            "slot":         slot_id,
            "slot_label":   p.lineupSlot,
            "injury":       injury,
            "pct_own":      round(getattr(p, "percent_owned", 0) or 0, 1),
            "eligible":     [_slot_id_from_name(s) for s in (p.eligibleSlots or [])],
            "on_il":        slot_id in IL_SLOTS,
            "on_bench":     slot_id == BENCH_SLOT,
            "is_active":    slot_id in ACTIVE_SLOTS,
            "season_stats": season_stats,
            "recent_stats": recent_stats,
            "proj_stats":   proj_stats,
        })
    return players

def _slot_id_from_name(name: str) -> int:
    """Map slot name -> first matching slot ID. 'P' resolves to slot 11."""
    name = (name or "").strip()
    for k, v in SLOT_NAMES.items():
        if v == name:
            return k
    return 19  # default to bench

# ── Standings ──────────────────────────────────────────────────────────────────
def get_standings(league: League) -> list[dict]:
    rows = []
    for t in league.teams:
        rows.append({
            "id":   t.team_id,
            "name": t.team_name,
            "W":    t.wins,
            "L":    t.losses,
            "T":    t.ties,
            "pct":  t.wins / max(t.wins + t.losses + t.ties, 1),
            "pf":   getattr(t, "points_for", 0),
        })
    rows.sort(key=lambda x: (-x["pct"], -x["pf"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows

# ── Matchup ────────────────────────────────────────────────────────────────────
def get_current_matchup(league: League) -> dict | None:
    try:
        box_scores = league.box_scores()
        for box in box_scores:
            if box.home_team == TEAM_ID or (hasattr(box.home_team, 'team_id') and box.home_team.team_id == TEAM_ID):
                opp = box.away_team
                return {
                    "my_pts":   box.home_score,
                    "opp_pts":  box.away_score,
                    "opp_name": opp.team_name if hasattr(opp, 'team_name') else str(opp),
                }
            if box.away_team == TEAM_ID or (hasattr(box.away_team, 'team_id') and box.away_team.team_id == TEAM_ID):
                opp = box.home_team
                return {
                    "my_pts":   box.away_score,
                    "opp_pts":  box.home_score,
                    "opp_name": opp.team_name if hasattr(opp, 'team_name') else str(opp),
                }
    except Exception:
        pass
    return None

# ── Free agents ────────────────────────────────────────────────────────────────
def get_free_agents(league: League, limit: int = 25) -> list:
    try:
        return league.free_agents(size=limit)
    except Exception:
        return []

# ── Lineup change via ESPN transactions API ────────────────────────────────────
def move_player(cookies: dict, scoring_period: int,
                player_id: int, player_name: str,
                from_slot: int, to_slot: int,
                dry_run: bool = False) -> bool:
    from_label = SLOT_NAMES.get(from_slot, str(from_slot))
    to_label   = SLOT_NAMES.get(to_slot,   str(to_slot))

    if dry_run:
        log(f"  [DRY RUN] Would move {player_name}: {from_label} → {to_label}")
        return True

    payload = {
        "isKeepersTransaction": False,
        "scoringPeriodId": scoring_period,
        "teamId": TEAM_ID,
        "type": "LINEUP",
        "items": [{
            "fromLineupSlotId": from_slot,
            "toLineupSlotId":   to_slot,
            "playerId":         player_id,
            "type":             "LINEUP",
        }],
    }
    resp = requests.post(
        BASE_URL + "/transactions",
        cookies=cookies,
        json=payload,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=15,
    )
    if resp.status_code in (200, 201):
        log(f"  ✅ Moved {player_name}: {from_label} → {to_label}")
        return True
    else:
        log(f"  ❌ Failed to move {player_name}: HTTP {resp.status_code}")
        return False

# ── Email ──────────────────────────────────────────────────────────────────────
def send_email(cfg: dict, subject: str, body: str):
    smtp_host = cfg.get("smtp_host", "smtp.gmail.com")
    smtp_port = cfg.get("smtp_port", 587)
    sender    = cfg["gmail_address"]
    password  = cfg["gmail_app_password"]
    recipient = cfg.get("recipient_email", sender)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = sender
    msg["To"]      = recipient
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        server.starttls()
        server.login(sender, password)
        server.sendmail(sender, recipient, msg.as_string())

# ── Logging ────────────────────────────────────────────────────────────────────
def log(msg: str):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)
