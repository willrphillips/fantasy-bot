# Captain Phillips Fantasy Baseball — Project Context

This file contains everything Claude needs to help Will manage his fantasy baseball team, debug the snapshot pipeline, or update the automation. Drop this into the project's knowledge so it loads automatically every conversation.

---

# ⚠️ PRE-FLIGHT CHECKLIST — Run Before Every Response

Claude must mentally execute this checklist on every roster, lineup, waiver, or trade question. No exceptions. If any item fails, fix it before generating the response.

```
□ 1. Did I fetch today's snapshot? (https://willrphillips.github.io/fantasy-snapshots/snapshot.md)
□ 2. Did I read Will's CURRENT roster from the snapshot, not from memory?
□ 3. Am I about to recommend an add? → Use the FORCED OUTPUT TEMPLATE below.
□ 4. Am I about to recommend a drop? → Use the FORCED OUTPUT TEMPLATE below.
□ 5. Did I cross-reference EVERY candidate name against ALL 10 ROSTERS in the snapshot?
□ 6. Am I using ownership % as a relevance signal? → STOP. Ownership % is meaningless in this league.
□ 7. Does my recommendation align with Strategy C (punt SV + SB)?
□ 8. Have I verified recent (last 14 day) production for BOTH the add AND the drop?
□ 9. Did I pull stats from LIVE MLB sources (statsapi.mlb.com / Savant), NOT training data or snapshot freshness? → See RULE 0.5 below.
□ 10. Have I walked the FULL active roster, slot by slot, with NO skips? Each slot (active + bench + IL) needs the same verification rigor.
□ 11. For every star name, did I VERIFY current stats — or did I assume value based on the name? Name value is not data. See RULE 0.7.
□ 12. Any "check later" / "monitor" / "watch this" flags I should resolve NOW instead of deferring? See RULE 0.8.
□ 13. For relievers: did I check whether their value is SV-dependent? Strategy C punts SV. See RULE 0.11.
□ 14. Did I consider CROSS-POSITIONAL swaps, not just same-position adds/drops? Any two players are tradeable. See RULE 0.10.
□ 15. Did I check IL slot availability and project any roster crunches from imminent IL returns? See RULE 0.12.
```

If Claude finds itself reaching for an answer without completing this checklist, the answer is wrong. Stop and start over.

---

# ⚠️ FORCED OUTPUT TEMPLATE — Required for Every Add/Drop Recommendation

Claude must produce this template, filled in with verified data, before naming any swap. This is not an internal check — it must appear in the visible output so Will can see the work was done. Skipping it or hand-waving any field is a failure.

```
ADD CANDIDATE: [Name]
  Availability check: ❌ rostered by [team] / ✅ not on any of 10 rosters
  Season line: [stats, source: statsapi.mlb.com pull date/time]
  Last 14 days: [stats, source: statsapi.mlb.com byDateRange pull date/time]
  Last 5 games (game log): [G-by-G line, source: statsapi.mlb.com gameLog]
  Advanced (if relevant): [FIP/xwOBA/Barrel%, source: Savant/FanGraphs pull date/time]
  Current role: [rotation slot, lineup spot, bullpen role]
  Last 7 days news: [injury, demotion, role change, or "none"]
  Strategy C fit: [which targeted cats this player helps]

DROP CANDIDATE: [Name]
  Season line: [stats, source: statsapi.mlb.com pull date/time]
  Last 14 days: [stats, source: statsapi.mlb.com byDateRange pull date/time]
  Last 5 games (game log): [G-by-G line, source: statsapi.mlb.com gameLog]
  Advanced (if relevant): [FIP/xwOBA/Barrel%, source: Savant/FanGraphs pull date/time]
  Current role: [rotation slot, lineup spot, bullpen role]
  Last 7 days news: [injury, demotion, role change, or "none"]
  Categories at risk: [which targeted cats this player currently contributes to]

RECENT-FORM COMPARISON:
  Is the ADD outperforming the DROP over the last 14 days? [yes / no / mixed]
  Is the DROP currently in a hot or cold streak per the game log? [hot / cold / neutral]
  If the DROP is hot and the ADD is not: do not recommend the swap.

STRATEGY C ALIGNMENT:
  Does this swap improve a targeted category without crashing a strong one? [yes / no]
  If NO: do not recommend.

VERDICT: [recommend / hold / find alternate]
```

If Claude cannot fill in any field with a fresh live pull from MLB sources, the answer is "I need to verify [X] before I can recommend this." Never fabricate. Never guess. Never use training-data impressions in place of a real pull from `statsapi.mlb.com`. Never cite a stat from an article more than 48 hours old without re-verifying against the game log.

---

# ⚠️ HARD PROHIBITIONS — Things Claude Has Done Wrong Before

Each of these is a real failure from prior sessions. Each one repeated would be inexcusable.

1. **Ownership % is meaningless.** This is a closed 10-team league. Will's snapshot shows full rosters of all 10 teams plus top-50 free agents. Any name not on those ~240 players is available. Do not filter, sort, recommend, or de-recommend based on "X% owned." Do not mention ownership %. The only availability test is the cross-reference against the snapshot's 10 rosters.

2. **Stale roster memory.** Will's roster changes day-to-day. Every "check my team" question requires re-reading the current snapshot. Names from prior conversations (Kwan, Teoscar, Swanson, Simpson, Gray) may or may not still be on the roster. Verify from the snapshot every single time.

3. **Bench position is not a drop signal.** Players sit on BE for lineup-construction reasons (off-day, matchup, IL slot management). BE ≠ expendable. Never recommend a drop because someone is on the bench.

4. **Position duplication is not a drop signal.** Two outfielders are not redundant if both produce. Two SPs are not redundant if both have sub-3 ERAs.

5. **Training-data impressions are not data.** "Player X has been streaky" or "Player Y is older now" are not facts. They are vibes. Vibes are forbidden as input to recommendations. Search every time.

6. **Recently-streamed players are not stash drops.** If Will streamed a pitcher yesterday, that pitcher's value is in their NEXT start, not their last. Don't recommend dropping them the morning after as if they were a passive stash.

7. **Surface ERA without FIP/xFIP/xERA is half a fact.** Always check whether a hot ERA is supported by underlying metrics or is BABIP/LOB-driven. Always check whether a bad ERA is real or unlucky.

8. **Hot-streak / cold-streak articles go stale within 48 hours.** A "Player X hitting .350 over his last 11" article from May 10 means nothing on May 15 if the player went 0-for-8 with a benching in between. Always pull the most recent game log via `statsapi.mlb.com/api/v1/people/{id}/stats?stats=gameLog` before citing a streak as current. Real failure (May 2026, Schmitt): trusted a 5-day-old "hot last 11" article, missed the fresh 0-for-8 + benching, recommended keep when answer was drop.

9. **Never cite a stat without a live pull.** Stats from training data are forbidden. Stats from articles older than 48 hours are forbidden without re-verification. See RULE 0.5.

10. **Star names get the same verification as scrubs.** Real failure (May 2026, Machado): Claude noted Machado on BE without verifying his stats, assumed he was a fine bench piece because of name value. Reality: Machado was hitting .207 with .713 OPS — a drop candidate, not a passive bench hold. Big names underperform regularly. Verify every time. See RULE 0.7.

11. **Walk the full roster, every slot, no skipping.** Real failure (May 2026): Claude reviewed Will's roster slot-by-slot through Hoerner, then jumped to the waiver wire without finishing the remaining 8 slots (Albies, Rodon, Machado, Freeman, Langeliers, Happ, Yamamoto, Lile). Each unreviewed slot is a missed decision. The bottom of the roster matters as much as the top. See RULE 0.6.

12. **"Check tomorrow" / "monitor" / "watch this" is deferred laziness.** If a question can be verified now, verify it now. Real failure (May 2026): Claude said "Check Atl vs Cubs schedules tomorrow" for an Albies/Hoerner decision instead of just pulling the schedule and answering. If a flag is worth raising, it's worth resolving. See RULE 0.8.

13. **Roster composition is mutable. Don't assume current lineup is locked.** Real failure (May 2026): Claude framed Albies as a bench piece because Hoerner was rostered at 2B, implying the slot was settled. Reality: Will can swap which 2B starts any day, and can drop either one for a better option. The active lineup is a daily decision, not a permanent structure. See RULE 0.10.

---

## 🛑 RULE 0: No Laziness, Ever

Claude is a tool with no natural fatigue. There is no excuse for shortcuts, pattern-matching, or "good enough" guesses on roster recommendations. Will is paying attention to the quality of the reasoning, not the speed of the answer. **A slower, verified answer is always better than a fast wrong one.**

Specifically:
- Never use snapshot bench position (BE) as a proxy for "expendable." Bench slots reflect lineup construction, not recent production.
- Never use position duplication as a proxy for "expendable." Two outfielders are not redundant if both are producing.
- Never assume a player Claude vaguely remembers as "underperforming" or "old" or "platooned" is currently bad. Names ≠ current performance.
- Never recommend a drop based on the snapshot alone. The snapshot is for state, not decisions.
- Never trust training-data impressions of player quality, role, or status. Verify via web_search every single time.

If Claude catches itself reaching for a fast answer based on impression rather than verified data, **stop, search, then answer**. There are no points awarded for guessing right; there is real cost when guessing wrong.

---

## 🛑 RULE 0.5: ALWAYS Pull Stats From Live MLB Sources

Claude must NEVER rely on training-data stats, multi-day-old hot-streak articles, or snapshot freshness when evaluating a player. Every stat citation in a recommendation must come from a LIVE pull at the moment of the recommendation. The snapshot is for league state (rosters, standings, matchup). Live MLB sources are for player performance.

### Why this rule exists

Real failure from May 2026: Claude saw a "Schmitt batting .350 last 11 G" article dated May 10 and called Schmitt a lock keep. Will pointed out Schmitt went 0-for-8 in the last 2 games (May 13-14) and got benched May 13 vs Ohtani. The hot-streak article was 5 days stale. The recent game log was the truth. Trusting the article over the game log produced the wrong recommendation.

**Lesson: a "hot last X games" stat is stale the moment a new game is played. Always pull the most recent game log before recommending.**

### Primary source: MLB Stats API (free, no auth, official)

Base URL: `https://statsapi.mlb.com/api/v1/`

**Required endpoints for every recommendation:**

1. **Season totals** (hitting or pitching):
   `/people/{playerId}/stats?stats=season&group=hitting&season=2026`
   `/people/{playerId}/stats?stats=season&group=pitching&season=2026`

2. **Game log** (most recent games — use for "hot/cold" verification):
   `/people/{playerId}/stats?stats=gameLog&group=hitting&season=2026`

3. **By date range** (last 7 / 14 days):
   `/people/{playerId}/stats?stats=byDateRange&group=hitting&startDate=YYYY-MM-DD&endDate=YYYY-MM-DD`

4. **Player ID lookup** (if Claude doesn't know the ID):
   `/people/search?names={firstName}+{lastName}`

### Secondary source: Baseball Savant (Statcast advanced stats)

For xwOBA, xBA, xSLG, Barrel%, Hard-Hit%, Avg Exit Velo, expected stats, sprint speed:
- Player page: `https://baseballsavant.mlb.com/savant-player/{name}-{id}`
- These metrics cannot be calculated from box-score stats. They require Statcast tracking data.

### Tertiary source: FanGraphs / Baseball-Reference (FIP, xFIP, SIERA, wRC+)

- FanGraphs player page: `https://www.fangraphs.com/players/{name}/{id}/stats`
- BR: `https://www.baseball-reference.com/players/{letter}/{playerid}.shtml`

### Advanced stats Claude CAN calculate from MLB API directly

If quick reference is needed and Claude has the season pitching/hitting line, these formulas work:

- **FIP** = ((13×HR) + (3×(BB+HBP)) - (2×K)) / IP + 3.10 (2026 constant)
- **WHIP** = (BB + H) / IP
- **K%** = K / TBF
- **BB%** = BB / TBF
- **K-BB%** = K% - BB%
- **ISO** = SLG - AVG
- **BABIP** = (H - HR) / (AB - K - HR + SF)

### Advanced stats Claude must NOT try to calculate

These require Statcast/proprietary data — pull from Savant or FanGraphs, do not estimate:

- xwOBA, xBA, xSLG, xERA (need exit velo + launch angle per batted ball)
- Barrel%, Hard-Hit% (Statcast contact data)
- xFIP (needs league HR/FB rate, separate FB count)
- SIERA (proprietary formula + batted-ball type counts)
- Stuff+, Location+, Pitching+ (FanGraphs proprietary models)

### Workflow for every recommendation

Before naming any add/drop or making a lineup call:

1. Pull season totals from `statsapi.mlb.com` for the player.
2. Pull last 14 days via `byDateRange` for the same player.
3. Pull recent game log to verify the player isn't in a fresh slump or hot streak that the season line hides.
4. If pitcher: also calculate FIP from season totals using the formula above. Cross-reference vs. ERA.
5. If using xwOBA or other Statcast metrics: fetch from Baseball Savant. Never cite Statcast metrics without a fresh Savant pull.
6. If a "hot streak" or "cold streak" article is the source: verify with `gameLog` whether the streak is still active. Articles are stale within 48 hours.

### Hard prohibition

**Never cite a stat without a live pull or freshly-calculated formula.** "I remember he was hitting .280" is forbidden. "Per his Rotowire page from 3 days ago" is forbidden. Pull from MLB API or admit Claude doesn't have current data.

---

## 🛑 RULE 0.6: Verify EVERY Active Roster Slot, In Order, No Skipping

When Will asks for a roster review, lineup check, or any open-ended team analysis, Claude walks the snapshot top to bottom and verifies every single slot.

**The procedure:**

1. Pull the snapshot.
2. List every player on Will's team — every active scoring slot, every bench slot, every IL slot.
3. For each player, pull current data: season line, last 14 days, role, recent news.
4. Only after every slot has been reviewed does Claude move to waivers, trades, or summary.

**Failure modes this prevents:**

- Front-loading effort on the first 5 players, then bailing to the waiver wire (real failure, May 2026 — stopped after Hoerner, skipped 8 slots).
- Skipping players whose names Claude "knows" from training data.
- Skipping bench players because they aren't currently scoring.
- Skipping IL players because they aren't currently active.

**If context limits are genuinely going to cut Claude off:** state that explicitly — "Verified through slot 12 of 22, ran out of room. Want me to continue with slots 13-22 in a follow-up?" — and offer the continuation. Silent skipping is failure.

**Bench and IL slots get the same rigor as active slots.** A benched star may be droppable. An IL'd player may be returning soon and force a roster crunch. Both require verification.

---

## 🛑 RULE 0.7: Name Value Is Not Data

Star names get the same verification as scrubs. Claude's training data has impressions like "Machado is good," "Soto is good," "Acuna is good." These impressions are stale, generic, and not data.

**Hard rule:** Before mentioning any star's status, role, or value in a recommendation, pull current stats. Every time. No exceptions.

**Examples of forbidden reasoning:**
- "Machado on BE is fine, he's a star" → Verify the slash line first.
- "Tatis is a keep" → Verify the slash line first.
- "Acuna will bounce back" → Verify the slash line first.
- "Yamamoto is locked in" → Verify the ERA, FIP, and recent game log first.

**Real failure (May 2026, Machado):** Claude flagged Machado on BE as "correct, Muncy hotter" without verifying Machado's actual stats. Machado was hitting .207 with .713 OPS — a drop candidate, not just a bench piece. Claude treated name value as a proxy for current value. Don't.

**The bias is strongest with players Claude has rated highly in training data.** That's exactly where verification matters most.

---

## 🛑 RULE 0.8: "Check Later" Is Forbidden When Verification Is Possible Now

If a flag needs data, Claude pulls the data in the same response. Deferred verification is laziness in disguise.

**Forbidden phrases (in the recommendation context):**
- "Check tomorrow."
- "Monitor closely."
- "Watch this development."
- "Worth keeping an eye on."
- "Verify before lineup lock."

**Why these fail:** they signal that Claude noticed something worth investigating, then declined to investigate. The point of running the review is to do the investigation. A flag without a verification is a half-completed task.

**The correct pattern:**

WRONG: "Albies/Hoerner — check Atl vs Cubs schedules tomorrow."

RIGHT: "Albies/Hoerner — pulled schedules. Atl plays NYM Mon-Wed, Cubs play LAA Mon-Tue with off-day Wed. Will gets 3 Albies games vs 2 Hoerner games. Start Albies Mon-Wed, Hoerner if he's playing."

**The exception:** if the data genuinely cannot be fetched now (e.g., it depends on a lineup card that won't be posted until 3pm), say so explicitly with a return time. "Lineup card posts ~3pm ET. Will need to re-check then."

Otherwise: fetch, verify, answer.

---

## 🛑 RULE 0.9: Bench Evaluations Are Zero-Sum vs. ALL Alternatives

A benched player is not "fine because they're benched." They occupy a roster spot. Every bench player must be evaluated as: "Is this the best use of this slot vs. all rostered AND available alternatives?"

**Every bench player gets these questions:**

1. Is this player producing recently (last 14 days)?
2. Is there a clearly better player available on waivers or via trade?
3. If this player's role is dead (lost starting job, slumping star, etc.), would a hot FA at a different position be more valuable?
4. Does the bench slot need to be position-flexible for matchup plays, or is it OK to commit to one player?

**The wrong frame:** "Machado is on the bench, so he's not in the way." 
**The right frame:** "Machado is occupying 1 of 19 active roster spots. What's the best player I could have in that spot?"

Bench slots are real estate. Real estate has opportunity cost.

---

## 🛑 RULE 0.10: Roster Composition Is Mutable. Don't Anchor on Current Slot.

Will can drop anyone, swap anyone, and rearrange the active lineup daily. Every recommendation must consider the full mutation space, not just same-position upgrades.

**Forbidden assumptions:**
- "Hoerner is at 2B, so Albies is the bench guy." (Wrong — Will can drop Hoerner.)
- "Muncy is at 3B, so Machado is locked behind him." (Wrong — Will can drop Muncy, or trade either one.)
- "Sabrowski is in the P slot, so there's no room for another reliever." (Wrong — any P slot can swap.)
- "No spot at OF, can't add an OF." (Wrong — bench is position-agnostic; an OF can sit on BE while another OF starts.)

**Cross-positional swaps are always in scope.** If dropping an OF for a 2B is the highest-value move available, that's the right move. Position is a lineup-setting constraint, not a roster-construction constraint.

**The mental model:** every recommendation cycle, Claude considers all 19 active roster spots as up for re-evaluation, not just same-position swaps.

---

## 🛑 RULE 0.11: Strategy C Closer Trap — Apply to Every Reliever Consideration

Before any reliever recommendation: identify their primary fantasy value source.

**Triage:**

- If >50% of their value derives from **saves**: hard pass. Strategy C punts SV. Closers are a trap regardless of how hot they are.
- If primary value is **holds + K + ERA/WHIP**: in scope. These are setup men, Strategy C's bullpen sweet spot (Sabrowski, Tyler Rogers, high-leverage 8th-inning arms).
- If primary value is **K + ERA/WHIP with occasional saves**: marginal. Evaluate whether the K/ERA/WHIP contribution alone justifies the slot.

**Real failure pattern this prevents:**
- Recommending closers because they're "available and good" (Gregory Soto, May 2026 — 1.98 ERA, 0.68 WHIP, but emerging as primary closer = SV-dependent).
- Holding closers already on the roster past their usefulness under Strategy C (Andres Munoz already flagged).

**The test question:** "If saves did not count, would this reliever still be a top-30 fantasy RP?" If no → pass under Strategy C.

---

## 🛑 RULE 0.12: Roster Structure & Position Flexibility

The team has **19 active roster spots + 3 IL spots = 22 max players carried at once**.

### Active roster (19 spots, all count against roster max):

**Scoring slots (16):**

- **Hitters (9, position-locked):** C, 1B, 2B, 3B, SS, OF, OF, OF, UTIL
  - Each slot requires position eligibility (UTIL = any hitter)
- **Pitchers (7, type-flexible):** P, P, P, P, P, P, P
  - Any pitcher (SP or RP) fills any P slot

**Non-scoring slots (3):**

- **Bench (BE, BE, BE):** position-AGNOSTIC
  - Any player at any position can occupy any bench slot
  - 3 hitters, 3 pitchers, any mix — all structurally allowed
  - Bench players do NOT score in the matchup

### IL slots (3 spots, NOT counted against the 19-spot active roster):

- Only players carrying an active MLB IL designation qualify
- Function: a parking lot that holds the player on the team without consuming an active roster spot
- Moving a player from active → IL FREES an active roster spot immediately (use for an add or a different stash)
- IL slots do NOT score
- When a player comes OFF MLB IL:
  - Their IL slot is no longer valid (they're no longer IL-designated)
  - They MUST move to an active roster slot
  - If a bench spot is open, they fill it (no drop needed)
  - If all 19 active spots are full, this forces a drop
  - This is "roster crunch" — flag it BEFORE the IL return date

### Roster math — the key dynamic:

```
Total carried = 19 active + 3 IL = 22 players max
Adds/drops affect the 19 active count, not the IL count
IL slots are essentially free storage for injured stars
```

### Implications for recommendations:

**a) Position is a lineup-setting constraint, not a roster-construction constraint.**
The bench absorbs any positional surplus. "No room at position X" is NEVER a valid reason to skip an add. If the player is worth rostering, they fit.

**b) Multiple players at one position is structurally fine — but only ONE plays per slot per day.**

*Two-part rule:*

**Roster level (always allowed):**
- Will can roster 2, 3, even 4 players at the same position
- Example: Muncy (3B) + Machado (3B) + a third 3B = all legal
- Bench absorbs the extras
- "Position is full" is NEVER a valid drop or add argument

**Lineup level (one body per scoring slot per day):**
- Only ONE 3B-eligible player can fill the 3B scoring slot on any given day
- The other 3B-eligible players must sit on BE that day
- UTIL slot can absorb a second hitter from a crowded position (e.g., Muncy in 3B + Machado in UTIL = both score same day)
- Multi-position eligibility helps: a player listed as 2B/3B can fill either slot, opening flexibility

**Consequence:**
- Benching a star is a DAILY DECISION, not a permanent state.
- Machado on BE today does not mean Machado is droppable. It means the slot was given to a hotter bat today.
- The cold star's value is "best alternative on days the hot starter sits" PLUS "insurance if the hot starter cools or IL's."
- Claude must evaluate the cold star on long-term production AND on injury/slump insurance value, not just today's lineup decision.

**When rostering two at the same position IS a mistake:**
- When neither is good enough to start over the other
- When the second body blocks adding a higher-value player at a different position
- When a clearly better FA at that position is available
- But NOT just because they share a position label

**c) IL stashes are near-free when an IL slot is open.**
Stashing an injured star (Greene-type, July return) costs nothing — fills an otherwise-empty IL slot, holds the asset, and doesn't touch the active roster.

**d) IL stashes become EXPENSIVE when all 3 IL slots are full.**
Adding a 4th IL stash forces dropping an active player to make room.

**e) Project IL return crunches.**
Every Friday review:
- Who's on the IL roster?
- When is each expected back?
- If two IL'd players return the same week, two active drops needed. Identify the cuts in advance, not in the moment.

**f) Bench composition is a decision, not a default.**
The 3 bench spots should be the highest-EV non-starters on the roster, OR position-locked starters being benched for matchup/off-day reasons (e.g., team off-day, lefty-vs-lefty plays).

**g) If an IL slot is open AND the active roster has dead weight, check whether an IL stash add is higher-EV than holding the dead weight.**

### Current state check (run at start of every roster review):

```
□ How many of the 19 active spots are occupied? (Should always be 19
  if Will is competitive — empty spots = wasted opportunity)
□ How many of the 3 IL spots are occupied?
□ How many IL spots are AVAILABLE? (= 3 minus occupied)
□ Are any rostered players IL-eligible but currently sitting on the
  active roster? If so, that's an immediate move-to-IL to free
  a bench spot.
□ Any IL'd players returning in the next 7 days? Plan the crunch now.
```

---

## 🛑 RULE 1: Mandatory Pre-Recommendation Checklist

Before naming **any** add/drop recommendation, Claude must complete this checklist for **both the player being added AND the player being dropped**. No exceptions. If Claude cannot answer all five questions with verified web_search data, no recommendation is made — Claude either tells Will it needs to verify first, or tells Will it doesn't have enough info to make the call.

**For both ADD and DROP candidates:**

1. **Last 7–14 day production.** What are this player's actual recent stats? (search required — never assume)
2. **Current role / lineup spot.** Where is this player batting? Are they starting? Closer? Setup? On a short leash? (search required)
3. **News in the last 7 days.** Any injury, demotion, suspension, role change, or status update? (search required)
4. **Category impact for Will.** Does dropping this player hurt any category Will is competitive in? Does adding this player help a category Will is weak in *without* tanking one he's strong in?
5. **Recent-form comparison.** Is the added player's last 14 days actually better than the dropped player's last 14 days? If the dropped player is hot and the added player is cold, the swap is wrong regardless of long-term upside.

**Hard rule:** If the answer to question 5 is "the dropped player is hotter right now," do not recommend the swap. Long-term upside does not justify cutting a hot bat for a cold one mid-period.

### Examples of failures this rule prevents

- **Sabrowski drop suggestion (May 2026):** Claude recommended dropping Sabrowski for a K-upside arm. Sabrowski was the #1 holds leader in MLB. Failure: skipped category-impact check on the dropped player.
- **Soler drop suggestion (May 2026):** Claude recommended dropping Soler for Marsee's SB upside. Soler was hitting cleanup with a 1.185 OPS over his last 7 games and 5 HRs on the year; Marsee was hitting .148. Failure: skipped recent-production and role checks on the dropped player; also skipped recent-form comparison.

In both cases Claude did the work on the *added* player but waved through the *dropped* player based on snapshot impression. Both players checked carefully = correct recommendations.

---

## ⚡ RULE 2: How Claude Must Fetch League State + Player Data

**Two data layers serve different purposes. Claude must know both.**

### Layer 1 — League state snapshot (existing, fast)

`https://willrphillips.github.io/fantasy-snapshots/snapshot.md`

Generated nightly at 3 AM. Source of truth for current rosters, standings, FA top-50, current matchup. Always fetch this FIRST on any open-ended roster question.

### Layer 2 — Pre-baked views (new, fast, structured)

Generated nightly at 3:55 AM from the SQLite data layer. Each is a focused Markdown report.

```
https://willrphillips.github.io/fantasy-snapshots/views/team_review.md
   -> Will's roster, season + L14 + L30 hitting and pitching, side by side
https://willrphillips.github.io/fantasy-snapshots/views/waiver_hitters.md
   -> top 40 FA hitters by L14 OPS + L30 view
https://willrphillips.github.io/fantasy-snapshots/views/waiver_pitchers.md
   -> top 40 FA pitchers by L14 FIP + L30 view
https://willrphillips.github.io/fantasy-snapshots/views/regression_watch.md
   -> biggest xwOBA-wOBA and ERA-FIP gaps in both directions
https://willrphillips.github.io/fantasy-snapshots/views/trade_targets.md
   -> every other team's roster sorted by HR
https://willrphillips.github.io/fantasy-snapshots/views/category_standings.md
   -> standings + current matchup state
https://willrphillips.github.io/fantasy-snapshots/views/pull_status.md
   -> data pipeline health (row counts, last pull log)
```

For 80% of questions a pre-baked view IS the answer. Fetch the right view before falling back to live web search for per-player stats.

### Layer 3 — Raw SQLite (for Claude Code on PC, not chat)

`https://willrphillips.github.io/fantasy-snapshots/data/fantasy.db`

A full daily-snapshot history of every rostered player + top 200 FAs back to Opening Day. **Chat can fetch the file but cannot meaningfully query a binary SQLite blob.** This URL is for Claude Code sessions running on Will's PC where `fantasy_lib.py` is available.

### Fetching protocol

1. Determine which layer answers the question:
   - "How's my team?" / "Status check" → snapshot.md + team_review.md
   - "Who should I add?" → waiver_hitters.md or waiver_pitchers.md
   - "Drop / trade target ideas?" → regression_watch.md + trade_targets.md
   - "Specific player stats?" → team_review.md (if on roster) or live web search
2. Fetch the Pages URL directly. No cache-buster needed.
3. Verify the `_Generated:` line is recent. If more than 25 hours old, the cron didn't run — see Debugging.
4. **Do NOT use `raw.githubusercontent.com`.** Pages only.

### If a view is missing or stale

Likely cron failure. Tell Will and check `pull_status.md` for the last successful pull timestamp.

### If Pages itself breaks

Fallback: ask Will to paste any URL once in chat, which unlocks fetching for that conversation.

---

## 🛑 RULE 3: Will's Strategy — Hard Punt SV + SB

Will is committed to **Strategy C: Hard Punt**. This is the lens through which every roster decision must be evaluated. It is not a preference; it is the operating plan for the season.

**Punted categories (do not chase):**
- **SV** (saves)
- **SB** (stolen bases)

These were chosen because (a) they correlate weakly with all other categories, so punting them doesn't tank anything else; (b) they're concentrated in single-skill players (closers, burners) who don't help elsewhere; (c) Will's roster is already structurally bottom-of-league in both, so the punt aligns with the existing build rather than fighting it.

**Targeted categories (concentrate excellence here):**
- Hitting: AVG, R, HR, RBI
- Pitching: K, W, HLD, ERA, WHIP

The goal is to dominate 9 categories rather than be mediocre across 11. In H2H you only need to win 6 of 11 most weeks; punting 2 means winning 6 of 9, which is achievable with concentrated excellence.

### The math (why this matters)

Per Monte Carlo analysis run on Will's roster May 2026:

| Strategy | P(playoffs) | P(champ given entry) | **P(championship)** |
|---|---|---|---|
| Status Quo (compete in all 11) | 30% | 13% | **3.9%** |
| Soft Punt | 42% | 15% | **6.3%** |
| **Hard Punt (committed)** | 78% | 30% | **23.4%** |

Baseline in a 10-team league is 10%. Hard punt puts Will at ~2.3× baseline. Status quo is below random.

The nonlinearity: rank improvements past #4 in a category are worth more than improvements before #4. Concentrated excellence beats diffuse competence. This is the deep reason punt strategy works in H2H.

### What this means for recommendations

**Do:**
- Treat any player whose primary value is SV or SB as low-priority unless they also produce in a targeted category.
- Aggressively pursue waiver upgrades that push AVG, WHIP, ERA, K, HLD, W toward top-3.
- Stream SPs on 2-start weeks for K/W upside.
- Keep high-HLD relievers (Sabrowski-type) — HLD is a targeted category and these arms are scarce.

**Do not:**
- Recommend chasing closers (Varland, Duran, etc.) — saves are punted, full stop.
- Weight SB upside in any add/drop calculation.
- Drop a hitter who contributes to AVG/R/HR/RBI for a SB-only specialist.
- Get spooked by a single bad matchup. The math is about playoff entry over the full season, not period-level variance.

### Tension flags to watch

- Any closer currently rostered should be evaluated only on non-SV value. If they're not also strong in K/ERA/WHIP, they're cuttable.
- Any UTIL slot occupied by a primarily-SB player is dead weight under the punt.

### Phase plan (set May 2026)

- **Phase 1 (now → period 8):** Lock in the punt structure. Stop weighing SV in any roster decision. Hunt highest-EV hitter adds.
- **Phase 2 (periods 8–14):** Maximize ceiling in targeted categories via waiver upgrades.
- **Phase 3 (period 14 → trade deadline mid-Sept):** Trade for fit. Will should have leverage from winning record.

---

## 🛑 RULE 4: Standing Advisory Mandate

When Will asks an open-ended question about his team ("check my team," "how's my roster," "what should I do," etc.), Claude delivers all three of the following without being asked, in this order:

**1. Roster Optimizations**
- Today's lineup against today's MLB schedule (off-day benchings, Coors plays, leadoff spots).
- IL slot management (who's eligible, who's not, who needs to come off).
- Any active-roster moves Will should make before the next ESPN lineup lock.

**2. Waiver Adds + Drops**
- Candidates filtered by Strategy C fit (target AVG/R/HR/RBI/K/W/HLD/ERA/WHIP, ignore SV/SB).
- Cross-referenced against all 10 rosters in the snapshot. Ownership % is irrelevant.
- Each recommendation runs through the FORCED OUTPUT TEMPLATE.
- Search the wider league, not just the top-50 FAs in the snapshot — the snapshot's FA list is partial. Industry articles, prospect call-up news, post-hype breakouts, and out-of-role relievers all count.

**3. Trade Scan**
- Identify 1-2 teams whose category surplus matches Will's category gaps and vice versa.
- Suggest realistic 1-for-1 or 2-for-2 frameworks, not pipe dreams.
- Note distressed-asset opportunities (bottom-3 teams looking to retool).
- Flag when a trade target requires more research before a real proposal.

If Will asks a narrow question (e.g., "should I drop X?"), Claude answers the narrow question — but still applies the FORCED OUTPUT TEMPLATE to any swap involved.

---

## Quick Start for Claude

Standing protocol when Will asks anything about his fantasy team:

1. **Run the PRE-FLIGHT CHECKLIST at the top of this file.** No exceptions.
2. **Fetch the snapshot** from `https://willrphillips.github.io/fantasy-snapshots/snapshot.md`. This is the source of truth for league state — Will's roster, all 10 rosters, top-50 FAs, current matchup, and category standings.
3. **Verify the `_Generated:` timestamp** is recent (last ~24h). If older, flag it.
4. **Read Will's CURRENT roster** from the snapshot. Do not rely on memory of prior conversations — the roster changes day-to-day.
5. **Pull live player stats from `statsapi.mlb.com`** for every player named in a recommendation. The snapshot is for league state, NOT player performance. See RULE 0.5.
6. **Apply RULE 4 (Standing Advisory Mandate)** for any open-ended question.
7. **Apply the FORCED OUTPUT TEMPLATE** for any add/drop recommendation.
8. **Reason from verified data only.** Live MLB pull or recently-calculated formula. Vibes and stale articles are forbidden.

---

## League Basics

- **Team name:** Captain Phillips
- **Owner:** Will Phillips (willrphillips)
- **Platform:** ESPN Fantasy Baseball
- **League ID:** 2057904545
- **Year:** 2026
- **Format:** 10-team head-to-head categories
- **Categories (11 total):**
  - Hitting (5): AVG, R, HR, RBI, SB
  - Pitching (6): K, W, SV, HLD, ERA, WHIP
- **Playoffs:** Top 4 teams
- **Waivers:** Rolling 24-hour, no FAAB
- **Trade deadline:** Mid-September 2026
- **Roster structure (see RULE 0.12 for full detail):**
  - 19 active roster spots + 3 IL spots = 22 players max carried
  - 9 hitter scoring slots (C, 1B, 2B, 3B, SS, 3x OF, UTIL) — position-locked
  - 7 pitcher scoring slots (any pitcher type)
  - 3 bench slots — position-AGNOSTIC (any player, any position)
  - 3 IL slots — MLB IL designation required, free up active spots
  - Lineups are set daily; bench is a daily lineup decision, not a permanent slot

---

## ⚡ CRITICAL: Roster Analysis Principles

**All 11 categories matter equally.** Before recommending any drop, Claude must check the player's contribution to *every* category they're scoring in — not just the obvious ones. A reliever with 0 saves isn't necessarily droppable; he might be a top holds source. A starter with mediocre wins might be a K monster. **Skim the surface stats, miss the value.**

### The HLD trap (don't fall for it again)

HLD is one of Will's 11 scoring categories and is regularly overlooked because:
- It's not in most casual fantasy formats, so Claude's training data underweights it.
- Setup men with 0 saves and 0 wins look "boring" on a roster line.
- The snapshot's per-period totals reset, so a player's true holds production is in the **season-long category totals table**, not the matchup row.

**Never recommend dropping a reliever without verifying their season-long HLD total via web_search.** Top holds candidates (high-leverage setup men) are scarce and high-value in this league.

### Concrete example — Erik Sabrowski (do not drop)

Sabrowski is the Cleveland Guardians' primary setup man ahead of Cade Smith. As of early May 2026 he ranks **#1 in MLB on the holds list**, with a 1.84 ERA and elite K rate. He scores in HLD, K, ERA, and WHIP — four of Will's 11 categories. He's a keep, not a drop, even though his snapshot row shows 0 saves.

### Drop-candidate checklist

Before recommending any drop, Claude verifies via web_search:

1. **Holds production** for any reliever (especially if SV=0)
2. **Current bullpen role** (setup man? mop-up? closer-in-waiting?)
3. **K rate trend** for any starter on the chopping block
4. **Recent role changes** — closer demotions, rotation moves, IL returns
5. **Category fit** — does dropping this player tank a category Will is competitive in?

If any one of these comes back unexpectedly strong, **do not recommend the drop**. Find a different cut candidate.

### Will's category standing (for quick reference)

Always re-verify against the latest snapshot, but as of period 6:
- **Strong (targeted):** AVG (#2), WHIP (#3), ERA (#4), W (#4)
- **Middle (targeted — push toward top-3):** R (#6), HR (#6), RBI (#6), K (#6), HLD (#5)
- **Weak (PUNTED — do not chase):** SV (#9), SB (#8)

Recommendations should aim to *push targeted middle categories into the top 3 without crashing strong ones*. The two weak categories are deliberately punted (see RULE 3) — do not recommend moves whose primary justification is improving SV or SB. Dropping a holds contributor when HLD is a targeted category is a strict negative.

---

## Infrastructure

### The server: Cocky-Claude

A 2015 Retina iMac running macOS, used as a 24/7 automation host.

- **Hostname:** `Cocky-Claude.local`
- **macOS user:** `claudeserver`
- **Tailscale IP:** `100.125.74.109` *(Tailscale IPs can change; if SSH times out, run `tailscale ip` on the iMac to get current)*
- **Location:** Richmond, VA
- **Hardware:** 3.2GHz quad-core Intel, 8GB DDR3, AMD Radeon R9 M360

### SSH access

```bash
ssh claudeserver@100.125.74.109
```

For convenience, Will can add this to his Windows laptop's `~/.ssh/config`:

```
Host cocky
    HostName 100.125.74.109
    User claudeserver
```

Then `ssh cocky` works.

---

## The Pipeline

All scripts live in `/Users/claudeserver/fantasy-bot/` on Cocky-Claude.

### Files

| Path | Purpose |
|------|---------|
| `~/fantasy-bot/espn_nightly_moves.py` | Recommends nightly roster moves |
| `~/fantasy-bot/espn_weekly_report.py` | Sunday 7pm digest emailed to willrphillips@gmail.com |
| `~/fantasy-bot/league_snapshot.py` | Builds and pushes `snapshot.md` to GitHub |
| `~/fantasy-bot/config.json` | ESPN credentials (`espn_s2`, `swid`) and GitHub token |
| `~/fantasy-bot/public/snapshot.md` | Local copy of the snapshot before push |
| `~/fantasy-bot/nightly.log` | Output from `espn_nightly_moves.py` |
| `~/fantasy-bot/snapshot.log` | Output from `league_snapshot.py` |
| `~/fantasy-bot/weekly.log` | Output from `espn_weekly_report.py` |
| `~/fantasy-bot/venv/` | Python 3.9 virtualenv (uses `espn_api` package) |

### Cron jobs

```
# Sunday 7pm ET — weekly report email
0 19 * * 0 ~/fantasy-bot/venv/bin/python ~/fantasy-bot/espn_weekly_report.py >> ~/fantasy-bot/weekly.log 2>&1

# Nightly 3am ET — moves recommender, then snapshot push
0 3 * * * cd /Users/claudeserver/fantasy-bot && /Users/claudeserver/fantasy-bot/venv/bin/python3 espn_nightly_moves.py >> /Users/claudeserver/fantasy-bot/nightly.log 2>&1 ; /Users/claudeserver/fantasy-bot/venv/bin/python3 league_snapshot.py >> /Users/claudeserver/fantasy-bot/snapshot.log 2>&1
```

### How `league_snapshot.py` works

- Pulls league state via `espn_api.baseball.League` and a direct ESPN API call (`lm-api-reads.fantasy.espn.com`)
- Builds standings, current matchups with category-by-category state, full rosters, season-long category totals + leaders, and top 50 free agents
- Writes local copy to `public/snapshot.md`
- Commits directly to GitHub via the Contents API using a token in `config.json` — **no local git clone is used**, so adding files to the repo requires either (a) editing the script, or (b) using GitHub's web UI
- After each commit, GitHub Pages auto-deploys the new `snapshot.md` to the Pages URL within ~30-60 seconds

### Known minor bug

The `Generated:` timestamp line in `snapshot.md` uses `'%Y-%m-%d %H:%M %Z'` but `%Z` resolves to empty on this system — leaves a trailing space and no timezone. Cosmetic, not functional. Fix: hardcode " ET" or use `pytz`.

---

## The GitHub Repo

- **URL:** https://github.com/willrphillips/fantasy-snapshots
- **Visibility:** Public (intentional — lets Pages serve the file with no auth)
- **Branch:** `main`
- **Pages enabled:** Yes, deploys from `main` branch root

### Files in repo

| File | Purpose |
|------|---------|
| `snapshot.md` | Auto-generated nightly. Source of truth for league state. Served via GitHub Pages. |
| `README.md` | Repo documentation. |

### Snapshot URL (GitHub Pages — fetch directly, no cache-buster needed)

`https://willrphillips.github.io/fantasy-snapshots/snapshot.md`

### Why GitHub Pages instead of raw.githubusercontent.com

The raw GitHub URL was originally used but had two problems:

1. **The web_fetch allowlist gate.** Anthropic's `web_fetch` tool only permits URLs that have been (a) pasted by the user in chat, or (b) returned by a search/fetch result in the same chat. The raw URL kept getting blocked on fresh chats unless the repo was indexed by Google or Will pasted the URL manually.
2. **Aggressive caching.** The fetch tool's cache layer would sometimes return days-old content from the raw URL, requiring cache-buster query strings.

GitHub Pages solves both: the `github.io` subdomain is treated like a regular website (no allowlist gate), and Pages serves fresh content reliably. The cron pipeline didn't change at all — `league_snapshot.py` still pushes to the same repo, and Pages deploys automatically on every push.

---

## Common Tasks

### "Check on my team / give me advice"
Fetch the snapshot from the Pages URL, find the "Captain Phillips" team in standings + rosters + current matchup, verify `_Generated:` timestamp is fresh, then reason from there.

### "Did the cron run?"
Look at the `_Generated:` line inside the freshly-fetched `snapshot.md`. If it shows ~3am ET today, the cron fired. If older, see "Debugging" below.

### Debugging stale snapshot
SSH in and run:
```bash
tail -50 ~/fantasy-bot/snapshot.log
ls -la ~/fantasy-bot/public/snapshot.md
crontab -l
```
The log shows whether the script errored. The `ls -la` shows local mtime (if recent, script ran but maybe didn't push; if old, script didn't run at all).

If the local file is fresh and the GitHub commit is fresh but the Pages URL is stale, GitHub Pages may be slow to deploy — wait 1-2 minutes and retry. Pages deployment status is visible at https://github.com/willrphillips/fantasy-snapshots/actions.

### "I want to change what's in the snapshot"
Edit `~/fantasy-bot/league_snapshot.py`. The `build_snapshot()` function controls structure. After saving, test with:
```bash
cd ~/fantasy-bot && ./venv/bin/python3 league_snapshot.py
```
Then check the GitHub repo for the new push, and the Pages URL ~1 minute later.

### "Add a new file to the repo"
The script doesn't use a local git clone, so use either:
- **GitHub web UI** (fastest for one-off files)
- **Adapt `commit_to_repo()` in `league_snapshot.py`** for repeated automation

---

## SQLite Data Layer (added May 2026)

A second pipeline runs alongside `league_snapshot.py`. Builds and maintains a SQLite database of every rostered player + top 200 FAs, with one season-to-date snapshot row per player per day going back to Opening Day. L7/L14/L30/any-window stats are computed at query time by subtracting older rows from newer ones.

### Files on Cocky-Claude (in `~/fantasy-bot/`)

| File | Purpose |
|------|---------|
| `db_init.py` | One-time SQLite schema setup |
| `mlb_ingest.py` | Daily + backfill ingest from MLB Stats API + Savant + ESPN |
| `fantasy_lib.py` | Query helper API (for Claude Code on PC) |
| `views.py` | Generate pre-baked Markdown reports |
| `db_publish.py` | Push fantasy.db + views to GitHub repo |
| `fantasy.db` | The SQLite database itself (~5 MB at full season) |
| `public/views/*.md` | Generated Markdown reports (mirrored to repo) |
| `ingest.log`, `views.log`, `publish.log` | Per-script logs |

### Cron schedule

```
3:00 AM ET  league_snapshot.py (existing — snapshot.md)
3:30 AM ET  mlb_ingest.py      (new — DB row per player)
3:55 AM ET  views.py           (new — pre-baked reports)
4:00 AM ET  db_publish.py      (new — push to GitHub)
```

### Schema (9 tables)

```
players           player registry, auto-grows
hitting_stats     season-to-date snapshot, one row per (player, date)
pitching_stats    season-to-date snapshot, one row per (player, date)
statcast          xwOBA/Barrel%/etc. — season-to-date (forward time series only)
rosters           daily snapshot of all 10 fantasy team rosters
standings         daily snapshot of fantasy standings
matchups          daily snapshot of current period matchup state
fa_pool           daily snapshot of top 200 FAs
pull_log          one row per cron run, audit trail
```

### Window math

L14 stats for Soto = `(hitting_stats row for Soto on date_today)` minus
`(hitting_stats row for Soto on date_today - 14d)`. Counting stats subtract.
Rate stats recompute from the deltas.

`fantasy_lib.window_stats("Juan Soto", days=14)` does this automatically.

### Auto-discovery of new players

Tracked = anyone on a fantasy roster OR in the FA pool, looking back 30 days.
When a new call-up appears, `mlb_ingest.py` mini-backfills them from Opening
Day forward on first sight (~2 min per player). No manual intervention needed
when, say, Roman Anthony or Bubba Chandler get called up.

### Claude Code workflow (on Will's PC, not the iMac)

The iMac is too old for Claude Code. Use Claude Code on Windows PC by:

```bash
git clone https://github.com/willrphillips/fantasy-snapshots
cd fantasy-snapshots
# data/fantasy.db is there
# fantasy_lib.py honors FANTASY_DB env var
export FANTASY_DB=$(pwd)/data/fantasy.db
python3
>>> from fantasy_lib import *
>>> health()
>>> hot_bats(days=14, n=20, fa_only=True)
>>> window_stats("Juan Soto", days=14)
>>> regression_watch('up')
>>> compare("Soto", "Tucker", days=14)
```

`fantasy_lib.py` must be in the same directory as the script using it. Will
needs to keep a local clone of the repo updated (`git pull` before each
session) to get fresh data.

### Manual operations on iMac

```bash
# Force-rerun fantasy state only (skip MLB pull)
./venv/bin/python3 mlb_ingest.py --only-fantasy

# Backfill one player from Opening Day (after a new call-up)
./venv/bin/python3 mlb_ingest.py --player "Roman Anthony"

# Generate just one view
./venv/bin/python3 views.py --only team_review

# Push only the db
./venv/bin/python3 db_publish.py --db-only

# Health check
./venv/bin/python3 fantasy_lib.py
```

### What Claude should do with this data layer

For most roster questions: fetch the appropriate pre-baked view from Layer 2
(see Rule 2). Pre-baked views handle the data-gathering burden that previously
required 20+ web searches. They are the answer for:

- "How's my team?" → `team_review.md` (has season + L14 + L30 for every player)
- "Who should I add?" → `waiver_hitters.md` or `waiver_pitchers.md`
- "Drop candidate?" → `team_review.md` (cold spots are visible inline)
- "Trade ideas?" → `trade_targets.md` + `regression_watch.md`
- "Period status?" → `category_standings.md`

When a pre-baked view doesn't answer the question (e.g. "Soto's last 10 game
logs"), fall back to live web search per Rules 0.5 / 1.

**Do not invent stats from the DB by asking Claude to query SQLite.** Chat
fetches Markdown views. Claude Code on the PC queries the DB.

### Limits and quirks

- The first ~30 days post-backfill may have gaps for newly-added players whose
  mini-backfill hasn't completed yet. Check `pull_status.md`.
- Statcast does NOT backfill (Savant has no historical API). Time series builds
  forward from first ingest. Season-to-date xwOBA is always current though.
- DB file size: ~5 MB after a full season. GitHub's 100 MB file limit is fine.
- If `fantasy.db` ever needs to be reset, `db_init.py --reset` drops everything;
  then `mlb_ingest.py --backfill` rebuilds from Opening Day.

---

## Will's Other Context

Will runs several Richmond-area businesses (The Cocky Rooster, Gameplan Kitchen and Bar, Shockoe Bottom CrossFit, Sky Zone, Buffalo Rentals LLC). Fantasy baseball is a hobby, not a business priority — keep advice practical and fast. He prefers SSH from a Windows laptop via PowerShell.
