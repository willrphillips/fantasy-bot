#!/usr/bin/env python3
"""
views.py — Generate pre-baked Markdown reports from fantasy.db.

Run nightly after mlb_ingest.py. Output goes to ~/fantasy-bot/public/views/,
which db_publish.py then pushes to the GitHub repo.

Each report is fetched from chat via:
    https://willrphillips.github.io/fantasy-snapshots/views/<name>.md

Reports generated:
    team_review.md         your roster — season/L7/L14/L30 inline + advanced
    waiver_hitters.md      top 40 FA hitters by L14 OPS
    waiver_pitchers.md     top 40 FA pitchers by L14 FIP
    regression_watch.md    biggest xwOBA-wOBA gaps both directions; ERA-FIP gaps
    trade_targets.md       category surplus/deficit by team
    category_standings.md  current period matchup + season standings
    roster_optimize.md     L14 add/drop swap suggestions vs the FA pool
    pull_status.md         freshness / row counts / last pull log

Usage:
    python3 views.py             # generate all
    python3 views.py --only team_review
"""
import argparse
import datetime as dt
import os
import sqlite3
import sys
from pathlib import Path

# Use fantasy_lib for window math
sys.path.insert(0, str(Path(__file__).parent))
import fantasy_lib as fl

OUT_DIR = Path(os.path.expanduser("~/fantasy-bot/public/views"))
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _check_freshness():
    """Bail if stats are stale — prevents publishing yesterday-or-older
    data when the 3:30 AM ingest failed. Returns the latest date if
    fresh; sends an alert and returns None if stale. An empty db
    (first-run case) is treated as 'not stale' so init runs aren't
    blocked."""
    latest = fl.latest_date()
    if not latest:
        return ""  # empty db, let downstream handle
    yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    if latest < yesterday:
        msg = (
            f"hitting_stats latest date = {latest}, expected >= "
            f"{yesterday}. Refusing to regenerate views against stale "
            f"data. Investigate why mlb_ingest didn't run or didn't "
            f"write yesterday's snapshot."
        )
        print(f"STALE: {msg}", file=sys.stderr)
        try:
            from notify import alert
            alert(
                "views",
                f"STALE: views.py refused to run (latest={latest})",
                msg,
            )
        except Exception as e:
            print(f"alert dispatch failed: {e}", file=sys.stderr)
        return None
    return latest


def _ts_line():
    return f"_Generated: {dt.datetime.now().isoformat(timespec='minutes')} ET — db latest pull: {fl.latest_date()}_\n"


def _df_to_md(df, max_rows=None):
    """Render a DataFrame or list-of-dicts as a Markdown table."""
    if df is None:
        return "_no data_\n"
    if hasattr(df, "to_markdown"):
        if max_rows:
            df = df.head(max_rows)
        try:
            return df.to_markdown(index=False, floatfmt=".3f") + "\n"
        except Exception:
            pass
    # fallback: hand-rolled
    if not df:
        return "_no data_\n"
    rows = df[:max_rows] if max_rows else df
    cols = list(rows[0].keys())
    out = "| " + " | ".join(cols) + " |\n"
    out += "|" + "|".join(["---"] * len(cols)) + "|\n"
    for r in rows:
        out += "| " + " | ".join(str(r.get(c, "")) for c in cols) + " |\n"
    return out


# ============================================================
# Reports
# ============================================================

def report_team_review():
    title = "# Captain Phillips — Team Review\n\n"
    body = _ts_line() + "\n"

    rost = fl.my_roster()
    body += "## Roster\n\n"
    body += _df_to_md(rost)
    body += "\n## Hitting — season vs L14 vs L30\n\n"

    # Pull each roster hitter through window_stats for L14 and L30
    rows = []
    if hasattr(rost, "iterrows"):
        iterator = (r["player_name"] for _, r in rost.iterrows())
    else:
        iterator = (r["player_name"] for r in rost)
    for name in iterator:
        if not name:
            continue
        season = fl.player(name).get("hitting")
        if hasattr(season, "empty") and season.empty:
            continue
        if not hasattr(season, "empty") and not season:
            continue
        s = season.iloc[0].to_dict() if hasattr(season, "iloc") else season[0]
        if not s.get("pa"):
            continue
        w14 = fl.window_stats(name, days=14).get("hitting") or {}
        w30 = fl.window_stats(name, days=30).get("hitting") or {}
        rows.append({
            "Player": name,
            "S_AVG": s.get("avg"), "S_HR": s.get("hr"), "S_RBI": s.get("rbi"),
            "S_R": s.get("r"), "S_SB": s.get("sb"), "S_OPS": s.get("ops"),
            "L14_AVG": w14.get("avg"), "L14_HR": w14.get("hr"),
            "L14_RBI": w14.get("rbi"), "L14_R": w14.get("r"),
            "L14_OPS": w14.get("ops"),
            "L30_AVG": w30.get("avg"), "L30_HR": w30.get("hr"),
            "L30_RBI": w30.get("rbi"), "L30_OPS": w30.get("ops"),
        })
    body += _df_to_md(rows)

    body += "\n## Pitching — season vs L14 vs L30\n\n"
    rows = []
    if hasattr(rost, "iterrows"):
        iterator = (r["player_name"] for _, r in rost.iterrows())
    else:
        iterator = (r["player_name"] for r in rost)
    for name in iterator:
        if not name:
            continue
        season = fl.player(name).get("pitching")
        if hasattr(season, "empty") and season.empty:
            continue
        if not hasattr(season, "empty") and not season:
            continue
        s = season.iloc[0].to_dict() if hasattr(season, "iloc") else season[0]
        if not s.get("ip"):
            continue
        w14 = fl.window_stats(name, days=14, side="pit").get("pitching") or {}
        w30 = fl.window_stats(name, days=30, side="pit").get("pitching") or {}
        rows.append({
            "Player": name,
            "S_IP": s.get("ip"), "S_ERA": s.get("era"), "S_WHIP": s.get("whip"),
            "S_FIP": s.get("fip"), "S_K": s.get("so"), "S_W": s.get("w"),
            "S_SV": s.get("sv"), "S_HLD": s.get("hld"),
            "L14_IP": w14.get("ip"), "L14_ERA": w14.get("era"),
            "L14_FIP": w14.get("fip"), "L14_K": w14.get("so"),
            "L30_IP": w30.get("ip"), "L30_ERA": w30.get("era"),
            "L30_FIP": w30.get("fip"),
        })
    body += _df_to_md(rows)

    return title + body


def report_waiver_hitters():
    title = "# Waiver Hitters — L14 Hot Bats (FA only)\n\n"
    body = _ts_line() + "\n"
    body += _df_to_md(fl.hot_bats(days=14, n=40, min_pa=30, fa_only=True))
    body += "\n## L30 view\n\n"
    body += _df_to_md(fl.hot_bats(days=30, n=40, min_pa=60, fa_only=True))
    return title + body


def report_waiver_pitchers():
    title = "# Waiver Pitchers — L14 Best FIP (FA only)\n\n"
    body = _ts_line() + "\n"
    body += _df_to_md(fl.hot_arms(days=14, n=40, min_ip=5, fa_only=True))
    body += "\n## L30 view\n\n"
    body += _df_to_md(fl.hot_arms(days=30, n=40, min_ip=12, fa_only=True))
    return title + body


def report_regression_watch():
    title = "# Regression Watch\n\n"
    body = _ts_line() + "\n"
    body += "## Hitters — biggest xwOBA-wOBA gaps (positive = expect improvement)\n\n"
    body += _df_to_md(fl.regression_watch("up", n=20))
    body += "\n## Hitters — biggest wOBA-xwOBA gaps (negative = expect cooling)\n\n"
    body += _df_to_md(fl.regression_watch("down", n=20))
    body += "\n## Pitchers — biggest ERA-FIP gaps (positive = unlucky, expect improvement)\n\n"
    body += _df_to_md(fl.fip_era_gap("up", n=20))
    body += "\n## Pitchers — biggest FIP-ERA gaps (negative = lucky, expect regression)\n\n"
    body += _df_to_md(fl.fip_era_gap("down", n=20))
    return title + body


def report_trade_targets():
    title = "# Trade Targets — Other Teams' Power Bats\n\n"
    body = _ts_line() + "\n"
    teams = fl.teams_list()
    if hasattr(teams, "iterrows"):
        team_names = [r["team_name"] for _, r in teams.iterrows()]
    else:
        team_names = [r["team_name"] for r in teams]
    for team in team_names:
        if team == "Captain Phillips":
            continue
        body += f"## {team}\n\n"
        body += _df_to_md(fl.trade_scout(team, sort="hr"), max_rows=15)
        body += "\n"
    return title + body


def report_category_standings():
    title = "# Standings + Current Matchup\n\n"
    body = _ts_line() + "\n"
    body += "## Standings\n\n"
    body += _df_to_md(fl.standings())
    body += "\n## Current Matchup (category leaders)\n\n"
    body += _df_to_md(fl.matchups())
    return title + body


def report_roster_optimize():
    title = "# Roster Optimize — L14 Add/Drop Swaps\n\n"
    body = _ts_line() + "\n"
    body += (
        "Hitters ranked by L14 OPS, pitchers by L14 FIP. A swap is shown "
        "only when the FA shares at least one non-bench eligible slot with "
        "the rostered player AND clears the gap threshold (OPS +.050 / "
        "FIP -0.50). Pure drops are roster spots flagged IL/OUT/etc. with "
        "no FA match.\n\n"
    )
    opt_14 = fl.roster_optimize("Captain Phillips", days=14)
    body += "## Hitter swaps (L14)\n\n"
    body += _df_to_md(opt_14["swaps_hit"])
    body += "\n## Pitcher swaps (L14)\n\n"
    body += _df_to_md(opt_14["swaps_pit"])
    body += "\n## Pure drop candidates (status flagged, no FA match)\n\n"
    body += _df_to_md(opt_14["drop_only"])

    body += "\n## Your hitters — L14, worst to best\n\n"
    body += _df_to_md(opt_14["roster_hit"])
    body += "\n## Your pitchers — L14, worst to best\n\n"
    body += _df_to_md(opt_14["roster_pit"])

    opt_30 = fl.roster_optimize("Captain Phillips", days=30,
                                min_pa=60, min_ip=12)
    body += "\n## L30 cross-check — hitter swaps\n\n"
    body += _df_to_md(opt_30["swaps_hit"])
    body += "\n## L30 cross-check — pitcher swaps\n\n"
    body += _df_to_md(opt_30["swaps_pit"])
    return title + body


def report_pull_status():
    title = "# Data Pipeline Status\n\n"
    body = _ts_line() + "\n"
    h = fl.health()
    body += "## Row counts\n\n"
    for k, v in h.items():
        if k == "last_pull_log":
            continue
        body += f"- **{k}**: {v}\n"
    body += "\n## Last pull log\n\n"
    last = h.get("last_pull_log") or {}
    for k, v in last.items():
        body += f"- {k}: {v}\n"
    return title + body


REPORTS = {
    "team_review":         report_team_review,
    "waiver_hitters":      report_waiver_hitters,
    "waiver_pitchers":     report_waiver_pitchers,
    "regression_watch":    report_regression_watch,
    "trade_targets":       report_trade_targets,
    "category_standings":  report_category_standings,
    "roster_optimize":     report_roster_optimize,
    "pull_status":         report_pull_status,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", type=str, default=None,
                    help="generate only one report by name")
    ap.add_argument("--force-stale", action="store_true",
                    help="bypass the freshness gate (for manual regen)")
    args = ap.parse_args()

    if not args.force_stale and _check_freshness() is None:
        return 1

    targets = [args.only] if args.only else list(REPORTS.keys())
    failures = []
    for name in targets:
        fn = REPORTS.get(name)
        if not fn:
            print(f"unknown report: {name}", file=sys.stderr)
            failures.append((name, "unknown report name"))
            continue
        try:
            md = fn()
            out = OUT_DIR / f"{name}.md"
            out.write_text(md)
            print(f"OK: {out} ({len(md):,} bytes)")
        except Exception as e:
            import traceback
            print(f"FAIL {name}: {e}", file=sys.stderr)
            failures.append((name, traceback.format_exc()[-1500:]))

    if failures:
        body = "\n\n".join(f"### {n}\n{err}" for n, err in failures)
        try:
            from notify import alert
            alert(
                "views",
                f"WARNING: views.py - {len(failures)} report(s) failed",
                body,
            )
        except Exception as e:
            print(f"alert dispatch failed: {e}", file=sys.stderr)


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
                "views",
                "FAILURE: views.py crashed",
                f"{e}\n\n{traceback.format_exc()[-3500:]}",
            )
        except Exception:
            pass
        raise
