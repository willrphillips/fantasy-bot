#!/usr/bin/env python3
"""
fantasy_lib.py — Query helper on top of the daily-snapshot fantasy.db.

Key concept: windows are computed by SUBTRACTING two season-to-date snapshots.

    >>> from fantasy_lib import *
    >>> window_stats("Juan Soto", days=14)
    >>> window_stats("Juan Soto", days=30)
    >>> windows_all("Juan Soto")           # season + L7/L14/L30 in one call
    >>> hot_bats(days=14, n=20, fa_only=True)
    >>> hot_arms(days=14, n=20, fa_only=True)
    >>> regression_watch('up', n=15)
    >>> fip_era_gap('up', n=15)
    >>> roster("Captain Phillips")
    >>> trade_scout(target_team="Bay County Buccaneers")
    >>> health()

Returns pandas DataFrames if pandas available, else list of dicts.
Read-only by convention.
"""
from __future__ import annotations

import datetime as dt
import os
import sqlite3
from pathlib import Path
from typing import Iterable, Optional

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

DB_PATH = Path(os.path.expanduser("~/fantasy-bot/fantasy.db"))

# Override via env var if running off a downloaded copy on a laptop
if os.environ.get("FANTASY_DB"):
    DB_PATH = Path(os.environ["FANTASY_DB"])


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def query(sql: str, params: tuple = ()):
    """Run arbitrary SELECT. DataFrame if pandas available, else list of dicts."""
    if HAS_PANDAS:
        with _conn() as c:
            return pd.read_sql_query(sql, c, params=params)
    with _conn() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def _scalar(sql, params=()):
    with _conn() as c:
        row = c.execute(sql, params).fetchone()
        return row[0] if row else None


# ============================================================
# Freshness
# ============================================================

def latest_date() -> Optional[str]:
    """Most recent date_pulled across hitting_stats."""
    return _scalar("SELECT MAX(date_pulled) FROM hitting_stats")


def earliest_date() -> Optional[str]:
    return _scalar("SELECT MIN(date_pulled) FROM hitting_stats")


# Fantasy-state tables (rosters/fa_pool/standings/matchups) are written on a
# DIFFERENT cadence than stat snapshots: fetch_fantasy_state tags them with
# the run date (today), while stat snapshots are season-to-date as of
# yesterday. Anchoring fantasy-state queries on latest_date() (a stats max)
# silently returns nothing. Each fantasy-state table gets its own latest.
def latest_roster_date() -> Optional[str]:
    return _scalar("SELECT MAX(date_pulled) FROM rosters")


def latest_fa_date() -> Optional[str]:
    return _scalar("SELECT MAX(date_pulled) FROM fa_pool")


def latest_standings_date() -> Optional[str]:
    return _scalar("SELECT MAX(date_pulled) FROM standings")


def latest_matchup_date() -> Optional[str]:
    return _scalar("SELECT MAX(date_pulled) FROM matchups")


def has_snapshot_on(date_iso: str) -> bool:
    return bool(_scalar(
        "SELECT 1 FROM hitting_stats WHERE date_pulled = ? LIMIT 1",
        (date_iso,)
    ))


def nearest_snapshot_on_or_before(target_iso: str) -> Optional[str]:
    """Largest date <= target. For windowing when an exact day is missing."""
    return _scalar(
        "SELECT MAX(date_pulled) FROM hitting_stats WHERE date_pulled <= ?",
        (target_iso,),
    )


# ============================================================
# Window-by-subtraction
# ============================================================

# Counting hitting columns
_HIT_COUNT = ["games", "pa", "ab", "h", "doubles", "triples", "hr", "r", "rbi",
              "bb", "so", "sb", "cs", "hbp", "sf"]
# Counting pitching columns
_PIT_COUNT = ["games", "gs", "h", "er", "bb", "so", "hr", "hbp",
              "w", "l", "sv", "hld", "bs"]


def _resolve_id(name: str) -> Optional[int]:
    row = _scalar(
        "SELECT mlb_id FROM players WHERE LOWER(name) = LOWER(?)",
        (name,),
    )
    if row:
        return row
    # fuzzy
    return _scalar(
        "SELECT mlb_id FROM players WHERE LOWER(name) LIKE LOWER(?)",
        (f"%{name}%",),
    )


def window_stats(name: str, days: int = 14, side: str = "auto"):
    """
    Compute window stats for `days` ending at the latest snapshot.

    side: 'hit', 'pit', or 'auto' (returns whichever table has data).
    Returns dict with raw counts + recomputed rate stats.
    """
    pid = _resolve_id(name)
    if not pid:
        return {"error": f"player not found: {name}"}

    end = latest_date()
    if not end:
        return {"error": "no snapshots in db"}
    end_d = dt.date.fromisoformat(end)
    start_target = (end_d - dt.timedelta(days=days)).isoformat()
    start = nearest_snapshot_on_or_before(start_target)

    out = {"name": name, "mlb_id": pid, "end_date": end, "window_days": days,
           "snapshot_start": start, "snapshot_end": end}

    if side in ("hit", "auto"):
        h = _window_hit(pid, start, end)
        if h:
            out["hitting"] = h
    if side in ("pit", "auto"):
        p = _window_pit(pid, start, end)
        if p:
            out["pitching"] = p
    return out


def _window_hit(pid: int, start: Optional[str], end: str):
    with _conn() as c:
        end_row = c.execute(
            "SELECT * FROM hitting_stats WHERE mlb_id = ? AND date_pulled = ?",
            (pid, end),
        ).fetchone()
        if not end_row:
            return None
        start_row = None
        if start and start < end:
            start_row = c.execute(
                "SELECT * FROM hitting_stats WHERE mlb_id = ? AND date_pulled = ?",
                (pid, start),
            ).fetchone()

    deltas = {}
    for col in _HIT_COUNT:
        e = end_row[col] or 0
        s = (start_row[col] if start_row else 0) or 0
        deltas[col] = max(e - s, 0)

    ab = deltas["ab"]; pa = deltas["pa"]; h = deltas["h"]; bb = deltas["bb"]
    hbp = deltas["hbp"]; sf = deltas["sf"]
    tb = h + deltas["doubles"] + 2*deltas["triples"] + 3*deltas["hr"]

    avg = round(h / ab, 3) if ab else None
    obp = round((h + bb + hbp) / (ab + bb + hbp + sf), 3) if (ab + bb + hbp + sf) else None
    slg = round(tb / ab, 3) if ab else None
    ops = round(obp + slg, 3) if obp is not None and slg is not None else None

    deltas.update({"avg": avg, "obp": obp, "slg": slg, "ops": ops})
    return deltas


def _window_pit(pid: int, start: Optional[str], end: str):
    with _conn() as c:
        end_row = c.execute(
            "SELECT * FROM pitching_stats WHERE mlb_id = ? AND date_pulled = ?",
            (pid, end),
        ).fetchone()
        if not end_row:
            return None
        start_row = None
        if start and start < end:
            start_row = c.execute(
                "SELECT * FROM pitching_stats WHERE mlb_id = ? AND date_pulled = ?",
                (pid, start),
            ).fetchone()

    deltas = {}
    for col in _PIT_COUNT:
        e = end_row[col] or 0
        s = (start_row[col] if start_row else 0) or 0
        deltas[col] = max(e - s, 0)

    # IP is float, handled separately
    ip = (end_row["ip"] or 0) - ((start_row["ip"] if start_row else 0) or 0)
    if ip < 0:
        ip = 0
    deltas["ip"] = round(ip, 1)

    tbf = (end_row["tbf"] or 0) - ((start_row["tbf"] if start_row else 0) or 0)
    deltas["tbf"] = max(tbf, 0)

    so = deltas["so"]; bb = deltas["bb"]; er = deltas["er"]; h = deltas["h"]
    hr = deltas["hr"]; hbp = deltas["hbp"]

    era = round(er * 9 / ip, 2) if ip > 0 else None
    whip = round((bb + h) / ip, 2) if ip > 0 else None
    k9 = round(so * 9 / ip, 2) if ip > 0 else None
    bb9 = round(bb * 9 / ip, 2) if ip > 0 else None
    k_pct = round(so / tbf, 3) if tbf else None
    bb_pct = round(bb / tbf, 3) if tbf else None
    fip = round((13*hr + 3*(bb+hbp) - 2*so) / ip + 3.10, 2) if ip > 0 else None

    deltas.update({"era": era, "whip": whip, "k9": k9, "bb9": bb9,
                   "k_pct": k_pct, "bb_pct": bb_pct, "fip": fip})
    return deltas


def windows_all(name: str, side: str = "auto"):
    """Return season + L7 + L14 + L30 in one shot."""
    out = {"name": name, "windows": {}}
    pid = _resolve_id(name)
    if not pid:
        return {"error": f"player not found: {name}"}
    end = latest_date()

    # season
    if side in ("hit", "auto"):
        h = query(
            "SELECT * FROM hitting_stats WHERE mlb_id=? AND date_pulled=?",
            (pid, end),
        )
        if HAS_PANDAS and not h.empty:
            out["windows"]["season_hit"] = h.iloc[0].to_dict()
        elif not HAS_PANDAS and h:
            out["windows"]["season_hit"] = h[0]
    if side in ("pit", "auto"):
        p = query(
            "SELECT * FROM pitching_stats WHERE mlb_id=? AND date_pulled=?",
            (pid, end),
        )
        if HAS_PANDAS and not p.empty:
            out["windows"]["season_pit"] = p.iloc[0].to_dict()
        elif not HAS_PANDAS and p:
            out["windows"]["season_pit"] = p[0]

    for d in (7, 14, 30):
        w = window_stats(name, days=d, side=side)
        out["windows"][f"L{d}"] = {k: v for k, v in w.items()
                                    if k in ("hitting", "pitching")}
    return out


# ============================================================
# Roster / standings / matchup
# ============================================================

def roster(team_name: str, date: Optional[str] = None):
    date = date or latest_roster_date()
    return query(
        """
        SELECT r.team_name, r.slot, r.player_name, r.eligible_pos, r.status,
               p.team AS mlb_team, p.primary_pos, p.mlb_id
        FROM rosters r
        LEFT JOIN players p ON p.mlb_id = r.mlb_id
        WHERE r.date_pulled = ? AND r.team_name = ?
        ORDER BY r.slot
        """,
        (date, team_name),
    )


def my_roster(date: Optional[str] = None):
    return roster("Captain Phillips", date)


def standings(date: Optional[str] = None):
    date = date or latest_standings_date()
    return query(
        "SELECT * FROM standings WHERE date_pulled = ? ORDER BY rank",
        (date,),
    )


def matchups(date: Optional[str] = None):
    date = date or latest_matchup_date()
    return query(
        "SELECT * FROM matchups WHERE date_pulled = ? ORDER BY period, home_team, cat",
        (date,),
    )


def fa_pool_latest(date: Optional[str] = None):
    date = date or latest_fa_date()
    return query(
        "SELECT * FROM fa_pool WHERE date_pulled = ? ORDER BY player_name",
        (date,),
    )


# ============================================================
# Player lookups
# ============================================================

def player(name: str):
    """Latest snapshot row for a player (hitting + pitching)."""
    pid = _resolve_id(name)
    if not pid:
        return {"error": f"player not found: {name}"}
    end = latest_date()
    return {
        "hitting": query(
            "SELECT * FROM hitting_stats WHERE mlb_id=? AND date_pulled=?",
            (pid, end)
        ),
        "pitching": query(
            "SELECT * FROM pitching_stats WHERE mlb_id=? AND date_pulled=?",
            (pid, end)
        ),
        "statcast": query(
            "SELECT * FROM statcast WHERE mlb_id=? AND date_pulled=?",
            (pid, end)
        ),
    }


def player_history(name: str, table: str = "hitting"):
    """Time series of season-to-date snapshots."""
    pid = _resolve_id(name)
    if not pid:
        return {"error": f"player not found: {name}"}
    tbl = "hitting_stats" if table == "hitting" else "pitching_stats"
    return query(
        f"SELECT * FROM {tbl} WHERE mlb_id = ? ORDER BY date_pulled",
        (pid,),
    )


def compare(*names: str, days: Optional[int] = None):
    """Side-by-side. days=None for season-to-date, otherwise window."""
    out = []
    for n in names:
        if days is None:
            pid = _resolve_id(n)
            if not pid:
                out.append({"name": n, "error": "not found"})
                continue
            row = query(
                "SELECT * FROM hitting_stats WHERE mlb_id=? AND date_pulled=?",
                (pid, latest_date()),
            )
            if HAS_PANDAS and not row.empty:
                d = row.iloc[0].to_dict()
                d["name"] = n
                out.append(d)
            elif not HAS_PANDAS and row:
                d = row[0]; d["name"] = n; out.append(d)
        else:
            out.append({"name": n, **window_stats(n, days=days)})
    return pd.DataFrame(out) if HAS_PANDAS else out


# ============================================================
# League-wide leaderboards
# ============================================================

def hot_bats(days: int = 14, n: int = 20, min_pa: int = 30,
             fa_only: bool = False, my_team_only: bool = False):
    """Top hitters by window OPS. fa_only restricts to current FA pool."""
    end = latest_date()
    end_d = dt.date.fromisoformat(end)
    start = nearest_snapshot_on_or_before(
        (end_d - dt.timedelta(days=days)).isoformat()
    )

    # Build window deltas inline via SQL
    where = []
    params = [end, start, end, start]
    if fa_only:
        where.append("p.mlb_id IN (SELECT mlb_id FROM fa_pool WHERE date_pulled = ?)")
        params.append(end)
    if my_team_only:
        where.append(
            "p.mlb_id IN (SELECT mlb_id FROM rosters "
            "WHERE date_pulled = ? AND team_name = 'Captain Phillips')"
        )
        params.append(end)
    wclause = "AND " + " AND ".join(where) if where else ""

    sql = f"""
    SELECT p.name, p.team, p.primary_pos,
           (he.pa - COALESCE(hs.pa, 0)) AS pa,
           (he.ab - COALESCE(hs.ab, 0)) AS ab,
           (he.h  - COALESCE(hs.h,  0)) AS h,
           (he.hr - COALESCE(hs.hr, 0)) AS hr,
           (he.r  - COALESCE(hs.r,  0)) AS r,
           (he.rbi - COALESCE(hs.rbi, 0)) AS rbi,
           (he.bb - COALESCE(hs.bb, 0)) AS bb,
           (he.so - COALESCE(hs.so, 0)) AS so,
           (he.sb - COALESCE(hs.sb, 0)) AS sb,
           ROUND(CAST(he.h - COALESCE(hs.h,0) AS REAL) /
                 NULLIF(he.ab - COALESCE(hs.ab, 0), 0), 3) AS avg,
           ROUND(
             CAST(((he.h - COALESCE(hs.h,0))
                  + (he.bb - COALESCE(hs.bb,0))
                  + (he.hbp - COALESCE(hs.hbp,0))) AS REAL)
             / NULLIF(
                 (he.ab - COALESCE(hs.ab,0))
                 + (he.bb - COALESCE(hs.bb,0))
                 + (he.hbp - COALESCE(hs.hbp,0))
                 + (he.sf - COALESCE(hs.sf,0)), 0), 3) AS obp,
           ROUND(
             CAST(((he.h - COALESCE(hs.h,0))
                  + (he.doubles - COALESCE(hs.doubles,0))
                  + 2*(he.triples - COALESCE(hs.triples,0))
                  + 3*(he.hr - COALESCE(hs.hr,0))) AS REAL)
             / NULLIF(he.ab - COALESCE(hs.ab, 0), 0), 3) AS slg,
           ROUND(
             CAST(((he.h - COALESCE(hs.h,0))
                  + (he.bb - COALESCE(hs.bb,0))
                  + (he.hbp - COALESCE(hs.hbp,0))) AS REAL)
             / NULLIF(
                 (he.ab - COALESCE(hs.ab,0))
                 + (he.bb - COALESCE(hs.bb,0))
                 + (he.hbp - COALESCE(hs.hbp,0))
                 + (he.sf - COALESCE(hs.sf,0)), 0)
             +
             CAST(((he.h - COALESCE(hs.h,0))
                  + (he.doubles - COALESCE(hs.doubles,0))
                  + 2*(he.triples - COALESCE(hs.triples,0))
                  + 3*(he.hr - COALESCE(hs.hr,0))) AS REAL)
             / NULLIF(he.ab - COALESCE(hs.ab, 0), 0), 3) AS ops
    FROM hitting_stats he
    LEFT JOIN hitting_stats hs
      ON hs.mlb_id = he.mlb_id AND hs.date_pulled = ?
    JOIN players p ON p.mlb_id = he.mlb_id
    WHERE he.date_pulled = ?
      AND (he.pa - COALESCE(hs.pa, 0)) >= ?
      {wclause.replace(' AND ', ' AND ', 1)}
    """
    # final params order: end_hs, end_he, min_pa, then extras
    # Note: SQLite param substitution is positional; rebuild
    final_params = [start, end, min_pa]
    if fa_only:
        final_params.append(latest_fa_date())
    if my_team_only:
        final_params.append(latest_roster_date())
    final_params.append(n)
    sql += " ORDER BY ops DESC NULLS LAST LIMIT ?"
    return query(sql, tuple(final_params))


def hot_arms(days: int = 14, n: int = 20, min_ip: float = 5.0,
             fa_only: bool = False, my_team_only: bool = False):
    """Top pitchers by window FIP (lower is better)."""
    end = latest_date()
    end_d = dt.date.fromisoformat(end)
    start = nearest_snapshot_on_or_before(
        (end_d - dt.timedelta(days=days)).isoformat()
    )

    where = []
    if fa_only:
        where.append("p.mlb_id IN (SELECT mlb_id FROM fa_pool WHERE date_pulled = ?)")
    if my_team_only:
        where.append("p.mlb_id IN (SELECT mlb_id FROM rosters WHERE date_pulled = ? "
                     "AND team_name = 'Captain Phillips')")
    wclause = " AND " + " AND ".join(where) if where else ""

    sql = f"""
    SELECT p.name, p.team, p.primary_pos,
           (pe.gs - COALESCE(ps.gs, 0)) AS gs,
           ROUND(pe.ip - COALESCE(ps.ip, 0), 1) AS ip,
           (pe.so - COALESCE(ps.so, 0)) AS so,
           (pe.bb - COALESCE(ps.bb, 0)) AS bb,
           (pe.er - COALESCE(ps.er, 0)) AS er,
           (pe.hr - COALESCE(ps.hr, 0)) AS hr,
           (pe.w  - COALESCE(ps.w,  0)) AS w,
           (pe.sv - COALESCE(ps.sv, 0)) AS sv,
           (pe.hld - COALESCE(ps.hld, 0)) AS hld,
           ROUND(CAST(pe.er - COALESCE(ps.er,0) AS REAL) * 9 /
                 NULLIF(pe.ip - COALESCE(ps.ip,0), 0), 2) AS era,
           ROUND(CAST((pe.bb - COALESCE(ps.bb,0))
                    + (pe.h  - COALESCE(ps.h, 0)) AS REAL)
                 / NULLIF(pe.ip - COALESCE(ps.ip,0), 0), 2) AS whip,
           ROUND(
             (13.0 * (pe.hr - COALESCE(ps.hr,0))
              + 3.0 * ((pe.bb - COALESCE(ps.bb,0)) + (pe.hbp - COALESCE(ps.hbp,0)))
              - 2.0 * (pe.so - COALESCE(ps.so,0)))
             / NULLIF(pe.ip - COALESCE(ps.ip,0), 0) + 3.10, 2) AS fip
    FROM pitching_stats pe
    LEFT JOIN pitching_stats ps
      ON ps.mlb_id = pe.mlb_id AND ps.date_pulled = ?
    JOIN players p ON p.mlb_id = pe.mlb_id
    WHERE pe.date_pulled = ?
      AND (pe.ip - COALESCE(ps.ip,0)) >= ?
      {wclause}
    ORDER BY fip ASC NULLS LAST
    LIMIT ?
    """
    final_params = [start, end, min_ip]
    if fa_only:
        final_params.append(latest_fa_date())
    if my_team_only:
        final_params.append(latest_roster_date())
    final_params.append(n)
    return query(sql, tuple(final_params))


def cold_bats(days: int = 14, n: int = 20, min_pa: int = 40,
              my_team_only: bool = False):
    """Worst window OPS. Useful for spotting drop candidates."""
    end = latest_date()
    end_d = dt.date.fromisoformat(end)
    start = nearest_snapshot_on_or_before(
        (end_d - dt.timedelta(days=days)).isoformat()
    )
    where = ""
    extras = []
    if my_team_only:
        where = ("AND p.mlb_id IN (SELECT mlb_id FROM rosters WHERE date_pulled = ? "
                 "AND team_name = 'Captain Phillips')")
        extras.append(latest_roster_date())
    sql = f"""
    SELECT p.name, p.team,
           (he.pa - COALESCE(hs.pa, 0)) AS pa,
           ROUND(CAST(he.h - COALESCE(hs.h,0) AS REAL) /
                 NULLIF(he.ab - COALESCE(hs.ab,0), 0), 3) AS avg,
           (he.hr - COALESCE(hs.hr, 0)) AS hr,
           (he.rbi - COALESCE(hs.rbi, 0)) AS rbi,
           ROUND(
             CAST(((he.h - COALESCE(hs.h,0))
                  + (he.doubles - COALESCE(hs.doubles,0))
                  + 2*(he.triples - COALESCE(hs.triples,0))
                  + 3*(he.hr - COALESCE(hs.hr,0))) AS REAL)
             / NULLIF(he.ab - COALESCE(hs.ab,0), 0), 3) AS slg
    FROM hitting_stats he
    LEFT JOIN hitting_stats hs
      ON hs.mlb_id = he.mlb_id AND hs.date_pulled = ?
    JOIN players p ON p.mlb_id = he.mlb_id
    WHERE he.date_pulled = ?
      AND (he.pa - COALESCE(hs.pa, 0)) >= ?
      {where}
    ORDER BY slg ASC NULLS LAST
    LIMIT ?
    """
    return query(sql, tuple([start, end, min_pa] + extras + [n]))


# ============================================================
# Regression / luck signals
# ============================================================

def regression_watch(direction: str = "up", n: int = 20, min_pa: int = 80):
    """
    direction='up'   xwOBA > wOBA (under-performing on contact — expect improvement)
    direction='down' wOBA > xwOBA (out-performing contact — expect cooling)
    """
    end = latest_date()
    order = "DESC" if direction == "up" else "ASC"
    return query(
        f"""
        SELECT p.name, p.team, p.primary_pos,
               s.woba, s.xwoba,
               ROUND(s.xwoba - s.woba, 3) AS gap,
               h.pa, h.avg, h.hr, h.ops
        FROM statcast s
        JOIN players p ON p.mlb_id = s.mlb_id
        LEFT JOIN hitting_stats h
            ON h.mlb_id = s.mlb_id AND h.date_pulled = s.date_pulled
        WHERE s.date_pulled = ? AND s.side = 'bat'
          AND s.woba IS NOT NULL AND s.xwoba IS NOT NULL
          AND COALESCE(h.pa, 0) >= ?
        ORDER BY (s.xwoba - s.woba) {order}
        LIMIT ?
        """,
        (end, min_pa, n),
    )


def fip_era_gap(direction: str = "up", n: int = 20, min_ip: float = 25.0):
    """
    direction='up'   ERA > FIP (unlucky pitcher — expect improvement)
    direction='down' FIP > ERA (lucky pitcher — expect regression)
    """
    end = latest_date()
    order = "DESC" if direction == "up" else "ASC"
    return query(
        f"""
        SELECT p.name, p.team, pit.gs, pit.ip, pit.era, pit.fip,
               ROUND(pit.era - pit.fip, 2) AS gap,
               pit.whip, pit.k_pct
        FROM pitching_stats pit
        JOIN players p ON p.mlb_id = pit.mlb_id
        WHERE pit.date_pulled = ?
          AND pit.era IS NOT NULL AND pit.fip IS NOT NULL
          AND pit.ip >= ?
        ORDER BY (pit.era - pit.fip) {order}
        LIMIT ?
        """,
        (end, min_ip, n),
    )


# ============================================================
# Trade scouting
# ============================================================

def teams_list():
    return query(
        "SELECT DISTINCT team_name FROM rosters WHERE date_pulled = ? ORDER BY team_name",
        (latest_roster_date(),),
    )


def trade_scout(target_team: str, sort: str = "hr"):
    """Their roster sorted by a hitting cat. Use sort='ops' or 'hr' or 'rbi'."""
    r_end = latest_roster_date()   # rosters cadence (today)
    s_end = latest_date()          # stats cadence (yesterday)
    return query(
        f"""
        SELECT r.player_name, r.slot, r.eligible_pos, r.status,
               p.team AS mlb_team,
               h.avg, h.hr, h.rbi, h.r, h.sb, h.ops
        FROM rosters r
        LEFT JOIN players p ON p.mlb_id = r.mlb_id
        LEFT JOIN hitting_stats h
            ON h.mlb_id = r.mlb_id AND h.date_pulled = ?
        WHERE r.date_pulled = ? AND r.team_name = ?
        ORDER BY h.{sort} DESC NULLS LAST
        """,
        (s_end, r_end, target_team),
    )


# ============================================================
# Health
# ============================================================

def health():
    """Freshness + table row counts + last pull summary."""
    with _conn() as c:
        out = {
            "db_path": str(DB_PATH),
            "latest_pull": latest_date(),
            "earliest_pull": earliest_date(),
        }
        for t in ["players", "hitting_stats", "pitching_stats", "statcast",
                  "rosters", "standings", "matchups", "fa_pool", "pull_log"]:
            out[t] = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        row = c.execute(
            "SELECT * FROM pull_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        out["last_pull_log"] = dict(row) if row else None
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(health(), indent=2, default=str))
