from __future__ import annotations

import json
import os
import sys
from datetime import date

_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _DIR)

from espn_utils import send_email, load_config, log

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
    """Send failure email. Throttled: one per script per day.

    Returns True if sent. False if throttled or send failed.
    State is marked only AFTER a successful send so a transient SMTP
    failure does not burn the daily slot.
    """
    today = date.today().isoformat()
    state = _load_state()

    if state.get(script_name) == today:
        log(f"[notify] Throttled - '{script_name}' already alerted today")
        return False

    try:
        cfg = load_config()
        send_email(cfg, subject, body)
    except Exception as e:
        log(f"[notify] FAILED to send alert for '{script_name}': {e}")
        return False

    state[script_name] = today
    _save_state(state)
    log(f"[notify] Alert sent for '{script_name}': {subject}")
    return True
