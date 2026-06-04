# Suggested agents

Proposed on 2026-05-24. The nightly pipeline (mlb_ingest → views → db_publish → health_check) runs cleanly. The agent below sits *after* it to make the output useful at a glance.

## anomaly-detector

**Purpose.** Produce a nightly digest of unusual stat lines, compared to season-to-date baselines.

**Triggers.** After nightly ingest completes (chain off health_check or its own cron). Read-only.

**Reads.**
- The db views the pipeline publishes
- Prior-night digest (so it doesn't repeat itself)
- Season-to-date baselines per player

**Outputs.** Top-N anomalies as a short markdown digest. Examples:
- "Player X went 4-for-4 with 2 HR — season slash is .180/.250/.300."
- "Pitcher Y allowed 0 ER over 8 IP — career-best in 47 starts."
- "Team Z scored 14 runs — their season high was 9."

**Why valuable.** The pipeline already produces correct data. What it doesn't produce is *interesting* data. An anomaly digest turns a daily data drop into something worth reading without writing new pipeline code.

**Why narrow.** Read-only digest. Does not touch the pipeline. Does not write to the database. Does not change views. It reads, ranks, and writes one file (today's digest).

**Reference file when built.** `.claude/agents/anomaly-detector.md`
