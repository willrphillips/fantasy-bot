#!/usr/bin/env python3
"""Monte Carlo playoff-odds simulator for the Captain Phillips league.

H2H categories, 11 cats, weekly matchups, TOP 4 make playoffs. Standings are
cumulative CATEGORY records (W-L-T summing to weeks*11).

Model: each team's forward weekly output per category is drawn around its
season-to-date rate (counting cats ~ Normal(mean, sqrt(mean)); rate cats ~
Normal(rate, sigma)). Remaining regular weeks are played on a round-robin
schedule; final seeding is by category win pct; top 4 enter a seeded bracket.

Inputs are the snapshot.md "Season-Long Category Totals" + "Standings" tables.
Update the T and REC dicts from a fresh snapshot before trusting absolute levels;
the scenario *deltas* are the robust output. Assumptions (WEEKS_PLAYED,
REMAINING_REG, sigmas) are declared below — vary them to stress-test.

Run: python3 playoff_odds.py
"""
import random, math
random.seed(42)

CATS = ["AVG","R","HR","RBI","SB","K","W","SV","HLD","ERA","WHIP"]
LOWER_BETTER = {"ERA","WHIP"}
COUNTING = {"R","HR","RBI","SB","K","W","SV","HLD"}
RATE_SIGMA = {"AVG":0.022,"ERA":1.10,"WHIP":0.15}   # weekly team-level sd
WEEKS_PLAYED = 9            # standings sum 99 = 9*11
REMAINING_REG = 13         # periods 10..22 (assumption; robust +-2)
N = 6000

# --- snapshot.md: Season-Long Category Totals (update on refresh) ---
T = {
"Bay County":      [0.267,339,99,320,38,281,17,46,20,3.93,1.28],
"Sh'Dynasty":      [0.250,305,97,279,64,527,30,33, 3,3.14,1.11],
"Fellowship":      [0.239,301,79,267,61,538,36,23,14,3.43,1.07],
"Brian's":         [0.278,313,86,295,38,469,24,12, 1,3.34,1.25],
"Ellz Bellz":      [0.252,310,88,314,57,497,30,11, 3,4.02,1.26],
"Southside":       [0.230,238,67,242,59,601,39,29,17,3.81,1.23],
"Captain Phillips":[0.251,301,77,254,40,461,31, 8,11,3.56,1.16],
"EL TORNADO":      [0.257,278,76,265,40,306,18,17, 0,3.92,1.23],
"Antonio's":       [0.241,274,80,261,38,366,21,12,15,3.67,1.17],
"Pete's":          [0.253,224,57,191,35,234,12, 7, 1,4.15,1.14],
}
# current cumulative category record (W,L,T) from snapshot standings
REC = {
"Bay County":(51,42,6),"Sh'Dynasty":(51,42,6),"Fellowship":(51,43,5),
"Brian's":(51,37,11),"Ellz Bellz":(51,37,11),"Southside":(46,48,5),
"Captain Phillips":(46,42,11),"EL TORNADO":(40,49,10),"Antonio's":(39,53,7),
"Pete's":(26,59,14),
}
TEAMS = list(T.keys())

def weekly_params(totals):
    p = {}
    for i, c in enumerate(CATS):
        if c in COUNTING:
            mean = totals[i] / WEEKS_PLAYED
            p[c] = ("count", mean, math.sqrt(max(mean, 0.25)))
        else:
            p[c] = ("rate", totals[i], RATE_SIGMA[c])
    return p

def draw(params, c):
    kind, mean, sd = params[c]
    v = random.gauss(mean, sd)
    return max(0, round(v)) if kind == "count" else max(0.0, v)

def week_values(params):
    return {c: draw(params, c) for c in CATS}

def cat_winner(va, vb, c):
    if va == vb: return 0
    better = va < vb if c in LOWER_BETTER else va > vb
    return 1 if better else -1

def round_robin(idx):
    n = len(idx); rounds = []; fixed = idx[0]; rot = idx[1:]
    for _ in range(n - 1):
        pairs = [(fixed, rot[-1])]
        for k in range(len(rot) // 2):
            pairs.append((rot[k], rot[len(rot) - 2 - k]))
        rounds.append(pairs); rot = [rot[-1]] + rot[:-1]
    return rounds
SCHED = round_robin(list(range(len(TEAMS))))

def simulate(params_by_team):
    cp = TEAMS.index("Captain Phillips")
    playoff = finals = champ = 0
    for _ in range(N):
        W = {t: REC[TEAMS[t]][0] for t in range(len(TEAMS))}
        L = {t: REC[TEAMS[t]][1] for t in range(len(TEAMS))}
        Tt = {t: REC[TEAMS[t]][2] for t in range(len(TEAMS))}
        for wk in range(REMAINING_REG):
            vals = [week_values(params_by_team[TEAMS[t]]) for t in range(len(TEAMS))]
            for a, b in SCHED[wk % len(SCHED)]:
                for c in CATS:
                    r = cat_winner(vals[a][c], vals[b][c], c)
                    if r == 1: W[a] += 1; L[b] += 1
                    elif r == -1: W[b] += 1; L[a] += 1
                    else: Tt[a] += 1; Tt[b] += 1
        pct = {t: (W[t] + 0.5 * Tt[t]) / (W[t] + L[t] + Tt[t]) for t in range(len(TEAMS))}
        order = sorted(range(len(TEAMS)), key=lambda t: pct[t], reverse=True)
        top4 = order[:4]
        if cp not in top4:
            continue
        playoff += 1
        seed = {t: top4.index(t) for t in top4}
        def play(a, b):
            va, vb = week_values(params_by_team[TEAMS[a]]), week_values(params_by_team[TEAMS[b]])
            wa = sum(1 for c in CATS if cat_winner(va[c], vb[c], c) == 1)
            wb = sum(1 for c in CATS if cat_winner(va[c], vb[c], c) == -1)
            if wa > wb: return a
            if wb > wa: return b
            return a if seed[a] < seed[b] else b
        s1, s2 = play(top4[0], top4[3]), play(top4[1], top4[2])
        if cp in (s1, s2): finals += 1
        fin = play(s1, s2) if seed[s1] < seed[s2] else play(s2, s1)
        if fin == cp: champ += 1
    return playoff / N, finals / N, champ / N

def cp_variant(**delta):
    """delta: per-WEEK change for counting cats, absolute change for rate cats."""
    tot = T["Captain Phillips"][:]
    for c, dv in delta.items():
        i = CATS.index(c)
        tot[i] += dv * WEEKS_PLAYED if c in COUNTING else dv
    p = dict(base); p["Captain Phillips"] = weekly_params(tot); return p

base = {t: weekly_params(T[t]) for t in TEAMS}

SCENARIOS = {
    "BASELINE (current roster)":   base,
    "+Marsh (bench AVG/R)":        cp_variant(AVG=0.004, R=1.0, RBI=0.4, HR=0.1),
    "+Aranda (bench RBI/HR)":      cp_variant(AVG=0.001, R=0.6, RBI=1.1, HR=0.45),
    "TRADE: SP -> mid-order bat":  cp_variant(HR=2.0, RBI=3.5, R=2.0, AVG=0.003, W=-0.5, K=-6),
    "TRADE: 2-for-2 elite bat":    cp_variant(HR=3.0, RBI=5.0, R=3.0, AVG=0.005, W=-0.4, K=-5),
}

if __name__ == "__main__":
    print(f"Monte Carlo: N={N}, remaining reg weeks={REMAINING_REG}, TOP 4 make playoffs\n")
    print(f"{'Scenario':30s} {'P(playoffs)':>12s} {'P(finals)':>10s} {'P(champ)':>9s}")
    for name, p in SCENARIOS.items():
        po, fi, ch = simulate(p)
        print(f"{name:30s} {po*100:10.1f}% {fi*100:8.1f}% {ch*100:7.1f}%")
