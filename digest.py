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
from urllib.parse import urlparse

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


_KIND_TO_METRIC = {
    "commit": "commits_created",
    "pr_opened": "prs_or_mrs_opened",
    "pr_merged": "prs_or_mrs_merged",
    "issue_opened": "issues_opened",
    "issue_closed": "issues_closed",
}


def compute_metrics(events: list[dict]) -> dict:
    """Counts normalized events (from sources/*.py) into the §3 metric set."""
    metrics = {key: 0 for key in _KIND_TO_METRIC.values()}
    for event in events:
        key = _KIND_TO_METRIC.get(event["kind"])
        if key:
            metrics[key] += 1
    return metrics


def platform_label(account: dict) -> str:
    """GitHub / GitLab / account's own label / generic 'GitLab (self-hosted)'.

    Deliberately never renders the real base_url hostname (PLAN.md §2/§3) —
    digest output leaves the host via the harness, so a self-hosted
    hostname without an explicit label falls back to a generic tag rather
    than being interpolated in.
    """
    if account.get("label"):
        return account["label"]
    if account["type"] == "github":
        return "GitHub"

    hostname = urlparse(account.get("base_url") or "https://gitlab.com").hostname
    if hostname == "gitlab.com":
        return "GitLab"
    return "GitLab (self-hosted)"
