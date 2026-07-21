#!/usr/bin/env python3
"""
Fantasy baseball — nightly roster advisor.

Runs every morning at 4:00 AM ET (scheduled in-process by edwin's bot.py, for the same no-root
reason as the other daily loops: a systemd timer would need a root install this account is sealed
from). Sits deliberately AFTER the 03:30 MLB/ESPN ingest so the database is same-day fresh.

What it does: rebuilds the analysis views, reads the LIVE ESPN roster (authoritative — waiver
claims process overnight, so the database's roster can be a few hours behind), folds the lot into
exactly ONE `claude -p` call, and posts a short butler-voiced brief to #baseball-fantasy naming
concrete moves worth making today.

What it does NOT do: touch the roster. Submitting a lineup change, add, drop or claim to ESPN is a
standing gate that needs Will present and saying yes, every time. This job proposes; he disposes.

Mirrors tools/email_rundown.py in the edwin repo in shape (one cheap LLM call, deterministic
fallback, chunked Discord post) so the scheduled jobs all behave the same way.

Config (env):
  DISCORD_TOKEN              read from ~/CodeProjects/edwin/.env if not already in the environment
  FANTASY_CHANNEL_ID         channel to post to (default: #baseball-fantasy below)
  FANTASY_ADVISOR_TZ         IANA tz for the heading date (default America/New_York)
  FANTASY_INGEST_WAIT        max minutes to wait for a still-running ingest (default 30)
  CLAUDE_BIN / CLAUDE_FLAGS / CLAUDE_TIMEOUT
  FANTASY_ADVISOR_DRY_RUN=1  build + call Claude, print the brief, but do NOT post
"""
import os
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


def roster_block(roster):
    if not roster:
        return "(live ESPN roster unavailable — fall back to the roster in the team review below, " \
               "and say plainly in the brief that it may be a few hours stale)"
    try:
        from espn_utils import SLOT_NAMES
    except Exception:
        SLOT_NAMES = {}
    lines = ["| slot | player | positions | injury | mlb_team | pct_owned |", "|---|---|---|---|---|---|"]
    for p in roster:
        elig = ",".join(SLOT_NAMES.get(s, str(s)) for s in (p.get("eligible") or []))
        lines.append("| {} | {} | {} | {} | {} | {} |".format(
            p.get("slot_label", "?"), p.get("name", "?"), elig or p.get("position", ""),
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

Write the brief. Rules, all binding:
- Open with a one-line verdict: is there anything worth doing today, or is the roster right as it
  stands? Say "Nothing worth changing this morning, sir" and stop if that is the honest answer. A
  quiet day is a perfectly good brief; never manufacture a move to look busy.
- Then at most THREE concrete proposals, each one sentence or two: the exact move (start X over Y,
  drop A for B) and the single number that justifies it. Name real players from the data above.
- Check the obvious things: an empty active slot, a starter who is slumping badly with a better bat
  on the bench, an arm with a wretched last-fortnight line, a free agent clearly better than your
  worst rostered player. An unfilled pitching slot is free innings not collected — flag it.
- Roster is capped, so any add requires a drop. Always name both sides.
- Close with one line: nothing has been submitted, and say the word to execute.
- If you open by saying how many proposals there are, the count must match what follows exactly.
- Voice: Alfred Pennyworth. Curt, dry, understated, full sentences, no em dashes, no exclamation
  marks, no bullet-point report formatting, no bold labels, no enthusiasm. "Sir" at most once.
- Never announce candour. Phrases like "the honest fix", "to be honest", "frankly", "in fairness",
  "worth noting" are banned outright. State the thing; do not flag that you are about to.
- Under 250 words. Plain prose. Discord-friendly.
Output only the brief itself. No preamble, no timestamp line, no heading, no sign-off."""


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
