# Agent feedback (from the Codex dashboard)

Open items are asks from Will about an agent's .md — apply in a session here, then tick the box (or mark done on the dashboard).

- [x] 2026-07-22 **fantasy chat persona** — I've set a lot of rules for this agent in the past as to what my strategy is for fantasy baseball. Can you fold in all those rules? Is this what Edwin does? <!-- fb:mrwc1qww -->
  - Applied 2026-07-27: the strategy rules lived in `fantasy_baseball_instructions.md` (Strategy C hard-punt SV+SB, RULES 0–4, forced-output template, HLD/closer traps, pre-flight checklist, phase plan). `CHAT_PROJECT_INSTRUCTIONS.md` (the claude.ai project Instructions field) had none of them — pure data-plumbing. Folded the full strategy + behavioral ruleset into it so the persona enforces the strategy on its own. Also surfaced `fantasy_baseball_instructions.md` as its own agent ("Fantasy baseball rules") in the dashboard roster. (Not what Edwin does — Edwin is the separate Discord butler persona.)
