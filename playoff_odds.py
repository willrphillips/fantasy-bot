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
WEEKS_PLAYED = 14           # standings sum 154 = 14*11
REMAINING_REG = 7          # periods 15..21 (21-week reg season)
N = 6000

# --- snapshot.md: Season-Long Category Totals (update on refresh) ---
T = {
"Bay County":      [0.266,521,161,498,68,440,23,76,34,3.87,1.24],
"Sh'Dynasty":      [0.253,479,156,454,87,803,44,49, 4,3.05,1.11],
"Fellowship":      [0.241,460,131,426,81,872,54,42,20,3.56,1.11],
"Brian's":         [0.269,476,137,449,55,727,42,17, 1,3.70,1.25],
"Ellz Bellz":      [0.260,492,140,485,76,748,46,32, 3,3.85,1.22],
"Southside":       [0.231,404,119,396,95,947,57,41,25,4.03,1.25],
"Captain Phillips":[0.250,463,135,417,55,639,46,11,13,4.00,1.19],
"EL TORNADO":      [0.255,452,121,417,64,476,28,25, 0,3.96,1.27],
"Antonio's":       [0.254,420,126,404,56,552,36,18,19,3.49,1.15],
"Pete's":          [0.249,342, 82,289,58,384,20,21, 1,3.74,1.16],
}
# current cumulative category record (W,L,T) from snapshot standings
REC = {
"Bay County":(88,56,10),"Sh'Dynasty":(74,67,13),"Fellowship":(74,67,13),
"Brian's":(74,62,18),"Ellz Bellz":(77,57,20),"Southside":(77,63,14),
"Captain Phillips":(61,77,16),"EL TORNADO":(64,74,16),"Antonio's":(58,85,11),
"Pete's":(47,86,21),
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
    "Lineup fix (Teoscar active)": cp_variant(AVG=0.001, R=0.5, RBI=0.7, HR=0.3),
    "+Aranda 1B (AVG/RBI, for Ctreras)": cp_variant(AVG=0.004, R=0.6, RBI=1.1, HR=0.35),
    "+setup HLD arms (Whitlock/Morejon)": cp_variant(HLD=1.5, K=2.0, W=0.1, ERA=-0.12, WHIP=-0.03),
    "+ratio SP (drop Elder->Mize/Rogers)": cp_variant(K=1.0, W=0.1, ERA=-0.20, WHIP=-0.04),
    "ALL BARONBALL MOVES stacked":  cp_variant(AVG=0.004, R=1.0, RBI=1.6, HR=0.6, HLD=1.5, K=3.0, W=0.2, ERA=-0.30, WHIP=-0.07),
}

if __name__ == "__main__":
    print(f"Monte Carlo: N={N}, remaining reg weeks={REMAINING_REG}, TOP 4 make playoffs\n")
    print(f"{'Scenario':30s} {'P(playoffs)':>12s} {'P(finals)':>10s} {'P(champ)':>9s}")
    for name, p in SCENARIOS.items():
        po, fi, ch = simulate(p)
        print(f"{name:30s} {po*100:10.1f}% {fi*100:8.1f}% {ch*100:7.1f}%")
