#!/usr/bin/env python3
"""Generate full league snapshot with category matchups and commit to GitHub repo."""
import json, base64, os, requests
from datetime import datetime
from espn_api.baseball import League

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(HERE, 'config.json')))
REPO = "willrphillips/fantasy-snapshots"
FILE_PATH = "snapshot.md"
BRANCH = "main"
LEAGUE_ID = 2057904545
YEAR = 2026

# statId -> human-readable category name (only the 11 in this league)
CAT_NAMES = {
    2: "AVG", 5: "HR", 20: "R", 21: "RBI", 23: "SB",
    48: "K", 53: "W", 57: "SV", 60: "HLD",
    47: "ERA", 41: "WHIP",
}
# Order: hitting first, then pitching
CAT_ORDER = [2, 20, 5, 21, 23, 48, 53, 57, 60, 47, 41]
# Reverse cats (lower is better)
REVERSE_CATS = {41, 47}

def fmt_score(stat_id, score):
    """Format ratio cats vs counting cats. Coerce strings to float."""
    try:
        score = float(score) if score is not None else 0.0
    except (ValueError, TypeError):
        return str(score)
    if score == float("inf") or score != score:  # inf or NaN
        return "—"
    if stat_id == 2:  # AVG
        return f"{score:.3f}"
    if stat_id in (47, 41):  # ERA, WHIP
        return f"{score:.2f}"
    return f"{int(score)}" if score == int(score) else f"{score:.1f}"

def build_snapshot():
    lg = League(league_id=LEAGUE_ID, year=YEAR,
                espn_s2=CFG['espn_s2'], swid=CFG['swid'])
    
    # Pull raw matchup data from ESPN API
    api_url = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb/seasons/{YEAR}/segments/0/leagues/{LEAGUE_ID}"
    cookies = {'espn_s2': CFG['espn_s2'], 'SWID': CFG['swid']}
    r = requests.get(api_url, cookies=cookies, params={'view': ['mMatchup','mMatchupScore','mTeam']})
    raw = r.json()
    
    current_mp = raw.get('status', {}).get('currentMatchupPeriod', 1)
    schedule = raw.get('schedule', [])
    teams_lookup = {t['id']: t for t in raw.get('teams', [])}
    
    def team_name(team_id):
        t = teams_lookup.get(team_id, {})
        return t.get('name') or f"Team {team_id}"
    
    out = []
    out.append("# Captain Phillips League Snapshot")
    out.append(f"_Generated: {datetime.now().strftime('%Y-%m-%d %H:%M %Z')}_")
    out.append(f"_Current matchup period: {current_mp}_\n")
    
    # === STANDINGS ===
    out.append("## Standings\n")
    out.append("| # | Team | W | L | T | PCT |")
    out.append("|---|------|---|---|---|-----|")
    for i, t in enumerate(sorted(lg.teams, key=lambda x: -x.wins), 1):
        pct = t.wins / max(1, t.wins + t.losses + t.ties)
        out.append(f"| {i} | {t.team_name} | {t.wins} | {t.losses} | {t.ties} | {pct:.3f} |")
    
    # === CURRENT MATCHUPS WITH CATEGORY BREAKDOWN ===
    out.append(f"\n## Current Matchups (Period {current_mp})\n")
    out.append("Each matchup shows category-by-category state. ✅ = winning, ❌ = losing, 🟰 = tied.\n")
    
    current_matchups = [m for m in schedule if m.get('matchupPeriodId') == current_mp]
    for m in current_matchups:
        home = m.get('home', {})
        away = m.get('away', {})
        h_name = team_name(home.get('teamId'))
        a_name = team_name(away.get('teamId'))
        h_scores = home.get('cumulativeScore', {}).get('scoreByStat', {}) or {}
        a_scores = away.get('cumulativeScore', {}).get('scoreByStat', {}) or {}
        h_w = home.get('cumulativeScore', {}).get('wins', 0)
        h_t = home.get('cumulativeScore', {}).get('ties', 0)
        h_l = home.get('cumulativeScore', {}).get('losses', 0)
        
        out.append(f"### {a_name} @ {h_name}")
        out.append(f"**Cat record:** {h_name} {h_w}-{h_l}-{h_t}\n")
        out.append(f"| Cat | {a_name[:20]} | {h_name[:20]} | Leader |")
        out.append("|-----|--------|--------|--------|")
        for stat_id in CAT_ORDER:
            sid_str = str(stat_id)
            h_data = h_scores.get(sid_str, {})
            a_data = a_scores.get(sid_str, {})
            h_score = h_data.get('score', 0)
            a_score = a_data.get('score', 0)
            result = h_data.get('result')  # WIN/LOSS/TIE from home perspective
            
            # Determine winner neutrally (use score comparison, not result field which is from home perspective)
            try:
                hs = float(h_score); as_ = float(a_score)
            except (ValueError, TypeError):
                hs = as_ = 0
            if hs == as_ or (hs == float("inf") and as_ == float("inf")):
                marker = "🟰 Tied"
            else:
                lower_better = stat_id in REVERSE_CATS
                # Treat inf as worst for reverse cats (no IP = bad ERA/WHIP)
                if hs == float("inf"): hs_eff = -1 if not lower_better else float("inf")
                else: hs_eff = hs
                if as_ == float("inf"): as_eff = -1 if not lower_better else float("inf")
                else: as_eff = as_
                if lower_better:
                    winner = h_name if hs_eff < as_eff else a_name
                else:
                    winner = h_name if hs_eff > as_eff else a_name
                marker = f"✅ {winner[:18]}"
            
            cat = CAT_NAMES.get(stat_id, f"stat{stat_id}")
            out.append(f"| {cat} | {fmt_score(stat_id, a_score)} | {fmt_score(stat_id, h_score)} | {marker} |")
        out.append("")
    
    # === ROSTERS ===
    out.append("\n## Rosters\n")
    for t in sorted(lg.teams, key=lambda x: -x.wins):
        out.append(f"### {t.team_name} ({t.wins}-{t.losses}-{t.ties})\n")
        out.append("| Slot | Player | Pos | Team | Status |")
        out.append("|------|--------|-----|------|--------|")
        for p in t.roster:
            inj = getattr(p, 'injuryStatus', '') or ''
            inj = inj if inj and inj != 'ACTIVE' else ''
            out.append(f"| {p.lineupSlot} | {p.name} | {p.position} | {p.proTeam} | {inj} |")
        out.append("")
    
    # === SEASON-LONG CATEGORY TOTALS ===
    out.append("\n## Season-Long Category Totals\n")
    out.append("Where each team ranks across all 11 categories. Bold = league leader, italic = bottom.\n")
    raw_teams = raw.get('teams', [])
    team_stats = {}  # team_id -> {stat_id: value}
    for rt in raw_teams:
        team_stats[rt['id']] = rt.get('valuesByStat', {})
    
    # Build header row
    header = "| Team |"
    sep = "|------|"
    for sid in CAT_ORDER:
        header += f" {CAT_NAMES.get(sid, sid)} |"
        sep += "------|"
    out.append(header)
    out.append(sep)
    
    # For each team (sorted by record), show their stat values
    for t in sorted(lg.teams, key=lambda x: -x.wins):
        stats = team_stats.get(t.team_id, {})
        row = f"| {t.team_name[:22]} |"
        for sid in CAT_ORDER:
            raw_val = stats.get(str(sid), 0)
            row += f" {fmt_score(sid, raw_val)} |"
        out.append(row)
    
    # Now add a "league leader by category" summary
    out.append("\n### Category Leaders\n")
    out.append("| Cat | Leader | Value | You rank |")
    out.append("|-----|--------|-------|----------|")
    your_team = next((t for t in lg.teams if t.team_name == "Captain Phillips"), None)
    your_id = your_team.team_id if your_team else None
    for sid in CAT_ORDER:
        # Collect (team_id, value) pairs
        pairs = [(tid, float(stats.get(str(sid), 0) or 0)) for tid, stats in team_stats.items()]
        lower_better = sid in REVERSE_CATS
        # Filter out 0/inf for ratio cats to avoid garbage rankings
        if sid in (2, 41, 47):
            pairs = [(tid, v) for tid, v in pairs if v > 0 and v != float("inf")]
        pairs.sort(key=lambda x: x[1], reverse=not lower_better)
        if not pairs:
            continue
        leader_id, leader_val = pairs[0]
        leader_team = next((t for t in lg.teams if t.team_id == leader_id), None)
        leader_name = leader_team.team_name[:22] if leader_team else "?"
        # Find your rank
        your_rank = "?"
        for i, (tid, _) in enumerate(pairs, 1):
            if tid == your_id:
                your_rank = f"#{i}/{len(pairs)}"
                break
        out.append(f"| {CAT_NAMES.get(sid)} | {leader_name} | {fmt_score(sid, leader_val)} | {your_rank} |")
    out.append("")
    
    # === FREE AGENTS ===
    out.append("## Top 50 Free Agents\n")
    out.append("| Player | Pos | Team | %Owned |")
    out.append("|--------|-----|------|--------|")
    for p in lg.free_agents(size=50):
        out.append(f"| {p.name} | {p.position} | {p.proTeam} | {p.percent_owned:.1f}% |")
    
    return "\n".join(out)

def commit_to_repo(content):
    headers = {"Authorization": f"token {CFG['github_token']}",
               "Accept": "application/vnd.github+json"}
    api_base = f"https://api.github.com/repos/{REPO}/contents/{FILE_PATH}"
    r = requests.get(api_base, headers=headers, params={"ref": BRANCH})
    sha = r.json().get("sha") if r.status_code == 200 else None
    payload = {
        "message": f"Snapshot {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "content": base64.b64encode(content.encode('utf-8')).decode('ascii'),
        "branch": BRANCH,
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(api_base, headers=headers, json=payload)
    r.raise_for_status()
    return f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/{FILE_PATH}"


if __name__ == '__main__':
    print("Building snapshot...")
    content = build_snapshot()
    print(f"Snapshot length: {len(content)} chars")
    with open(os.path.join(HERE, 'public', 'snapshot.md'),'w') as out:
        out.write(content)
    print("Wrote local copy")
    print("Committing to GitHub...")
    url = commit_to_repo(content)
    print(f"Raw URL: {url}")
