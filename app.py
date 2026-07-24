"""FastAPI app: read-only HTTP endpoints for digest data (entrypoint HTTP).

No /accounts endpoint here by design — account management is CLI-only,
see PLAN.md §2 / AGENTS.md. Every endpoint below (including /health, per a
literal reading of PLAN.md §4's endpoint table) is gated by X-API-Key.
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query

app = FastAPI(title="Ergasia Digest")


def require_api_key(x_api_key: Optional[str] = Header(None)) -> None:
    expected = os.environ.get("API_KEY")
    if not expected or not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")
