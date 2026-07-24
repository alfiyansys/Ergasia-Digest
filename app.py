"""FastAPI app: read-only HTTP endpoints for digest data (entrypoint HTTP).

No /accounts endpoint here by design — account management is CLI-only,
see PLAN.md §2 / AGENTS.md. Every endpoint below (including /health, per a
literal reading of PLAN.md §4's endpoint table) is gated by X-API-Key.
There is no persistent state anywhere in this project — every call to
/digest/run is a fresh live fetch from GitHub/GitLab (see AGENTS.md).
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query

import accounts_store
import digest

app = FastAPI(title="Ergasia Digest")


def require_api_key(x_api_key: Optional[str] = Header(None)) -> None:
    expected = os.environ.get("API_KEY")
    if not expected or not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")


@app.get("/health", dependencies=[Depends(require_api_key)])
def health() -> dict:
    return {"status": "ok"}


@app.post("/digest/run", dependencies=[Depends(require_api_key)])
def digest_run(
    account: Optional[str] = Query(None),
    hours: Optional[int] = Query(None),
    days: Optional[int] = Query(None),
) -> dict:
    """Fetch + build the digest, always live. No notify.py call — sending
    to chat is deferred to the agentic harness reading this response
    directly, see PLAN.md §4/§6. Nothing is persisted anywhere; defaults
    to the last DEFAULT_LOOKBACK_HOURS unless overridden."""
    if hours is not None and days is not None:
        raise HTTPException(status_code=400, detail="hours and days are mutually exclusive")

    since_override = None
    if hours is not None:
        since_override = datetime.now(timezone.utc) - timedelta(hours=hours)
    elif days is not None:
        since_override = datetime.now(timezone.utc) - timedelta(days=days)

    accounts = accounts_store.load_accounts()
    if account is not None:
        accounts = [a for a in accounts if a["id"] == account]
        if not accounts:
            raise HTTPException(status_code=404, detail=f"account '{account}' not found")

    results = digest.fetch_all_events(since_override=since_override, accounts=accounts)
    return digest.build_digest(results)
