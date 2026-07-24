"""GitHub Events API client.

Takes credentials as parameters — never reads env vars directly, so it can
be reused per account (see PLAN.md §2 / AGENTS.md).
"""

from __future__ import annotations

from datetime import datetime, timezone

import requests

GITHUB_API_BASE = "https://api.github.com"
REQUEST_TIMEOUT = 15


class SourceError(Exception):
    """Raised when the GitHub API can't be reached or returns an unexpected shape."""


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _normalize_event(event: dict, created_at: datetime) -> list[dict]:
    """Maps one raw GitHub event to zero or more normalized events:
    {"kind", "created_at"}, kind in {commit, pr_opened, pr_merged,
    issue_opened, issue_closed}. A single PushEvent can carry several
    commits, so it expands into one "commit" entry per commit."""
    event_type = event.get("type")
    payload = event.get("payload", {})

    if event_type == "PushEvent":
        commits = payload.get("commits", [])
        return [{"kind": "commit", "created_at": created_at} for _ in commits]

    if event_type == "PullRequestEvent":
        action = payload.get("action")
        if action == "opened":
            return [{"kind": "pr_opened", "created_at": created_at}]
        if action == "closed" and payload.get("pull_request", {}).get("merged"):
            return [{"kind": "pr_merged", "created_at": created_at}]
        return []

    if event_type == "IssuesEvent":
        action = payload.get("action")
        if action == "opened":
            return [{"kind": "issue_opened", "created_at": created_at}]
        if action == "closed":
            return [{"kind": "issue_closed", "created_at": created_at}]
        return []

    return []


def fetch_events(username: str, token: str, since: datetime) -> list[dict]:
    """Returns normalized events since `since`: [{"kind", "created_at"}, ...].

    Uses the public/private Events API for `username`, which GitHub only
    retains for a limited window (~90 days) — acceptable for a digest whose
    `since` is always recent (last_run or a short hours/days override, see
    PLAN.md §4)."""
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)

    normalized: list[dict] = []
    page = 1
    while True:
        resp = requests.get(
            f"{GITHUB_API_BASE}/users/{username}/events",
            headers=_headers(token),
            params={"per_page": 100, "page": page},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 401:
            raise SourceError("authentication failed: token is invalid or expired")
        if resp.status_code == 403:
            raise SourceError("forbidden: token lacks permission or rate limit exceeded")
        if resp.status_code == 404:
            raise SourceError(f"username '{username}' not found")
        if resp.status_code != 200:
            raise SourceError(f"unexpected GitHub API response: {resp.status_code}")

        events = resp.json()
        if not events:
            break

        reached_older_than_since = False
        for event in events:
            created_at = _parse_timestamp(event["created_at"])
            if created_at < since:
                reached_older_than_since = True
                continue
            normalized.extend(_normalize_event(event, created_at))

        if reached_older_than_since or len(events) < 100:
            break
        page += 1

    return normalized
