#!/usr/bin/env python3
"""Monte Carlo playoff-odds simulator for the Captain Phillips league.

H2H categories, 11 cats, weekly matchups, top 4 make a seeded 2-week bracket.
Standings are cumulative CATEGORY records (W-L-T summing to weeks * 11).

Everything is pulled LIVE — nothing is hardcoded from a stale snapshot:
  * league settings (regular-season length, playoff team count) from ESPN
  * current category records from ESPN
  * the actual remaining schedule from ESPN (not a synthetic round robin)
  * each team's forward weekly output BOOTSTRAPPED from its own completed
    weekly totals in fantasy.db, resampling whole weeks so the correlation
    between categories inside a week is preserved

Run: venv/bin/python3 playoff_odds.py [--sims 20000] [--json]
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import math
import random
import sqlite3
import sys

import requests

import espn_utils as E

DB = "fantasy.db"
CATS = ["AVG", "R", "HR", "RBI", "SB", "K", "W", "SV", "HLD", "ERA", "WHIP"]
LOWER_BETTER = {"ERA", "WHIP"}
RATE = {"AVG", "ERA", "WHIP"}
ME = "Captain Phillips"
UA = {"User-Agent": "Mozilla/5.0"}


# ---------------------------------------------------------------- live pulls
def espn(views):
    cfg = E.load_config()
    url = (f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb"
           f"/seasons/{E.SEASON}/segments/0/leagues/{E.LEAGUE_ID}")
    r = requests.get(url, params={"view": views}, cookies=E.cookies_from_cfg(cfg),
                     headers=UA, timeout=30)
    r.raise_for_status()
    return r.json()


def league_state():
    d = espn(["mSettings", "mTeam", "mMatchup"])
    sch = d["settings"]["scheduleSettings"]
    names = {t["id"]: t["name"] for t in d["teams"]}
    recs = {}
    for t in d["teams"]:
        r = t["record"]["division"]
        recs[names[t["id"]]] = (r["wins"], r["losses"], r["ties"])
    cur = d["status"]["currentMatchupPeriod"]
    last_reg = sch["matchupPeriodCount"]
    remaining = collections.defaultdict(list)
    for m in d["schedule"]:
        p = m["matchupPeriodId"]
        if cur <= p <= last_reg:
            remaining[p].append((names[m["home"]["teamId"]], names[m["away"]["teamId"]]))
    return {
        "names": list(names.values()),
        "records": recs,
        "current_week": cur,
        "last_reg_week": last_reg,
        "playoff_teams": sch["playoffTeamCount"],
        "playoff_week_len": sch["playoffMatchupPeriodLength"],
        "schedule": dict(sorted(remaining.items())),
    }


# ------------------------------------------------- historical weekly samples
def weekly_history(db=DB):
    """Completed weekly category totals per team, from the daily matchup pulls.

    The matchup rows are cumulative WITHIN a week and reset each Monday, so the
    Sunday pull is that week's final line. Only weeks with a Sunday pull count.
    """
    con = sqlite3.connect(db)
    rows = con.execute(
        "select date_pulled, home_team, away_team, cat, home_value, away_value "
        "from matchups where cat in (%s)" % ",".join("?" * len(CATS)), CATS
    ).fetchall()
    con.close()

    by_date = collections.defaultdict(dict)   # date -> (team, cat) -> value
    for date, home, away, cat, hv, av in rows:
        by_date[date][(home, cat)] = hv
        by_date[date][(away, cat)] = av

    weeks = collections.defaultdict(list)     # monday -> [dates]
    for date in by_date:
        d = dt.date.fromisoformat(date)
        weeks[d - dt.timedelta(days=d.weekday())].append(date)

    samples = collections.defaultdict(list)   # team -> [{cat: value}]
    for monday, dates in sorted(weeks.items()):
        sunday = (monday + dt.timedelta(days=6)).isoformat()
        if sunday not in dates:
            continue                          # partial / in-flight week
        snap = by_date[sunday]
        for t in {t for (t, _) in snap}:
            wk = {}
            for c in CATS:
                v = snap.get((t, c))
                if v is None or not math.isfinite(v):
                    wk = None
                    break
                wk[c] = v
            if wk:
                samples[t].append(wk)
    return samples


# ------------------------------------------------------------------ simulate
def cat_result(a, b, c):
    if a == b:
        return 0
    better = a < b if c in LOWER_BETTER else a > b
    return 1 if better else -1


def score_week(wa, wb):
    """Category W-L-T for team a against team b over one (or a summed) line."""
    w = l = t = 0
    for c in CATS:
        r = cat_result(wa[c], wb[c], c)
        w += r == 1
        l += r == -1
        t += r == 0
    return w, l, t


def combine(lines):
    """Fold N weekly lines into one playoff-round line (rate cats averaged)."""
    return {c: (sum(x[c] for x in lines) / len(lines) if c in RATE
                else sum(x[c] for x in lines)) for c in CATS}


def simulate(state, samples, n_sims, rng, boost=None):
    teams = state["names"]
    pool = {t: samples.get(t, []) for t in teams}
    thin = [t for t in teams if len(pool[t]) < 4]
    if thin:
        raise SystemExit(f"not enough weekly history for: {thin}")

    def draw(t):
        wk = dict(rng.choice(pool[t]))
        if boost and t == ME:
            for c, dv in boost.items():
                wk[c] = max(0.0, wk[c] + dv)
        return wk

    n_playoff = state["playoff_teams"]
    rounds = state["playoff_week_len"]
    made = finals = champ = 0
    seeds = collections.Counter()

    for _ in range(n_sims):
        W = {t: state["records"][t][0] for t in teams}
        L = {t: state["records"][t][1] for t in teams}
        T = {t: state["records"][t][2] for t in teams}
        for pairs in state["schedule"].values():
            lines = {t: draw(t) for t in teams}
            for home, away in pairs:
                w, l, t_ = score_week(lines[home], lines[away])
                W[home] += w; L[home] += l; T[home] += t_
                W[away] += l; L[away] += w; T[away] += t_

        pct = {t: (W[t] + 0.5 * T[t]) / (W[t] + L[t] + T[t]) for t in teams}
        order = sorted(teams, key=lambda t: (pct[t], rng.random()), reverse=True)
        seeds[order.index(ME) + 1] += 1
        bracket = order[:n_playoff]
        if ME not in bracket:
            continue
        made += 1

        rank = {t: i for i, t in enumerate(bracket)}

        def play(a, b):
            la = combine([draw(a) for _ in range(rounds)])
            lb = combine([draw(b) for _ in range(rounds)])
            w, l, _t = score_week(la, lb)
            if w != l:
                return a if w > l else b
            return a if rank[a] < rank[b] else b      # higher seed breaks ties

        f1 = play(bracket[0], bracket[3])
        f2 = play(bracket[1], bracket[2])
        if ME in (f1, f2):
            finals += 1
            if play(*sorted([f1, f2], key=lambda t: rank[t])) == ME:
                champ += 1
    return made / n_sims, finals / n_sims, champ / n_sims, seeds


def ordinal(n):
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


# ---------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sims", type=int, default=20000)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    state = league_state()
    samples = weekly_history()
    rng = random.Random(42)

    weeks_left = len(state["schedule"])
    hist = len(samples[ME])

    scenarios = {
        "Baseline (roster as it stands)": None,
        "Bats wake up (+3 R, +1 HR, +3 RBI, +.008 AVG)":
            {"R": 3, "HR": 1, "RBI": 3, "AVG": 0.008},
        "Arms deliver (+8 K, -0.40 ERA, -0.06 WHIP)":
            {"K": 8, "W": 0.5, "ERA": -0.40, "WHIP": -0.06},
        "Baronball breaks right (both)":
            {"R": 3, "HR": 1, "RBI": 3, "AVG": 0.008,
             "K": 8, "W": 0.5, "ERA": -0.40, "WHIP": -0.06},
    }

    results, base_seeds = {}, None
    for name, boost in scenarios.items():
        made, fin, ch, seeds = simulate(state, samples, a.sims, rng, boost)
        results[name] = {"playoffs": made, "finals": fin, "champ": ch}
        if base_seeds is None:
            base_seeds = seeds

    rec = state["records"][ME]
    pcts = sorted((((w + 0.5 * t) / (w + l + t)), n)
                  for n, (w, l, t) in state["records"].items())
    pcts.reverse()
    cut = pcts[state["playoff_teams"] - 1][0]
    mine = next(p for p, n in pcts if n == ME)
    gap = round((cut - mine) * sum(rec), 1)

    out = {"generated": dt.datetime.now().isoformat(timespec="seconds"),
           "current_week": state["current_week"], "weeks_left": weeks_left,
           "playoff_teams": state["playoff_teams"], "history_weeks": hist,
           "sims": a.sims, "record": f"{rec[0]}-{rec[1]}-{rec[2]}",
           "pct": round(mine, 4), "cutline_pct": round(cut, 4), "gap_cat_wins": gap,
           "scenarios": results,
           "seed_distribution": {k: v / a.sims for k, v in sorted(base_seeds.items())}}

    if a.json:
        print(json.dumps(out, indent=2))
        return

    print(f"Playoff odds — {ME}   (week {state['current_week']}, "
          f"{weeks_left} weeks left, top {state['playoff_teams']} advance)")
    print(f"Record {out['record']} ({mine:.3f}); the 4th seed sits at {cut:.3f}, "
          f"a gap of ~{gap} category wins")
    print(f"Bootstrapped from {hist} completed weeks per team, {a.sims:,} sims\n")
    print(f"{'Scenario':50s} {'Playoffs':>9s} {'Finals':>8s} {'Title':>7s}")
    for name, r in results.items():
        print(f"{name:50s} {r['playoffs']*100:8.1f}% "
              f"{r['finals']*100:7.1f}% {r['champ']*100:6.1f}%")
    print("\nFinish distribution (baseline):")
    for seed, p in out["seed_distribution"].items():
        if p >= 0.005:
            print(f"  {ordinal(seed):>4s}  {p*100:5.1f}%")


if __name__ == "__main__":
    sys.exit(main())
