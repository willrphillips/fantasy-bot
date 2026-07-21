#!/usr/bin/env python3
"""Portable ESPN execution module — the piece Edwin runs on its own box.

This is deliberately SELF-CONTAINED. It does not import espn_utils (which
hardcodes the baseball league/team and drags in smtplib), does not read
fantasy.db, and does not assume the iMac. Copy this one file plus a config
JSON anywhere that has `requests` + `espn-api` and it works.

Division of labour:
  * the iMac produces DATA (nightly ingest -> fantasy.db + views -> published)
  * Edwin READS that published data, proposes a move, and — on the user's yes —
    calls into this module to EXECUTE the ESPN transaction on its own box.

Public contract (all return a result dict, never raise on ESPN failure):

    add_drop(add_name, drop_name, txn_type="WAIVER", dry_run=False, league=None)
    set_lineup(starters, dry_run=False, league=None)
    propose_trade(send, receive, dry_run=False, to_team=None, league=None)
    get_roster(league=None)
    whoami(league=None)

Result dict:
    {"ok": bool,            # did the action succeed (dry runs report the
                            #   would-be outcome, so ok=True means "valid")
     "dry_run": bool,
     "action": str,         # "add_drop" | "set_lineup" | ...
     "league": str,         # which league key was used
     "detail": str,         # one-line human summary, safe to post to Discord
     "error": str | None,   # None on success
     ...}                   # action-specific extras (see each function)

`dry_run=True` validates everything — resolves names, checks eligibility,
computes the exact moves — and submits nothing. Always dry-run first.

Config (JSON). Either the flat legacy shape (the iMac's existing config.json
works as-is) or the multi-league shape:

    {
      "espn_s2": "...",
      "swid": "{...}",
      "default_league": "baseball",
      "leagues": {
        "baseball":           {"league_id": 2057904545, "team_id": 9, "season": 2026},
        "cast_final_fantasy": {"league_id": null, "team_id": null, "season": 2026},
        "sunday_funday":      {"league_id": null, "team_id": null, "season": 2026}
      }
    }

Path resolution: explicit `config_path=` arg -> $FANTASY_EXEC_CONFIG ->
./config.json next to this file. Mirrors fantasy_lib's $FANTASY_DB convention.

NOTE ON DUPLICATION: the eligibility matching below is a port of
set_lineup.compute_moves. Two copies exist on purpose — the iMac scripts stay
untouched until this module is validated against live ESPN. Once it is, collapse
set_lineup.py / waiver_move.py onto this module so there is one implementation.

CLI (for testing — dry run is the default, --apply is required to submit):
    python3 fantasy_exec.py roster
    python3 fantasy_exec.py add-drop --add "Ben Brown" --drop "Landen Roupp"
    python3 fantasy_exec.py lineup --starters starters.json
    python3 fantasy_exec.py trade --send "Kevin McGonigle" --receive "Kyle Bradish"
    python3 fantasy_exec.py selftest        # offline; no ESPN, no config needed
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# espn_api / requests are imported LAZILY so the pure logic (compute_moves and
# the selftest) stays importable on a box that hasn't installed them yet.
def _deps():
    try:
        import requests
        from espn_api.baseball import League
    except ImportError as e:                  # pragma: no cover — env problem
        raise RuntimeError(
            f"missing dependency — {e}. Run: pip install requests espn-api")
    return requests, League

# ── Slot constants ─────────────────────────────────────────────────────────────
# ESPN's real lineupSlotId map for baseball (flb). Verified against
# settings.rosterSettings.lineupSlotCounts on league 2057904545: a slot the
# league doesn't use simply has a count of 0. Getting these wrong is not a
# cosmetic error — ESPN rejects the transaction with a slot-limit 409.
SLOT_NAMES = {
    0: "C", 1: "1B", 2: "2B", 3: "3B", 4: "SS",
    5: "OF", 6: "2B/SS", 7: "1B/3B",
    8: "LF", 9: "CF", 10: "RF", 11: "DH", 12: "UTIL",
    13: "P", 14: "SP", 15: "RP",
    16: "BE", 17: "IL", 19: "IF",
}
BENCH_SLOT = 16
IL_SLOTS = {17}

# Defaults for this baseball league; a league entry may override either list.
# Repeats are meaningful: three OF slots all carry id 5, seven P slots all
# carry id 13. The matcher seats by position, not by id, so repeats work.
DEFAULT_HITTER_SLOTS = [0, 1, 2, 3, 4, 5, 5, 5, 12]   # C 1B 2B 3B SS OFx3 UTIL
DEFAULT_PITCHER_SLOTS = [13] * 7

# Which eligibility group a hitter slot demands. None = UTIL (any hitter),
# 5 = any OF slot.
SLOT_GROUP = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4,
              5: 5, 8: 5, 9: 5, 10: 5, 12: None}


# ── Config ─────────────────────────────────────────────────────────────────────
class ConfigError(RuntimeError):
    """Raised for config problems — these are operator errors, not ESPN errors."""


def load_config(config_path: str | None = None) -> dict:
    path = (config_path
            or os.environ.get("FANTASY_EXEC_CONFIG")
            or os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"))
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        raise ConfigError(f"config not found at {path} — set $FANTASY_EXEC_CONFIG")
    except json.JSONDecodeError as e:
        raise ConfigError(f"config at {path} is not valid JSON: {e}")


class LeagueCtx:
    """Everything one league needs: creds, ids, slot layout, base URL."""

    def __init__(self, key: str, espn_s2: str, swid: str,
                 league_id: int, team_id: int, season: int,
                 hitter_slots: list[int], pitcher_slots: list[int]):
        self.key = key
        self.espn_s2 = espn_s2
        self.swid = swid
        self.league_id = league_id
        self.team_id = team_id
        self.season = season
        self.hitter_slots = hitter_slots
        self.pitcher_slots = pitcher_slots

    @property
    def cookies(self) -> dict:
        return {"espn_s2": self.espn_s2, "SWID": self.swid}

    @property
    def base_url(self) -> str:
        # fantasy.espn.com/apis/v3 is fronted by Akamai and now 403s every API
        # call. Transactions must go to the lm-api-writes host.
        return (f"https://lm-api-writes.fantasy.espn.com/apis/v3/games/flb"
                f"/seasons/{self.season}/segments/0/leagues/{self.league_id}")

    def __repr__(self) -> str:
        return (f"<LeagueCtx {self.key} league_id={self.league_id} "
                f"team_id={self.team_id} season={self.season}>")


def resolve_league(cfg: dict, league: str | None = None) -> LeagueCtx:
    """Pick a league out of the config. Supports flat and multi-league shapes."""
    for cred in ("espn_s2", "swid"):
        if not cfg.get(cred):
            raise ConfigError(f"config is missing '{cred}'")

    leagues = cfg.get("leagues") or {}
    if leagues:
        key = league or cfg.get("default_league")
        if not key:
            raise ConfigError(
                f"no league given and no 'default_league' set "
                f"(available: {', '.join(sorted(leagues)) or 'none'})")
        if key not in leagues:
            raise ConfigError(
                f"unknown league {key!r} (available: {', '.join(sorted(leagues))})")
        entry = leagues[key] or {}
    else:
        # Flat legacy shape — the iMac's existing config.json.
        if league and league not in ("baseball", "default"):
            raise ConfigError(
                f"config has no 'leagues' map, so league={league!r} can't be "
                f"resolved; add a 'leagues' entry for it")
        key = league or "baseball"
        entry = cfg

    league_id, team_id = entry.get("league_id"), entry.get("team_id")
    if not league_id or not team_id:
        raise ConfigError(
            f"league {key!r} is not configured yet — needs both 'league_id' "
            f"and 'team_id' (got league_id={league_id!r}, team_id={team_id!r})")

    return LeagueCtx(
        key=key,
        espn_s2=cfg["espn_s2"],
        swid=cfg["swid"],
        league_id=int(league_id),
        team_id=int(team_id),
        season=int(entry.get("season") or cfg.get("season") or 2026),
        hitter_slots=list(entry.get("hitter_slots") or DEFAULT_HITTER_SLOTS),
        pitcher_slots=list(entry.get("pitcher_slots") or DEFAULT_PITCHER_SLOTS),
    )


# ── Roster parsing ─────────────────────────────────────────────────────────────
def _slot_id_from_name(name: str) -> int:
    """Map slot name -> first matching slot ID. 'P' resolves to 11, 'OF' to 5."""
    name = (name or "").strip()
    for k, v in SLOT_NAMES.items():
        if v == name:
            return k
    return BENCH_SLOT


def parse_roster(team) -> list[dict]:
    """espn_api Team roster -> our standard player dicts."""
    out = []
    for p in team.roster:
        slot_id = _slot_id_from_name(p.lineupSlot)
        out.append({
            "name": p.name,
            "player_id": p.playerId,
            "pro_team": getattr(p, "proTeam", "") or "",
            "position": getattr(p, "position", "") or "",
            "slot": slot_id,
            "slot_label": p.lineupSlot,
            "injury": (p.injuryStatus or "ACTIVE").strip().upper(),
            "pct_own": round(getattr(p, "percent_owned", 0) or 0, 1),
            "eligible": [_slot_id_from_name(s) for s in (p.eligibleSlots or [])],
            "on_il": slot_id in IL_SLOTS,
            "on_bench": slot_id == BENCH_SLOT,
        })
    return out


def _resolve_name(name: str, players: list[dict]) -> dict | None:
    """Exact match wins; otherwise a substring match only if it is unambiguous."""
    low = name.strip().lower()
    for p in players:
        if p["name"].lower() == low:
            return p
    hits = [p for p in players if low in p["name"].lower()]
    return hits[0] if len(hits) == 1 else None


# ── Eligibility-safe lineup solving (port of set_lineup.compute_moves) ─────────
def _is_pitcher(p: dict) -> bool:
    return 13 in p["eligible"]      # 13 = P; every pitcher carries it


def _hitter_can_fill(elig: list[int], slot: int) -> bool:
    g = SLOT_GROUP.get(slot)
    if g is None:            # UTIL — any hitter
        return True
    if g == 5:               # any OF slot
        return 5 in elig
    return g in elig


def _match(starters: list[dict], slots: list[int], can_fill,
           indices: list[int] | None = None) -> dict | None:
    """Kuhn's bipartite matching. Returns {starter_index: slot_id} or None.

    Matching is by POSITION in `slots`, not by slot id, because a league's
    repeated slots share one id (three OF slots are all id 5). Keying on the id
    would seat exactly one outfielder and call the rest unplaceable.
    """
    if indices is None:
        indices = list(range(len(starters)))
    pos_to_starter: dict[int, int] = {}

    def try_assign(i: int, seen: set[int]) -> bool:
        for pos, s in enumerate(slots):
            if pos not in seen and can_fill(starters[i]["eligible"], s):
                seen.add(pos)
                if pos not in pos_to_starter or try_assign(pos_to_starter[pos], seen):
                    pos_to_starter[pos] = i
                    return True
        return False

    for i in indices:
        if not try_assign(i, set()):
            return None
    return {i: slots[pos] for pos, i in pos_to_starter.items()}


def _match_stable(starters: list[dict], slots: list[int], can_fill) -> dict | None:
    """Match, but leave a starter where he already sits when that is legal.

    A plain matching is free to send a player from OF slot 6 to OF slot 5, or
    from P slot 13 to P slot 11 — identical in every way that matters, but it
    still counts as a move and would submit a pointless transaction. So: pin
    everyone already sitting in an acceptable slot, then match only the rest
    into what is left. If that pinning makes the remainder unsolvable, fall
    back to matching from scratch, which is always at least as feasible.
    """
    assign: dict[int, int] = {}
    free = list(slots)
    for i, p in enumerate(starters):
        cur = p.get("slot")
        if cur in free and can_fill(p["eligible"], cur):
            assign[i] = cur
            free.remove(cur)

    rest = [i for i in range(len(starters)) if i not in assign]
    if not rest:
        return assign

    partial = _match(starters, free, can_fill, indices=rest)
    if partial is not None:
        assign.update(partial)
        return assign

    return _match(starters, slots, can_fill)      # pinning boxed us in — redo


def compute_moves(roster: list[dict], starter_names: list[str],
                  hitter_slots: list[int] | None = None,
                  pitcher_slots: list[int] | None = None):
    """Return (moves, error). Eligibility-safe; never emits an illegal move."""
    hitter_slots = hitter_slots or DEFAULT_HITTER_SLOTS
    pitcher_slots = pitcher_slots or DEFAULT_PITCHER_SLOTS

    starters, missing = [], []
    for n in starter_names:
        p = _resolve_name(n, roster)
        starters.append(p) if p else missing.append(n)
    if missing:
        return None, f"Not on roster (or ambiguous): {', '.join(missing)}"

    dupes = {p["player_id"] for p in starters}
    if len(dupes) != len(starters):
        return None, "Duplicate player named in starters."

    il = [p for p in starters if p["on_il"]]
    if il:
        return None, f"Can't start IL players: {', '.join(p['name'] for p in il)}"

    hitters = [p for p in starters if not _is_pitcher(p)]
    pitchers = [p for p in starters if _is_pitcher(p)]
    if len(hitters) > len(hitter_slots):
        return None, f"{len(hitters)} hitters named; only {len(hitter_slots)} hitter slots."
    if len(pitchers) > len(pitcher_slots):
        return None, f"{len(pitchers)} pitchers named; only {len(pitcher_slots)} P slots."

    h_assign = _match_stable(hitters, hitter_slots, _hitter_can_fill)
    if h_assign is None:
        return None, ("Can't seat all hitters within their eligible positions. "
                      "Check your 9 cover C/1B/2B/3B/SS/3xOF/UTIL.")
    p_assign = _match_stable(pitchers, pitcher_slots, lambda e, s: True)  # any P
    if p_assign is None:
        return None, "Can't seat all pitchers."

    target: dict[int, int] = {}
    for i, slot in h_assign.items():
        target[hitters[i]["player_id"]] = slot
    for i, slot in p_assign.items():
        target[pitchers[i]["player_id"]] = slot
    for p in roster:                          # everyone else (non-IL) -> bench
        if p["player_id"] not in target and not p["on_il"]:
            target[p["player_id"]] = BENCH_SLOT

    moves = []
    for p in roster:
        pid = p["player_id"]
        if pid not in target or p["slot"] == target[pid]:
            continue
        from_label = SLOT_NAMES.get(p["slot"], str(p["slot"]))
        to_label = SLOT_NAMES.get(target[pid], str(target[pid]))
        # Drop cosmetic churn. espn_api reports lineupSlot as a LABEL, so every
        # pitcher parses back as slot 11 and every outfielder as slot 5 — the
        # specific numeric slot is not recoverable. That makes the solver want
        # to "move" P->P and OF->OF, which ESPN treats as identical. Submitting
        # those would be a pointless transaction, so drop them.
        if from_label == to_label:
            continue
        moves.append({"player_id": pid, "name": p["name"],
                      "from_slot": p["slot"], "to_slot": target[pid],
                      "from_label": from_label, "to_label": to_label})
    return moves, None


# ── ESPN session ───────────────────────────────────────────────────────────────
def _connect(ctx: LeagueCtx):
    """Return (league, scoring_period, my_team, roster). Raises RuntimeError."""
    _, League = _deps()
    league = League(league_id=ctx.league_id, year=ctx.season,
                    espn_s2=ctx.espn_s2, swid=ctx.swid)
    sp = getattr(league, "scoringPeriodId", None)
    if sp is None:
        raise RuntimeError("could not determine scoringPeriodId from ESPN")
    team = next((t for t in league.teams if t.team_id == ctx.team_id), None)
    if team is None:
        raise RuntimeError(f"team_id {ctx.team_id} not found in league {ctx.league_id}")
    return league, sp, team, parse_roster(team)


def _post_transaction(ctx: LeagueCtx, payload: dict) -> tuple[bool, str]:
    requests, _ = _deps()
    try:
        resp = requests.post(
            # Trailing slash matters, and the kona headers identify us as the
            # web client; without them ESPN answers 400 Invalid Input.
            ctx.base_url + "/transactions/",
            cookies=ctx.cookies, json=payload,
            headers={"Accept": "application/json",
                     "Content-Type": "application/json",
                     "X-Fantasy-Source": "kona",
                     "X-Fantasy-Platform": "kona-PROD"},
            timeout=15,
        )
    except requests.RequestException as e:
        return False, f"network error posting to ESPN: {e}"
    if resp.status_code in (200, 201):
        return True, ""
    return False, f"HTTP {resp.status_code} — {resp.text[:300]}"


def _result(action: str, ctx_key: str, ok: bool, dry_run: bool,
            detail: str, error: str | None = None, **extra) -> dict:
    return {"ok": ok, "dry_run": dry_run, "action": action, "league": ctx_key,
            "detail": detail, "error": error, **extra}


# ── Public API ─────────────────────────────────────────────────────────────────
def whoami(league: str | None = None, config_path: str | None = None) -> dict:
    """Sanity check: confirm cookies work and report the team we'd act on."""
    try:
        ctx = resolve_league(load_config(config_path), league)
    except ConfigError as e:
        return _result("whoami", league or "?", False, True, "config error", str(e))
    try:
        _, sp, team, roster = _connect(ctx)
    except Exception as e:                    # noqa: BLE001 — report, don't raise
        return _result("whoami", ctx.key, False, True,
                       "could not reach ESPN", str(e))
    return _result("whoami", ctx.key, True, True,
                   f"{team.team_name} (team {ctx.team_id}, league {ctx.league_id}), "
                   f"scoringPeriod {sp}, {len(roster)} players",
                   team_name=team.team_name, scoring_period=sp,
                   roster_size=len(roster))


def get_roster(league: str | None = None, config_path: str | None = None) -> dict:
    """Current roster, as player dicts. Read-only."""
    try:
        ctx = resolve_league(load_config(config_path), league)
    except ConfigError as e:
        return _result("get_roster", league or "?", False, True, "config error", str(e))
    try:
        _, sp, team, roster = _connect(ctx)
    except Exception as e:                    # noqa: BLE001
        return _result("get_roster", ctx.key, False, True,
                       "could not reach ESPN", str(e))
    return _result("get_roster", ctx.key, True, True,
                   f"{len(roster)} players on {team.team_name}",
                   roster=roster, scoring_period=sp)


def add_drop(add_name: str, drop_name: str, txn_type: str = "WAIVER",
             dry_run: bool = False, league: str | None = None,
             config_path: str | None = None, bid: int = 0) -> dict:
    """Add a free agent / waiver player and drop a rostered player, atomically.

    txn_type: "WAIVER" for a rolling-waiver claim (this league has no FAAB, so
    bid stays 0) or "FREEAGENT" for an instant add of a player not on waivers.
    The add lands on the BENCH — call set_lineup() afterward to slot him.

    Extras in the result: add_player, drop_player (name/id dicts).
    """
    action = "add_drop"
    if txn_type not in ("WAIVER", "FREEAGENT"):
        return _result(action, league or "?", False, dry_run, "bad txn_type",
                       f"txn_type must be WAIVER or FREEAGENT, got {txn_type!r}")
    try:
        ctx = resolve_league(load_config(config_path), league)
    except ConfigError as e:
        return _result(action, league or "?", False, dry_run, "config error", str(e))
    try:
        lg, sp, team, roster = _connect(ctx)
    except Exception as e:                    # noqa: BLE001
        return _result(action, ctx.key, False, dry_run,
                       "could not reach ESPN", str(e))

    try:
        pool = lg.free_agents(size=400)
    except Exception as e:                    # noqa: BLE001
        return _result(action, ctx.key, False, dry_run,
                       "could not load free agents", str(e))

    add_p = _resolve_name(add_name, [{"name": getattr(p, "name", ""), "obj": p}
                                     for p in pool])
    if add_p is None:
        return _result(action, ctx.key, False, dry_run, "add target not found",
                       f"no available free agent matching {add_name!r} "
                       f"(or the name is ambiguous)")
    add_obj = add_p["obj"]

    drop_p = _resolve_name(drop_name, roster)
    if drop_p is None:
        return _result(action, ctx.key, False, dry_run, "drop target not found",
                       f"{drop_name!r} is not on the roster (or is ambiguous)")

    summary = f"{txn_type}: add {add_obj.name} / drop {drop_p['name']}"
    extras = {"add_player": {"name": add_obj.name, "player_id": add_obj.playerId},
              "drop_player": {"name": drop_p["name"], "player_id": drop_p["player_id"]},
              "scoring_period": sp}

    if dry_run:
        return _result(action, ctx.key, True, True, f"[DRY RUN] would {summary}",
                       None, **extras)

    payload = {
        "bidAmount": bid,
        "executionType": "EXECUTE",
        "isActingAsTeamOwner": False,
        "isLeagueManager": False,
        "isPending": False,
        "scoringPeriodId": sp,
        "teamId": ctx.team_id,
        "type": txn_type,
        "items": [
            {"fromTeamId": 0, "isKeeper": False, "playerId": add_obj.playerId,
             "toTeamId": ctx.team_id, "type": "ADD", "toLineupSlotId": BENCH_SLOT},
            {"fromTeamId": ctx.team_id, "isKeeper": False,
             "playerId": drop_p["player_id"], "toTeamId": 0, "type": "DROP"},
        ],
    }
    ok, err = _post_transaction(ctx, payload)
    return _result(action, ctx.key, ok, False,
                   summary if ok else f"failed — {summary}",
                   None if ok else err, **extras)


def set_lineup(starters: list[str], dry_run: bool = False,
               league: str | None = None, config_path: str | None = None) -> dict:
    """Seat `starters` in slots they are eligible for; bench everyone else.

    Submits all slot changes as ONE transaction — ESPN validates the END state,
    which avoids the "slot occupied" errors you get moving players one at a time.
    If a starter cannot be legally seated, nothing is submitted and the result
    carries the reason.

    Extras in the result: moves (list of {player_id,name,from_slot,to_slot,...}).
    """
    action = "set_lineup"
    if not starters:
        return _result(action, league or "?", False, dry_run, "no starters given",
                       "starters list is empty")
    try:
        ctx = resolve_league(load_config(config_path), league)
    except ConfigError as e:
        return _result(action, league or "?", False, dry_run, "config error", str(e))
    try:
        _, sp, team, roster = _connect(ctx)
    except Exception as e:                    # noqa: BLE001
        return _result(action, ctx.key, False, dry_run,
                       "could not reach ESPN", str(e))

    moves, err = compute_moves(roster, starters, ctx.hitter_slots, ctx.pitcher_slots)
    if err:
        return _result(action, ctx.key, False, dry_run, "lineup rejected", err)

    extras = {"moves": moves, "scoring_period": sp}
    if not moves:
        return _result(action, ctx.key, True, dry_run,
                       "no lineup changes needed — already optimal", None, **extras)

    listed = "; ".join(f"{m['name']} {m['from_label']}->{m['to_label']}" for m in moves)
    summary = f"{len(moves)} lineup move(s): {listed}"
    if dry_run:
        return _result(action, ctx.key, True, True, f"[DRY RUN] {summary}",
                       None, **extras)

    # The envelope is type ROSTER with executionType EXECUTE; only the ITEMS
    # are type LINEUP. An envelope of type LINEUP is rejected as invalid input.
    payload = {
        "isLeagueManager": False,
        "scoringPeriodId": sp,
        "teamId": ctx.team_id,
        "memberId": ctx.swid,
        "type": "ROSTER",
        "executionType": "EXECUTE",
        "items": [
            {"fromLineupSlotId": m["from_slot"], "toLineupSlotId": m["to_slot"],
             "playerId": m["player_id"], "type": "LINEUP"}
            for m in moves
        ],
    }
    ok, post_err = _post_transaction(ctx, payload)
    return _result(action, ctx.key, ok, False,
                   summary if ok else f"failed — {summary}",
                   None if ok else post_err, **extras)


def _league_wide_players(lg) -> list[dict]:
    """Every rostered player in the league, tagged with the team that owns him."""
    out = []
    for t in lg.teams:
        for p in t.roster:
            out.append({"name": p.name, "player_id": p.playerId,
                        "position": getattr(p, "position", "") or "",
                        "team_id": t.team_id, "team_name": t.team_name,
                        "obj": p})
    return out


def propose_trade(send: list[str], receive: list[str], dry_run: bool = False,
                  to_team: str | None = None, league: str | None = None,
                  config_path: str | None = None) -> dict:
    """Propose a trade: `send` players go out, `receive` players come back.

    The counterparty is inferred from whoever owns the `receive` players — they
    must all sit on one roster. `to_team` (a team name substring or id) is an
    optional cross-check; if given and it disagrees with the inferred owner, the
    trade is refused rather than sent to the wrong manager.

    Sides need not be equal in size, but ESPN rejects a proposal that would leave
    either roster over its limit, so keep them balanced unless you know better.

    Extras in the result: send_players, receive_players, to_team_id, to_team_name.
    """
    action = "propose_trade"
    if not send or not receive:
        return _result(action, league or "?", False, dry_run, "incomplete trade",
                       "both `send` and `receive` need at least one player")
    try:
        ctx = resolve_league(load_config(config_path), league)
    except ConfigError as e:
        return _result(action, league or "?", False, dry_run, "config error", str(e))
    try:
        lg, sp, team, roster = _connect(ctx)
    except Exception as e:                    # noqa: BLE001
        return _result(action, ctx.key, False, dry_run,
                       "could not reach ESPN", str(e))

    mine, theirs = [], []
    pool = _league_wide_players(lg)
    for name in send:
        p = _resolve_name(name, roster)
        if p is None:
            return _result(action, ctx.key, False, dry_run, "send target not found",
                           f"{name!r} is not on your roster (or is ambiguous)")
        mine.append({"name": p["name"], "player_id": p["player_id"]})
    for name in receive:
        p = _resolve_name(name, pool)
        if p is None:
            return _result(action, ctx.key, False, dry_run, "receive target not found",
                           f"no rostered player matching {name!r} (or ambiguous) — "
                           f"free agents go through add_drop, not a trade")
        if p["team_id"] == ctx.team_id:
            return _result(action, ctx.key, False, dry_run, "already yours",
                           f"{p['name']} is on your own roster")
        theirs.append(p)

    owners = {p["team_id"] for p in theirs}
    if len(owners) > 1:
        names = ", ".join(sorted({f"{p['name']} ({p['team_name']})" for p in theirs}))
        return _result(action, ctx.key, False, dry_run, "multi-team trade",
                       f"ESPN trades are between two teams; you asked for {names}")
    other_id = theirs[0]["team_id"]
    other_name = theirs[0]["team_name"]

    if to_team:
        want = str(to_team).strip().lower()
        if want.isdigit():
            match = int(want) == other_id
        else:
            match = want in other_name.lower()
        if not match:
            return _result(action, ctx.key, False, dry_run, "counterparty mismatch",
                           f"those players belong to {other_name} (team {other_id}), "
                           f"not {to_team!r} — refusing to send it to the wrong manager")

    out_names = ", ".join(p["name"] for p in mine)
    in_names = ", ".join(p["name"] for p in theirs)
    summary = f"trade to {other_name}: send {out_names} / receive {in_names}"
    extras = {"send_players": mine,
              "receive_players": [{"name": p["name"], "player_id": p["player_id"]}
                                  for p in theirs],
              "to_team_id": other_id, "to_team_name": other_name,
              "scoring_period": sp}

    if dry_run:
        return _result(action, ctx.key, True, True, f"[DRY RUN] would propose {summary}",
                       None, **extras)

    # A proposal is a TRADE_PROPOSAL envelope left PENDING — the other manager
    # accepts or declines in their app. Items carry type TRADE and name both
    # ends explicitly; toLineupSlotId lands incoming players on the bench.
    payload = {
        "isLeagueManager": False,
        "isPending": True,
        "scoringPeriodId": sp,
        "teamId": ctx.team_id,
        "memberId": ctx.swid,
        "type": "TRADE_PROPOSAL",
        "executionType": "EXECUTE",
        "items": (
            [{"playerId": p["player_id"], "type": "TRADE", "isKeeper": False,
              "fromTeamId": ctx.team_id, "toTeamId": other_id,
              "toLineupSlotId": BENCH_SLOT} for p in mine]
            + [{"playerId": p["player_id"], "type": "TRADE", "isKeeper": False,
                "fromTeamId": other_id, "toTeamId": ctx.team_id,
                "toLineupSlotId": BENCH_SLOT} for p in theirs]
        ),
    }
    ok, err = _post_transaction(ctx, payload)
    return _result(action, ctx.key, ok, False,
                   f"proposed {summary}" if ok else f"failed — {summary}",
                   None if ok else err, **extras)


# ── Offline self-test ──────────────────────────────────────────────────────────
def _selftest() -> int:
    """Exercise the eligibility solver with a synthetic roster. No ESPN, no config."""
    def pl(pid, name, elig, slot=BENCH_SLOT, il=False):
        return {"player_id": pid, "name": name, "eligible": elig, "slot": slot,
                "on_il": il, "on_bench": slot == BENCH_SLOT}

    roster = [
        pl(1, "Shea Langeliers", [0, 12]),
        pl(2, "Freddie Freeman", [1, 12]),
        pl(3, "Casey Schmitt", [2, 4, 12]),
        pl(4, "Max Muncy", [3, 12]),
        pl(5, "Kevin McGonigle", [4, 12]),
        pl(6, "Juan Soto", [5, 12]),
        pl(7, "JJ Bleday", [5, 12]),
        pl(8, "Brandon Marsh", [5, 12]),
        pl(9, "Willson Contreras", [0, 1, 12]),
        pl(10, "Yoshinobu Yamamoto", [13]),
        pl(11, "Bryce Elder", [13]),
        pl(12, "Braxton Ashcraft", [13]),
        pl(13, "Benchy McBench", [5, 12], slot=5),   # starting now, should bench
        pl(14, "Hurt Guy", [5, 12], slot=17, il=True),
    ]
    names = [p["name"] for p in roster[:12]]
    fails = []

    moves, err = compute_moves(roster, names)
    if err:
        fails.append(f"valid lineup rejected: {err}")
    else:
        seated = {m["player_id"]: m["to_slot"] for m in moves}
        if seated.get(13) != BENCH_SLOT:
            fails.append("non-starter was not benched")
        if any(m["player_id"] == 14 for m in moves):
            fails.append("IL player was moved")
        for m in moves:
            p = next(x for x in roster if x["player_id"] == m["player_id"])
            to = m["to_slot"]
            if to in DEFAULT_HITTER_SLOTS and not _hitter_can_fill(p["eligible"], to):
                fails.append(f"{p['name']} seated in ineligible slot {to}")
            if to in DEFAULT_PITCHER_SLOTS and 13 not in p["eligible"]:
                fails.append(f"non-pitcher {p['name']} seated in P slot {to}")

    # An IL player named as a starter must be refused.
    _, err = compute_moves(roster, names[:11] + ["Hurt Guy"])
    if not err or "IL" not in err:
        fails.append("IL starter was not refused")

    # Ten hitters cannot fit nine hitter slots.
    _, err = compute_moves(roster, [p["name"] for p in roster[:9]] + ["Benchy McBench"])
    if not err:
        fails.append("over-full hitter lineup was not refused")

    # An unknown name must be refused, not silently dropped.
    _, err = compute_moves(roster, names[:11] + ["Nobody At All"])
    if not err or "Not on roster" not in err:
        fails.append("unknown starter was not refused")

    # Stability: a lineup that is already set must produce ZERO moves, even
    # though three OF share slot id 5 and seven P share slot id 13.
    seated = [
        pl(1, "Shea Langeliers", [0, 12], slot=0),
        pl(2, "Freddie Freeman", [1, 12], slot=1),
        pl(3, "Casey Schmitt", [2, 4, 12], slot=4),    # 2B/SS man seated at SS
        pl(4, "Max Muncy", [3, 12], slot=3),
        pl(5, "Kevin McGonigle", [4, 12], slot=12),    # SS man parked in UTIL
        pl(6, "Juan Soto", [5, 12], slot=5),
        pl(7, "JJ Bleday", [5, 12], slot=5),
        pl(8, "Brandon Marsh", [5, 12], slot=5),
        pl(9, "Ozzie Albies", [2, 12], slot=2),
        pl(10, "Yoshinobu Yamamoto", [13], slot=13),
        pl(11, "Bryce Elder", [13], slot=13),
        pl(12, "Braxton Ashcraft", [13], slot=13),
    ]
    moves, err = compute_moves(seated, [p["name"] for p in seated])
    if err:
        fails.append(f"already-set lineup rejected: {err}")
    elif moves:
        churn = "; ".join(f"{m['name']} {m['from_label']}->{m['to_label']}" for m in moves)
        fails.append(f"already-set lineup produced {len(moves)} no-op move(s): {churn}")

    # The live shape: a full 9-hitter + 4-pitcher lineup with every OF on 5 and
    # every P on 13, which is exactly what ESPN reports back.
    live_shape = [
        pl(1, "Shea Langeliers", [0, 12], slot=0),
        pl(2, "Casey Schmitt", [1, 2, 4, 12], slot=1),   # ESPN has him at 1B
        pl(3, "Ozzie Albies", [2, 12], slot=2),
        pl(4, "Max Muncy", [3, 12], slot=3),
        pl(5, "Kevin McGonigle", [4, 12], slot=4),
        pl(6, "Juan Soto", [5, 12], slot=5),
        pl(7, "Brandon Marsh", [5, 12], slot=5),
        pl(8, "JJ Bleday", [5, 12], slot=5),
        pl(9, "Freddie Freeman", [1, 12], slot=12),
        pl(10, "Yoshinobu Yamamoto", [13], slot=13),
        pl(11, "Emerson Hancock", [13], slot=13),
        pl(12, "Bryce Elder", [13], slot=13),
        pl(13, "Braxton Ashcraft", [13], slot=13),
    ]
    moves, err = compute_moves(live_shape, [p["name"] for p in live_shape])
    if err:
        fails.append(f"live-shape lineup rejected: {err}")
    elif moves:
        churn = "; ".join(f"{m['name']} {m['from_label']}->{m['to_label']}" for m in moves)
        fails.append(f"live-shape lineup produced {len(moves)} no-op move(s): {churn}")

    # A real bench-for-starter swap must still be emitted, not filtered away.
    swap = live_shape + [pl(14, "Bench Bat", [5, 12], slot=BENCH_SLOT)]
    want = [p["name"] for p in live_shape if p["name"] != "JJ Bleday"] + ["Bench Bat"]
    moves, err = compute_moves(swap, want)
    if err:
        fails.append(f"bench-swap rejected: {err}")
    else:
        by_name = {m["name"]: m for m in moves}
        if "Bench Bat" not in by_name:
            fails.append("bench-swap: incoming starter was not moved off the bench")
        if by_name.get("JJ Bleday", {}).get("to_label") != "BE":
            fails.append("bench-swap: displaced starter was not benched")

    # Three catchers cannot be seated (C, UTIL, then nowhere).
    triple_c = [pl(20, "C One", [0, 12]), pl(21, "C Two", [0, 12]),
                pl(22, "C Three", [0, 12])]
    _, err = compute_moves(triple_c, ["C One", "C Two", "C Three"])
    if not err:
        fails.append("impossible C/C/C lineup was not refused")

    if fails:
        for f in fails:
            print(f"  FAIL: {f}")
        print(f"selftest FAILED ({len(fails)} problem(s))")
        return 1
    print("selftest OK — eligibility solver behaves")
    return 0


# ── CLI ────────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="Portable ESPN execution module.")
    ap.add_argument("--league", default=None, help="league key from config 'leagues'")
    ap.add_argument("--config", default=None, help="path to config JSON")
    ap.add_argument("--apply", action="store_true",
                    help="actually submit (default is a dry run)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("selftest", help="offline solver check; no ESPN or config")
    sub.add_parser("whoami", help="verify cookies and show the team we'd act on")
    sub.add_parser("roster", help="print the current roster")

    ad = sub.add_parser("add-drop", help="add a FA/waiver player, drop a rostered one")
    ad.add_argument("--add", required=True)
    ad.add_argument("--drop", required=True)
    ad.add_argument("--type", default="WAIVER", choices=["WAIVER", "FREEAGENT"])

    ln = sub.add_parser("lineup", help="set the lineup from a starters JSON file")
    ln.add_argument("--starters", required=True,
                    help="JSON file: {\"starters\": [\"Name\", ...]}")

    tr = sub.add_parser("trade", help="propose a trade to another team")
    tr.add_argument("--send", required=True, action="append",
                    help="player of yours to send (repeat for multiple)")
    tr.add_argument("--receive", required=True, action="append",
                    help="player to receive (repeat for multiple)")
    tr.add_argument("--to-team", default=None,
                    help="optional cross-check: counterparty name or team id")

    args = ap.parse_args()

    if args.cmd == "selftest":
        return _selftest()

    dry = not args.apply
    kw = {"league": args.league, "config_path": args.config}

    if args.cmd == "whoami":
        res = whoami(**kw)
    elif args.cmd == "roster":
        res = get_roster(**kw)
        if res["ok"]:
            for p in sorted(res["roster"], key=lambda x: x["slot"]):
                print(f"  {SLOT_NAMES.get(p['slot'], p['slot']):>4}  {p['name']:<24} "
                      f"{p['position']:<6} {p['injury']}")
    elif args.cmd == "add-drop":
        res = add_drop(args.add, args.drop, txn_type=args.type, dry_run=dry, **kw)
    elif args.cmd == "lineup":
        with open(args.starters) as f:
            res = set_lineup(json.load(f)["starters"], dry_run=dry, **kw)
    elif args.cmd == "trade":
        res = propose_trade(args.send, args.receive, dry_run=dry,
                            to_team=args.to_team, **kw)
    else:                                     # pragma: no cover — argparse guards
        ap.error(f"unknown command {args.cmd}")

    print(("OK  " if res["ok"] else "ERR ") + res["detail"])
    if res.get("error"):
        print("     " + res["error"])
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
