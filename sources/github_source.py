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


_COMMIT_SPIKE_THRESHOLD = 500


def _count_push_commits(repo_full_name: str, before: str, head: str, token: str) -> int:
    """GitHub's Events API no longer includes a `commits` list (or even a
    `size`/`distinct_size` count) in PushEvent payloads — verified against
    a real account: the payload only carries `ref`/`head`/`before` now.
    The Compare API's `total_commits` is the accurate way to count commits
    in a push today.

    A push event happens at a point in time; the user's real activity in the
    window is bounded by how many commits they can reasonably author. When
    the Compare API reports an implausible spike (fork syncs, re-parented
    history, force-pushes, upstream merges), trust the PushEvent: count it
    as a small, sane number instead of the full divergence distance (see
    PLAN.md §4 — the digest is an activity digest, not a history-distance
    report)."""
    if before == "0" * 40:
        # New branch with no prior history to diff against — can't measure
        # "commits added" precisely; count the push itself as one commit
        # rather than guessing.
        return 1
    resp = requests.get(
        f"{GITHUB_API_BASE}/repos/{repo_full_name}/compare/{before}...{head}",
        headers=_headers(token),
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code != 200:
        return 1  # can't determine the exact count; don't undercount to 0
    total = resp.json().get("total_commits", 1)
    if total > _COMMIT_SPIKE_THRESHOLD:
        # Diverged fork / rebase / re-parent: total_commits is the distance
        # between unrelated histories, not the commits pushed in this event.
        return 1
    return total


def _normalize_event(event: dict, created_at: datetime, token: str) -> list[dict]:
    """Maps one raw GitHub event to zero or more normalized events:
    {"kind", "created_at"}, kind in {commit, pr_opened, pr_merged,
    issue_opened, issue_closed}. A single PushEvent can carry several
    commits — since the payload itself no longer says how many (see
    _count_push_commits), one extra API call resolves the real count."""
    event_type = event.get("type")
    payload = event.get("payload", {})

    if event_type == "PushEvent":
        repo_full_name = event.get("repo", {}).get("name")
        before, head = payload.get("before"), payload.get("head")
        if repo_full_name and before and head:
            count = _count_push_commits(repo_full_name, before, head, token)
        else:
            count = 1
        return [{"kind": "commit", "created_at": created_at} for _ in range(count)]

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
            normalized.extend(_normalize_event(event, created_at, token))

        if reached_older_than_since or len(events) < 100:
            break
        page += 1

    return normalized


def verify_access(username: str, token: str) -> tuple[bool, str]:
    """Live, minimal call confirming the token authenticates and can read
    `username`'s events. Does NOT require any events to exist — a valid,
    empty result is a pass (see PLAN.md §2 — this is an access check, not
    an activity check). Used by cli.py accounts add before persisting."""
    resp = requests.get(
        f"{GITHUB_API_BASE}/users/{username}/events",
        headers=_headers(token),
        params={"per_page": 1},
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code == 200:
        return True, "ok"
    if resp.status_code == 401:
        return False, "token is invalid or expired"
    if resp.status_code == 403:
        return False, "token is valid but forbidden (missing scope or rate limited)"
    if resp.status_code == 404:
        return False, f"username '{username}' not found"
    return False, f"unexpected response from GitHub API: {resp.status_code}"
