"""Tracks per-account last-run timestamps and caches the latest generated digest."""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Optional

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")


def _load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {"last_run": {}, "latest_digest": None}
    with open(STATE_FILE, "r") as f:
        content = f.read().strip()
    if not content:
        return {"last_run": {}, "latest_digest": None}
    state = json.loads(content)
    state.setdefault("last_run", {})
    state.setdefault("latest_digest", None)
    return state


def _save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


def get_last_run(account_id: str) -> Optional[datetime]:
    ts = _load_state()["last_run"].get(account_id)
    return datetime.fromisoformat(ts) if ts else None


def set_last_run(account_id: str, ts: datetime) -> None:
    state = _load_state()
    state["last_run"][account_id] = ts.isoformat()
    _save_state(state)


def delete_account_state(account_id: str) -> None:
    """Called from accounts_store.delete_account / cli.py accounts delete so no
    stale last_run entry is left behind for a removed account."""
    state = _load_state()
    state["last_run"].pop(account_id, None)
    _save_state(state)
