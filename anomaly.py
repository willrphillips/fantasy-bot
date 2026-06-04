#!/usr/bin/env python3
"""
anomaly.py — Nightly anomaly digest of standout single-game stat lines.

Runs after views.py, before db_publish.py. Writes one Markdown report,
anomaly_digest.md, into the same views dir db_publish.py globs, so it
publishes automatically with the rest:

    https://willrphillips.github.io/fantasy-snapshots/views/anomaly_digest.md

What it does
------------
The pipeline already produces correct data. It does not produce
*interesting* data. This turns the daily drop into something worth
reading at a glance: the standout hitting and pitching lines from the
most recent game day, each shown against the player's season-to-date
baseline.

How a "game line" is computed
-----------------------------
hitting_stats / pitching_stats are CUMULATIVE season-to-date per
(player, date). A single day's line is therefore the latest snapshot
minus the snapshot immediately before it — the same subtract-two-
snapshots trick fantasy_lib.window_stats uses, with days=1.

Correctness note (load-bearing): we INNER JOIN the prior snapshot.
A LEFT JOIN with COALESCE(prev, 0) — as the multi-day window leaderboards
use — would, for any player missing the prior day's row, treat their
ENTIRE season as "one game" and report a fake 40-hit, 12-HR night. For
a 1-day delta that failure mode is unacceptable, so a player with no
prior-day snapshot is skipped rather than COALESCEd to zero.

Honesty note: baselines are SEASON-to-date only. This db has no
career history, so the digest never claims "career best" — only
"season" comparisons, which are the only ones the data supports.

No prior-night dedup is needed: each night's game lines are a fresh
day's deltas and do not repeat the previous digest.

Usage:
    python3 anomaly.py                 # generate the digest
    python3 anomaly.py --force-stale   # bypass the freshness gate (manual regen)
    python3 anomaly.py --stdout        # print to stdout instead of writing the file
"""
import argparse
import datetime as dt
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import fantasy_lib as fl

OUT_DIR = Path(os.path.expanduser("~/fantasy-bot/public/views"))
OUT_NAME = "anomaly_digest.md"

# How many of each to surface.
TOP_HITTERS = 15
TOP_PITCHERS = 10


def _check_freshness():
    """Mirror views.py: refuse to regenerate against stale data. Returns
    the latest date if fresh; alerts and returns None if stale; returns
    '' for an empty db so first-run scenarios don't block."""
    latest = fl.latest_date()
    if not latest:
        return ""
    yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    if latest < yesterday:
        msg = (
            f"hitting_stats latest date = {latest}, expected >= "
            f"{yesterday}. Refusing to regenerate the anomaly digest "
            f"against stale data."
        )
        print(f"STALE: {msg}", file=sys.stderr)
        try:
            from notify import alert
            alert(
                "anomaly",
                f"STALE: anomaly.py refused to run (latest={latest})",
                msg,
            )
        except Exception as e:
            print(f"alert dispatch failed: {e}", file=sys.stderr)
        return None
    return latest


def _prev_snapshot(end: str):
    """The snapshot immediately before `end` (handles gaps in cadence)."""
    end_d = dt.date.fromisoformat(end)
    return fl.nearest_snapshot_on_or_before((end_d - dt.timedelta(days=1)).isoformat())


# ============================================================
# Daily deltas (one query each; INNER JOIN to the prior snapshot)
# ============================================================

def _hitter_lines(prev: str, end: str):
    return fl.query(
        """
        SELECT p.name, p.team, p.primary_pos,
               (he.ab      - hp.ab)      AS ab,
               (he.h       - hp.h)       AS h,
               (he.doubles - hp.doubles) AS doubles,
               (he.triples - hp.triples) AS triples,
               (he.hr      - hp.hr)      AS hr,
               (he.r       - hp.r)       AS r,
               (he.rbi     - hp.rbi)     AS rbi,
               (he.bb      - hp.bb)      AS bb,
               (he.sb      - hp.sb)      AS sb,
               he.avg AS s_avg, he.obp AS s_obp, he.slg AS s_slg,
               he.ops AS s_ops, he.hr AS s_hr, he.pa AS s_pa
        FROM hitting_stats he
        JOIN hitting_stats hp
          ON hp.mlb_id = he.mlb_id AND hp.date_pulled = ?
        JOIN players p ON p.mlb_id = he.mlb_id
        WHERE he.date_pulled = ?
        """,
        (prev, end),
    )


def _pitcher_lines(prev: str, end: str):
    return fl.query(
        """
        SELECT p.name, p.team, p.primary_pos,
               (pe.gs - pp.gs) AS gs,
               ROUND(pe.ip - pp.ip, 1) AS ip,
               (pe.h   - pp.h)   AS h,
               (pe.er  - pp.er)  AS er,
               (pe.bb  - pp.bb)  AS bb,
               (pe.so  - pp.so)  AS so,
               (pe.hr  - pp.hr)  AS hr,
               (pe.w   - pp.w)   AS w,
               (pe.sv  - pp.sv)  AS sv,
               (pe.hld - pp.hld) AS hld,
               pe.era AS s_era, pe.fip AS s_fip, pe.whip AS s_whip,
               pe.so AS s_so, pe.ip AS s_ip
        FROM pitching_stats pe
        JOIN pitching_stats pp
          ON pp.mlb_id = pe.mlb_id AND pp.date_pulled = ?
        JOIN players p ON p.mlb_id = pe.mlb_id
        WHERE pe.date_pulled = ?
        """,
        (prev, end),
    )


def _rows(df):
    """Normalize a DataFrame or list-of-dicts to a list of plain dicts."""
    if df is None:
        return []
    if hasattr(df, "to_dict"):
        return df.to_dict("records")
    return list(df)


# ============================================================
# Notability filters + scoring
# ============================================================

def _hitter_notable(r):
    h = r.get("h") or 0
    hr = r.get("hr") or 0
    rbi = r.get("rbi") or 0
    run = r.get("r") or 0
    sb = r.get("sb") or 0
    xbh = (r.get("doubles") or 0) + (r.get("triples") or 0) + hr
    return (
        hr >= 2
        or h >= 4
        or rbi >= 5
        or run >= 4
        or sb >= 3
        or (h >= 3 and hr >= 1)
        or xbh >= 3
    )


def _hitter_score(r):
    hr = r.get("hr") or 0
    xbh = (r.get("doubles") or 0) + (r.get("triples") or 0) + hr
    return (
        hr * 4
        + (r.get("h") or 0)
        + (r.get("rbi") or 0) * 1.5
        + (r.get("r") or 0)
        + (r.get("sb") or 0) * 2
        + xbh
    )


def _pitcher_notable(r):
    ip = r.get("ip") or 0
    er = r.get("er") or 0
    so = r.get("so") or 0
    # A start/relief outing only — needs real innings.
    if ip < 5:
        return so >= 10  # rare short-relief K explosion still counts
    return (
        (ip >= 7 and er <= 1)
        or (ip >= 6 and er == 0)
        or so >= 10
    )


def _pitcher_score(r):
    return (
        (r.get("so") or 0)
        + (r.get("ip") or 0)
        - (r.get("er") or 0) * 2
        + (10 if (r.get("so") or 0) >= 10 else 0)
        + (5 if (r.get("er") or 0) == 0 and (r.get("ip") or 0) >= 6 else 0)
    )


# ============================================================
# Rendering
# ============================================================

def _fmt_avg(x):
    if x is None:
        return "—"
    return f"{x:.3f}".lstrip("0") if 0 <= x < 1 else f"{x:.3f}"


def _hitter_md(r):
    pos = f", {r['primary_pos']}" if r.get("primary_pos") else ""
    parts = [f"{r.get('h', 0)}-for-{r.get('ab', 0)}"]
    if (r.get("hr") or 0):
        parts.append(f"{r['hr']} HR")
    if (r.get("doubles") or 0):
        parts.append(f"{r['doubles']} 2B")
    if (r.get("triples") or 0):
        parts.append(f"{r['triples']} 3B")
    if (r.get("rbi") or 0):
        parts.append(f"{r['rbi']} RBI")
    if (r.get("r") or 0):
        parts.append(f"{r['r']} R")
    if (r.get("bb") or 0):
        parts.append(f"{r['bb']} BB")
    if (r.get("sb") or 0):
        parts.append(f"{r['sb']} SB")
    line = ", ".join(parts)
    season = (
        f"Season: {_fmt_avg(r.get('s_avg'))}/{_fmt_avg(r.get('s_obp'))}/"
        f"{_fmt_avg(r.get('s_slg'))}, {r.get('s_hr', 0)} HR "
        f"in {r.get('s_pa', 0)} PA"
    )
    return f"- **{r['name']}** ({r.get('team', '?')}{pos}) — {line}. _{season}_"


def _pitcher_md(r):
    parts = [f"{r.get('ip', 0)} IP", f"{r.get('er', 0)} ER", f"{r.get('so', 0)} K"]
    if (r.get("bb") or 0):
        parts.append(f"{r['bb']} BB")
    if (r.get("h") or 0) is not None:
        parts.append(f"{r.get('h', 0)} H")
    if (r.get("w") or 0):
        parts.append("W")
    if (r.get("sv") or 0):
        parts.append("SV")
    if (r.get("hld") or 0):
        parts.append("HLD")
    line = ", ".join(parts)
    era = "—" if r.get("s_era") is None else f"{r['s_era']:.2f}"
    fip = "—" if r.get("s_fip") is None else f"{r['s_fip']:.2f}"
    season = f"Season: {era} ERA, {fip} FIP, {r.get('s_so', 0)} K in {r.get('s_ip', 0)} IP"
    return f"- **{r['name']}** ({r.get('team', '?')}) — {line}. _{season}_"


def build_digest(end: str):
    prev = _prev_snapshot(end)
    header = "# Anomaly Digest — Standout Game Lines\n\n"
    ts = (
        f"_Generated: {dt.datetime.now().isoformat(timespec='minutes')} ET — "
        f"game day {end}"
    )
    if prev and prev != end:
        ts += f" (delta vs {prev})"
    ts += "._\n\n"

    if not prev or prev == end:
        return (
            header + ts
            + "_Only one snapshot in the database — no prior day to diff "
            "against yet. The digest populates once a second nightly "
            "snapshot lands._\n"
        )

    hitters = [r for r in _rows(_hitter_lines(prev, end)) if _hitter_notable(r)]
    hitters.sort(key=_hitter_score, reverse=True)
    pitchers = [r for r in _rows(_pitcher_lines(prev, end)) if _pitcher_notable(r)]
    pitchers.sort(key=_pitcher_score, reverse=True)

    body = header + ts
    body += "## Hitting\n\n"
    if hitters:
        body += "\n".join(_hitter_md(r) for r in hitters[:TOP_HITTERS]) + "\n"
        if len(hitters) > TOP_HITTERS:
            body += f"\n_+{len(hitters) - TOP_HITTERS} more notable hitting lines._\n"
    else:
        body += "_No standout hitting lines for this game day._\n"

    body += "\n## Pitching\n\n"
    if pitchers:
        body += "\n".join(_pitcher_md(r) for r in pitchers[:TOP_PITCHERS]) + "\n"
        if len(pitchers) > TOP_PITCHERS:
            body += f"\n_+{len(pitchers) - TOP_PITCHERS} more notable pitching lines._\n"
    else:
        body += "_No standout pitching lines for this game day._\n"

    body += (
        "\n---\n_Thresholds: hitters need 2+ HR, 4+ H, 5+ RBI, 4+ R, 3+ SB, "
        "a multi-hit homer game, or 3+ XBH. Pitchers need 7+ IP with ≤1 ER, "
        "a 6+ IP shutout, or 10+ K. Baselines are season-to-date only._\n"
    )
    return body


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force-stale", action="store_true",
                    help="bypass the freshness gate (for manual regen)")
    ap.add_argument("--stdout", action="store_true",
                    help="print to stdout instead of writing the view file")
    args = ap.parse_args()

    if not args.force_stale:
        latest = _check_freshness()
        if latest is None:
            return 1
        end = latest or fl.latest_date()
    else:
        end = fl.latest_date()

    if not end:
        print("no snapshots in db; nothing to do", file=sys.stderr)
        return 0

    try:
        md = build_digest(end)
    except Exception as e:
        import traceback
        print(f"FAIL: {e}", file=sys.stderr)
        try:
            from notify import alert
            alert(
                "anomaly",
                "WARNING: anomaly.py failed to build the digest",
                traceback.format_exc()[-1500:],
            )
        except Exception as ee:
            print(f"alert dispatch failed: {ee}", file=sys.stderr)
        return 1

    if args.stdout:
        # Encode explicitly: the digest uses non-ASCII (em dash, ≤) and the
        # Windows console default codec (cp1252) can't encode it.
        sys.stdout.buffer.write(md.encode("utf-8"))
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / OUT_NAME
    out.write_text(md, encoding="utf-8")
    print(f"OK: {out} ({len(md):,} bytes)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main() or 0)
    except SystemExit:
        raise
    except Exception as e:
        import traceback
        try:
            from notify import alert
            alert(
                "anomaly",
                "FAILURE: anomaly.py crashed",
                f"{e}\n\n{traceback.format_exc()[-3500:]}",
            )
        except Exception:
            pass
        raise
