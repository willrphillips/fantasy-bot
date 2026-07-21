from __future__ import annotations

import json
import os
import sys
from datetime import date

_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _DIR)

from espn_utils import send_discord, _discord_url, load_config, log

_STATE_PATH = os.path.join(_DIR, ".alert_state")


def _load_state() -> dict:
    try:
        with open(_STATE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    tmp = _STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, _STATE_PATH)


def alert(script_name: str, subject: str, body: str) -> bool:
    """Post a failure alert to Discord. Throttled: one per script per day.

    Email has been retired — alerts go to the Discord "alerts" channel (falling
    back to the "baseball" webhook, then any default). If no webhook is
    configured the alert is logged locally only. Returns True if posted, False
    if throttled, unconfigured, or the post failed. State is marked only AFTER a
    successful post so a transient failure does not burn the daily slot.
    """
    today = date.today().isoformat()
    state = _load_state()

    if state.get(script_name) == today:
        log(f"[notify] Throttled - '{script_name}' already alerted today")
        return False

    try:
        cfg = load_config()
        channel = "alerts" if _discord_url(cfg, "alerts") else "baseball"
        if not _discord_url(cfg, channel):
            log(f"[notify] No Discord webhook configured — '{subject}' logged only")
            return False
        send_discord(cfg, f"**[{script_name}] {subject}**\n{body}", channel=channel)
    except Exception as e:
        log(f"[notify] FAILED to post alert for '{script_name}': {e}")
        return False

    state[script_name] = today
    _save_state(state)
    log(f"[notify] Alert posted for '{script_name}': {subject}")
    return True
