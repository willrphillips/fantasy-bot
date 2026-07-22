#!/usr/bin/env python3
"""
Fantasy baseball — nightly roster advisor.

Runs every morning at 4:00 AM ET (scheduled in-process by edwin's bot.py, for the same no-root
reason as the other daily loops: a systemd timer would need a root install this account is sealed
from). Sits deliberately AFTER the 03:30 MLB/ESPN ingest so the database is same-day fresh.

What it does: rebuilds the analysis views, reads the LIVE ESPN roster (authoritative — waiver
claims process overnight, so the database's roster can be a few hours behind), folds the lot into
exactly ONE `claude -p` call, EXECUTES the moves that call decides on, and posts a short
butler-voiced brief to #baseball-fantasy saying what was done and why.

Will lifted the approval gate on roster moves on 2026-07-21: lineup changes, adds, drops and
waiver claims this job now makes outright and reports after. Two things it must never do from
here, both still gated on Will saying yes in person: propose a TRADE (it lands in another
manager's lap) and spend money. There is no trade path in this file, deliberately.

Alongside the prose the model emits a fenced ```moves JSON block, which is parsed, stripped from
the brief, capped at MAX_MOVES, and run through fantasy_exec. Adds go first, then one single
set_lineup call for the seating, since ESPN validates the whole end state at once. Every outcome,
success or failure, is appended to the posted brief — a move that silently fails is worse than one
never attempted.

Mirrors tools/email_rundown.py in the edwin repo in shape (one cheap LLM call, deterministic
fallback, chunked Discord post) so the scheduled jobs all behave the same way.

Config (env):
  DISCORD_TOKEN              read from ~/CodeProjects/edwin/.env if not already in the environment
  FANTASY_CHANNEL_ID         channel to post to (default: #baseball-fantasy below)
  FANTASY_ADVISOR_TZ         IANA tz for the heading date (default America/New_York)
  FANTASY_INGEST_WAIT        max minutes to wait for a still-running ingest (default 30)
  CLAUDE_BIN / CLAUDE_FLAGS / CLAUDE_TIMEOUT
  FANTASY_ADVISOR_DRY_RUN=1  build + call Claude, print the brief, but do NOT post or execute
  FANTASY_ADVISOR_EXECUTE=0  the kill switch: write the brief as a proposal only, submit nothing
  FANTASY_ADVISOR_MAX_MOVES  cap on moves executed in one morning (default 3)
"""
import os
import re
import sys
import glob
import json
import time
import shlex
import shutil
import logging
import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timedelta

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("fantasy-advisor")


def _load_edwin_env():
    """Pull DISCORD_TOKEN out of edwin's .env without depending on python-dotenv, which this
    repo's venv doesn't carry. Only fills what isn't already set in the environment."""
    env_file = Path(os.path.expanduser("~/CodeProjects/edwin/.env"))
    if not env_file.exists():
        return
    try:
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    except Exception as e:
        log.warning("could not read edwin .env: %s", e)


_load_edwin_env()

TOKEN = (os.getenv("DISCORD_TOKEN") or os.getenv("DISCORD_BOT_TOKEN") or "").strip()
CHANNEL_ID = (os.getenv("FANTASY_CHANNEL_ID") or "1529088498232197182").strip()  # #baseball-fantasy
TIMEZONE = (os.getenv("FANTASY_ADVISOR_TZ") or "America/New_York").strip()
INGEST_WAIT_MIN = int((os.getenv("FANTASY_INGEST_WAIT") or "30").strip() or "30")
CLAUDE_FLAGS = shlex.split(os.getenv("CLAUDE_FLAGS") or "")
CLAUDE_TIMEOUT = int((os.getenv("CLAUDE_TIMEOUT") or "420").strip() or "420")
DRY_RUN = (os.getenv("FANTASY_ADVISOR_DRY_RUN") or "").strip() in ("1", "true", "yes")
EXECUTE = (os.getenv("FANTASY_ADVISOR_EXECUTE") or "1").strip() not in ("0", "false", "no")
MAX_MOVES = int((os.getenv("FANTASY_ADVISOR_MAX_MOVES") or "3").strip() or "3")

DISCORD_API = "https://discord.com/api/v10"
UA = "EdwinFantasyAdvisor (https://github.com/edwin, 1.0)"
DISCORD_HEADERS = {"Authorization": f"Bot {TOKEN}", "User-Agent": UA, "Content-Type": "application/json"}

PY = str(REPO / "venv" / "bin" / "python3")
VIEWS_DIR = REPO / "public" / "views"
# The views worth feeding Claude. trade_targets is deliberately omitted: it's the largest file by
# far and trades are a deadline-window decision, not a daily one.
VIEW_FILES = ["team_review.md", "category_standings.md", "waiver_hitters.md",
              "waiver_pitchers.md", "regression_watch.md", "playoff_odds.md"]


def resolve_claude(name):
    if os.path.isabs(name) and os.path.exists(name):
        return name
    found = shutil.which(name)
    if found:
        return found
    cands = sorted(glob.glob(os.path.expanduser("~/.nvm/versions/node/*/bin/claude")), reverse=True)
    cands += ["/usr/bin/claude", "/usr/local/bin/claude", "/opt/homebrew/bin/claude",
              os.path.expanduser("~/.claude/local/claude")]
    for c in cands:
        if os.path.exists(c):
            return c
    return name


CLAUDE_BIN = resolve_claude((os.getenv("CLAUDE_BIN") or "claude").strip())


def _now():
    return datetime.now(ZoneInfo(TIMEZONE)) if ZoneInfo else datetime.now()


def wait_for_ingest(deadline_minutes=INGEST_WAIT_MIN):
    """The 03:30 ingest takes ~28 minutes, so a 4am start can land while it's still writing.
    Poll until yesterday's snapshot exists (stats run one day behind by design), then proceed.
    Returns the latest snapshot date, whether or not it ever caught up."""
    import fantasy_lib as fl
    want = (_now().date() - timedelta(days=1)).isoformat()
    give_up = time.time() + deadline_minutes * 60
    while True:
        try:
            latest = fl.latest_date()
        except Exception as e:
            log.warning("latest_date() failed: %s", e)
            return None
        if latest and latest >= want:
            return latest
        if time.time() >= give_up:
            log.warning("ingest still behind after %s min (latest=%s, want=%s); proceeding anyway",
                        deadline_minutes, latest, want)
            return latest
        log.info("waiting on ingest (latest=%s, want=%s)", latest, want)
        time.sleep(120)


def rebuild_views():
    """Regenerate the analysis views off the fresh database. Best-effort: a failure here just
    means the brief runs on yesterday's views, which is still useful."""
    try:
        proc = subprocess.run([PY, str(REPO / "views.py")], cwd=str(REPO),
                              stdin=subprocess.DEVNULL, capture_output=True, timeout=900)
        if proc.returncode != 0:
            log.warning("views.py exited %s: %s", proc.returncode,
                        (proc.stderr or b"").decode("utf-8", "replace")[-500:])
            return False
        return True
    except Exception as e:
        log.warning("views.py run failed: %s", e)
        return False


def live_roster():
    """Authoritative roster straight from ESPN. Waiver claims process overnight, so this can and
    does differ from the database's roster table at 4am."""
    try:
        import fantasy_exec
        res = fantasy_exec.get_roster()
        return res.get("roster") or []
    except Exception as e:
        log.warning("live roster read failed: %s", e)
        return []


def empty_slots(roster):
    """Which active lineup slots are sitting unfilled. ESPN returns only the players it has, so an
    empty slot is invisible unless you diff the filled slots against the league's layout. This is
    the one check worth waking up for, so it's computed here rather than left to the model."""
    try:
        import fantasy_exec as fe
        ctx = fe.resolve_league(fe.load_config())
        want = list(ctx.hitter_slots) + list(ctx.pitcher_slots)
    except Exception as e:
        log.warning("slot layout unavailable: %s", e)
        return []
    for p in roster:
        if p.get("slot") in want:
            want.remove(p["slot"])
    try:
        from espn_utils import SLOT_NAMES
    except Exception:
        SLOT_NAMES = {}
    return [SLOT_NAMES.get(s, str(s)) for s in want]


def today_column(roster):
    """{player_id: short phrase} saying what the man is actually doing today.

    Without this the brief is blind to the single most valuable fact in a daily-lineup league:
    which of the rostered starters is on the mound tonight. A pitcher who is not starting scores
    nothing at all, so a reliever whose team plays beats him every time, and an idle SP left in an
    active slot is a wasted slot. Sourced from the MLB probables, not ESPN."""
    try:
        from daily_projections import build_team_matchups
        from name_matcher import normalize
        from espn_utils import SLOT_NAMES
    except Exception as e:
        log.warning("today column unavailable: %s", e)
        return {}
    try:
        matchups = build_team_matchups()
    except Exception as e:
        log.warning("probables fetch failed: %s", e)
        return {}
    out = {}
    for p in roster:
        m = matchups.get(p.get("pro_team") or "", {})
        if not m.get("has_game"):
            out[p.get("player_id")] = "no game"
            continue
        elig = {SLOT_NAMES.get(s, str(s)) for s in (p.get("eligible") or [])}
        opp = m.get("opponent") or "?"
        if not (elig & {"SP", "RP", "P"}):
            out[p.get("player_id")] = f"plays {opp}"
        elif normalize(m.get("probable_pitcher") or "") == normalize(p.get("name") or ""):
            out[p.get("player_id")] = f"STARTING today vs {opp}"
        elif "RP" in elig:
            out[p.get("player_id")] = f"reliever, team plays {opp}"
        else:
            out[p.get("player_id")] = "NOT starting today"
    return out


def roster_block(roster):
    if not roster:
        return "(live ESPN roster unavailable — fall back to the roster in the team review below, " \
               "and say plainly in the brief that it may be a few hours stale)"
    try:
        from espn_utils import SLOT_NAMES
    except Exception:
        SLOT_NAMES = {}
    today = today_column(roster)
    lines = ["| slot | player | positions | today | injury | mlb_team | pct_owned |",
             "|---|---|---|---|---|---|---|"]
    for p in roster:
        elig = ",".join(SLOT_NAMES.get(s, str(s)) for s in (p.get("eligible") or []))
        lines.append("| {} | {} | {} | {} | {} | {} | {} |".format(
            p.get("slot_label", "?"), p.get("name", "?"), elig or p.get("position", ""),
            today.get(p.get("player_id"), "?"),
            p.get("injury", ""), p.get("pro_team", ""), p.get("pct_own", "")))
    gaps = empty_slots(roster)
    lines.append("")
    lines.append(f"EMPTY ACTIVE SLOTS: {', '.join(gaps) if gaps else 'none, every active slot is filled'}")
    return "\n".join(lines)


def read_views():
    parts = []
    for fn in VIEW_FILES:
        path = VIEWS_DIR / fn
        try:
            parts.append(f"===== {fn} =====\n" + path.read_text(encoding="utf-8", errors="replace"))
        except Exception as e:
            log.warning("view %s unreadable: %s", fn, e)
    return "\n\n".join(parts)


def build_prompt(nice_date, roster, views_text, latest):
    return f"""You are Edwin, Will's butler-assistant, writing the morning fantasy baseball brief for
{nice_date}. It posts unprompted to #baseball-fantasy while Will is asleep, so it must stand on its
own.

Will's team is "Captain Phillips" (team_id 9) in the Miami Pro H2H Categories League. His stated
strategy is BARONBALL: he is out of playoff contention and swinging for the fences, so prefer
ceiling over safety every time. Buy strikeout upside and power; do not protect ratios at the cost
of upside. Steady, safe value is worthless to him now. The trade deadline is 16 August.

LIVE ESPN ROSTER (authoritative, read minutes ago):
{roster}

ANALYSIS VIEWS (built from the database, stats current through {latest}):
{views_text}

Will has lifted the approval gate on roster moves. Whatever you decide below WILL BE SUBMITTED to
ESPN automatically, minutes from now, while he sleeps. Write as a man reporting what he has done,
not one asking leave. You may not propose a trade and you may not spend money; those two still
wait on him.

Write the brief. Rules, all binding:
- Open with a one-line verdict: is there anything worth doing today, or is the roster right as it
  stands? Say "Nothing worth changing this morning, sir" and stop if that is the honest answer. A
  quiet day is a perfectly good brief; never manufacture a move to look busy. You are acting
  unsupervised, so the bar for touching the roster is a clear improvement, not a marginal one.
- Then at most THREE moves, each one sentence or two: the exact move (start X over Y, drop A for
  B) and the single number that justifies it. Name real players from the data above.
- Check the obvious things: an empty active slot, a starter who is slumping badly with a better bat
  on the bench, an arm with a wretched last-fortnight line, a free agent clearly better than your
  worst rostered player. An unfilled pitching slot is free innings not collected — fill it.
- PITCHING SLOTS, and this one is absolute. Read the "today" column and seat the pitching slots in
  this order of preference, every single morning, before you consider anything else:
    1. anyone marked STARTING today — they are the whole point, seat every one of them;
    2. then relievers whose team plays today, who at least can throw;
    3. only then an SP marked NOT starting today, who will record nothing whatever.
  A reliever ALWAYS outranks a starter who is not on the mound today, however good the starter is.
  Never bench a man marked STARTING today, and never leave a NOT-starting SP in an active pitching
  slot while a healthy reliever whose team plays sits on the bench. This is a daily lineup and the
  brief runs again tomorrow, so benching an idle ace today costs nothing.
- Judge a reliever on strikeouts and appearances, not on wins or innings. An arm that pitches three
  times a week in relief is worth more here than a fifth starter who is never seated on his day.
- Roster is capped, so any add requires a drop. Always name both sides.
- Never drop a player who is on the IL, and never drop one of the team's genuinely best assets to
  chase a marginal upgrade.
- Close with one line saying the moves are done and he can reverse any of them.
- If you open by saying how many moves there are, the count must match what follows exactly.
- Voice: Alfred Pennyworth. Curt, dry, understated, full sentences, no em dashes, no exclamation
  marks, no bullet-point report formatting, no bold labels, no enthusiasm. "Sir" at most once.
- Never announce candour. Phrases like "the honest fix", "to be honest", "frankly", "in fairness",
  "worth noting" are banned outright. State the thing; do not flag that you are about to.
- Under 250 words. Plain prose. Discord-friendly.

After the brief, and ONLY if you named moves, append a fenced code block tagged `moves` holding a
JSON array of the same moves in machine form, in the order they should run. Nothing after it.

```moves
[
  {{"type": "add_drop", "add": "Free Agent Name", "drop": "Rostered Name", "txn": "FREEAGENT"}},
  {{"type": "start", "start": "Player To Seat", "bench": "Player To Sit"}}
]
```

The JSON is binding and must match the prose exactly: every move you describe appears once, and no
move appears that you did not describe. Names must be spelled as they appear on the roster or in
the waiver views. Use "txn": "WAIVER" if the player is on waivers, "FREEAGENT" if he is freely
available; when unsure use "WAIVER". A player you add lands on the bench, so if he is meant to
start, follow the add_drop with a "start" move seating him. Only these two move types exist. If
there is nothing to do, write no block at all.

Output only the brief and, if applicable, that one block. No preamble, no timestamp line, no
heading, no sign-off."""


def call_claude(prompt):
    cmd = [CLAUDE_BIN, "-p"] + CLAUDE_FLAGS + [prompt]
    log.info("calling claude (%s), timeout=%ss", CLAUDE_BIN, CLAUDE_TIMEOUT)
    try:
        proc = subprocess.run(cmd, cwd=str(REPO), stdin=subprocess.DEVNULL,
                              capture_output=True, timeout=CLAUDE_TIMEOUT)
    except subprocess.TimeoutExpired:
        log.error("claude timed out")
        return None
    except FileNotFoundError:
        log.error("claude CLI not found at %s", CLAUDE_BIN)
        return None
    if proc.returncode != 0:
        log.error("claude exited %s: %s", proc.returncode,
                  (proc.stderr or b"").decode("utf-8", "replace")[:500])
        return None
    return (proc.stdout or b"").decode("utf-8", "replace").strip()


def tidy(text):
    """Strip the stray leading timestamp line the model sometimes stamps on despite being told not
    to. Belt and braces on top of the prompt rule."""
    lines = (text or "").strip().splitlines()
    while lines and (not lines[0].strip() or (lines[0].strip().startswith("[") and lines[0].strip().endswith("]"))):
        lines.pop(0)
    return "\n".join(lines).strip()


MOVES_BLOCK = re.compile(r"```(?:moves|json)?\s*\n(\[.*?\])\s*\n?```", re.DOTALL)


def split_moves(text):
    """Pull the machine-readable moves block out of the brief and return (prose, moves).

    The block is stripped from the prose either way: Will reads the sentences, not the JSON. A
    malformed block yields no moves, which fails safe — the brief still posts, nothing is
    submitted, and the log carries the reason."""
    text = text or ""
    m = MOVES_BLOCK.search(text)
    if not m:
        return text.strip(), []
    prose = (text[:m.start()] + text[m.end():]).strip()
    try:
        moves = json.loads(m.group(1))
    except Exception as e:
        log.error("moves block did not parse as JSON: %s", e)
        return prose, []
    if not isinstance(moves, list):
        log.error("moves block was %s, not a list", type(moves).__name__)
        return prose, []
    clean = []
    for mv in moves:
        if not isinstance(mv, dict):
            continue
        kind = (mv.get("type") or "").strip().lower()
        if kind == "add_drop" and mv.get("add") and mv.get("drop"):
            txn = (mv.get("txn") or "WAIVER").strip().upper()
            clean.append({"type": "add_drop", "add": str(mv["add"]).strip(),
                          "drop": str(mv["drop"]).strip(),
                          "txn": txn if txn in ("WAIVER", "FREEAGENT") else "WAIVER"})
        elif kind == "start" and mv.get("start") and mv.get("bench"):
            clean.append({"type": "start", "start": str(mv["start"]).strip(),
                          "bench": str(mv["bench"]).strip()})
        else:
            log.warning("discarding unusable move: %r", mv)
    if len(clean) > MAX_MOVES:
        log.warning("model returned %s moves, capping at %s", len(clean), MAX_MOVES)
        clean = clean[:MAX_MOVES]
    return prose, clean


def execute_moves(moves):
    """Submit the moves to ESPN and return one report line per move.

    Adds run first and individually, since each is its own ESPN transaction. The seating is then
    done in ONE set_lineup call off a freshly-read roster, because set_lineup takes the complete
    intended starting nine-plus-seven and benches everyone else — feeding it a partial list would
    quietly bench the rest of the team."""
    lines = []
    try:
        import fantasy_exec as fe
    except Exception as e:
        log.error("fantasy_exec unavailable: %s", e)
        return [f"Could not reach the roster tooling ({e}); nothing was submitted."]

    for mv in [m for m in moves if m["type"] == "add_drop"]:
        try:
            res = fe.add_drop(mv["add"], mv["drop"], txn_type=mv["txn"])
        except Exception as e:                      # noqa: BLE001
            log.error("add_drop raised: %s", e)
            lines.append(f"Failed to add {mv['add']} for {mv['drop']}: {e}")
            continue
        if res.get("ok"):
            claim = " (waiver claim, processes overnight)" if mv["txn"] == "WAIVER" else ""
            lines.append(f"Added {mv['add']}, dropped {mv['drop']}{claim}.")
        else:
            lines.append(f"Failed to add {mv['add']} for {mv['drop']}: "
                         f"{res.get('error') or res.get('detail')}")

    seats = [m for m in moves if m["type"] == "start"]
    if not seats:
        return lines

    roster = live_roster()
    if not roster:
        lines.append("Could not re-read the roster, so the lineup was left alone.")
        return lines

    starters = [p["name"] for p in roster if not p.get("on_bench") and not p.get("on_il")]
    changed = False
    for mv in seats:
        into = fe._resolve_name(mv["start"], roster)
        out = fe._resolve_name(mv["bench"], roster)
        if not into or not out:
            missing = mv["start"] if not into else mv["bench"]
            lines.append(f"Could not seat {mv['start']} over {mv['bench']}: {missing} is not on "
                         f"the roster.")
            continue
        if out["name"] in starters:
            starters.remove(out["name"])
        if into["name"] not in starters:
            starters.append(into["name"])
        changed = True
        lines.append(f"Started {into['name']} over {out['name']}.")

    if not changed:
        return lines
    try:
        res = fe.set_lineup(starters)
    except Exception as e:                          # noqa: BLE001
        log.error("set_lineup raised: %s", e)
        lines.append(f"The lineup change did not take: {e}")
        return lines
    if not res.get("ok"):
        # The per-move "Started X over Y" lines above were optimistic; correct them.
        lines = [ln for ln in lines if not ln.startswith("Started ")]
        lines.append(f"The lineup change did not take: {res.get('error') or res.get('detail')}")
    return lines


def api_post(path, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(DISCORD_API + path, data=data, headers=DISCORD_HEADERS, method="POST")
    for _ in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                retry = float(e.headers.get("Retry-After", "1") or "1")
                time.sleep(min(retry + 0.25, 10))
                continue
            log.error("POST %s -> HTTP %s: %s", path, e.code, e.read().decode("utf-8", "replace")[:300])
            return None
        except Exception as e:
            log.warning("POST %s failed: %s", path, e)
            time.sleep(1)
    return None


def post_message(text):
    chunks = [text[i:i + 1900] for i in range(0, len(text), 1900)] or ["(empty)"]
    for c in chunks:
        if api_post(f"/channels/{CHANNEL_ID}/messages", {"content": c}) is None:
            log.error("failed to post brief chunk to channel %s", CHANNEL_ID)
            return False
        time.sleep(0.4)
    return True


def fallback_note(nice_date, roster, latest):
    """If Claude is unavailable, still say something true and useful: whether every active slot is
    filled, which is the one check worth waking up for."""
    head = f"**{nice_date} — fantasy morning brief**"
    if not roster:
        return f"{head}\nI could not reach ESPN or Claude this morning, sir. The brief will have to wait."
    empty = empty_slots(roster)
    body = (f"{len(empty)} active slot(s) sitting empty: {', '.join(empty)}."
            if empty else "Every active slot is filled.")
    return (f"{head}\nAnalysis was unavailable this morning, sir, so this is the bare check only. "
            f"{body} Stats current through {latest or 'an unknown date'}.")


def main():
    now = _now()
    nice_date = now.strftime("%B %-d, %Y")
    log.info("fantasy advisor for %s (tz=%s, dry_run=%s)", nice_date, TIMEZONE, DRY_RUN)

    latest = wait_for_ingest()
    rebuilt = rebuild_views()
    log.info("db latest=%s, views rebuilt=%s", latest, rebuilt)

    roster = live_roster()
    prompt = build_prompt(nice_date, roster_block(roster), read_views(), latest or "unknown")

    out = tidy(call_claude(prompt))
    if not out:
        out = fallback_note(nice_date, roster, latest)
        log.error("claude gave no output; posting deterministic fallback")

    out, moves = split_moves(out)
    log.info("brief names %s move(s)", len(moves))
    if moves and EXECUTE and not DRY_RUN:
        results = execute_moves(moves)
        if results:
            out = out + "\n\n" + "\n".join(results)
    elif moves:
        why = "dry run" if DRY_RUN else "execution disabled"
        log.info("not submitting (%s): %s", why, json.dumps(moves))
        out = out + f"\n\n(Not submitted — {why}.)\n" + json.dumps(moves, indent=2)

    try:
        (VIEWS_DIR / "nightly_advice.md").write_text(
            f"# Morning brief — {nice_date}\n\n_db latest: {latest}_\n\n{out}\n", encoding="utf-8")
    except Exception as e:
        log.warning("could not save brief to views: %s", e)

    if DRY_RUN:
        print(out)
        return 0
    ok = post_message(out)
    log.info("fantasy advisor complete (posted=%s)", ok)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
