"""GitLab Events API client (gitlab.com or self-hosted, via base_url).

Takes credentials as parameters — never reads env vars directly, so it can
be reused per account (see PLAN.md §2 / AGENTS.md).
"""

from __future__ import annotations

from datetime import datetime, timezone

import requests

REQUEST_TIMEOUT = 15


class SourceError(Exception):
    """Raised when the GitLab API can't be reached or returns an unexpected shape."""


def _headers(token: str) -> dict:
    return {"PRIVATE-TOKEN": token}


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _resolve_user_id(base_url: str, username: str, token: str) -> int:
    resp = requests.get(
        f"{base_url}/api/v4/users",
        headers=_headers(token),
        params={"username": username},
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code == 401:
        raise SourceError("authentication failed: token is invalid or expired")
    if resp.status_code != 200:
        raise SourceError(f"unexpected GitLab API response resolving username: {resp.status_code}")

    matches = [u for u in resp.json() if u.get("username") == username]
    if not matches:
        raise SourceError(f"username '{username}' not found on {base_url}")
    return matches[0]["id"]


def _normalize_event(event: dict, created_at: datetime) -> list[dict]:
    """Maps one raw GitLab event to zero or more normalized events:
    {"kind", "created_at"}, kind in {commit, pr_opened, pr_merged,
    issue_opened, issue_closed}. A single push event can carry several
    commits (push_data.commit_count), so it expands into one "commit"
    entry per commit."""
    action = event.get("action_name")
    target_type = event.get("target_type")

    if target_type is None and action in ("pushed to", "pushed new"):
        count = (event.get("push_data") or {}).get("commit_count", 1)
        return [{"kind": "commit", "created_at": created_at} for _ in range(count)]

    if target_type == "MergeRequest":
        if action == "opened":
            return [{"kind": "pr_opened", "created_at": created_at}]
        if action in ("merged", "accepted"):
            return [{"kind": "pr_merged", "created_at": created_at}]
        return []

    if target_type == "Issue":
        if action == "opened":
            return [{"kind": "issue_opened", "created_at": created_at}]
        if action == "closed":
            return [{"kind": "issue_closed", "created_at": created_at}]
        return []

    return []


def fetch_events(base_url: str, username: str, token: str, since: datetime) -> list[dict]:
    """Returns normalized events since `since`: [{"kind", "created_at"}, ...]."""
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)

    user_id = _resolve_user_id(base_url, username, token)

    normalized: list[dict] = []
    page = 1
    while True:
        resp = requests.get(
            f"{base_url}/api/v4/users/{user_id}/events",
            headers=_headers(token),
            params={"per_page": 100, "page": page, "after": since.date().isoformat()},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 401:
            raise SourceError("authentication failed: token is invalid or expired")
        if resp.status_code == 403:
            raise SourceError("forbidden: token lacks permission (needs at least 'read_api' scope)")
        if resp.status_code != 200:
            raise SourceError(f"unexpected GitLab API response: {resp.status_code}")

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


def verify_access(base_url: str, username: str, token: str) -> tuple[bool, str]:
    """Live, minimal call confirming the token authenticates, belongs to
    `username`, and can read that user's events. Does NOT require any
    events to exist — a valid, empty result is a pass (see PLAN.md §2 —
    this is an access check, not an activity check). Used by cli.py
    accounts add before persisting."""
    resp = requests.get(f"{base_url}/api/v4/user", headers=_headers(token), timeout=REQUEST_TIMEOUT)
    if resp.status_code == 401:
        return False, "token is invalid or expired"
    if resp.status_code != 200:
        return False, f"unexpected response from GitLab API: {resp.status_code}"

    actual_username = resp.json().get("username")
    if actual_username != username:
        return False, f"token belongs to '{actual_username}', not '{username}'"

    try:
        user_id = _resolve_user_id(base_url, username, token)
    except SourceError as e:
        return False, str(e)

    events_resp = requests.get(
        f"{base_url}/api/v4/users/{user_id}/events",
        headers=_headers(token),
        params={"per_page": 1},
        timeout=REQUEST_TIMEOUT,
    )
    if events_resp.status_code == 200:
        return True, "ok"
    if events_resp.status_code == 403:
        return False, "token is valid but missing 'read_api' scope (needed to read events)"
    return False, f"unexpected response reading events: {events_resp.status_code}"
