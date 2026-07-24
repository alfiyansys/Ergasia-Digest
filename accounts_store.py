"""Account storage: CRUD over accounts.json.

Field-level validation only — no network calls here. Live verification
against the real GitHub/GitLab API is cli.py's job (see PLAN.md §2).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from typing import Optional

ACCOUNTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "accounts.json")


@dataclass
class Account:
    id: str
    type: str
    username: str
    api_key: str
    base_url: Optional[str] = None
    label: Optional[str] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


def load_accounts() -> list[dict]:
    if not os.path.exists(ACCOUNTS_FILE):
        return []
    with open(ACCOUNTS_FILE, "r") as f:
        content = f.read().strip()
    return json.loads(content) if content else []


def save_accounts(accounts: list[dict]) -> None:
    with open(ACCOUNTS_FILE, "w") as f:
        json.dump(accounts, f, indent=2)
        f.write("\n")
    os.chmod(ACCOUNTS_FILE, 0o600)
