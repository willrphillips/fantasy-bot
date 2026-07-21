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
    # fantasy.espn.com/apis/v3 is fronted by Akamai and now 403s every API call.
    # lm-api-writes accepts both reads and writes with the same cookies.
    f"https://lm-api-writes.fantasy.espn.com/apis/v3/games/flb"
    f"/seasons/{SEASON}/segments/0/leagues/{LEAGUE_ID}"
)

# Lineup slot ID → label. ESPN's real flb map — see fantasy_exec.SLOT_NAMES;
# the earlier guesswork here sent players to slot 19 (capacity 0) and every
# transaction came back a 409.
SLOT_NAMES = {
    0:"C", 1:"1B", 2:"2B", 3:"3B", 4:"SS",
    5:"OF", 6:"2B/SS", 7:"1B/3B",
    8:"LF", 9:"CF", 10:"RF", 11:"DH", 12:"UTIL",
    13:"P", 14:"SP", 15:"RP",
    16:"BE", 17:"IL", 19:"IF",
}

ACTIVE_SLOTS = set(range(0, 16))
BENCH_SLOT   = 16
IL_SLOTS     = {17}

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
    """Map slot name -> first matching slot ID. 'P' resolves to slot 13."""
    name = (name or "").strip()
    for k, v in SLOT_NAMES.items():
        if v == name:
            return k
    return BENCH_SLOT

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
        "type": "ROSTER",
        "executionType": "EXECUTE",
        "items": [{
            "fromLineupSlotId": from_slot,
            "toLineupSlotId":   to_slot,
            "playerId":         player_id,
            "type":             "LINEUP",
        }],
    }
    resp = requests.post(
        BASE_URL + "/transactions/",
        cookies=cookies,
        json=payload,
        headers={"Accept": "application/json", "Content-Type": "application/json",
                 "X-Fantasy-Source": "kona", "X-Fantasy-Platform": "kona-PROD"},
        timeout=15,
    )
    if resp.status_code in (200, 201):
        log(f"  ✅ Moved {player_name}: {from_label} → {to_label}")
        return True
    else:
        log(f"  ❌ Failed to move {player_name}: HTTP {resp.status_code}")
        return False

def apply_lineup_moves(cookies: dict, scoring_period: int,
                       moves: list[dict], dry_run: bool = False) -> bool:
    """Apply a set of lineup slot changes in ONE atomic transaction.

    moves: list of {player_id, name, from_slot, to_slot}. ESPN validates the
    END state, so submitting every swap together avoids the 'slot occupied'
    errors you hit moving players one at a time.
    """
    moves = [m for m in moves if m["from_slot"] != m["to_slot"]]
    if not moves:
        log("  No lineup changes needed — already optimal.")
        return True
    for m in moves:
        log(f"  {'[DRY RUN] ' if dry_run else ''}{m['name']}: "
            f"{SLOT_NAMES.get(m['from_slot'], m['from_slot'])} -> "
            f"{SLOT_NAMES.get(m['to_slot'], m['to_slot'])}")
    if dry_run:
        return True
    payload = {
        "isKeepersTransaction": False,
        "scoringPeriodId": scoring_period,
        "teamId": TEAM_ID,
        "type": "ROSTER",
        "executionType": "EXECUTE",
        "items": [
            {"fromLineupSlotId": m["from_slot"], "toLineupSlotId": m["to_slot"],
             "playerId": m["player_id"], "type": "LINEUP"}
            for m in moves
        ],
    }
    resp = requests.post(
        BASE_URL + "/transactions/",
        cookies=cookies,
        json=payload,
        headers={"Accept": "application/json", "Content-Type": "application/json",
                 "X-Fantasy-Source": "kona", "X-Fantasy-Platform": "kona-PROD"},
        timeout=15,
    )
    if resp.status_code in (200, 201):
        log(f"  ✅ Applied {len(moves)} lineup move(s).")
        return True
    log(f"  ❌ Lineup transaction failed: HTTP {resp.status_code} — {resp.text[:200]}")
    return False

def waiver_move(cookies: dict, scoring_period: int,
                add_id: int, add_name: str,
                drop_id: int, drop_name: str,
                txn_type: str = "WAIVER", bid: int = 0,
                dry_run: bool = False) -> bool:
    """Add a free-agent / waiver player and drop a rostered player atomically.

    txn_type: 'WAIVER' for a rolling-waiver claim (this league has no FAAB, so
    bid stays 0) or 'FREEAGENT' for an instant add of a player not on waivers.
    The added player lands on the bench (slot 19); slot him with set_lineup after.
    """
    log(f"  {'[DRY RUN] ' if dry_run else ''}ADD {add_name}  /  DROP {drop_name}  ({txn_type})")
    if dry_run:
        return True
    payload = {
        "bidAmount": bid,
        "executionType": "EXECUTE",
        "isActingAsTeamOwner": False,
        "isLeagueManager": False,
        "isPending": False,
        "scoringPeriodId": scoring_period,
        "teamId": TEAM_ID,
        "type": txn_type,
        "items": [
            {"fromTeamId": 0, "isKeeper": False, "playerId": add_id,
             "toTeamId": TEAM_ID, "type": "ADD", "toLineupSlotId": BENCH_SLOT},
            {"fromTeamId": TEAM_ID, "isKeeper": False, "playerId": drop_id,
             "toTeamId": 0, "type": "DROP"},
        ],
    }
    resp = requests.post(
        BASE_URL + "/transactions/",
        cookies=cookies,
        json=payload,
        headers={"Accept": "application/json", "Content-Type": "application/json",
                 "X-Fantasy-Source": "kona", "X-Fantasy-Platform": "kona-PROD"},
        timeout=15,
    )
    if resp.status_code in (200, 201):
        log(f"  ✅ {txn_type}: added {add_name}, dropped {drop_name}.")
        return True
    log(f"  ❌ {txn_type} failed: HTTP {resp.status_code} — {resp.text[:200]}")
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


# ── Discord notifications ───────────────────────────────────────────────────────
# Webhook URLs live in config.json (gitignored), so they stay iMac-local just like
# the ESPN cookies. One webhook per league/channel lets each league post to its own
# Discord channel:
#
#   "discord_webhooks": {
#       "baseball":           "https://discord.com/api/webhooks/...",
#       "cast_final_fantasy": "https://discord.com/api/webhooks/...",
#       "sunday_funday":      "https://discord.com/api/webhooks/..."
#   },
#   "discord_webhook": "https://discord.com/api/webhooks/..."   # optional default
def _discord_url(cfg: dict, channel: str = None) -> str | None:
    hooks = cfg.get("discord_webhooks") or {}
    if channel and channel in hooks:
        return hooks[channel]
    if cfg.get("discord_webhook"):
        return cfg["discord_webhook"]
    if len(hooks) == 1:                       # single league configured — use it
        return next(iter(hooks.values()))
    return None


def send_discord(cfg: dict, content: str, channel: str = None,
                 username: str = "Fantasy Bot"):
    """Post a message to a Discord channel webhook. Raises if none configured."""
    url = _discord_url(cfg, channel)
    if not url:
        raise RuntimeError(f"no Discord webhook configured for channel={channel!r}")
    # Discord hard-caps message content at 2000 chars.
    resp = requests.post(url, json={"content": content[:1990], "username": username},
                         timeout=10)
    resp.raise_for_status()


def notify(cfg: dict, subject: str, body: str, channel: str = None):
    """Run notification: Discord webhook preferred, email as fallback."""
    if _discord_url(cfg, channel):
        try:
            send_discord(cfg, f"**{subject}**\n{body}", channel=channel)
            return
        except Exception as e:                # noqa: BLE001 — fall back to email
            log(f"[notify] Discord post failed ({e}); trying email")
    try:
        send_email(cfg, subject, body)
    except Exception as e:                    # noqa: BLE001 — best effort
        log(f"[notify] email fallback also failed: {e}")

# ── Logging ────────────────────────────────────────────────────────────────────
def log(msg: str):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)
