"""
Daily game/matchup data from MLB Stats API.
Free, no auth, no scraping. Replaces the failed Razzball scraper.

Returns per-team info: has_game_today, opponent, probable_pitcher, game_time.
This is enough to decide: bench anyone with no game, start probable SPs,
factor in opponent SP quality.
"""
from __future__ import annotations
import os
import json
import datetime as dt
import requests

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    _ET = None

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

MLB_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"

HEADERS = {
    "User-Agent": "fantasy-bot/1.0 (personal use)",
}

# ESPN team abbreviations -> MLB Stats API team IDs
# We need this because ESPN uses 3-letter codes (Atl, NYY) and MLB API uses numeric IDs
ESPN_TO_MLB_TEAM = {
    "Ari": 109, "Atl": 144, "Bal": 110, "Bos": 111, "ChC": 112, "ChW": 145,
    "Cin": 113, "Cle": 114, "Col": 115, "Det": 116, "Hou": 117, "KC": 118,
    "LAA": 108, "LAD": 119, "Mia": 146, "Mil": 158, "Min": 142, "NYM": 121,
    "NYY": 147, "Oak": 133, "Phi": 143, "Pit": 134, "SD": 135, "Sea": 136,
    "SF": 137, "StL": 138, "TB": 139, "Tex": 140, "Tor": 141, "Wsh": 120,
}

# Reverse: MLB ID -> ESPN abbreviation
MLB_TO_ESPN_TEAM = {v: k for k, v in ESPN_TO_MLB_TEAM.items()}


def _today():
    """MLB's slate turns over on the Eastern calendar day, not UTC. Using system/UTC
    'today' here misreads the schedule for roughly the last 4-5 hours of every ET day
    (system rolls to tomorrow at 00:00 UTC = 8pm ET) — exactly the window when evening
    lineup checks matter most."""
    now = dt.datetime.now(_ET) if _ET else dt.datetime.utcnow()
    return now.date().isoformat()


def _cache_path(date_str):
    return os.path.join(CACHE_DIR, f"schedule_{date_str}.json")


def _game_time_et(game_date_utc):
    """MLB's gameDate is UTC ISO8601 ("2026-07-28T02:40:00Z"). Truncating that
    string raw and calling it a local time reads a 6:40pm ET first pitch as
    10:40pm — the exact bug that made a lineup swap look safe when the game,
    and the ESPN lineup lock with it, had already started. Convert properly."""
    if not game_date_utc:
        return None
    try:
        ts = dt.datetime.fromisoformat(game_date_utc.replace("Z", "+00:00"))
        if _ET:
            ts = ts.astimezone(_ET)
        return ts.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return game_date_utc[:16].replace("T", " ")


def fetch_schedule(date_str=None, force_refresh=False):
    """Fetch MLB schedule + probable pitchers for a given date.
    Returns the raw API response (cached daily)."""
    if date_str is None:
        date_str = _today()
    cache_file = _cache_path(date_str)

    if os.path.exists(cache_file) and not force_refresh:
        with open(cache_file) as f:
            return json.load(f)

    params = {
        "sportId": 1,
        "date": date_str,
        "hydrate": "probablePitcher,linescore",
    }
    r = requests.get(MLB_SCHEDULE_URL, params=params, headers=HEADERS, timeout=15)
    r.raise_for_status()
    data = r.json()

    with open(cache_file, "w") as f:
        json.dump(data, f, indent=2)
    return data


def build_team_matchups(date_str=None, force_refresh=False):
    """Returns dict keyed by ESPN team abbrev:
    {
        'NYY': {
            'has_game': True,
            'started': False,
            'opponent': 'Bal',
            'home': True,
            'probable_pitcher': 'Max Fried',
            'opp_probable_pitcher': 'Corbin Burnes',
            'game_time': '19:05',
        },
        'Wsh': {'has_game': False, ...},
    }

    `started` is True once that team's game is past Preview. ESPN freezes a player's roster
    slot the moment his game begins, so a caller deciding lineups mid-day must treat those
    players as immovable. Pass `force_refresh=True` to get it: the schedule is cached once per
    day, and a cached copy written in the morning says Preview all night (Will, 2026-08-30).
    """
    data = fetch_schedule(date_str, force_refresh=force_refresh)
    result = {}

    # Initialize all teams as no-game
    for espn_abbrev in ESPN_TO_MLB_TEAM:
        result[espn_abbrev] = {
            "has_game": False,
            "started": False,
            "opponent": None,
            "home": None,
            "probable_pitcher": None,
            "opp_probable_pitcher": None,
            "game_time": None,
        }

    dates = data.get("dates", [])
    if not dates:
        return result

    games = dates[0].get("games", [])
    for g in games:
        teams = g.get("teams", {})
        home_id = teams.get("home", {}).get("team", {}).get("id")
        away_id = teams.get("away", {}).get("team", {}).get("id")
        home_abbrev = MLB_TO_ESPN_TEAM.get(home_id)
        away_abbrev = MLB_TO_ESPN_TEAM.get(away_id)
        if not home_abbrev or not away_abbrev:
            continue

        home_sp = teams.get("home", {}).get("probablePitcher", {}).get("fullName")
        away_sp = teams.get("away", {}).get("probablePitcher", {}).get("fullName")
        game_time = _game_time_et(g.get("gameDate", ""))

        # A postponed game reads as Final but never locks anyone, so it stays movable.
        status = g.get("status") or {}
        started = (status.get("abstractGameState") != "Preview"
                   and not str(status.get("detailedState") or "").startswith("Postponed"))

        result[home_abbrev] = {
            "has_game": True,
            "started": started,
            "opponent": away_abbrev,
            "home": True,
            "probable_pitcher": home_sp,
            "opp_probable_pitcher": away_sp,
            "game_time": game_time,
        }
        result[away_abbrev] = {
            "has_game": True,
            "started": started,
            "opponent": home_abbrev,
            "home": False,
            "probable_pitcher": away_sp,
            "opp_probable_pitcher": home_sp,
            "game_time": game_time,
        }

    return result


def get_pitcher_starts_in_window(start_date=None, days=7):
    """For pitcher streaming - returns dict {pitcher_name: count_of_starts}
    over the next N days. Used to detect two-start weeks."""
    if start_date is None:
        start_date = dt.date.today()
    elif isinstance(start_date, str):
        start_date = dt.date.fromisoformat(start_date)

    counts = {}
    for i in range(days):
        d = (start_date + dt.timedelta(days=i)).isoformat()
        try:
            data = fetch_schedule(d)
        except Exception as e:
            print(f"  WARN: couldn't fetch schedule for {d}: {e}")
            continue
        for date_block in data.get("dates", []):
            for g in date_block.get("games", []):
                for side in ("home", "away"):
                    sp = g.get("teams", {}).get(side, {}).get("probablePitcher", {})
                    name = sp.get("fullName")
                    if name:
                        counts[name] = counts.get(name, 0) + 1
    return counts


if __name__ == "__main__":
    print("=== daily_projections (MLB Stats API) self-test ===")
    print()
    matchups = build_team_matchups()
    teams_with_games = [t for t, m in matchups.items() if m["has_game"]]
    teams_off = [t for t, m in matchups.items() if not m["has_game"]]
    print(f"Teams with games today: {len(teams_with_games)}")
    print(f"Teams off today: {len(teams_off)} -> {teams_off}")
    print()
    print("Sample matchups:")
    for t in sorted(teams_with_games)[:6]:
        m = matchups[t]
        ha = "vs" if m["home"] else "@"
        sp = m["probable_pitcher"] or "TBD"
        opp_sp = m["opp_probable_pitcher"] or "TBD"
        print(f"  {t} {ha} {m['opponent']:4s} | SP: {sp:25s} | opp SP: {opp_sp}")
    print()
    print("=== Two-start pitcher check (next 7 days) ===")
    starts = get_pitcher_starts_in_window(days=7)
    two_start = sorted([(n, c) for n, c in starts.items() if c >= 2], key=lambda x: -x[1])
    print(f"Pitchers with 2+ starts in next 7 days: {len(two_start)}")
    for name, count in two_start[:10]:
        print(f"  {count}x  {name}")
