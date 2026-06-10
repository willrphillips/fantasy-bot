#!/usr/bin/env python3
"""
mlb_ingest.py — Daily and backfill ingest from MLB Stats API + Savant + ESPN.

PHILOSOPHY:
    Every player has one row per day = season-to-date as of that date.
    L7/L14/L30 are computed at query time by subtracting older from newer.

MODES:
    --backfill            walk every date from Opening Day to yesterday,
                          pulling season-to-date snapshots per tracked player.
                          One-time, expensive (~30-90 min depending on roster size).
    --nightly  (default)  pull yesterday's season-to-date snapshot per tracked
                          player. Fast (~3-8 min).
    --only-fantasy        skip MLB pulls, just refresh rosters/standings/matchups/FAs.
    --player NAME         backfill one specific player (use after a new call-up).
    --limit N             cap players processed (testing).

AUTO-DISCOVERY:
    Tracked players = anyone on a fantasy roster (today) + anyone in the top-200
    FA pool (today). New call-ups join automatically the day they're rostered or
    picked up. When a new player is discovered, mini-backfill runs for them
    from Opening Day forward.

CONFIG:
    Reuses ~/fantasy-bot/config.json (espn_s2, swid, league_id).

OUTPUT:
    Inserts into ~/fantasy-bot/fantasy.db. Logs to ~/fantasy-bot/ingest.log.
    Writes one pull_log row per run.
"""
import argparse
import csv
import datetime as dt
import io
import json
import logging
import os
import re
import sqlite3
import sys
import time
import unicodedata
from pathlib import Path

import requests

try:
    from espn_api.baseball import League
except ImportError:
    League = None

DB_PATH = Path(os.path.expanduser("~/fantasy-bot/fantasy.db"))
CONFIG_PATH = Path(os.path.expanduser("~/fantasy-bot/config.json"))
LOG_PATH = Path(os.path.expanduser("~/fantasy-bot/ingest.log"))

MLB_API = "https://statsapi.mlb.com/api/v1"
SAVANT_BASE = "https://baseballsavant.mlb.com/leaderboard"

# Fallback if config.json has no league_id. Matches espn_utils.LEAGUE_ID,
# the value the proven-working Sunday email bot uses on this box.
LEAGUE_ID_FALLBACK = 2057904545

SEASON = dt.datetime.now().year
OPENING_DAY = f"{SEASON}-03-26"  # adjust per season if needed
SLEEP_BETWEEN_CALLS = 0.10        # be polite to MLB API (10/sec is gentle)

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("ingest")


# ============================================================
# HTTP
# ============================================================

def get_json(url, params=None, retries=3, timeout=20):
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            log.warning(f"HTTP {r.status_code} on {url} params={params}")
        except requests.RequestException as e:
            log.warning(f"req error attempt {attempt+1}: {e}")
        time.sleep(1 + attempt)
    return None


# ============================================================
# MLB Stats API
# ============================================================

def fetch_player_detail(mlb_id):
    data = get_json(f"{MLB_API}/people/{mlb_id}")
    if not data or not data.get("people"):
        return {}
    p = data["people"][0]
    return {
        "name": p.get("fullName"),
        "team": (p.get("currentTeam") or {}).get("abbreviation") or
                (p.get("currentTeam") or {}).get("name"),
        "primary_pos": (p.get("primaryPosition") or {}).get("abbreviation"),
        "bats": (p.get("batSide") or {}).get("code"),
        "throws": (p.get("pitchHand") or {}).get("code"),
        "birth_date": p.get("birthDate"),
    }


def lookup_mlb_id(name):
    """Find MLB ID by player name. Best-effort."""
    data = get_json(f"{MLB_API}/people/search", params={"names": name})
    if not data or not data.get("people"):
        return None
    return data["people"][0].get("id")


# ============================================================
# ESPN id -> MLB id crosswalk
#
# espn_api exposes only ESPN's playerId, which is NOT the MLBAM id the
# Stats API / Savant use. Resolve by normalized name, disambiguating
# duplicates by team. Ambiguous-with-no-team-match is left UNRESOLVED
# (mlb_id NULL) and logged, never silently mis-mapped.
# ============================================================

# ESPN proTeam abbrev -> MLBAM abbrev, only where they differ.
ESPN_TEAM_ALIASES = {
    "CHW": "CWS",
    "OAK": "ATH",
    "WAS": "WSH",
}


def _norm_name(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", s)
    s = re.sub(r"[^a-z0-9 ]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _norm_team(s):
    if not s:
        return ""
    s = s.strip().upper()
    return ESPN_TEAM_ALIASES.get(s, s)


def fetch_team_abbr_map():
    """{mlb_team_id: ABBR} so the bulk player list can be tagged with team."""
    data = get_json(f"{MLB_API}/teams", params={"sportId": 1})
    out = {}
    if data:
        for t in data.get("teams", []):
            tid = t.get("id")
            ab = t.get("abbreviation")
            if tid and ab:
                out[tid] = ab.upper()
    return out


def fetch_mlb_player_index():
    """{normalized_name: [{id, team}]} from the full season player list.
    One Stats API call (+ one teams call). Reused for every resolution."""
    teams = fetch_team_abbr_map()
    data = get_json(f"{MLB_API}/sports/1/players", params={"season": SEASON})
    index = {}
    if not data:
        log.error("could not fetch MLB season player index - id resolution disabled")
        return index
    for p in data.get("people", []):
        pid = p.get("id")
        nm = _norm_name(p.get("fullName"))
        if not pid or not nm:
            continue
        tid = (p.get("currentTeam") or {}).get("id")
        index.setdefault(nm, []).append({"id": pid, "team": teams.get(tid, "")})
    log.info(f"MLB player index: {len(index)} distinct names")
    return index


def fetch_mlb_player_records():
    """Full list of MLB season-roster players: [{id, name, team}].
    One API call (+ teams map). Used to expand the tracked universe
    beyond just rostered/FA players in the ESPN league."""
    teams = fetch_team_abbr_map()
    data = get_json(f"{MLB_API}/sports/1/players", params={"season": SEASON})
    out = []
    if not data:
        log.error("MLB universe fetch returned no data; universe expansion skipped")
        return out
    for p in data.get("people", []):
        pid = p.get("id")
        name = p.get("fullName")
        if not pid or not name:
            continue
        tid = (p.get("currentTeam") or {}).get("id")
        out.append({"id": pid, "name": name, "team": teams.get(tid)})
    return out


def populate_mlb_universe(cur, today, records, counts):
    """Upsert every active MLB player into the players table and refresh
    last_tracked to today. This is the universe that tracked_player_ids
    draws from. Players bio fields (bats/throws/birth_date) are left NULL
    here; ensure_player_metadata fills them in lazily on first stat pull."""
    new = 0
    for r in records:
        existing = cur.execute(
            "SELECT 1 FROM players WHERE mlb_id = ?", (r["id"],)
        ).fetchone()
        if existing:
            cur.execute(
                "UPDATE players SET last_tracked = ?, "
                "team = COALESCE(?, team), name = COALESCE(?, name) "
                "WHERE mlb_id = ?",
                (today, r.get("team"), r.get("name"), r["id"]),
            )
        else:
            cur.execute(
                """
                INSERT INTO players (mlb_id, name, team, primary_pos, bats,
                                     throws, birth_date, first_tracked,
                                     last_tracked, source)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (r["id"], r["name"], r.get("team"), None, None, None, None,
                 today, today, "mlb_universe"),
            )
            new += 1
    counts["universe_total"] = len(records)
    counts["universe_new"] = new


def resolve_mlb_id(cur, espn_id, name, pro_team, index):
    """ESPN id -> MLB id. Cached in id_map. Returns int or None.
    Ambiguous (multiple same-name players, no confident team match) ->
    None + WARNING. Visible-missing beats silently-wrong."""
    if not espn_id:
        return None
    row = cur.execute(
        "SELECT mlb_id FROM id_map WHERE espn_id = ?", (espn_id,)
    ).fetchone()
    if row and row[0] is not None:
        return row[0]

    cands = index.get(_norm_name(name), [])
    mlb_id = None
    if len(cands) == 1:
        mlb_id = cands[0]["id"]
    elif len(cands) > 1:
        pt = _norm_team(pro_team)
        team_hits = [c for c in cands if c["team"] and c["team"] == pt]
        if len(team_hits) == 1:
            mlb_id = team_hits[0]["id"]
        else:
            log.warning(
                f"resolve espn={espn_id} '{name}': {len(cands)} MLB players "
                f"share that name, espn_team={pro_team!r} did not "
                f"disambiguate - left UNRESOLVED"
            )
    else:
        log.warning(f"resolve espn={espn_id} '{name}': no MLB match - UNRESOLVED")

    now = dt.datetime.now().isoformat()
    cur.execute(
        """
        INSERT INTO id_map (espn_id, mlb_id, name, pro_team, resolved_ts, miss_count)
        VALUES (?,?,?,?,?,?)
        ON CONFLICT(espn_id) DO UPDATE SET
            mlb_id=excluded.mlb_id,
            name=excluded.name,
            pro_team=excluded.pro_team,
            resolved_ts=excluded.resolved_ts,
            miss_count=id_map.miss_count
                + (CASE WHEN excluded.mlb_id IS NULL THEN 1 ELSE 0 END)
        """,
        (espn_id, mlb_id, name, pro_team, now, 0 if mlb_id else 1),
    )
    return mlb_id


def fetch_season_to_date(mlb_id, group, end_date):
    """
    Pull season-to-date stats as of `end_date`.

    Uses byDateRange from Opening Day -> end_date. Returns dict of raw stats or None.
    group: 'hitting' or 'pitching'
    """
    params = {
        "stats": "byDateRange",
        "group": group,
        "season": SEASON,
        "startDate": OPENING_DAY,
        "endDate": end_date,
    }
    data = get_json(f"{MLB_API}/people/{mlb_id}/stats", params=params)
    if not data:
        return None
    try:
        splits = data["stats"][0]["splits"]
        if not splits:
            return None
        return splits[0]["stat"]
    except (KeyError, IndexError, TypeError):
        return None


def calc_fip(stat):
    try:
        ip = float(stat.get("inningsPitched") or 0)
        if ip <= 0:
            return None
        hr = int(stat.get("homeRuns") or 0)
        bb = int(stat.get("baseOnBalls") or 0)
        hbp = int(stat.get("hitBatsmen") or 0)
        k = int(stat.get("strikeOuts") or 0)
        return round(((13*hr) + (3*(bb+hbp)) - (2*k)) / ip + 3.10, 2)
    except (TypeError, ValueError):
        return None


def parse_hitting(stat):
    if not stat:
        return None
    return {
        "games":   int(stat.get("gamesPlayed") or 0),
        "pa":      int(stat.get("plateAppearances") or 0),
        "ab":      int(stat.get("atBats") or 0),
        "h":       int(stat.get("hits") or 0),
        "doubles": int(stat.get("doubles") or 0),
        "triples": int(stat.get("triples") or 0),
        "hr":      int(stat.get("homeRuns") or 0),
        "r":       int(stat.get("runs") or 0),
        "rbi":     int(stat.get("rbi") or 0),
        "bb":      int(stat.get("baseOnBalls") or 0),
        "so":      int(stat.get("strikeOuts") or 0),
        "sb":      int(stat.get("stolenBases") or 0),
        "cs":      int(stat.get("caughtStealing") or 0),
        "hbp":     int(stat.get("hitByPitch") or 0),
        "sf":      int(stat.get("sacFlies") or 0),
        "avg":     float(stat["avg"]) if stat.get("avg") not in (None, ".---") else None,
        "obp":     float(stat["obp"]) if stat.get("obp") not in (None, ".---") else None,
        "slg":     float(stat["slg"]) if stat.get("slg") not in (None, ".---") else None,
        "ops":     float(stat["ops"]) if stat.get("ops") not in (None, ".---") else None,
    }


def parse_pitching(stat):
    if not stat:
        return None
    ip = float(stat.get("inningsPitched") or 0)
    tbf = int(stat.get("battersFaced") or 0) or None
    so = int(stat.get("strikeOuts") or 0)
    bb = int(stat.get("baseOnBalls") or 0)
    return {
        "games": int(stat.get("gamesPlayed") or 0),
        "gs":    int(stat.get("gamesStarted") or 0),
        "ip":    ip,
        "tbf":   tbf,
        "h":     int(stat.get("hits") or 0),
        "er":    int(stat.get("earnedRuns") or 0),
        "bb":    bb,
        "so":    so,
        "hr":    int(stat.get("homeRuns") or 0),
        "hbp":   int(stat.get("hitBatsmen") or 0),
        "w":     int(stat.get("wins") or 0),
        "l":     int(stat.get("losses") or 0),
        "sv":    int(stat.get("saves") or 0),
        "hld":   int(stat.get("holds") or 0),
        "bs":    int(stat.get("blownSaves") or 0),
        "era":   float(stat["era"]) if stat.get("era") not in (None, "-.--") else None,
        "whip":  float(stat["whip"]) if stat.get("whip") not in (None, "-.--") else None,
        "k9":    round(so * 9 / ip, 2) if ip > 0 else None,
        "bb9":   round(bb * 9 / ip, 2) if ip > 0 else None,
        "k_pct":  round(so / tbf, 3) if tbf else None,
        "bb_pct": round(bb / tbf, 3) if tbf else None,
        "fip":   calc_fip(stat),
    }


# ============================================================
# Savant (Statcast advanced) — season-to-date snapshots only
# ============================================================

def fetch_savant_csv(player_type):
    url = (
        f"{SAVANT_BASE}/expected_statistics?type={player_type}"
        f"&year={SEASON}&position=&team=&filter=&min=q&csv=true"
    )
    try:
        r = requests.get(url, timeout=30)
        if r.status_code != 200:
            log.warning(f"Savant {player_type} HTTP {r.status_code}")
            return []
        # Savant ships a UTF-8 BOM. Without stripping it, csv.DictReader
        # fails to recognize the leading quoted field ("last_name,
        # first_name") as quoted -> columns shift by one and player_id
        # ends up holding the year (2026), collapsing every row onto the
        # same UNIQUE key. Strip the BOM (and any leading whitespace).
        text = r.text.lstrip("﻿").lstrip()
        return list(csv.DictReader(io.StringIO(text)))
    except Exception as e:
        log.error(f"Savant {player_type} fetch failed: {e}")
        return []


def parse_savant_row(row, side):
    def _f(k):
        v = row.get(k)
        if v in (None, "", "null"):
            return None
        try:
            return float(v)
        except ValueError:
            return None
    return {
        "side":         side,
        "avg_ev":       _f("avg_hit_speed") or _f("avg_exit_velo"),
        "max_ev":       _f("max_hit_speed"),
        "hard_hit_pct": _f("hard_hit_percent"),
        "barrel_pct":   _f("brl_pa") or _f("barrel_batted_rate"),
        "woba":         _f("woba"),
        "xwoba":        _f("est_woba") or _f("xwoba"),
        "xba":          _f("est_ba") or _f("xba"),
        "xslg":         _f("est_slg") or _f("xslg"),
        "xera":         _f("xera"),
        "whiff_pct":    _f("whiff_percent"),
        "k_pct":        _f("k_percent"),
        "bb_pct":       _f("bb_percent"),
    }


# ============================================================
# DB writers
# ============================================================

def upsert_player(cur, mlb_id, info, date_today, source):
    cur.execute(
        """
        INSERT INTO players
        (mlb_id, name, team, primary_pos, bats, throws, birth_date,
         first_tracked, last_tracked, source)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(mlb_id) DO UPDATE SET
            name=COALESCE(excluded.name, players.name),
            team=COALESCE(excluded.team, players.team),
            primary_pos=COALESCE(excluded.primary_pos, players.primary_pos),
            last_tracked=excluded.last_tracked
        """,
        (
            mlb_id, info.get("name"), info.get("team"), info.get("primary_pos"),
            info.get("bats"), info.get("throws"), info.get("birth_date"),
            date_today, date_today, source,
        ),
    )


def insert_hitting(cur, mlb_id, date_pulled, s):
    cur.execute(
        """
        INSERT OR REPLACE INTO hitting_stats
        (mlb_id, date_pulled, games, pa, ab, h, doubles, triples, hr, r,
         rbi, bb, so, sb, cs, hbp, sf, avg, obp, slg, ops)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (mlb_id, date_pulled, s["games"], s["pa"], s["ab"], s["h"],
         s["doubles"], s["triples"], s["hr"], s["r"], s["rbi"], s["bb"],
         s["so"], s["sb"], s["cs"], s["hbp"], s["sf"],
         s["avg"], s["obp"], s["slg"], s["ops"]),
    )


def insert_pitching(cur, mlb_id, date_pulled, s):
    cur.execute(
        """
        INSERT OR REPLACE INTO pitching_stats
        (mlb_id, date_pulled, games, gs, ip, tbf, h, er, bb, so, hr, hbp,
         w, l, sv, hld, bs, era, whip, k9, bb9, k_pct, bb_pct, fip)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (mlb_id, date_pulled, s["games"], s["gs"], s["ip"], s["tbf"],
         s["h"], s["er"], s["bb"], s["so"], s["hr"], s["hbp"],
         s["w"], s["l"], s["sv"], s["hld"], s["bs"], s["era"], s["whip"],
         s["k9"], s["bb9"], s["k_pct"], s["bb_pct"], s["fip"]),
    )


def insert_statcast(cur, mlb_id, date_pulled, row):
    cur.execute(
        """
        INSERT OR REPLACE INTO statcast
        (mlb_id, date_pulled, side, avg_ev, max_ev, hard_hit_pct, barrel_pct,
         woba, xwoba, xba, xslg, xera, whiff_pct, k_pct, bb_pct)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (mlb_id, date_pulled, row["side"], row["avg_ev"], row["max_ev"],
         row["hard_hit_pct"], row["barrel_pct"], row["woba"], row["xwoba"],
         row["xba"], row["xslg"], row["xera"], row["whiff_pct"],
         row["k_pct"], row["bb_pct"]),
    )


# ============================================================
# ESPN fantasy state
# ============================================================

def load_league():
    if League is None:
        log.warning("espn_api not installed")
        return None
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
    except Exception as e:
        log.error(f"config.json unreadable: {e}")
        return None
    league_id = cfg.get("league_id") or cfg.get("leagueId") or LEAGUE_ID_FALLBACK
    try:
        return League(
            league_id=int(league_id),
            year=int(cfg.get("year", SEASON)),
            espn_s2=cfg["espn_s2"],
            swid=cfg["swid"],
        )
    except KeyError as e:
        log.error(f"config.json missing required key: {e}")
        return None
    except Exception as e:
        log.error(f"league init failed: {e}")
        return None


def fetch_fantasy_state(cur, lg, today):
    """Insert rosters / standings / matchups / FAs for `today`. Returns counts."""
    out = {"roster_rows": 0, "standings_rows": 0, "matchup_rows": 0,
           "fa_rows": 0, "ids_resolved": 0, "ids_unresolved": 0}

    index = fetch_mlb_player_index()

    def _resolve(p):
        espn_pid = getattr(p, "playerId", None)
        mlb_id = resolve_mlb_id(
            cur, espn_pid, p.name, getattr(p, "proTeam", None), index
        )
        if mlb_id:
            out["ids_resolved"] += 1
        else:
            out["ids_unresolved"] += 1
        return espn_pid, mlb_id

    # Rosters
    for team in lg.teams:
        for p in team.roster:
            espn_pid, mlb_id = _resolve(p)
            cur.execute(
                """
                INSERT OR REPLACE INTO rosters
                (date_pulled, team_name, team_id, espn_id, mlb_id, player_name,
                 slot, eligible_pos, status)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (today, team.team_name, team.team_id,
                 espn_pid, mlb_id, p.name,
                 getattr(p, "lineupSlot", None),
                 ",".join(getattr(p, "eligibleSlots", []) or []),
                 getattr(p, "injuryStatus", None)),
            )
            out["roster_rows"] += 1

    # Standings. rank = ESPN's authoritative `team.standing` (already
    # tiebreaker-resolved), not the loop index. pct counts ties as half
    # a win, matching ESPN's H2H category win% (raw wins/gp understated
    # every team and could reorder ties-heavy records).
    for i, team in enumerate(lg.standings(), start=1):
        gp = team.wins + team.losses + team.ties
        rank = getattr(team, "standing", None) or i
        cur.execute(
            """
            INSERT OR REPLACE INTO standings
            (date_pulled, team_name, rank, wins, losses, ties, pct)
            VALUES (?,?,?,?,?,?,?)
            """,
            (today, team.team_name, rank, team.wins, team.losses, team.ties,
             round((team.wins + 0.5 * team.ties) / gp, 3) if gp else None),
        )
        out["standings_rows"] += 1

    # Current matchup. espn_api box_scores expose home_stats/away_stats as
    # {CAT: {"value": float, "result": "WIN"|"LOSS"|"TIE"|None}}. The old
    # code read a nonexistent "score" key, so every value landed NULL and
    # every leader "tied". Two fixes: read "value", and derive the leader
    # from the home team's "result" (ESPN already accounts for lower-is-
    # better cats like ERA/WHIP — a raw value compare would invert them).
    # Component stats (AB, H, OUTS, ER, P_H, P_BB) carry result=None and
    # are NOT scoring categories; skip them so only the real cats persist.
    try:
        period = lg.current_week
        box = lg.box_scores(matchup_period=period)
        for b in box:
            home = b.home_team.team_name if b.home_team else "BYE"
            away = b.away_team.team_name if b.away_team else "BYE"
            hs = getattr(b, "home_stats", {}) or {}
            as_ = getattr(b, "away_stats", {}) or {}
            for cat in hs:
                hcell = hs.get(cat) or {}
                acell = as_.get(cat) or {}
                hres = hcell.get("result") if isinstance(hcell, dict) else None
                if hres is None:
                    continue  # component stat, not a scored category
                hv = hcell.get("value") if isinstance(hcell, dict) else None
                av = acell.get("value") if isinstance(acell, dict) else None
                leader = ("home" if hres == "WIN"
                          else "away" if hres == "LOSS" else "tied")
                cur.execute(
                    """
                    INSERT OR REPLACE INTO matchups
                    (date_pulled, period, home_team, away_team, cat,
                     home_value, away_value, leader)
                    VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (today, period, home, away, cat, hv, av, leader),
                )
                out["matchup_rows"] += 1
    except Exception as e:
        log.warning(f"matchup pull error: {e}")

    # FAs (top 200)
    try:
        fas = lg.free_agents(size=200)
        for p in fas:
            espn_pid, mlb_id = _resolve(p)
            cur.execute(
                """
                INSERT OR REPLACE INTO fa_pool
                (date_pulled, espn_id, mlb_id, player_name, eligible_pos,
                 team, owned_pct)
                VALUES (?,?,?,?,?,?,?)
                """,
                (today, espn_pid, mlb_id, p.name,
                 ",".join(getattr(p, "eligibleSlots", []) or []),
                 getattr(p, "proTeam", None),
                 getattr(p, "percent_owned", None)),
            )
            out["fa_rows"] += 1
    except Exception as e:
        log.warning(f"FA pull error: {e}")

    return out


# ============================================================
# Tracked player set
# ============================================================

def tracked_player_ids(cur, today):
    """
    Universe = every active MLB player (anchored on players.last_tracked,
    refreshed each run by populate_mlb_universe) PLUS any roster/FA
    appearance in the last 30 days (so a recently-dropped player still
    has fresh stats and fantasy-state continuity).
    """
    rows = cur.execute(
        """
        SELECT DISTINCT mlb_id FROM (
            SELECT mlb_id FROM players
            WHERE last_tracked >= date(?, '-30 days')
              AND mlb_id IS NOT NULL
            UNION
            SELECT mlb_id FROM rosters
            WHERE date_pulled >= date(?, '-30 days') AND mlb_id IS NOT NULL
            UNION
            SELECT mlb_id FROM fa_pool
            WHERE date_pulled >= date(?, '-30 days') AND mlb_id IS NOT NULL
        )
        """,
        (today, today, today),
    ).fetchall()
    return [r[0] for r in rows if r[0]]


def player_has_history(cur, mlb_id):
    row = cur.execute(
        "SELECT 1 FROM hitting_stats WHERE mlb_id = ? LIMIT 1", (mlb_id,)
    ).fetchone()
    if row:
        return True
    row = cur.execute(
        "SELECT 1 FROM pitching_stats WHERE mlb_id = ? LIMIT 1", (mlb_id,)
    ).fetchone()
    return bool(row)


# ============================================================
# Pull strategies
# ============================================================

def ingest_one_day(cur, mlb_id, date_str, counts):
    """Pull season-to-date hitting + pitching for one player as of date_str."""
    try:
        hit = parse_hitting(fetch_season_to_date(mlb_id, "hitting", date_str))
        if hit and hit["pa"] > 0:
            insert_hitting(cur, mlb_id, date_str, hit)
            counts["hit_rows"] += 1
        time.sleep(SLEEP_BETWEEN_CALLS)
    except Exception as e:
        log.error(f"hit ingest {mlb_id} {date_str}: {e}")
        counts["errors"] += 1

    try:
        pit = parse_pitching(fetch_season_to_date(mlb_id, "pitching", date_str))
        if pit and pit["ip"] > 0:
            insert_pitching(cur, mlb_id, date_str, pit)
            counts["pit_rows"] += 1
        time.sleep(SLEEP_BETWEEN_CALLS)
    except Exception as e:
        log.error(f"pit ingest {mlb_id} {date_str}: {e}")
        counts["errors"] += 1


def date_range(start_iso, end_iso):
    start = dt.date.fromisoformat(start_iso)
    end = dt.date.fromisoformat(end_iso)
    cur = start
    while cur <= end:
        yield cur.isoformat()
        cur += dt.timedelta(days=1)


def backfill_player(cur, mlb_id, from_date, to_date, counts):
    """Walk dates and pull season-to-date snapshots."""
    for d in date_range(from_date, to_date):
        # skip if we already have it
        exists_h = cur.execute(
            "SELECT 1 FROM hitting_stats WHERE mlb_id=? AND date_pulled=?",
            (mlb_id, d),
        ).fetchone()
        exists_p = cur.execute(
            "SELECT 1 FROM pitching_stats WHERE mlb_id=? AND date_pulled=?",
            (mlb_id, d),
        ).fetchone()
        if exists_h and exists_p:
            continue
        ingest_one_day(cur, mlb_id, d, counts)


def ensure_player_metadata(cur, mlb_id, today, source):
    """If the player isn't in `players` yet, fetch bio and insert."""
    row = cur.execute("SELECT 1 FROM players WHERE mlb_id = ?", (mlb_id,)).fetchone()
    if row:
        cur.execute(
            "UPDATE players SET last_tracked = ? WHERE mlb_id = ?",
            (today, mlb_id),
        )
        return False
    info = fetch_player_detail(mlb_id) or {}
    if not info.get("name"):
        # fall back to the name we resolved from ESPN, so we never hit
        # NOT NULL on players.name
        nm = cur.execute(
            "SELECT name FROM id_map WHERE mlb_id = ? AND name IS NOT NULL LIMIT 1",
            (mlb_id,),
        ).fetchone()
        if nm and nm[0]:
            info["name"] = nm[0]
    if not info.get("name"):
        log.warning(f"no name for mlb_id={mlb_id}; skipping players insert")
        return False
    upsert_player(cur, mlb_id, info, today, source)
    return True


# ============================================================
# Main
# ============================================================

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true",
                    help="Walk dates from Opening Day to yesterday")
    ap.add_argument("--nightly", action="store_true",
                    help="Pull yesterday's snapshot (default)")
    ap.add_argument("--only-fantasy", action="store_true")
    ap.add_argument("--skip-statcast", action="store_true")
    ap.add_argument("--player", type=str, default=None,
                    help="backfill a single player by name")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--from-date", type=str, default=OPENING_DAY,
                    help=f"backfill start (default {OPENING_DAY})")
    ap.add_argument("--to-date", type=str, default=None,
                    help="backfill end (default yesterday)")
    args = ap.parse_args()

    today = dt.date.today().isoformat()
    yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    to_date = args.to_date or yesterday
    mode = "backfill" if args.backfill else ("only-fantasy" if args.only_fantasy
                                              else "nightly")

    started = dt.datetime.now()
    log.info(f"===== ingest start mode={mode} =====")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    counts = {
        "players_tracked": 0, "new_players": 0,
        "hit_rows": 0, "pit_rows": 0, "statcast_rows": 0,
        "roster_rows": 0, "standings_rows": 0,
        "matchup_rows": 0, "fa_rows": 0, "errors": 0,
        "universe_total": 0, "universe_new": 0,
    }

    # ----- 1. Fantasy state first (drives discovery) -----
    lg = load_league()
    if lg:
        try:
            counts.update(fetch_fantasy_state(cur, lg, today))
            conn.commit()
        except Exception as e:
            log.error(f"fantasy state pull failed: {e}")
            counts["errors"] += 1

    if args.only_fantasy:
        _finalize(conn, cur, today, mode, started, counts, args)
        return

    # ----- 2. Single-player path -----
    if args.player:
        pid = lookup_mlb_id(args.player)
        if not pid:
            log.error(f"could not resolve {args.player}")
            print(f"ERROR: no MLB ID found for {args.player}")
            sys.exit(1)
        ensure_player_metadata(cur, pid, today, "manual")
        conn.commit()
        log.info(f"backfilling {args.player} ({pid}) from {args.from_date} to {to_date}")
        backfill_player(cur, pid, args.from_date, to_date, counts)
        conn.commit()
        counts["players_tracked"] = 1
        _finalize(conn, cur, today, mode, started, counts, args)
        return

    # ----- 2b. Universe expansion: every active MLB player -----
    try:
        records = fetch_mlb_player_records()
        populate_mlb_universe(cur, today, records, counts)
        conn.commit()
        log.info(
            f"MLB universe: {counts['universe_total']} active, "
            f"{counts['universe_new']} new"
        )
    except Exception as e:
        log.error(f"universe populate failed: {e}")
        counts["errors"] += 1

    # ----- 3. Tracked players -----
    ids = tracked_player_ids(cur, today)
    if args.limit:
        ids = ids[: args.limit]
    counts["players_tracked"] = len(ids)
    log.info(f"tracked players: {len(ids)}")

    for i, pid in enumerate(ids, 1):
        try:
            is_new = ensure_player_metadata(cur, pid, today, "auto")
            if is_new:
                counts["new_players"] += 1

            # Trigger mini-backfill whenever a tracked player has no stat
            # history yet, regardless of whether ensure_player_metadata
            # considered them "new". populate_mlb_universe inserts every
            # active MLB player into `players` up front, so `is_new` is
            # always False for universe-discovered players — but they
            # still need their Opening-Day-to-yesterday history walked
            # the first time they're tracked. Keying on player_has_history
            # is the right signal.
            if args.backfill or not player_has_history(cur, pid):
                start = args.from_date
                end = to_date
                backfill_player(cur, pid, start, end, counts)
            else:
                # nightly forward pull = season-to-date as of yesterday
                ingest_one_day(cur, pid, yesterday, counts)

            if i % 20 == 0:
                conn.commit()
                log.info(f"progress {i}/{len(ids)}")
        except Exception as e:
            log.error(f"player {pid}: {e}")
            counts["errors"] += 1
    conn.commit()

    # ----- 4. Statcast (always season-to-date) -----
    if not args.skip_statcast:
        for side, kind in [("bat", "batter"), ("pit", "pitcher")]:
            rows = fetch_savant_csv(kind)
            for row in rows:
                mlb_id_raw = row.get("player_id") or row.get("playerid")
                if not mlb_id_raw:
                    continue
                try:
                    parsed = parse_savant_row(row, side)
                    insert_statcast(cur, int(mlb_id_raw), today, parsed)
                    counts["statcast_rows"] += 1
                except Exception as e:
                    log.error(f"statcast {side} {mlb_id_raw}: {e}")
                    counts["errors"] += 1
            conn.commit()
            log.info(f"statcast {side}: {len(rows)} rows pulled")

    _finalize(conn, cur, today, mode, started, counts, args)


def _finalize(conn, cur, today, mode, started, counts, args):
    ended = dt.datetime.now()
    cur.execute(
        """
        INSERT INTO pull_log
        (date_pulled, mode, start_ts, end_ts, duration_sec,
         players_tracked, new_players, hit_rows, pit_rows, statcast_rows,
         roster_rows, standings_rows, matchup_rows, fa_rows, errors, notes)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (today, mode, started.isoformat(), ended.isoformat(),
         (ended - started).total_seconds(),
         counts["players_tracked"], counts["new_players"],
         counts["hit_rows"], counts["pit_rows"], counts["statcast_rows"],
         counts["roster_rows"], counts["standings_rows"],
         counts["matchup_rows"], counts["fa_rows"], counts["errors"],
         f"args={vars(args)}"),
    )
    conn.commit()
    conn.close()
    log.info(f"===== ingest done in {(ended-started).total_seconds():.1f}s "
             f"{counts} =====")
    print(json.dumps(counts, indent=2))
    _maybe_alert(mode, counts)


def _maybe_alert(mode, counts):
    """Failure-only, post-run soft check. Backfill = crash-only (see __main__)."""
    if mode == "backfill":
        return
    problems = []
    if mode == "nightly" and (counts["hit_rows"] + counts["pit_rows"]) == 0:
        problems.append(
            f"nightly ingested 0 stat rows "
            f"(players_tracked={counts['players_tracked']}) - pull silently dead"
        )
    if mode == "only-fantasy" and counts["roster_rows"] == 0:
        problems.append("only-fantasy pulled 0 roster rows - ESPN auth likely dead")
    if counts["errors"] > 25:
        problems.append(f"error count {counts['errors']} exceeded threshold (25)")
    if not problems:
        return
    try:
        from notify import alert
        alert(
            "mlb_ingest",
            f"WARNING: mlb_ingest.py {mode} degraded",
            "\n".join(problems) + f"\n\ncounts={json.dumps(counts, indent=2)}",
        )
    except Exception as e:
        log.error(f"alert dispatch failed: {e}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        import traceback
        try:
            from notify import alert
            alert(
                "mlb_ingest",
                "FAILURE: mlb_ingest.py crashed",
                f"Uncaught exception:\n{e}\n\n{traceback.format_exc()[-3500:]}",
            )
        except Exception as ne:
            log.error(f"alert dispatch failed: {ne}")
        raise
