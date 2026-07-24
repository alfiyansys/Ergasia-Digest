"""Core digest logic: fetch per-account events, compute metrics, render output.

Pure functions — shared by app.py (HTTP) and cli.py (CLI), no logic
duplicated between the two entrypoints (see AGENTS.md). Notably:
fetch_all_events() does NOT write to state.py — it only *reads*
last_run to resolve the default `since` window. Callers decide whether
to persist state afterwards (e.g. /digest/preview must not touch state,
/digest/run must), using each result's "fetched_at" as the new
last_run value on success.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import accounts_store
import state
from sources import github_source, gitlab_source

DEFAULT_LOOKBACK_HOURS = 24


def _resolve_since(account: dict, since_override: Optional[datetime]) -> datetime:
    if since_override is not None:
        return since_override
    last_run = state.get_last_run(account["id"])
    if last_run is not None:
        return last_run
    return datetime.now(timezone.utc) - timedelta(hours=DEFAULT_LOOKBACK_HOURS)


def fetch_all_events(
    since_override: Optional[datetime] = None,
    accounts: Optional[list[dict]] = None,
) -> list[dict]:
    """Fetches events for every account (pass a pre-filtered `accounts` list
    for the ?account=<id> single-account case; defaults to all accounts in
    accounts_store).

    Returns one result dict per account:
      success: {"account", "since", "fetched_at", "events"}
      failure: {"account", "since", "fetched_at", "error"}

    One account's fetch failure (network error, bad token, etc.) does not
    stop the others from being processed.
    """
    if accounts is None:
        accounts = accounts_store.load_accounts()

    results = []
    for account in accounts:
        since = _resolve_since(account, since_override)
        fetched_at = datetime.now(timezone.utc)
        try:
            if account["type"] == "github":
                events = github_source.fetch_events(account["username"], account["api_key"], since)
            else:
                events = gitlab_source.fetch_events(
                    account.get("base_url") or "https://gitlab.com",
                    account["username"],
                    account["api_key"],
                    since,
                )
            results.append({"account": account, "since": since, "fetched_at": fetched_at, "events": events})
        except Exception as e:
            results.append({"account": account, "since": since, "fetched_at": fetched_at, "error": str(e)})

    return results
