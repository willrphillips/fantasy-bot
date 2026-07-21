import json, os
HERE = os.path.dirname(os.path.abspath(__file__))
c = json.load(open(os.path.join(HERE, 'config.json')))

print("Testing espn-api library...")
try:
    from espn_api.baseball import League
    league = League(league_id=2057904545, year=2026,
                    espn_s2=c['espn_s2'], swid=c['swid'])
    print(f"SUCCESS! League: {league.settings.name}")
    print(f"Teams: {[t.team_name for t in league.teams]}")
except Exception as e:
    print(f"FAILED: {e}")
