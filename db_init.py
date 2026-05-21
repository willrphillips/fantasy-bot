#!/usr/bin/env python3
"""
db_init.py — One-time SQLite schema setup for the fantasy-bot data layer.

Design:
    Every row in hitting_stats/pitching_stats is a SEASON-TO-DATE snapshot
    as of `date_pulled`. Windows (L7/L14/L30/etc.) are computed at query
    time by subtracting an older snapshot from a newer one.

    Backfill (one-time) populates daily snapshots from Opening Day to today.
    Nightly cron adds one row per tracked player thereafter.

Usage:
    python3 db_init.py             # create db if missing
    python3 db_init.py --reset     # DROP all tables and recreate (DESTRUCTIVE)
"""
import argparse
import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.path.expanduser("~/fantasy-bot/fantasy.db"))

SCHEMA = """
-- MLB player registry. Auto-grows as new players appear on rosters/FAs.
CREATE TABLE IF NOT EXISTS players (
    mlb_id          INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    team            TEXT,
    primary_pos     TEXT,
    bats            TEXT,
    throws          TEXT,
    birth_date      TEXT,
    first_tracked   TEXT NOT NULL,    -- date_pulled of first appearance
    last_tracked    TEXT NOT NULL,
    source          TEXT              -- 'roster' or 'fa' or 'manual'
);
CREATE INDEX IF NOT EXISTS idx_players_name ON players(name);
CREATE INDEX IF NOT EXISTS idx_players_team ON players(team);

-- Season-to-date snapshot, one row per (player, date_pulled).
-- To compute L14: pick today's row and the row from 14 days ago; subtract.
CREATE TABLE IF NOT EXISTS hitting_stats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    mlb_id          INTEGER NOT NULL,
    date_pulled     TEXT NOT NULL,           -- ISO date, season-to-date as of EOD
    games           INTEGER,
    pa              INTEGER,
    ab              INTEGER,
    h               INTEGER,
    doubles         INTEGER,
    triples         INTEGER,
    hr              INTEGER,
    r               INTEGER,
    rbi             INTEGER,
    bb              INTEGER,
    so              INTEGER,
    sb              INTEGER,
    cs              INTEGER,
    hbp             INTEGER,
    sf              INTEGER,
    avg             REAL,
    obp             REAL,
    slg             REAL,
    ops             REAL,
    UNIQUE(mlb_id, date_pulled),
    FOREIGN KEY(mlb_id) REFERENCES players(mlb_id)
);
CREATE INDEX IF NOT EXISTS idx_hit_player_date ON hitting_stats(mlb_id, date_pulled);
CREATE INDEX IF NOT EXISTS idx_hit_date ON hitting_stats(date_pulled);

CREATE TABLE IF NOT EXISTS pitching_stats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    mlb_id          INTEGER NOT NULL,
    date_pulled     TEXT NOT NULL,
    games           INTEGER,
    gs              INTEGER,
    ip              REAL,
    tbf             INTEGER,
    h               INTEGER,
    er              INTEGER,
    bb              INTEGER,
    so              INTEGER,
    hr              INTEGER,
    hbp             INTEGER,
    w               INTEGER,
    l               INTEGER,
    sv              INTEGER,
    hld             INTEGER,
    bs              INTEGER,
    era             REAL,
    whip            REAL,
    k9              REAL,
    bb9             REAL,
    k_pct           REAL,
    bb_pct          REAL,
    fip             REAL,                    -- computed from raw counts
    UNIQUE(mlb_id, date_pulled),
    FOREIGN KEY(mlb_id) REFERENCES players(mlb_id)
);
CREATE INDEX IF NOT EXISTS idx_pit_player_date ON pitching_stats(mlb_id, date_pulled);
CREATE INDEX IF NOT EXISTS idx_pit_date ON pitching_stats(date_pulled);

-- Statcast is season-to-date snapshots only (Savant doesn't expose historical).
-- Time series here builds forward from first ingest, NOT backward.
CREATE TABLE IF NOT EXISTS statcast (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    mlb_id          INTEGER NOT NULL,
    date_pulled     TEXT NOT NULL,
    side            TEXT NOT NULL,           -- 'bat' or 'pit'
    avg_ev          REAL,
    max_ev          REAL,
    hard_hit_pct    REAL,
    barrel_pct      REAL,
    woba            REAL,
    xwoba           REAL,
    xba             REAL,
    xslg            REAL,
    xera            REAL,
    whiff_pct       REAL,
    k_pct           REAL,
    bb_pct          REAL,
    UNIQUE(mlb_id, date_pulled, side),
    FOREIGN KEY(mlb_id) REFERENCES players(mlb_id)
);
CREATE INDEX IF NOT EXISTS idx_statcast_player_date ON statcast(mlb_id, date_pulled);

-- ESPN playerId <-> MLBAM id crosswalk. ESPN's playerId is NOT the MLB
-- Stats API id; this table caches the name+team resolution so we don't
-- re-query the MLB roster index for every player every night.
-- mlb_id NULL = unresolved (ambiguous/no match) -> player is skipped and
-- surfaced by health_check rather than silently mis-mapped.
CREATE TABLE IF NOT EXISTS id_map (
    espn_id         INTEGER PRIMARY KEY,
    mlb_id          INTEGER,
    name            TEXT,
    pro_team        TEXT,
    resolved_ts     TEXT,
    miss_count      INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_id_map_mlb ON id_map(mlb_id);

-- Fantasy league state
CREATE TABLE IF NOT EXISTS rosters (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date_pulled     TEXT NOT NULL,
    team_name       TEXT NOT NULL,
    team_id         INTEGER,
    espn_id         INTEGER,
    mlb_id          INTEGER,
    player_name     TEXT NOT NULL,
    slot            TEXT,
    eligible_pos    TEXT,
    status          TEXT,
    UNIQUE(date_pulled, team_name, player_name)
);
CREATE INDEX IF NOT EXISTS idx_rosters_date ON rosters(date_pulled);
CREATE INDEX IF NOT EXISTS idx_rosters_team ON rosters(team_name);

CREATE TABLE IF NOT EXISTS standings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date_pulled     TEXT NOT NULL,
    team_name       TEXT NOT NULL,
    rank            INTEGER,
    wins            INTEGER,
    losses          INTEGER,
    ties            INTEGER,
    pct             REAL,
    UNIQUE(date_pulled, team_name)
);
CREATE INDEX IF NOT EXISTS idx_standings_date ON standings(date_pulled);

CREATE TABLE IF NOT EXISTS matchups (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date_pulled     TEXT NOT NULL,
    period          INTEGER,
    home_team       TEXT,
    away_team       TEXT,
    cat             TEXT,
    home_value      REAL,
    away_value      REAL,
    leader          TEXT,
    UNIQUE(date_pulled, period, home_team, away_team, cat)
);
CREATE INDEX IF NOT EXISTS idx_matchups_date ON matchups(date_pulled);

CREATE TABLE IF NOT EXISTS fa_pool (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date_pulled     TEXT NOT NULL,
    espn_id         INTEGER,
    mlb_id          INTEGER,
    player_name     TEXT NOT NULL,
    eligible_pos    TEXT,
    team            TEXT,
    owned_pct       REAL,
    UNIQUE(date_pulled, player_name)
);
CREATE INDEX IF NOT EXISTS idx_fa_date ON fa_pool(date_pulled);

-- Audit log: one row per cron run
CREATE TABLE IF NOT EXISTS pull_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date_pulled     TEXT NOT NULL,
    mode            TEXT,                    -- 'backfill' or 'nightly' or 'manual'
    start_ts        TEXT,
    end_ts          TEXT,
    duration_sec    REAL,
    players_tracked INTEGER,
    new_players     INTEGER,
    hit_rows        INTEGER,
    pit_rows        INTEGER,
    statcast_rows   INTEGER,
    roster_rows     INTEGER,
    standings_rows  INTEGER,
    matchup_rows    INTEGER,
    fa_rows         INTEGER,
    errors          INTEGER,
    notes           TEXT
);
"""

DROP_ALL = """
DROP TABLE IF EXISTS hitting_stats;
DROP TABLE IF EXISTS pitching_stats;
DROP TABLE IF EXISTS statcast;
DROP TABLE IF EXISTS rosters;
DROP TABLE IF EXISTS standings;
DROP TABLE IF EXISTS matchups;
DROP TABLE IF EXISTS fa_pool;
DROP TABLE IF EXISTS pull_log;
DROP TABLE IF EXISTS id_map;
DROP TABLE IF EXISTS players;
"""


def init(reset: bool = False):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    if reset:
        confirm = input(f"DESTRUCTIVE: drop all tables in {DB_PATH}? Type 'yes': ")
        if confirm.strip().lower() != "yes":
            print("Aborted.")
            return
        cur.executescript(DROP_ALL)
    cur.executescript(SCHEMA)
    conn.commit()
    conn.close()
    print(f"OK: schema initialized at {DB_PATH}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--reset", action="store_true", help="drop all tables first")
    args = p.parse_args()
    init(reset=args.reset)
