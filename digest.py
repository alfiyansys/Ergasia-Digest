"""Core digest logic: fetch per-account events, compute metrics, render output.

Pure functions — shared by app.py (HTTP) and cli.py (CLI), no logic
duplicated between the two entrypoints (see AGENTS.md). There is no
persistent state anywhere in this project (removed by design, see
AGENTS.md) — every call is a fresh live fetch from GitHub/GitLab. The
window defaults to a fixed rolling lookback (DEFAULT_LOOKBACK_HOURS)
and can be overridden via `since_override`; nothing is ever read from
or written to disk here beyond the account list itself.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

import accounts_store
from sources import github_source, gitlab_source

DEFAULT_LOOKBACK_HOURS = 24


def _resolve_since(since_override: Optional[datetime]) -> datetime:
    if since_override is not None:
        return since_override
    return datetime.now(timezone.utc) - timedelta(hours=DEFAULT_LOOKBACK_HOURS)


def fetch_all_events(
    since_override: Optional[datetime] = None,
    accounts: Optional[list[dict]] = None,
) -> list[dict]:
    """Fetches events for every account (pass a pre-filtered `accounts` list
    for the ?account=<id> single-account case; defaults to all accounts in
    accounts_store).

    Returns one result dict per account:
      success: {"account", "since", "events"}
      failure: {"account", "since", "error"}

    One account's fetch failure (network error, bad token, etc.) does not
    stop the others from being processed.
    """
    if accounts is None:
        accounts = accounts_store.load_accounts()

    since = _resolve_since(since_override)

    results = []
    for account in accounts:
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
            results.append({"account": account, "since": since, "events": events})
        except Exception as e:
            results.append({"account": account, "since": since, "error": str(e)})

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


def _count_phrase(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"


def _render_activities(account: dict, metrics: dict) -> str:
    """§3: commits_created always shown (even 0); other metrics dropped
    when 0. Uses "pull request" for github, "merge request" for gitlab."""
    request_noun = "pull request" if account["type"] == "github" else "merge request"

    parts = [_count_phrase(metrics["commits_created"], "commit created", "commits created")]
    if metrics["prs_or_mrs_opened"]:
        parts.append(_count_phrase(metrics["prs_or_mrs_opened"], f"{request_noun} opened", f"{request_noun}s opened"))
    if metrics["prs_or_mrs_merged"]:
        parts.append(_count_phrase(metrics["prs_or_mrs_merged"], f"{request_noun} merged", f"{request_noun}s merged"))
    if metrics["issues_opened"]:
        parts.append(_count_phrase(metrics["issues_opened"], "issue opened", "issues opened"))
    if metrics["issues_closed"]:
        parts.append(_count_phrase(metrics["issues_closed"], "issue closed", "issues closed"))

    return ", ".join(parts)


def build_digest(fetch_results: list[dict]) -> dict:
    """Renders fetch_all_events() results into the §3 format: one
    Platform/Username/Activities block per account (failed accounts get an
    "Activities: fetch failed (...)" block instead of being skipped), plus
    the structured per-account data behind it.

    Returns {"text": str, "data": {"accounts": [...]}}.
    """
    blocks = []
    account_data = []

    for result in fetch_results:
        account = result["account"]
        label = platform_label(account)

        if "error" in result:
            activities = f"fetch failed ({result['error']})"
            account_data.append({
                "account_id": account["id"],
                "platform": label,
                "username": account["username"],
                "error": result["error"],
            })
        else:
            metrics = compute_metrics(result["events"])
            activities = _render_activities(account, metrics)
            account_data.append({
                "account_id": account["id"],
                "platform": label,
                "username": account["username"],
                "metrics": metrics,
            })

        blocks.append(f"Platform: {label}\nUsername: {account['username']}\nActivities: {activities}")

    return {"text": "\n\n".join(blocks), "data": {"accounts": account_data}}
